"""Data layer : every SQL statement of the application goes through this module.

* Connection   : mysql-connector-python, one connection per request (kept in flask.g)
* Safety       : ALL values are passed as parameters (%s) - never string-formatted -
                 so SQL injection is not possible.
* Transactions : `with transaction() as tx:` groups several statements into ONE
                 atomic unit (commit on success, rollback on any error).
* Primary keys : the tables have no AUTO_INCREMENT, so new ids are generated as
                 MAX(id)+1 inside a transaction guarded by a named lock.
                 (It also works if AUTO_INCREMENT is added later.)
* Retries      : a deadlock or lock-wait time-out is retried automatically.
"""
import re
import time
from contextlib import contextmanager
import mysql.connector
from mysql.connector import errors as dberr
from flask import g, current_app


class AppError(Exception):
    """An error whose message is safe to show to the user."""


def connect(cfg=None):
    c = cfg or current_app.config
    return mysql.connector.connect(
        host=c["DB_HOST"], port=c["DB_PORT"], user=c["DB_USER"],
        password=c["DB_PASSWORD"], database=c["DB_NAME"],
        autocommit=False, charset="utf8mb4", connection_timeout=10)


def get_db():
    if "db" not in g:
        g.db = connect()
    else:
        try:
            g.db.ping(reconnect=True, attempts=2, delay=0)
        except Exception:
            g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


# ------------------------------------------------------------------ simple helpers
def query(sql, params=(), one=False):
    """SELECT -> list of dicts (or a single dict / None when one=True)."""
    db = get_db()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
    db.rollback()          # end the read transaction so the next query sees fresh data
    if one:
        return rows[0] if rows else None
    return rows


def scalar(sql, params=()):
    row = query(sql, params, one=True)
    return None if row is None else next(iter(row.values()))


def execute(sql, params=()):
    """INSERT / UPDATE / DELETE with commit. Returns affected row count."""
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(sql, params)
        n = cur.rowcount
        db.commit()
        return n
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()


# ------------------------------------------------------------------ transactions
def id_lock(table):
    return f"evoting_id_{table}"


class Tx:
    """Thin wrapper handed out by `transaction()`. All methods run inside the open transaction."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor(dictionary=True, buffered=True)

    def execute(self, sql, params=()):
        self.cur.execute(sql, params)
        return self.cur.rowcount

    def all(self, sql, params=()):
        self.cur.execute(sql, params)
        return self.cur.fetchall()

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=()):
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))

    def next_id(self, table, id_col):
        """Next primary key. The caller must hold id_lock(table) (see transaction(locks=...))."""
        return self.scalar(f"SELECT COALESCE(MAX({id_col}), 0) + 1 FROM {table}")

    def insert_row(self, table, id_col, data):
        new_id = self.next_id(table, id_col)
        cols = [id_col] + list(data.keys())
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join(['%s'] * len(cols))})")
        self.cur.execute(sql, [new_id] + list(data.values()))
        return new_id


@contextmanager
def transaction(locks=()):
    """ACID unit of work.
         with db.transaction(locks=[db.id_lock('Voter')]) as tx:
             vid = tx.insert_row('Voter', 'VoterID', {...})
             tx.execute('INSERT INTO SecurityEvent ...')
       Everything is committed together - or, if anything raises, rolled back together."""
    conn = get_db()
    conn.rollback()                      # leave any read-only snapshot behind
    tx = Tx(conn)
    held = []
    try:
        for name in sorted(locks):
            tx.cur.execute("SELECT GET_LOCK(%s, 10) AS got", (name,))
            if tx.cur.fetchone()["got"] != 1:
                raise AppError("The system is busy, please try again.")
            held.append(name)
        yield tx
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        for name in held:
            try:
                tx.cur.execute("DO RELEASE_LOCK(%s)", (name,))
                tx.cur.fetchall() if tx.cur.with_rows else None
            except Exception:
                pass
        try:
            tx.cur.close()
        except Exception:
            pass


def insert_row(table, id_col, data):
    """INSERT one row with the next primary key (atomic). Returns the new id."""
    with transaction(locks=[id_lock(table)]) as tx:
        return tx.insert_row(table, id_col, data)


_RETRYABLE = (1213, 1205)               # deadlock, lock wait time-out


def call_proc(name, args, retries=2):
    """CALL a stored procedure. `args` may contain None for OUT parameters.
    Returns the list of parameter values after the call (OUT values filled in).
    A deadlock / lock time-out is retried a couple of times before giving up."""
    for attempt in range(retries + 1):
        db = get_db()
        cur = db.cursor()
        try:
            res = cur.callproc(name, args)
            db.commit()
            return list(res)
        except dberr.Error as e:
            db.rollback()
            if getattr(e, "errno", None) in _RETRYABLE and attempt < retries:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()


# ------------------------------------------------------------------ error text
# order matters: the first key found in the MySQL message wins
_FRIENDLY_KEYS = {
    "uq_voter_identity": "This identity number is already registered to a voter account. "
                         "One person can hold only one voter account.",
    "uq_voter_email_norm": "This e-mail address (or a variation of it) is already registered.",
    "uq_voter_phone_norm": "This phone number is already registered to another voter.",
    "uq_voter_photo_sha": "This photograph is already registered to another voter.",
    "Email": "That e-mail address is already registered.",
    "Username": "That username is already taken.",
    "uq_constituency": "This constituency (name + state + district) already exists.",
    "PartyName": "A party with this name already exists.",
    "ElectionCode": "An election with this code already exists.",
    "uq_nomination": "This candidate is already nominated for this election.",
    "uq_election_voter": "This voter is already assigned to this election.",
    "uq_one_vote_per_election": "You have already voted in this election.",
    "uq_result": "Results for this candidate already exist.",
}


def friendly_error(exc):
    """Translate a database / app exception into a short, readable message."""
    if isinstance(exc, AppError):
        return str(exc)
    if isinstance(exc, dberr.Error):
        no = getattr(exc, "errno", None)
        msg = getattr(exc, "msg", None) or str(exc)
        if no == 1644:                       # our SIGNAL ... MESSAGE_TEXT
            return msg
        if no in (1451, 3008):               # parent row referenced (ON DELETE RESTRICT)
            return ("This record cannot be deleted because other records depend on it "
                    "(referential integrity protects it).")
        if no == 1452:
            return "The selected related record does not exist."
        if no == 1062:
            for key, text in _FRIENDLY_KEYS.items():
                if key.lower() in msg.lower():
                    return text
            return "A record with the same unique value already exists."
        if no in (3819, 4025, 4026):         # CHECK constraint (MySQL / MariaDB)
            m = re.search(r"[`']?(chk_\w+)[`']?", msg)
            return ("A value breaks a rule of the system" +
                    (f" ({m.group(1)})." if m else "."))
        if no in (1048, 1364):
            return "A required field is missing."
        if no in (1406, 1265):
            return "A value is too long or in the wrong format."
        if no in (1292, 1366, 1411):
            return "A value is in the wrong format (check dates and numbers)."
        if no in (1205, 1213):
            return "The system is busy right now. Please try again in a moment."
        if no in (2003, 2002, 2006, 2013, 1045, 1049):
            return "The service cannot reach its data store right now. Please try again shortly."
        return "The request could not be completed. Please check your input and try again."
    return "Something unexpected happened. Please try again."
