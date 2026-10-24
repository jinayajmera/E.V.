"""Runs sql/tests_negative.sql : every statement must FAIL with the expected message.
Read-only in effect (failed statements change nothing).     python tests/run_negative_sql.py"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import mysql.connector                              # noqa: E402
from app.config import Config                       # noqa: E402
from install_objects import split_statements        # noqa: E402

text = (ROOT / "sql" / "tests_negative.sql").read_text(encoding="utf-8")
blocks = re.split(r"\n(?=-- EXPECT:)", text)[1:]
conn = mysql.connector.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                               password=Config.DB_PASSWORD, database=Config.DB_NAME)
cur = conn.cursor()
bad = 0
for b in blocks:
    expect = re.match(r"-- EXPECT:\s*(.+)", b).group(1).strip()
    title = re.search(r"^-- (N\d+\..*)$", b, re.M).group(1)
    stmt = split_statements(b)[0]
    try:
        cur.execute(stmt)
        if cur.with_rows:
            cur.fetchall()
        conn.rollback()
        print(f"[FAIL] {title}\n       statement SUCCEEDED but should have failed")
        bad += 1
    except mysql.connector.Error as e:
        conn.rollback()
        ok = expect.lower() in str(e).lower()
        print(f"[{' OK ' if ok else 'FAIL'}] {title}\n       -> {e.msg if hasattr(e,'msg') else e}")
        bad += 0 if ok else 1
print("\nAll negative tests behave as expected." if not bad else f"\n{bad} negative test(s) did not behave as expected.")
sys.exit(1 if bad else 0)
