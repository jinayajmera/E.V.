"""OFFICER area : dashboard, voter verification, people & election management, security, integrity, reports."""
import re
from datetime import date
from flask import (Blueprint, render_template, request, redirect, url_for, flash, session, abort, current_app)
from . import db, crud, voters, security, photos, acid
from .db import AppError, friendly_error
from .auth import officer_required
from .entities import ENTITIES
from .validators import clean, parse_date
from .routes_main import csv_response

officer = Blueprint("officer", __name__, url_prefix="/officer")
PAGE = 12


def _page_args(total):
    page = max(1, request.args.get("page", 1, type=int) or 1)
    pages = max(1, -(-total // PAGE))
    page = min(page, pages)
    return page, pages, (page - 1) * PAGE


def _qs(**over):
    """Current query-string as a dict with some keys replaced (used by pagination links)."""
    d = {k: v for k, v in request.args.items() if k != "page" and v != ""}
    d.update({k: v for k, v in over.items() if v not in (None, "")})
    return d


# ------------------------------------------------------------------ dashboard
@officer.route("/")
@officer_required
def dashboard():
    stats = {
        "voters": db.scalar("SELECT COUNT(*) FROM Voter"),
        "verified": db.scalar("SELECT COUNT(*) FROM Voter WHERE VerificationStatus = 'Verified'"),
        "pending": db.scalar("SELECT COUNT(*) FROM Voter WHERE VerificationStatus IN ('Pending','Flagged')"),
        "elections": db.scalar("SELECT COUNT(*) FROM Election"),
        "active": db.scalar("SELECT COUNT(*) FROM Election WHERE Status = 'Active'"),
        "votes": db.scalar("SELECT COUNT(*) FROM Vote"),
        "noms": db.scalar("SELECT COUNT(*) FROM CandidateNomination WHERE Status = 'Pending'"),
        "alerts": db.scalar("SELECT COUNT(*) FROM SecurityEvent WHERE EventTime > NOW() - INTERVAL 1 DAY "
                            "AND EventType IN ('LOGIN_FAIL','DUPLICATE_BLOCKED','CSRF_REJECTED','LOGIN_BLOCKED')"),
    }
    summary = db.query("SELECT * FROM v_election_summary ORDER BY StartDate DESC")
    for s in summary:
        s["turnout"] = float(s["TurnoutPercent"] or 0)
    audit = db.query("SELECT * FROM AuditLog ORDER BY LogID DESC LIMIT 8")
    by_const = db.query("SELECT co.ConstituencyName AS label, COUNT(v.VoterID) AS n FROM Constituency co "
                        "LEFT JOIN Voter v ON v.ConstituencyID = co.ConstituencyID GROUP BY co.ConstituencyID, co.ConstituencyName "
                        "ORDER BY n DESC, co.ConstituencyName LIMIT 8")
    top = max((r["n"] for r in by_const), default=0) or 1
    for r in by_const:
        r["pct"] = round(100 * r["n"] / top)
    queue = db.query("SELECT VoterID, Name, VerificationStatus, RegistrationDate FROM Voter "
                     "WHERE VerificationStatus IN ('Pending','Flagged') ORDER BY RegistrationDate, VoterID LIMIT 5")
    return render_template("officer_dashboard.html", stats=stats, summary=summary, audit=audit,
                           by_const=by_const, queue=queue)


# ------------------------------------------------------------------ voters
def _voter_filter():
    q = clean(request.args.get("q"))
    ver = request.args.get("verification", "")
    acc = request.args.get("account", "")
    cons = request.args.get("constituency", type=int)
    sql, params = " FROM v_voter_card v WHERE 1 = 1", []
    if q:
        sql += (" AND (v.Name LIKE %s OR v.Username LIKE %s OR v.Email LIKE %s OR v.NationalIDLast4 = %s "
                "OR CAST(v.VoterID AS CHAR) = %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%", q.upper(), q]
    if ver in ("Pending", "Verified", "Rejected", "Flagged"):
        sql += " AND v.VerificationStatus = %s"
        params.append(ver)
    if acc in ("Active", "Suspended", "Inactive"):
        sql += " AND v.AccountStatus = %s"
        params.append(acc)
    if cons:
        sql += " AND v.ConstituencyID = %s"
        params.append(cons)
    return sql, params, dict(q=q, verification=ver, account=acc, constituency=cons)


@officer.route("/voters")
@officer_required
def voters_list():
    sql, params, f = _voter_filter()
    total = db.scalar("SELECT COUNT(*)" + sql, params)
    page, pages, off = _page_args(total)
    rows = db.query("SELECT v.*" + sql + " ORDER BY v.VoterID DESC LIMIT %s OFFSET %s", params + [PAGE, off])
    return render_template("voters_list.html", rows=rows, total=total, page=page, pages=pages, f=f,
                           constituencies=voters.constituency_choices(), qs=_qs)


@officer.route("/voters.csv")
@officer_required
def voters_csv():
    sql, params, _ = _voter_filter()
    rows = db.query("SELECT v.VoterID, v.Username, v.Name, v.Gender, v.DOB, v.Email, v.Phone, v.ConstituencyName, "
                    "v.AccountStatus, v.VerificationStatus, v.NationalIDLast4, v.RegistrationDate, v.VotesCast"
                    + sql + " ORDER BY v.VoterID", params)
    cols = ["VoterID", "Username", "Name", "Gender", "DOB", "Email", "Phone", "ConstituencyName", "AccountStatus",
            "VerificationStatus", "NationalIDLast4", "RegistrationDate", "VotesCast"]
    return csv_response("voters.csv", cols, rows)


@officer.route("/voters/new", methods=["GET", "POST"])
@officer_required
def voter_new():
    values = {}
    if request.method == "POST":
        values = {k: v for k, v in request.form.items() if k not in ("password", "password2", "csrf_token")}
        try:
            vid, status = voters.register_voter(request.form, request.files.get("photo"), officer_id=session["uid"])
            flash(f"Voter #{vid} registered ({status}).", "success")
            return redirect(url_for("officer.voter_detail", vid=vid))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("voter_new.html", constituencies=voters.constituency_choices(), values=values)


@officer.route("/voters/<int:vid>")
@officer_required
def voter_detail(vid):
    v = db.query("SELECT * FROM v_voter_card WHERE VoterID = %s", (vid,), one=True)
    if not v:
        abort(404)
    v["Address"] = db.scalar("SELECT Address FROM Voter WHERE VoterID = %s", (vid,))
    v["VerifiedByName"] = db.scalar("SELECT o.Name FROM Voter x JOIN ElectionOfficer o ON o.OfficerID = x.VerifiedBy "
                                    "WHERE x.VoterID = %s", (vid,))
    suspects = db.query(
        "SELECT s.Reason, o.VoterID, o.Name, o.VerificationStatus FROM v_duplicate_suspects s "
        "JOIN Voter o ON o.VoterID = IF(s.VoterA = %s, s.VoterB, s.VoterA) WHERE %s IN (s.VoterA, s.VoterB)", (vid, vid))
    elections = db.query("SELECT e.ElectionCode, e.ElectionName, ev.EligibilityStatus, ev.HasVoted, ev.VotedTimestamp "
                         "FROM ElectionVoter ev JOIN Election e ON e.ElectionID = ev.ElectionID "
                         "WHERE ev.VoterID = %s ORDER BY e.StartDate DESC", (vid,))
    events = db.query("SELECT EventTime, EventType, IpAddress, Details FROM SecurityEvent "
                      "WHERE Role = 'voter' AND Username = %s ORDER BY EventID DESC LIMIT 8", (v["Username"],))
    locked, mins = security.lock_status("voter", v["Username"])
    return render_template("voter_detail.html", v=v, suspects=suspects, elections=elections, events=events,
                           locked=locked, mins=mins)


@officer.route("/voters/<int:vid>/edit", methods=["GET", "POST"])
@officer_required
def voter_edit(vid):
    row = db.query("SELECT * FROM Voter WHERE VoterID = %s", (vid,), one=True)
    if not row:
        abort(404)
    values = {"name": row["Name"], "email": row["Email"], "phone": row["Phone"] or "", "dob": str(row["DOB"]),
              "gender": row["Gender"], "address": row["Address"] or "", "constituency_id": str(row["ConstituencyID"]),
              "account_status": row["AccountStatus"]}
    if request.method == "POST":
        values = {k: v for k, v in request.form.items() if k not in ("password", "csrf_token")}
        try:
            voters.update_details(vid, request.form)
            flash("Voter details updated.", "success")
            return redirect(url_for("officer.voter_detail", vid=vid))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("voter_edit.html", v=row, values=values, constituencies=voters.constituency_choices())


@officer.route("/voters/<int:vid>/photo", methods=["POST"])
@officer_required
def voter_photo(vid):
    try:
        voters.change_photo(vid, request.files.get("photo"), by_officer=True)
        flash("Photograph replaced.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.voter_detail", vid=vid))


@officer.route("/voters/<int:vid>/identity", methods=["POST"])
@officer_required
def voter_identity(vid):
    try:
        voters.set_identity(vid, request.form.get("identity_number"))
        flash("Identity number saved.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.voter_detail", vid=vid))


@officer.route("/voters/<int:vid>/verify", methods=["POST"])
@officer_required
def voter_verify(vid):
    decision = request.form.get("decision", "")
    try:
        voters.verify(vid, session["uid"], decision, clean(request.form.get("note")))
        flash(f"Voter #{vid} marked {decision}.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    nxt = request.form.get("next")
    if nxt == "queue":
        return redirect(url_for("officer.verification"))
    return redirect(url_for("officer.voter_detail", vid=vid))


@officer.route("/voters/<int:vid>/status", methods=["POST"])
@officer_required
def voter_status(vid):
    new = request.form.get("status")
    try:
        if new not in ("Active", "Suspended", "Inactive"):
            raise AppError("Invalid status.")
        db.execute("UPDATE Voter SET AccountStatus = %s WHERE VoterID = %s", (new, vid))
        flash(f"Account set to {new}.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(request.referrer or url_for("officer.voter_detail", vid=vid))


@officer.route("/voters/<int:vid>/unlock", methods=["POST"])
@officer_required
def voter_unlock(vid):
    name = db.scalar("SELECT Username FROM Voter WHERE VoterID = %s", (vid,))
    if not name:
        abort(404)
    security.unlock_account("voter", name)
    flash("Sign-in lock cleared.", "success")
    return redirect(url_for("officer.voter_detail", vid=vid))


@officer.route("/voters/<int:vid>/delete", methods=["POST"])
@officer_required
def voter_delete(vid):
    try:
        if voters.delete_voter(vid):
            flash("Voter deleted.", "success")
            return redirect(url_for("officer.voters_list"))
        flash("Nothing to delete.", "warning")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.voter_detail", vid=vid))


@officer.route("/verification")
@officer_required
def verification():
    rows = db.query("SELECT v.*, (SELECT COUNT(*) FROM v_duplicate_suspects s WHERE v.VoterID IN (s.VoterA, s.VoterB)) AS Suspects "
                    "FROM v_voter_card v WHERE v.VerificationStatus IN ('Pending','Flagged') "
                    "ORDER BY FIELD(v.VerificationStatus,'Flagged','Pending'), v.VoterID")
    return render_template("verification.html", rows=rows)


# ------------------------------------------------------------------ candidates: photo
@officer.route("/candidates/<int:cid>/photo", methods=["POST"])
@officer_required
def candidate_photo(cid):
    try:
        if not db.scalar("SELECT COUNT(*) FROM Candidate WHERE CandidateID = %s", (cid,)):
            raise AppError("Candidate not found.")
        info = photos.process_upload(request.files.get("photo"))
        name = photos.save_candidate_photo(info["data"])
        db.execute("UPDATE Candidate SET Photo = %s WHERE CandidateID = %s", (name, cid))
        flash("Candidate picture updated.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.crud_edit", entity="candidates", rid=cid))


# ------------------------------------------------------------------ security log + integrity
@officer.route("/security")
@officer_required
def security_log():
    t = request.args.get("type", "")
    q = clean(request.args.get("q"))
    sql, params = " FROM SecurityEvent WHERE 1 = 1", []
    if t:
        sql += " AND EventType = %s"
        params.append(t)
    if q:
        sql += " AND (Username LIKE %s OR IpAddress LIKE %s OR Details LIKE %s)"
        params += [f"%{q}%"] * 3
    total = db.scalar("SELECT COUNT(*)" + sql, params)
    page, pages, off = _page_args(total)
    rows = db.query("SELECT *" + sql + " ORDER BY EventID DESC LIMIT %s OFFSET %s", params + [PAGE, off])
    types = [r["EventType"] for r in db.query("SELECT DISTINCT EventType FROM SecurityEvent ORDER BY EventType")]
    summary = db.query("SELECT EventType, COUNT(*) AS n FROM SecurityEvent WHERE EventTime > NOW() - INTERVAL 7 DAY "
                       "GROUP BY EventType ORDER BY n DESC")
    locked = db.query("SELECT Role, Username, COUNT(*) AS Fails FROM SecurityEvent WHERE EventType = 'LOGIN_FAIL' "
                      "AND EventTime > NOW() - INTERVAL %s MINUTE GROUP BY Role, Username HAVING COUNT(*) >= %s",
                      (current_app.config["LOGIN_LOCK_MINUTES"], current_app.config["LOGIN_MAX_FAILS"]))
    locked = [x for x in locked if security.lock_status(x["Role"], x["Username"])[0]]
    return render_template("security_log.html", rows=rows, total=total, page=page, pages=pages, t=t, q=q,
                           types=types, summary=summary, locked=locked, qs=_qs)


@officer.route("/security/unlock", methods=["POST"])
@officer_required
def security_unlock():
    role, name = request.form.get("role"), clean(request.form.get("username"))
    if role in ("voter", "officer") and name:
        security.unlock_account(role, name)
        flash(f"{name} can sign in again.", "success")
    return redirect(url_for("officer.security_log"))


@officer.route("/integrity")
@officer_required
def integrity():
    checks = db.query("SELECT * FROM v_integrity_checks ORDER BY CheckNo")
    suspects = db.query("SELECT s.Reason, a.VoterID AS AID, a.Name AS AName, b.VoterID AS BID, b.Name AS BName "
                        "FROM v_duplicate_suspects s JOIN Voter a ON a.VoterID = s.VoterA JOIN Voter b ON b.VoterID = s.VoterB "
                        "ORDER BY s.Reason, a.VoterID")
    vars_ = {r["Variable_name"]: r["Value"] for r in db.query(
        "SHOW VARIABLES WHERE Variable_name IN ('version','innodb_flush_log_at_trx_commit','transaction_isolation',"
        "'default_storage_engine')")}
    tables = db.query("SELECT TABLE_NAME AS name, TABLE_ROWS AS n, ENGINE AS engine FROM information_schema.TABLES "
                      "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME")
    return render_template("integrity.html", checks=checks, suspects=suspects, vars=vars_, tables=tables, demo=None)


@officer.route("/integrity/acid", methods=["POST"])
@officer_required
def integrity_acid():
    demo = acid.run_all()
    checks = db.query("SELECT * FROM v_integrity_checks ORDER BY CheckNo")
    suspects = []
    vars_ = {r["Variable_name"]: r["Value"] for r in db.query(
        "SHOW VARIABLES WHERE Variable_name IN ('version','innodb_flush_log_at_trx_commit','transaction_isolation',"
        "'default_storage_engine')")}
    tables = db.query("SELECT TABLE_NAME AS name, TABLE_ROWS AS n, ENGINE AS engine FROM information_schema.TABLES "
                      "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME")
    return render_template("integrity.html", checks=checks, suspects=suspects, vars=vars_, tables=tables, demo=demo)


# ------------------------------------------------------------------ generic CRUD
def _ctx(id_=None):
    return {"current_officer": session.get("uid"), "id": id_}


def _spec(entity):
    spec = ENTITIES.get(entity)
    if not spec:
        abort(404)
    return spec


@officer.route("/manage/<entity>")
@officer_required
def crud_list(entity):
    spec = _spec(entity)
    q = clean(request.args.get("q"))
    rows = crud.list_rows(spec, q)
    return render_template("crud_list.html", spec=spec, entity=entity, rows=rows, q=q)


@officer.route("/manage/<entity>/new", methods=["GET", "POST"])
@officer_required
def crud_new(entity):
    spec = _spec(entity)
    choices = crud.load_choices(spec)
    values = crud.blank_values(spec, _ctx())
    if request.method == "POST":
        values = {f["name"]: request.form.get(f["name"], "") for f in spec["fields"] if f["type"] != "password"}
        try:
            data = crud.parse_form(spec, request.form, True, choices)
            if spec.get("hook"):
                spec["hook"](data, True, _ctx())
            new_id = db.insert_row(spec["table"], spec["pk"], data)
            flash(f"{spec['title']} added (ID {new_id}).", "success")
            return redirect(url_for("officer.crud_list", entity=entity))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("crud_form.html", spec=spec, entity=entity, choices=choices,
                           values=values, is_new=True, rid=None)


@officer.route("/manage/<entity>/<int:rid>/edit", methods=["GET", "POST"])
@officer_required
def crud_edit(entity, rid):
    spec = _spec(entity)
    row = db.query(f"SELECT * FROM {spec['table']} WHERE {spec['pk']} = %s", (rid,), one=True)
    if not row:
        abort(404)
    choices = crud.load_choices(spec)
    values = crud.row_to_values(spec, row)
    if request.method == "POST":
        values = {f["name"]: request.form.get(f["name"], "") for f in spec["fields"]
                  if f["type"] != "password" and f["edit"]}
        for f in spec["fields"]:               # keep read-only values visible
            if not f["edit"]:
                values[f["name"]] = "" if row.get(f["name"]) is None else str(row[f["name"]])
        try:
            data = crud.parse_form(spec, request.form, False, choices)
            if spec.get("hook"):
                spec["hook"](dict(row, **data), False, _ctx(rid))
            if data:
                sets = ", ".join(f"{k} = %s" for k in data)
                db.execute(f"UPDATE {spec['table']} SET {sets} WHERE {spec['pk']} = %s",
                           list(data.values()) + [rid])
            flash(f"{spec['title']} updated.", "success")
            return redirect(url_for("officer.crud_list", entity=entity))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("crud_form.html", spec=spec, entity=entity, choices=choices,
                           values=values, is_new=False, rid=rid)


@officer.route("/manage/<entity>/<int:rid>/delete", methods=["POST"])
@officer_required
def crud_delete(entity, rid):
    spec = _spec(entity)
    try:
        if spec.get("delete_guard"):
            spec["delete_guard"](rid, _ctx(rid))
        n = db.execute(f"DELETE FROM {spec['table']} WHERE {spec['pk']} = %s", (rid,))
        flash(f"{spec['title']} deleted." if n else "Nothing to delete.", "success" if n else "warning")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.crud_list", entity=entity))


@officer.route("/manage/<entity>/<int:rid>/status", methods=["POST"])
@officer_required
def crud_status(entity, rid):
    """Quick suspend / activate for voters, candidates and officers."""
    spec = _spec(entity)
    new = request.form.get("status")
    if "status_field" not in spec and entity not in ("candidates", "officers"):
        abort(404)
    try:
        if new not in ("Active", "Suspended", "Inactive"):
            raise AppError("Invalid status.")
        if entity == "officers" and rid == session["uid"] and new != "Active":
            raise AppError("You cannot deactivate your own account.")
        db.execute(f"UPDATE {spec['table']} SET AccountStatus = %s WHERE {spec['pk']} = %s", (new, rid))
        flash(f"Account set to {new}.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(request.referrer or url_for("officer.crud_list", entity=entity))


# ------------------------------------------------------------------ election actions
@officer.route("/elections/<int:eid>")
@officer_required
def election_detail(eid):
    el = db.query("SELECT * FROM v_election_summary WHERE ElectionID = %s", (eid,), one=True)
    if not el:
        abort(404)
    noms = db.query("SELECT n.*, ca.Name AS CandidateName, p.PartyName, co.ConstituencyName, "
                    "fn_votes_for(n.ElectionID, n.CandidateID) AS Votes "
                    "FROM CandidateNomination n JOIN Candidate ca ON ca.CandidateID = n.CandidateID "
                    "JOIN PoliticalParty p ON p.PartyID = ca.PartyID "
                    "JOIN Constituency co ON co.ConstituencyID = n.ConstituencyID "
                    "WHERE n.ElectionID = %s ORDER BY co.ConstituencyName, ca.Name", (eid,))
    return render_template("election_detail.html", el=el, noms=noms)


@officer.route("/elections/<int:eid>/open", methods=["POST"])
@officer_required
def election_open(eid):
    try:
        st = db.scalar("SELECT Status FROM Election WHERE ElectionID = %s", (eid,))
        if st != "Upcoming":
            raise AppError("Only an Upcoming election can be opened.")
        db.execute("UPDATE Election SET Status = 'Active' WHERE ElectionID = %s", (eid,))
        flash("Election is now Active - eligible voters can vote.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.election_detail", eid=eid))


@officer.route("/elections/<int:eid>/close", methods=["POST"])
@officer_required
def election_close(eid):
    try:
        res = db.call_proc("sp_close_election_and_generate_results", [eid, None])
        flash(f"Election closed. {res[1]} result rows generated.", "success")
        return redirect(url_for("main.results", eid=eid))
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
        return redirect(url_for("officer.election_detail", eid=eid))


# ------------------------------------------------------------------ nominations
def _nom_form_data():
    return dict(
        candidates=db.query("SELECT CandidateID, Name FROM Candidate WHERE AccountStatus = 'Active' ORDER BY Name"),
        elections=db.query("SELECT ElectionID, ElectionCode, Status FROM Election WHERE Status <> 'Closed' ORDER BY ElectionCode"),
        constituencies=db.query("SELECT ConstituencyID, ConstituencyName FROM Constituency ORDER BY ConstituencyName"))


@officer.route("/nominations")
@officer_required
def nominations():
    eid = request.args.get("election", type=int)
    status = request.args.get("status", "")
    sql = ("SELECT n.NominationID, e.ElectionCode, ca.Name AS CandidateName, p.PartyName, "
           "co.ConstituencyName, n.NominationDate, n.Status, e.Status AS ElectionStatus "
           "FROM CandidateNomination n JOIN Election e ON e.ElectionID = n.ElectionID "
           "JOIN Candidate ca ON ca.CandidateID = n.CandidateID "
           "JOIN PoliticalParty p ON p.PartyID = ca.PartyID "
           "JOIN Constituency co ON co.ConstituencyID = n.ConstituencyID WHERE 1 = 1")
    params = []
    if eid:
        sql += " AND n.ElectionID = %s"
        params.append(eid)
    if status in ("Pending", "Approved", "Rejected"):
        sql += " AND n.Status = %s"
        params.append(status)
    rows = db.query(sql + " ORDER BY e.ElectionCode, co.ConstituencyName, ca.Name", params)
    elections = db.query("SELECT ElectionID, ElectionCode FROM Election ORDER BY ElectionCode")
    return render_template("nominations.html", rows=rows, elections=elections, eid=eid, status=status)


@officer.route("/nominations/new", methods=["GET", "POST"])
@officer_required
def nomination_new():
    form = _nom_form_data()
    values = {"NominationDate": date.today().isoformat()}
    if request.method == "POST":
        values = request.form.to_dict()
        try:
            cid = int(values.get("CandidateID", ""))
            eid = int(values.get("ElectionID", ""))
            coid = int(values.get("ConstituencyID", ""))
            nd = parse_date(clean(values.get("NominationDate")), "Nomination date")
            new_id = db.insert_row("CandidateNomination", "NominationID",
                                   dict(CandidateID=cid, ElectionID=eid, ConstituencyID=coid,
                                        NominationDate=nd, Status="Pending"))
            flash(f"Nomination {new_id} filed (status Pending).", "success")
            return redirect(url_for("officer.nominations", election=eid))
        except ValueError:
            flash("Please select a candidate, election and constituency.", "danger")
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("nomination_form.html", values=values, **form)


@officer.route("/nominations/<int:nid>/status", methods=["POST"])
@officer_required
def nomination_status(nid):
    new = request.form.get("status")
    try:
        if new not in ("Approved", "Rejected", "Pending"):
            raise AppError("Invalid status.")
        row = db.query("SELECT n.*, e.Status AS ElectionStatus FROM CandidateNomination n "
                       "JOIN Election e ON e.ElectionID = n.ElectionID WHERE n.NominationID = %s", (nid,), one=True)
        if not row:
            raise AppError("Nomination not found.")
        if row["ElectionStatus"] == "Closed":
            raise AppError("The election is closed - nominations can no longer be changed.")
        if row["Status"] == "Approved" and new != "Approved" and db.scalar(
                "SELECT COUNT(*) FROM Vote WHERE ElectionID = %s AND CandidateID = %s",
                (row["ElectionID"], row["CandidateID"])):
            raise AppError("Votes were already cast for this candidate; the nomination cannot be withdrawn.")
        db.execute("UPDATE CandidateNomination SET Status = %s WHERE NominationID = %s", (new, nid))
        flash(f"Nomination {nid} set to {new}.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(request.referrer or url_for("officer.nominations"))


@officer.route("/nominations/<int:nid>/delete", methods=["POST"])
@officer_required
def nomination_delete(nid):
    try:
        row = db.query("SELECT n.*, e.Status AS ElectionStatus FROM CandidateNomination n "
                       "JOIN Election e ON e.ElectionID = n.ElectionID WHERE n.NominationID = %s", (nid,), one=True)
        if not row:
            raise AppError("Nomination not found.")
        if row["ElectionStatus"] == "Closed":
            raise AppError("The election is closed - nominations can no longer be deleted.")
        if row["Status"] == "Approved" or db.scalar(
                "SELECT COUNT(*) FROM Vote WHERE ElectionID = %s AND CandidateID = %s",
                (row["ElectionID"], row["CandidateID"])):
            raise AppError("Only Pending / Rejected nominations without votes can be deleted. Reject it first.")
        db.execute("DELETE FROM CandidateNomination WHERE NominationID = %s", (nid,))
        flash("Nomination deleted.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(request.referrer or url_for("officer.nominations"))


# ------------------------------------------------------------------ election voters (eligibility)
@officer.route("/eligibility")
@officer_required
def eligibility():
    elections = db.query("SELECT ElectionID, ElectionCode, ElectionName, Status FROM Election ORDER BY ElectionCode")
    eid = request.args.get("election", type=int) or (elections[0]["ElectionID"] if elections else None)
    rows, voters, constituencies, el = [], [], [], None
    if eid:
        el = db.query("SELECT * FROM Election WHERE ElectionID = %s", (eid,), one=True)
        rows = db.query("SELECT ev.ElectionVoterID, ev.VoterID, v.Name, v.Username, co.ConstituencyName, "
                        "ev.EligibilityStatus, ev.HasVoted, ev.VotedTimestamp "
                        "FROM ElectionVoter ev JOIN Voter v ON v.VoterID = ev.VoterID "
                        "JOIN Constituency co ON co.ConstituencyID = v.ConstituencyID "
                        "WHERE ev.ElectionID = %s ORDER BY v.Name", (eid,))
        voters = db.query("SELECT VoterID, Name, Username FROM Voter WHERE VerificationStatus = 'Verified' AND AccountStatus = 'Active' AND VoterID NOT IN "
                          "(SELECT VoterID FROM ElectionVoter WHERE ElectionID = %s) ORDER BY Name", (eid,))
        constituencies = db.query("SELECT ConstituencyID, ConstituencyName FROM Constituency ORDER BY ConstituencyName")
    return render_template("eligibility.html", elections=elections, eid=eid, el=el, rows=rows,
                           voters=voters, constituencies=constituencies)


@officer.route("/eligibility/add", methods=["POST"])
@officer_required
def eligibility_add():
    eid = request.form.get("election_id", type=int)
    try:
        vid = request.form.get("voter_id", type=int)
        status = request.form.get("status", "Eligible")
        if not eid or not vid or status not in ("Eligible", "Not Eligible"):
            raise AppError("Select a voter.")
        if db.scalar("SELECT Status FROM Election WHERE ElectionID = %s", (eid,)) == "Closed":
            raise AppError("Cannot add voters to a closed election.")
        db.insert_row("ElectionVoter", "ElectionVoterID",
                      dict(ElectionID=eid, VoterID=vid, EligibilityStatus=status, HasVoted=0, VotedTimestamp=None))
        flash("Voter added to the election.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.eligibility", election=eid))


@officer.route("/eligibility/bulk", methods=["POST"])
@officer_required
def eligibility_bulk():
    eid = request.form.get("election_id", type=int)
    try:
        coid = request.form.get("constituency_id", type=int)
        if not eid or not coid:
            raise AppError("Select a constituency.")
        res = db.call_proc("sp_assign_constituency_voters", [eid, coid, None])
        flash(f"{res[2]} voter(s) added to the election.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.eligibility", election=eid))


@officer.route("/eligibility/<int:evid>/toggle", methods=["POST"])
@officer_required
def eligibility_toggle(evid):
    row = db.query("SELECT ev.*, e.Status AS ElectionStatus FROM ElectionVoter ev "
                   "JOIN Election e ON e.ElectionID = ev.ElectionID WHERE ElectionVoterID = %s", (evid,), one=True)
    if not row:
        abort(404)
    try:
        if row["ElectionStatus"] == "Closed":
            raise AppError("The election is closed.")
        new = "Not Eligible" if row["EligibilityStatus"] == "Eligible" else "Eligible"
        db.execute("UPDATE ElectionVoter SET EligibilityStatus = %s WHERE ElectionVoterID = %s", (new, evid))
        flash(f"Eligibility set to {new}.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.eligibility", election=row["ElectionID"]))


@officer.route("/eligibility/<int:evid>/remove", methods=["POST"])
@officer_required
def eligibility_remove(evid):
    row = db.query("SELECT * FROM ElectionVoter WHERE ElectionVoterID = %s", (evid,), one=True)
    if not row:
        abort(404)
    try:
        if row["HasVoted"]:
            raise AppError("This voter has already voted and cannot be removed from the election.")
        db.execute("DELETE FROM ElectionVoter WHERE ElectionVoterID = %s", (evid,))
        flash("Voter removed from the election.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("officer.eligibility", election=row["ElectionID"]))


# ------------------------------------------------------------------ audit + reports
@officer.route("/audit")
@officer_required
def audit():
    table = clean(request.args.get("table"))
    action = request.args.get("action", "")
    q = clean(request.args.get("q"))
    sql, params = " FROM AuditLog WHERE 1 = 1", []
    if table:
        sql += " AND TableName = %s"
        params.append(table)
    if action and re.fullmatch(r"[A-Z_]{3,30}", action):
        sql += " AND Action = %s"
        params.append(action)
    if q:
        sql += " AND (CAST(RecordID AS CHAR) = %s OR Details LIKE %s)"
        params += [q, f"%{q}%"]
    total = db.scalar("SELECT COUNT(*)" + sql, params)
    page, pages, off = _page_args(total)
    rows = db.query("SELECT *" + sql + " ORDER BY LogID DESC LIMIT %s OFFSET %s", params + [PAGE, off])
    tables = [r["TableName"] for r in db.query("SELECT DISTINCT TableName FROM AuditLog ORDER BY TableName")]
    return render_template("audit.html", rows=rows, total=total, page=page, pages=pages, table=table,
                           action=action, q=q, tables=tables, qs=_qs)


_REPORT_CACHE = {}


def load_reports():
    """Parse sql/queries.sql into [{no, title, group, sql}] so the app and the SQL
    script always show the very same queries."""
    path = current_app.config["SQL_DIR"] / "queries.sql"
    mtime = path.stat().st_mtime
    if _REPORT_CACHE.get("m") == mtime:
        return _REPORT_CACHE["r"]
    reports, group, cur = [], "General", None
    for line in path.read_text(encoding="utf-8").splitlines():
        mg = re.match(r"^-- =+\s*(.+?)\s*=*\s*$", line)
        mq = re.match(r"^-- (Q\d+[a-z]?)\.\s*(.+)$", line)
        if mg and not mq:
            group = mg.group(1).strip("= ")
            continue
        if mq:
            cur = dict(no=mq.group(1), title=mq.group(2), group=group, lines=[])
            reports.append(cur)
            continue
        if cur is not None and not line.startswith("--") and line.strip():
            cur["lines"].append(line)
    for r in reports:
        r["sql"] = "\n".join(r.pop("lines")).strip().rstrip(";").strip()
    reports = [r for r in reports if r["sql"]]
    _REPORT_CACHE.update(m=mtime, r=reports)
    return reports


@officer.route("/reports")
@officer_required
def reports():
    groups = {}
    for r in load_reports():
        groups.setdefault(r["group"], []).append(r)
    return render_template("reports.html", groups=groups)


@officer.route("/reports/<no>")
@officer_required
def report_run(no):
    rep = next((r for r in load_reports() if r["no"] == no), None)
    if not rep:
        abort(404)
    rows, err = [], None
    try:
        rows = db.query(rep["sql"])
    except Exception as e:          # noqa
        err = friendly_error(e)
    cols = list(rows[0].keys()) if rows else []
    if request.args.get("format") == "csv" and not err:
        return csv_response(f"report_{rep['no']}.csv", cols, rows)
    return render_template("report_run.html", rep=rep, rows=rows, cols=cols, err=err)
