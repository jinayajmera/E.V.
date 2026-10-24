"""Live demonstration of the four ACID guarantees (shown on Officer -> Integrity).

Every demo opens its OWN database connections and works on the scratch table TxnScratch, so the real
election data is never touched. (The one check that uses a real table is rolled back at once.)
Each function returns a dict:  {"title", "property", "ok": bool, "steps": [(text, status)]}
status is "info", "pass" or "fail".
"""
import threading
import time
import mysql.connector
from mysql.connector import errors as dberr
from flask import current_app
from . import db


def _conn():
    c = db.connect(current_app.config)
    return c


def _reset(cur):
    cur.execute("DELETE FROM TxnScratch")


def _count(cur, where="1=1"):
    cur.execute(f"SELECT COUNT(*) FROM TxnScratch WHERE {where}")
    return cur.fetchone()[0]


def _short(exc):
    return (getattr(exc, "msg", None) or str(exc)).split(" (")[0][:140]


# ---------------------------------------------------------------- A  Atomicity
def atomicity():
    steps, ok = [], True
    c = _conn()
    cur = c.cursor()
    try:
        _reset(cur)
        c.commit()
        cur.execute("START TRANSACTION")
        cur.execute("INSERT INTO TxnScratch VALUES (1, 'Ballot slip A', 10)")
        cur.execute("INSERT INTO TxnScratch VALUES (2, 'Ballot slip B', 5)")
        steps.append(("START TRANSACTION, then two valid rows are inserted. Inside the transaction "
                      f"{_count(cur)} rows are visible.", "info"))
        try:
            cur.execute("INSERT INTO TxnScratch VALUES (3, 'Faulty slip', -1)")
            steps.append(("The faulty row was accepted - unexpected.", "fail"))
            ok = False
        except dberr.Error as e:
            steps.append((f"A third row breaks a rule and is refused: {_short(e)}", "info"))
        cur.execute("ROLLBACK")
        left = _count(cur)
        good = left == 0
        ok = ok and good
        steps.append((f"ROLLBACK. Rows left in the table: {left}. The two valid rows were undone as well - "
                      "the unit of work is all-or-nothing.", "pass" if good else "fail"))
    finally:
        c.rollback()
        c.close()
    return dict(title="Atomicity", property="A", ok=ok, steps=steps,
                summary="A transaction either completes fully or leaves no trace.")


# ---------------------------------------------------------------- C  Consistency
def consistency():
    steps, ok = [], True
    c = _conn()
    cur = c.cursor()
    try:
        _reset(cur)
        cur.execute("INSERT INTO TxnScratch VALUES (1, 'Ballot slip A', 10)")
        c.commit()
        tests = [
            ("INSERT INTO TxnScratch VALUES (2, 'Negative quantity', -5)", "CHECK rule (quantity cannot be negative)"),
            ("INSERT INTO TxnScratch VALUES (1, 'Same key again', 1)", "PRIMARY KEY rule (no duplicate ids)"),
            ("INSERT INTO TxnScratch VALUES (4, NULL, 1)", "NOT NULL rule (a note is required)"),
        ]
        for sql, label in tests:
            try:
                cur.execute(sql)
                c.rollback()
                steps.append((f"{label}: the bad row was accepted - unexpected.", "fail"))
                ok = False
            except dberr.Error as e:
                c.rollback()
                steps.append((f"{label}: refused - {_short(e)}", "pass"))
        # a rule on a real table (rolled back immediately, nothing is stored)
        cur.execute("SELECT VoterID, ElectionID, CandidateID FROM Vote ORDER BY VoteID LIMIT 1")
        v = cur.fetchone()
        if v:
            try:
                cur.execute("INSERT INTO Vote (VoteID, VoterID, ElectionID, CandidateID, VoteTime) "
                            "VALUES (999999, %s, %s, %s, NOW())", v)
                c.rollback()
                steps.append(("Second vote by the same voter in the same election was accepted - unexpected.", "fail"))
                ok = False
            except dberr.Error as e:
                c.rollback()
                steps.append((f"Second vote by the same voter in the same election (real vote table): refused - {_short(e)}", "pass"))
        good = _count(cur) == 1
        ok = ok and good
        steps.append((f"After all attempts the scratch table still holds exactly its {_count(cur)} valid row - "
                      "the data never left a valid state.", "pass" if good else "fail"))
    finally:
        c.rollback()
        c.close()
    return dict(title="Consistency", property="C", ok=ok, steps=steps,
                summary="Every change moves the data from one valid state to another valid state.")


# ---------------------------------------------------------------- I  Isolation
def isolation():
    steps, ok = [], True
    a, b = _conn(), _conn()
    ca, cb = a.cursor(), b.cursor()
    try:
        _reset(ca)
        ca.execute("INSERT INTO TxnScratch VALUES (1, 'Shared counter', 100)")
        a.commit()
        cb.execute("SET SESSION innodb_lock_wait_timeout = 1")
        ca.execute("START TRANSACTION")
        ca.execute("UPDATE TxnScratch SET Qty = 150 WHERE ScratchID = 1")
        steps.append(("Session A starts a transaction and changes the counter from 100 to 150 (not committed yet).", "info"))
        cb.execute("START TRANSACTION")
        cb.execute("SELECT Qty FROM TxnScratch WHERE ScratchID = 1")
        seen = cb.fetchone()[0]
        good = seen == 100
        ok = ok and good
        steps.append((f"Session B reads the counter and sees {seen}: A's uncommitted change is invisible "
                      "(no dirty reads).", "pass" if good else "fail"))
        t0 = time.time()
        try:
            cb.execute("UPDATE TxnScratch SET Qty = 7 WHERE ScratchID = 1")
            steps.append(("Session B changed the same row while A held it - unexpected.", "fail"))
            ok = False
        except dberr.Error as e:
            steps.append((f"Session B tries to change the same row and has to wait; after {time.time() - t0:.1f}s it "
                          f"gives up: {_short(e)} - the row lock keeps the two writers apart.", "pass"))
        b.rollback()
        a.commit()
        steps.append(("Session A commits.", "info"))
        cb.execute("START TRANSACTION")
        cb.execute("SELECT Qty FROM TxnScratch WHERE ScratchID = 1")
        now = cb.fetchone()[0]
        b.rollback()
        good = now == 150
        ok = ok and good
        steps.append((f"Session B now reads {now}: committed work becomes visible.", "pass" if good else "fail"))
    finally:
        for c in (a, b):
            try:
                c.rollback()
                c.close()
            except Exception:
                pass
    return dict(title="Isolation", property="I", ok=ok, steps=steps,
                summary="Concurrent transactions do not see or disturb each other's unfinished work.")


# ---------------------------------------------------------------- D  Durability
def durability():
    steps, ok = [], True
    a = _conn()
    ca = a.cursor()
    try:
        _reset(ca)
        ca.execute("INSERT INTO TxnScratch VALUES (1, 'Durable row', 42)")
        a.commit()
        a.close()
        steps.append(("A row is inserted and COMMITted, then that connection is closed completely.", "info"))
        c = _conn()
        cc = c.cursor()
        cc.execute("SELECT Note, Qty FROM TxnScratch WHERE ScratchID = 1")
        row = cc.fetchone()
        good = row == ("Durable row", 42)
        ok = ok and good
        steps.append((f"A brand-new connection reads it back: {row}.", "pass" if good else "fail"))
        cc.execute("SHOW VARIABLES WHERE Variable_name IN ('innodb_flush_log_at_trx_commit', "
                   "'innodb_doublewrite', 'log_bin', 'default_storage_engine')")
        vars_ = {k: v for k, v in cc.fetchall()}
        c.close()
        flush = vars_.get("innodb_flush_log_at_trx_commit")
        good = flush == "1"
        ok = ok and good
        steps.append((f"Engine settings: storage engine {vars_.get('default_storage_engine')}, "
                      f"innodb_flush_log_at_trx_commit = {flush}, doublewrite = {vars_.get('innodb_doublewrite')}, "
                      f"binary log = {vars_.get('log_bin')}. With value 1 every COMMIT is written to the redo log on disk, "
                      "so a power failure after COMMIT cannot lose the vote.", "pass" if good else "fail"))
    finally:
        try:
            a.close()
        except Exception:
            pass
    return dict(title="Durability", property="D", ok=ok, steps=steps,
                summary="Once a transaction is committed it survives crashes and restarts.")


def run_all():
    out = []
    for fn in (atomicity, consistency, isolation, durability):
        try:
            out.append(fn())
        except Exception as exc:        # noqa
            current_app.logger.warning("acid demo %s failed: %s", fn.__name__, exc)
            out.append(dict(title=fn.__name__.title(), property=fn.__name__[0].upper(), ok=False,
                            steps=[("The demonstration could not run: " + db.friendly_error(exc), "fail")],
                            summary=""))
    # leave the scratch table empty again
    try:
        c = _conn()
        cur = c.cursor()
        _reset(cur)
        c.commit()
        c.close()
    except Exception:
        pass
    return out
