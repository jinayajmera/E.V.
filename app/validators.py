"""Server-side validators (the database constraints are the second line of defence)."""
import re
from datetime import date, datetime
from .db import AppError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[0-9]{10,15}$")
USER_RE = re.compile(r"^[A-Za-z0-9_.]{3,50}$")
NAME_RE = re.compile(r"^[^\W\d_][\w .'\-]{1,98}$", re.UNICODE)
ID_RE = re.compile(r"^[A-Z]{3}[0-9]{7}$")

COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyuiop", "iloveyou1", "welcome1", "welcome123", "admin123", "admin1234",
    "letmein123", "abc12345", "abcd1234", "test1234", "changeme1", "officer123", "pass1234",
    "voter123", "india123", "india1234", "11111111", "00000000",
}


def clean(v):
    return (v or "").strip()


def parse_date(text, label):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise AppError(f"{label}: enter a valid date (YYYY-MM-DD).")


def years_between(dob: date, on: date):
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def check_password_strength(pw, username=None, email=None):
    if len(pw) < 8:
        raise AppError("Password must be at least 8 characters.")
    if len(pw) > 72:
        raise AppError("Password can be at most 72 characters.")
    if pw.isalpha() or pw.isdigit():
        raise AppError("Password must contain both letters and numbers.")
    low = pw.lower()
    if low in COMMON_PASSWORDS:
        raise AppError("That password is too common. Please choose a less predictable one.")
    if username and username.lower() in low and len(username) >= 4:
        raise AppError("Password must not contain your username.")
    if email and email.split("@")[0].lower() in low and len(email.split("@")[0]) >= 4:
        raise AppError("Password must not contain your e-mail name.")


def check_person_name(name):
    if not name or len(name) < 2:
        raise AppError("Enter your full name.")
    if not NAME_RE.match(name):
        raise AppError("Name may contain only letters, spaces and . ' -")
