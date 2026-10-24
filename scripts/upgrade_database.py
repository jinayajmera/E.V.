"""One-step upgrade of an existing database to the current version.

    python scripts/upgrade_database.py

What it does (it NEVER drops or empties a table, and it can be run again safely):
  1. saves a JSON backup of every table to  backups/
  2. installs / refreshes the stored procedures, functions, triggers and views (sql/objects.sql)
  3. adds the new columns, rules and tables for identity, photographs, verification and security (sql/final_upgrade.sql)
  4. gives the sample voters their sample identity numbers and portraits, so the demo works straight away
"""
import io
import json
import sys
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mysql.connector                      # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402
from app.config import Config               # noqa: E402
import install_objects                      # noqa: E402


def connect():
    return mysql.connector.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                                   password=Config.DB_PASSWORD, database=Config.DB_NAME)


def backup():
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'",
                (Config.DB_NAME,))
    names = [r["t"] for r in cur.fetchall()]
    data = {}
    for t in names:
        cur.execute(f"SELECT * FROM `{t}`")
        data[t] = cur.fetchall()
    conn.close()
    out = ROOT / "backups"
    out.mkdir(exist_ok=True)
    f = out / f"backup_{datetime.now():%Y%m%d_%H%M%S}.json"

    def enc(o):
        if isinstance(o, (datetime, date, Decimal)):
            return str(o)
        if isinstance(o, (bytes, bytearray)):
            return o.hex()
        return str(o)
    f.write_text(json.dumps(data, default=enc, indent=1), encoding="utf-8")
    print(f"Backup saved: {f.relative_to(ROOT)}  ({sum(len(v) for v in data.values())} rows in {len(names)} tables)")


def seed_samples():
    from app import create_app, security, photos
    app = create_app()
    with app.app_context():
        conn = connect()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT VoterID, Username, NationalIDHash, PhotoFile FROM Voter ORDER BY VoterID")
        rows = cur.fetchall()
        seeded_id = seeded_photo = 0
        for r in rows:
            seed = ROOT / "seed" / "voters" / f"{r['Username']}.jpg"
            if not seed.is_file():
                continue                                    # real (non-sample) voters add their own details
            if r["NationalIDHash"] is None:
                ident = f"VTR{1000000 + r['VoterID']}"
                cur.execute("UPDATE Voter SET NationalIDHash = %s, NationalIDLast4 = %s, IdentitySource = 'Registered' "
                            "WHERE VoterID = %s", (security.hash_identity(ident), ident[-4:], r["VoterID"]))
                seeded_id += 1
            if r["PhotoFile"] is None:
                info = photos.process_upload(FileStorage(stream=io.BytesIO(seed.read_bytes()), filename=seed.name))
                name = photos.save_voter_photo(info["data"])
                cur.execute("UPDATE Voter SET PhotoFile = %s, PhotoSha256 = %s, PhotoDHash = %s WHERE VoterID = %s",
                            (name, info["sha256"], info["dhash"], r["VoterID"]))
                seeded_photo += 1
        conn.commit()
        cur.execute("SELECT COUNT(*) AS n FROM Voter WHERE NationalIDHash IS NULL")
        missing = cur.fetchone()["n"]
        conn.close()
    print(f"Sample data: {seeded_id} identity number(s) and {seeded_photo} portrait(s) added; "
          f"{missing} voter(s) still need an identity number (they are asked at their next sign-in).")


def main():
    try:
        connect().close()
    except mysql.connector.Error as e:
        print(f"Cannot connect to MySQL ({e}).\nCheck the values in your .env file and that MySQL is running.")
        return 1
    backup()
    for name in ("objects.sql", "final_upgrade.sql"):
        if install_objects.main(ROOT / "sql" / name) != 0:
            print(f"\nStopped while installing {name}. Nothing was deleted; your backup is in the backups folder.")
            return 1
    seed_samples()
    print("\nUpgrade finished. Start the site with:  python run.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
