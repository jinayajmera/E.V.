"""Clears a sign-in lock-out (use when an OFFICER is locked out and cannot unlock themselves).

    python scripts/unlock_account.py officer rajesh_off
    python scripts/unlock_account.py voter aarav01
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mysql.connector                      # noqa: E402
from app.config import Config               # noqa: E402

if len(sys.argv) != 3 or sys.argv[1] not in ("voter", "officer"):
    sys.exit(__doc__)
c = mysql.connector.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                            password=Config.DB_PASSWORD, database=Config.DB_NAME)
cur = c.cursor()
cur.execute("INSERT INTO SecurityEvent (EventType, Role, Username, IpAddress, Details) VALUES ('UNLOCK', %s, %s, 'console', 'Cleared from the command line')",
            (sys.argv[1], sys.argv[2]))
c.commit()
print(f"{sys.argv[2]} can sign in again.")
