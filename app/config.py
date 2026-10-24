"""Configuration - values come from the .env file (see .env.example)."""
import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class Config:
    APP_NAME = os.getenv("APP_NAME", "Janmat")

    # ---- database
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = _int("DB_PORT", 3306)
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "evoting")

    # ---- secrets
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    # key used to turn identity numbers into one-way HMAC hashes (defaults to SECRET_KEY)
    ID_PEPPER = os.getenv("ID_PEPPER") or SECRET_KEY

    # ---- sessions / cookies
    SESSION_COOKIE_NAME = "janmat_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"      # set COOKIE_SECURE=1 behind HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=_int("SESSION_MINUTES", 30))   # idle time-out
    TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"                   # behind nginx / a load balancer

    # ---- uploads
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024                                 # whole request, 4 MB
    PHOTO_MAX_BYTES = 3 * 1024 * 1024
    PHOTO_MIN_PIXELS = 160
    PHOTO_DUP_DISTANCE = 6                                               # perceptual-hash distance that counts as "same photo"
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "instance" / "uploads")))
    LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))

    # ---- abuse protection
    LOGIN_MAX_FAILS = _int("LOGIN_MAX_FAILS", 5)                         # wrong passwords before a lock-out
    LOGIN_LOCK_MINUTES = _int("LOGIN_LOCK_MINUTES", 10)
    LOGIN_IP_MAX_FAILS = _int("LOGIN_IP_MAX_FAILS", 40)
    REGISTER_LIMIT_PER_HOUR = _int("REGISTER_LIMIT_PER_HOUR", 10)        # registrations per IP address

    SQL_DIR = BASE_DIR / "sql"
    STATIC_PEOPLE_DIR = BASE_DIR / "app" / "static" / "people"
