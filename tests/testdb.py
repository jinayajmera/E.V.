"""Builds a THROW-AWAY copy of your database (named evoting_test) for the automated tests.
Your real database is only READ. The copy is rebuilt from scratch each run (only a database whose name
starts with 'evoting_test' can ever be dropped by this module)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import mysql.connector                                  # noqa: E402

TEST_NAME = "evoting_test"
TABLE_ORDER = ["Constituency", "PoliticalParty", "ElectionOfficer", "Voter", "Election", "Candidate",
               "CandidateNomination", "ElectionVoter", "Vote", "ElectionResult", "AuditLog"]


def build(src_name, conn_args):
    c = mysql.connector.connect(**conn_args)
    cur = c.cursor()
    assert TEST_NAME.startswith("evoting_test")
    cur.execute(f"DROP DATABASE IF EXISTS `{TEST_NAME}`")
    cur.execute(f"CREATE DATABASE `{TEST_NAME}` CHARACTER SET utf8mb4")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("SET sql_mode = REPLACE(@@sql_mode, 'NO_AUTO_VALUE_ON_ZERO', '')")
    cur.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'", (src_name,))
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute(f"SHOW CREATE TABLE `{src_name}`.`{t}`")
        ddl = cur.fetchone()[1]
        cur.execute(f"USE `{TEST_NAME}`")
        cur.execute(ddl)
    for t in tables:
        if t in ("SecurityEvent", "TxnScratch"):
            continue
        cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                    "AND generation_expression = '' ORDER BY ordinal_position", (TEST_NAME, t))
        cols = ", ".join(f"`{r[0]}`" for r in cur.fetchall())
        cur.execute(f"INSERT INTO `{TEST_NAME}`.`{t}` ({cols}) SELECT {cols} FROM `{src_name}`.`{t}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    # the business-rule triggers of the source database (the install scripts re-create their own afterwards)
    cur.execute("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = %s", (src_name,))
    for (trg,) in cur.fetchall():
        cur.execute(f"SHOW CREATE TRIGGER `{src_name}`.`{trg}`")
        ddl = cur.fetchone()[2]
        cur.execute(f"USE `{TEST_NAME}`")
        cur.execute(f"DROP TRIGGER IF EXISTS `{trg}`")
        cur.execute(ddl)
    c.commit()
    c.close()
