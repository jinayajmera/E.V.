"""Public pages, sign-in / registration, media, and the VOTER area."""
import csv
import io
from flask import (Blueprint, render_template, request, redirect, url_for, flash, session,
                   jsonify, abort, Response, send_file)
from . import db, security, photos, voters
from .db import AppError, friendly_error
from .auth import (authenticate, login_user, voter_required, hash_password, check_password,
                   is_active_session)
from .validators import clean, check_password_strength, EMAIL_RE, PHONE_RE

main = Blueprint("main", __name__)
voter = Blueprint("voter", __name__, url_prefix="/voter")


def _safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") and "\\" not in url else None


def csv_response(filename, columns, rows):
    """Excel-friendly CSV. Cells that start with = + - @ are neutralised (CSV injection)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        out = []
        for c in columns:
            v = r[c] if isinstance(r, dict) else r[columns.index(c)]
            v = "" if v is None else str(v)
            if v[:1] in ("=", "+", "-", "@"):
                v = "'" + v
            out.append(v)
        w.writerow(out)
    resp = Response("﻿" + buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ------------------------------------------------------------------ public
@main.route("/")
def home():
    elections = db.query("SELECT * FROM v_election_summary ORDER BY FIELD(Status,'Active','Upcoming','Closed'), StartDate DESC")
    featured = db.query(
        "SELECT b.CandidateID, b.CandidateName, b.PartyName, b.Symbol, b.ConstituencyName, b.ElectionCode "
        "FROM v_ballot b WHERE b.ElectionStatus = 'Active' ORDER BY b.ElectionID, b.ConstituencyName, b.CandidateName LIMIT 3")
    if not featured:
        featured = db.query(
            "SELECT b.CandidateID, b.CandidateName, b.PartyName, b.Symbol, b.ConstituencyName, b.ElectionCode "
            "FROM v_ballot b ORDER BY b.ElectionID DESC, b.ConstituencyName, b.CandidateName LIMIT 3")
    stats = {
        "voters": db.scalar("SELECT COUNT(*) FROM Voter WHERE VerificationStatus = 'Verified'"),
        "elections": db.scalar("SELECT COUNT(*) FROM Election"),
        "votes": db.scalar("SELECT COUNT(*) FROM Vote"),
    }
    return render_template("home.html", elections=elections, featured=featured, stats=stats)


@main.route("/health")
def health():
    """Liveness / readiness probe (used by hosting platforms and monitors). No internal details are exposed."""
    try:
        db.scalar("SELECT 1")
        return jsonify(status="ok", service="up", data="connected")
    except Exception as e:          # noqa
        db.close_db()
        return jsonify(status="degraded", service="up", data="unavailable"), 503


@main.route("/media/voter/<int:vid>")
def media_voter(vid):
    """A voter's photograph: visible to the voter and to election officers only."""
    allowed = (is_active_session("officer")) or (is_active_session("voter") and session.get("uid") == vid)
    if not allowed:
        abort(403)
    row = db.query("SELECT Name, PhotoFile FROM Voter WHERE VoterID = %s", (vid,), one=True)
    if not row:
        abort(404)
    p = photos.voter_photo_path(row["PhotoFile"])
    if p:
        resp = send_file(p, mimetype="image/jpeg", max_age=0)
    else:
        resp = Response(photos.avatar_svg(f"voter-{vid}-{row['Name']}"), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@main.route("/media/candidate/<int:cid>")
def media_candidate(cid):
    """Candidate pictures are public (they appear on the ballot)."""
    row = db.query("SELECT Name, Photo FROM Candidate WHERE CandidateID = %s", (cid,), one=True)
    if not row:
        abort(404)
    p = photos.candidate_photo_path(row["Photo"])
    if p:
        mt = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        resp = send_file(p, mimetype=mt, max_age=3600)
    else:
        resp = Response(photos.avatar_svg(f"candidate-{cid}-{row['Name']}"), mimetype="image/svg+xml")
        resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@main.route("/elections/<int:eid>")
def election_public(eid):
    el = db.query("SELECT * FROM v_election_summary WHERE ElectionID = %s", (eid,), one=True)
    if not el:
        abort(404)
    ballot = db.query("SELECT * FROM v_ballot WHERE ElectionID = %s ORDER BY ConstituencyName, CandidateName", (eid,))
    groups = {}
    for r in ballot:
        groups.setdefault(r["ConstituencyName"], []).append(r)
    return render_template("election_public.html", el=el, groups=groups)


@main.route("/results")
def results_index():
    q = clean(request.args.get("q"))
    status = request.args.get("status", "")
    sql, params = "SELECT * FROM v_election_summary WHERE 1 = 1", []
    if q:
        sql += " AND (ElectionName LIKE %s OR ElectionCode LIKE %s OR ElectionType LIKE %s)"
        params += [f"%{q}%"] * 3
    if status in ("Active", "Upcoming", "Closed"):
        sql += " AND Status = %s"
        params.append(status)
    rows = db.query(sql + " ORDER BY StartDate DESC", params)
    return render_template("results_index.html", elections=rows, q=q, status=status)


def _results_data(eid):
    el = db.query("SELECT * FROM v_election_summary WHERE ElectionID = %s", (eid,), one=True)
    if not el:
        abort(404)
    rows = []
    if el["Status"] == "Closed":
        rows = db.query("SELECT * FROM v_public_results WHERE ElectionID = %s "
                        "ORDER BY ConstituencyName, VoteCount DESC, CandidateName", (eid,))
    return el, rows


@main.route("/results/<int:eid>")
def results(eid):
    el, rows = _results_data(eid)
    groups = {}
    for r in rows:
        groups.setdefault(r["ConstituencyName"], []).append(r)
    for g in groups.values():
        top = max((x["VoteCount"] for x in g), default=0) or 1
        for x in g:
            x["pct"] = round(100 * x["VoteCount"] / top)
    return render_template("results.html", el=el, groups=groups)


@main.route("/results/<int:eid>.csv")
def results_csv(eid):
    el, rows = _results_data(eid)
    if el["Status"] != "Closed":
        abort(404)
    cols = ["ConstituencyName", "CandidateName", "PartyName", "VoteCount", "ResultStatus"]
    return csv_response(f"results_{el['ElectionCode']}.csv", cols, rows)


# ------------------------------------------------------------------ sign in / out
@main.route("/login", methods=["GET", "POST"])
def login():
    role = request.values.get("role", "voter")
    if role not in ("voter", "officer"):
        role = "voter"
    if request.method == "POST":
        username = clean(request.form.get("username"))[:50]
        password = request.form.get("password") or ""
        try:
            row, msg = authenticate(role, username, password)
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
            return render_template("login.html", login_role=role), 503
        if not row:
            flash(msg, "danger")
            return render_template("login.html", login_role=role, username=username), 401
        login_user(role, row)
        flash(f"Welcome, {row['name']}.", "success")
        nxt = _safe_next(request.args.get("next"))
        if nxt and nxt.startswith("/" + role):
            return redirect(nxt)
        return redirect(url_for("officer.dashboard" if role == "officer" else "voter.dashboard"))
    return render_template("login.html", login_role=role)


@main.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.home"))


@main.route("/register", methods=["GET", "POST"])
def register():
    constituencies = voters.constituency_choices()
    values = {}
    if request.method == "POST":
        values = {k: v for k, v in request.form.items() if k not in ("password", "password2", "csrf_token")}
        try:
            vid, status = voters.register_voter(request.form, request.files.get("photo"))
            flash("Registration received. An election officer will verify your identity and photograph "
                  "before you can vote. You can sign in now.", "success")
            return redirect(url_for("main.login", role="voter"))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("register.html", constituencies=constituencies, values=values)


# ------------------------------------------------------------------ voter area
def _me(vid):
    return db.query("SELECT * FROM v_voter_card WHERE VoterID = %s", (vid,), one=True)


@voter.route("/")
@voter_required
def dashboard():
    me = _me(session["uid"])
    rows = db.query("SELECT s.*, fn_can_vote(s.VoterID, s.ElectionID) AS CanVote "
                    "FROM v_voter_status s WHERE s.VoterID = %s ORDER BY FIELD(s.ElectionStatus,'Active','Upcoming','Closed'), s.StartDate DESC",
                    (session["uid"],))
    receipts = db.query("SELECT v.VoteID, v.VoteTime, e.ElectionCode, e.ElectionName FROM Vote v "
                        "JOIN Election e ON e.ElectionID = v.ElectionID WHERE v.VoterID = %s ORDER BY v.VoteTime DESC",
                        (session["uid"],))
    return render_template("voter_dashboard.html", me=me, rows=rows, receipts=receipts)


def _why_cannot_vote(vid, eid):
    me = _me(vid)
    if me and me["VerificationStatus"] != "Verified":
        return {"Pending": "Your identity is waiting for verification by an election officer.",
                "Flagged": "Your registration is under review. An election officer will contact you.",
                "Rejected": "Your identity verification was declined. Please contact the election office."}.get(
            me["VerificationStatus"], "Your identity has not been verified.")
    s = db.query("SELECT * FROM v_voter_status WHERE VoterID = %s AND ElectionID = %s", (vid, eid), one=True)
    if not s:
        return "You are not registered for this election. Please contact the election office."
    if s["ElectionStatus"] == "Upcoming":
        return "This election has not started yet."
    if s["ElectionStatus"] == "Closed":
        return "This election is closed."
    if s["EligibilityStatus"] != "Eligible":
        return "You are marked as not eligible for this election."
    if s["HasVoted"]:
        return "You have already voted in this election."
    return None


def _ballot_for(vid, eid):
    me = db.query("SELECT ConstituencyID FROM Voter WHERE VoterID = %s", (vid,), one=True)
    ballot = db.query("SELECT * FROM v_ballot WHERE ElectionID = %s AND ConstituencyID = %s ORDER BY CandidateName",
                      (eid, me["ConstituencyID"]))
    const = db.scalar("SELECT ConstituencyName FROM Constituency WHERE ConstituencyID = %s", (me["ConstituencyID"],))
    return ballot, const


@voter.route("/vote/<int:eid>")
@voter_required
def vote(eid):
    vid = session["uid"]
    election = db.query("SELECT * FROM Election WHERE ElectionID = %s", (eid,), one=True)
    if not election:
        abort(404)
    if not db.scalar("SELECT fn_can_vote(%s, %s)", (vid, eid)):
        flash(_why_cannot_vote(vid, eid) or "You cannot vote in this election.", "warning")
        return redirect(url_for("voter.dashboard"))
    ballot, const = _ballot_for(vid, eid)
    return render_template("vote.html", election=election, ballot=ballot, const=const)


@voter.route("/vote/<int:eid>/review", methods=["POST"])
@voter_required
def vote_review(eid):
    vid = session["uid"]
    election = db.query("SELECT * FROM Election WHERE ElectionID = %s", (eid,), one=True)
    if not election:
        abort(404)
    if not db.scalar("SELECT fn_can_vote(%s, %s)", (vid, eid)):
        flash(_why_cannot_vote(vid, eid) or "You cannot vote in this election.", "warning")
        return redirect(url_for("voter.dashboard"))
    ballot, const = _ballot_for(vid, eid)
    try:
        cid = int(request.form.get("candidate_id", ""))
    except ValueError:
        flash("Please select a candidate.", "warning")
        return redirect(url_for("voter.vote", eid=eid))
    choice = next((c for c in ballot if c["CandidateID"] == cid), None)
    if not choice:
        flash("That candidate is not on your ballot.", "danger")
        return redirect(url_for("voter.vote", eid=eid))
    session["review"] = {"eid": eid, "cid": cid}
    return render_template("vote_review.html", election=election, choice=choice, const=const)


@voter.route("/vote/<int:eid>/cast", methods=["POST"])
@voter_required
def vote_cast(eid):
    vid = session["uid"]
    rev = session.pop("review", None)
    try:
        cid = int(request.form.get("candidate_id", ""))
    except ValueError:
        cid = None
    if not rev or rev.get("eid") != eid or rev.get("cid") != cid:
        flash("Please review your choice before casting your vote.", "warning")
        return redirect(url_for("voter.vote", eid=eid))
    try:
        res = db.call_proc("sp_cast_vote", [vid, eid, cid, None])
        return redirect(url_for("voter.receipt", vote_id=res[3]))
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
        return redirect(url_for("voter.dashboard"))


@voter.route("/receipt/<int:vote_id>")
@voter_required
def receipt(vote_id):
    r = db.query("SELECT v.VoteID, v.VoteTime, v.VoterID, e.ElectionCode, e.ElectionName FROM Vote v "
                 "JOIN Election e ON e.ElectionID = v.ElectionID "
                 "WHERE v.VoteID = %s AND v.VoterID = %s", (vote_id, session["uid"]), one=True)
    if not r:
        abort(404)
    r["code"] = security.receipt_code(r["VoteID"], r["VoterID"], r["VoteTime"])
    return render_template("receipt.html", r=r)


@voter.route("/profile", methods=["GET", "POST"])
@voter_required
def profile():
    vid = session["uid"]
    row = _me(vid)
    values = {"address": row["Address"] if "Address" in row else "", "email": row["Email"], "phone": row["Phone"] or ""}
    full = db.query("SELECT Address FROM Voter WHERE VoterID = %s", (vid,), one=True)
    values["address"] = full["Address"] or ""
    if request.method == "POST":
        values = {k: request.form.get(k, "") for k in ("address", "email", "phone")}
        try:
            email = clean(values["email"]).lower()
            digits = security.normalise_phone(values["phone"])
            if not EMAIL_RE.match(email) or len(email) > 100:
                raise AppError("Enter a valid e-mail address.")
            if not digits or len(digits) != 10 or not PHONE_RE.match(digits):
                raise AppError("Enter a valid 10-digit mobile number.")
            if db.scalar("SELECT COUNT(*) FROM Voter WHERE EmailNormalized = %s AND VoterID <> %s",
                         (security.normalise_email(email), vid)):
                raise AppError("This e-mail address (or a variation of it) is already registered.")
            if db.scalar("SELECT COUNT(*) FROM Voter WHERE PhoneNormalized = %s AND VoterID <> %s", (digits, vid)):
                raise AppError("This phone number is already registered to another voter.")
            db.execute("UPDATE Voter SET Address = %s, Email = %s, Phone = %s WHERE VoterID = %s",
                       (clean(values["address"])[:255] or None, email, digits, vid))
            flash("Contact details updated.", "success")
            return redirect(url_for("voter.profile"))
        except Exception as e:      # noqa
            flash(friendly_error(e), "danger")
    return render_template("voter_profile.html", me=row, values=values)


@voter.route("/profile/photo", methods=["POST"])
@voter_required
def change_photo():
    try:
        voters.change_photo(session["uid"], request.files.get("photo"))
        flash("Photograph updated. An election officer will verify it again before your next vote.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("voter.profile"))


@voter.route("/profile/identity", methods=["POST"])
@voter_required
def set_identity():
    try:
        voters.set_identity(session["uid"], request.form.get("identity_number"))
        flash("Identity number saved.", "success")
    except Exception as e:          # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("voter.profile"))


@voter.route("/password", methods=["POST"])
@voter_required
def change_password():
    old = request.form.get("old_password", "")
    new = request.form.get("new_password", "")
    try:
        row = db.query("SELECT Password, Username, Email FROM Voter WHERE VoterID = %s", (session["uid"],), one=True)
        ok, _ = check_password(row["Password"], old)
        if not ok:
            raise AppError("Current password is incorrect.")
        if new != request.form.get("new_password2", ""):
            raise AppError("The new passwords do not match.")
        check_password_strength(new, row["Username"], row["Email"])
        db.execute("UPDATE Voter SET Password = %s WHERE VoterID = %s", (hash_password(new), session["uid"]))
        flash("Password changed.", "success")
    except Exception as e:      # noqa
        flash(friendly_error(e), "danger")
    return redirect(url_for("voter.profile"))
