"""Login helpers : bcrypt hashing, legacy SHA-256 upgrade, lock-out, role decorators."""
import hashlib
import hmac
import re
from functools import wraps
import bcrypt
from flask import session, redirect, url_for, flash, request
from . import db
from . import security


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


_DUMMY_HASH = hash_password("not-a-real-password-0")      # keeps the timing of "unknown user" equal to "wrong password"


def check_password(stored: str, plain: str):
    """Returns (ok, needs_upgrade).
    The sample data was loaded with SHA2(...,256) (64 hex chars). Those still log in
    once, and are silently re-saved as bcrypt hashes (salted) at that moment."""
    if not stored:
        return False, False
    if stored.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("ascii")), False
        except ValueError:
            return False, False
    if re.fullmatch(r"[0-9a-fA-F]{64}", stored):
        digest = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        ok = hmac.compare_digest(digest, stored.lower())
        return ok, ok
    return False, False


ROLES = {
    "voter":   dict(table="Voter", pk="VoterID", name="Name"),
    "officer": dict(table="ElectionOfficer", pk="OfficerID", name="Name"),
}


def authenticate(role, username, password):
    """Returns (user_dict | None, message)."""
    spec = ROLES.get(role)
    if not spec:
        return None, "Choose a valid login type."
    locked, minutes = security.lock_status(role, username)
    if locked or security.ip_blocked():
        security.log_event("LOGIN_BLOCKED", role, username, "Attempt while locked")
        return None, (f"Too many wrong passwords. This account is locked for about {minutes or 1} more minute(s). "
                      "An election officer can unlock it sooner.")
    row = db.query(
        f"SELECT {spec['pk']} AS id, {spec['name']} AS name, Password, AccountStatus "
        f"FROM {spec['table']} WHERE Username = %s", (username,), one=True)
    if not row:
        check_password(_DUMMY_HASH, password)              # burn the same time as a real check
        security.log_event("LOGIN_FAIL", role, username, "Unknown user")
        return None, "Invalid username or password."
    ok, upgrade = check_password(row["Password"], password)
    if not ok:
        security.log_event("LOGIN_FAIL", role, username, "Wrong password")
        return None, "Invalid username or password."
    if row["AccountStatus"] != "Active":
        security.log_event("LOGIN_FAIL", role, username, f"Account {row['AccountStatus']}")
        return None, f"This account is {row['AccountStatus']}. Please contact the election office."
    if upgrade:
        db.execute(f"UPDATE {spec['table']} SET Password = %s WHERE {spec['pk']} = %s",
                   (hash_password(password), row["id"]))
    if role == "voter":
        db.execute("UPDATE Voter SET LastLoginAt = NOW() WHERE VoterID = %s", (row["id"],))
    security.log_event("LOGIN_OK", role, username, None)
    return row, "ok"


def login_user(role, row):
    session.clear()                                         # new session = no session fixation
    session["role"] = role
    session["uid"] = row["id"]
    session["name"] = row["name"]
    session.permanent = True


def current_role():
    return session.get("role")


def login_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if session.get("role") != role:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("main.login", role=role, next=request.path))
            # re-check the account is still Active (a suspension takes effect at once)
            spec = ROLES[role]
            st = db.scalar(f"SELECT AccountStatus FROM {spec['table']} WHERE {spec['pk']} = %s",
                           (session["uid"],))
            if st != "Active":
                session.clear()
                flash("Your account is no longer active.", "danger")
                return redirect(url_for("main.login", role=role))
            return fn(*a, **kw)
        return wrapper
    return deco


def is_active_session(role):
    """True when the browser session belongs to an Active account of this role."""
    if session.get("role") != role:
        return False
    spec = ROLES[role]
    return db.scalar(f"SELECT AccountStatus FROM {spec['table']} WHERE {spec['pk']} = %s",
                     (session.get("uid"),)) == "Active"


voter_required = login_required("voter")
officer_required = login_required("officer")
