"""Security helpers : identity hashing, e-mail / phone normalising, throttling, security log, HTTP headers."""
import hashlib
import hmac
import re
from flask import request, current_app, session
from . import db
from .db import AppError
from .validators import ID_RE

# ---------------------------------------------------------------- identity number
def normalise_identity(raw):
    """'abc 123-4567' -> 'ABC1234567' (spaces / dashes removed, upper-case)."""
    return re.sub(r"[\s\-]", "", (raw or "")).upper()


def check_identity_format(raw):
    ident = normalise_identity(raw)
    if not ID_RE.match(ident):
        raise AppError("Enter a valid voter identity number: 3 letters followed by 7 digits (example: ABC1234567).")
    return ident


def hash_identity(ident):
    """One-way HMAC-SHA256. The number itself is never stored; only the last 4 characters are kept for display."""
    key = current_app.config["ID_PEPPER"].encode("utf-8")
    return hmac.new(key, ("voter-id:" + ident).encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- e-mail / phone (mirror the SQL functions)
def normalise_email(email):
    mail = (email or "").strip().lower()
    if "@" not in mail:
        return mail
    local, _, domain = mail.rpartition("@")
    local = local.split("+", 1)[0]
    if domain == "googlemail.com":
        domain = "gmail.com"
    if domain == "gmail.com":
        local = local.replace(".", "")
    return f"{local}@{domain}"


def normalise_phone(phone):
    digits = re.sub(r"[^0-9]", "", phone or "")
    return digits[-10:] if digits else None


def receipt_code(vote_id, voter_id, vote_time):
    """Short code printed on the voter's receipt. It proves the receipt is genuine but reveals nothing about the choice."""
    key = current_app.config["SECRET_KEY"].encode("utf-8")
    raw = hmac.new(key, f"receipt|{vote_id}|{voter_id}|{vote_time}".encode("utf-8"), hashlib.sha256).hexdigest()[:12].upper()
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


# ---------------------------------------------------------------- client + security log
def client_ip():
    return (request.remote_addr or "unknown")[:45] if request else "unknown"


def log_event(event_type, role=None, username=None, details=None):
    """Write a line to SecurityEvent. A logging problem must never break the request."""
    try:
        db.execute("INSERT INTO SecurityEvent (EventType, Role, Username, IpAddress, Details) "
                   "VALUES (%s, %s, %s, %s, %s)",
                   (event_type, role, (username or "")[:50] or None, client_ip(), (details or "")[:255] or None))
    except Exception as exc:        # noqa
        try:
            current_app.logger.warning("security log failed: %s", exc)
        except Exception:
            pass


# ---------------------------------------------------------------- throttling
_RESET_TYPES = "('LOGIN_OK','UNLOCK')"


def lock_status(role, username):
    """(locked, minutes_left) for this account after too many wrong passwords."""
    cfg = current_app.config
    window = cfg["LOGIN_LOCK_MINUTES"]
    row = db.query(
        "SELECT COUNT(*) AS n, TIMESTAMPDIFF(SECOND, MAX(EventTime), NOW()) AS age "
        "FROM SecurityEvent WHERE EventType = 'LOGIN_FAIL' AND Role = %s AND Username = %s "
        "AND EventTime > NOW() - INTERVAL %s MINUTE "
        f"AND EventTime > COALESCE((SELECT MAX(EventTime) FROM SecurityEvent WHERE EventType IN {_RESET_TYPES} "
        "AND Role = %s AND Username = %s), '1970-01-01')",
        (role, username, window, role, username), one=True)
    if row and row["n"] >= cfg["LOGIN_MAX_FAILS"]:
        left = max(1, -(-(window * 60 - int(row["age"] or 0)) // 60))
        return True, left
    return False, 0


def ip_blocked():
    cfg = current_app.config
    n = db.scalar("SELECT COUNT(*) FROM SecurityEvent WHERE EventType = 'LOGIN_FAIL' AND IpAddress = %s "
                  "AND EventTime > NOW() - INTERVAL %s MINUTE", (client_ip(), cfg["LOGIN_LOCK_MINUTES"]))
    return n >= cfg["LOGIN_IP_MAX_FAILS"]


def registration_allowed():
    n = db.scalar("SELECT COUNT(*) FROM SecurityEvent WHERE EventType = 'REGISTER' AND IpAddress = %s "
                  "AND EventTime > NOW() - INTERVAL 1 HOUR", (client_ip(),))
    return n < current_app.config["REGISTER_LIMIT_PER_HOUR"]


def unlock_account(role, username):
    log_event("UNLOCK", role, username, "Lock-out cleared by an officer")


# ---------------------------------------------------------------- HTTP security headers
_CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'")


def init_security_headers(app):
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Content-Security-Policy", _CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # pages of signed-in users must never be kept by a shared browser / proxy cache
        if session.get("role") and not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
