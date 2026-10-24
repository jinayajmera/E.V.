"""Checks everything the application needs and tells you what is missing.

    python scripts/check_setup.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mysql.connector                      # noqa: E402
from app.config import Config               # noqa: E402

TABLES = ["Constituency", "PoliticalParty", "ElectionOfficer", "Voter", "Election", "Candidate",
          "CandidateNomination", "ElectionVoter", "Vote", "ElectionResult"]
VIEWS = ["v_ballot", "v_voter_status", "v_election_summary", "v_public_results", "v_voter_card", "v_duplicate_suspects", "v_integrity_checks"]
FUNCS = ["fn_turnout_percent", "fn_votes_for", "fn_can_vote", "fn_normalise_email", "fn_normalise_phone"]
PROCS = ["sp_cast_vote", "sp_close_election_and_generate_results", "sp_assign_constituency_voters", "sp_verify_voter"]
TRIGS = ["trg_vote_business_rules", "trg_vote_election_open", "trg_vote_after_insert",
         "trg_election_status_guard", "trg_election_audit", "trg_nomination_rules",
         "trg_nomination_audit", "trg_voter_delete_audit", "trg_voter_normalise_bi", "trg_voter_normalise_bu"]
bad = 0


def line(ok, text):
    global bad
    print(("  [ OK ] " if ok else "  [FAIL] ") + text)
    if not ok:
        bad += 1


try:
    conn = mysql.connector.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                                   password=Config.DB_PASSWORD, database=Config.DB_NAME)
except mysql.connector.Error as e:
    print(f"\nCannot connect to MySQL ({e}).\nCheck the values in your .env file and that the MySQL service is running.")
    sys.exit(1)

cur = conn.cursor()
cur.execute("SELECT VERSION()")
print(f"\nConnected to MySQL {cur.fetchone()[0]}, database '{Config.DB_NAME}'\n")


def names(sql):
    cur.execute(sql, (Config.DB_NAME,))
    return {r[0].lower() for r in cur.fetchall()}


have_t = names("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'")
have_v = names("SELECT table_name FROM information_schema.views WHERE table_schema=%s")
have_f = names("SELECT routine_name FROM information_schema.routines WHERE routine_schema=%s AND routine_type='FUNCTION'")
have_p = names("SELECT routine_name FROM information_schema.routines WHERE routine_schema=%s AND routine_type='PROCEDURE'")
have_g = names("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema=%s")

print("Tables (these must already exist):")
for t in TABLES:
    line(t.lower() in have_t, t)
if not all(t.lower() in have_t for t in TABLES):
    print("\n  -> The 10 base tables are missing. Make sure Config DB_NAME points to the database you built.")
    sys.exit(1)

print("\nSample data:")
for t in ("Voter", "Election", "Candidate", "ElectionVoter"):
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    n = cur.fetchone()[0]
    line(n > 0, f"{t}: {n} rows")

print("\nApplication objects (created by sql/objects.sql):")
for kind, want, have in (("view", VIEWS, have_v), ("function", FUNCS, have_f),
                         ("procedure", PROCS, have_p), ("trigger", TRIGS, have_g)):
    for n in want:
        line(n.lower() in have, f"{kind}: {n}")
line("auditlog" in have_t, "table: AuditLog")
line("securityevent" in have_t, "table: SecurityEvent")
line("txnscratch" in have_t, "table: TxnScratch")
cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='Voter' "
            "AND column_name IN ('NationalIDHash','PhotoFile','VerificationStatus','EmailNormalized','PhoneNormalized')", (Config.DB_NAME,))
line(cur.fetchone()[0] == 5, "Voter: identity / photo / verification columns")

print("\nPasswords:")
for t in ("Voter", "ElectionOfficer"):
    cur.execute(f"SELECT COUNT(*) FROM {t} WHERE Password REGEXP '^[0-9a-fA-F]{64}$'")
    line(True, f"{t}: {cur.fetchone()[0]} old SHA-256 hashes (they are upgraded to bcrypt on first login)")
cur.execute("SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='Voter' AND column_name='Password'", (Config.DB_NAME,))
w = cur.fetchone()[0]
line(w >= 60, f"Voter.Password width = {w} (bcrypt needs 60)")

print("\nResult:", "EVERYTHING IS READY" if bad == 0 else f"{bad} problem(s) found - read the FAIL lines above")
if bad:
    print("Run:  python scripts/upgrade_database.py   (safe to repeat - it never deletes your data)")
sys.exit(1 if bad else 0)
