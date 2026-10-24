"""Installs sql/objects.sql into evoting (same effect as running it in MySQL Workbench).
It NEVER drops tables or the database; it can be run repeatedly.

    python scripts/install_objects.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mysql.connector                      # noqa: E402
from app.config import Config               # noqa: E402


def split_statements(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    delim, buf, out = ";", [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("--") or not s:
            continue
        if s.upper().startswith("DELIMITER "):
            delim = s.split(None, 1)[1].strip()
            continue
        buf.append(line)
        if s.endswith(delim):
            stmt = "\n".join(buf).rstrip()
            out.append(stmt[: -len(delim)].rstrip())
            buf = []
    if buf:
        out.append("\n".join(buf).strip())
    return [s for s in out if s]


def main(path=None):
    path = Path(path) if path else ROOT / "sql" / "objects.sql"
    stmts = split_statements(path.read_text(encoding="utf-8"))
    conn = mysql.connector.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                                   password=Config.DB_PASSWORD, database=Config.DB_NAME)
    cur = conn.cursor()
    done = 0
    for st in stmts:
        if st.upper().startswith("USE "):          # we are already connected to the right database
            continue
        try:
            cur.execute(st)
            if cur.with_rows:
                cur.fetchall()
            done += 1
        except mysql.connector.Error as e:
            print(f"FAILED: {e}\n--- statement ---\n{st[:300]}")
            conn.close()
            return 1
    conn.commit()
    conn.close()
    print(f"OK - {done} statements executed from {path.name} on database '{Config.DB_NAME}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
