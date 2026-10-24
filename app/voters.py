"""Voter life-cycle : registration, identity, photograph, verification.
Used by the public registration page, the voter's own profile and the officer screens, so the
"one person - one account - one vote" rules are written exactly once."""
from datetime import date
from . import db, security, photos
from .db import AppError
from .auth import hash_password
from .validators import (clean, parse_date, years_between, EMAIL_RE, PHONE_RE, USER_RE,
                         check_password_strength, check_person_name)

GENDERS = ("Male", "Female", "Other")


def constituency_choices():
    return db.query("SELECT ConstituencyID, ConstituencyName, District FROM Constituency "
                    "ORDER BY ConstituencyName")


# ---------------------------------------------------------------- validation
def _clean_common(form, *, require_password):
    d = {}
    d["name"] = " ".join(clean(form.get("name")).split())
    check_person_name(d["name"])
    d["username"] = clean(form.get("username"))
    if not USER_RE.match(d["username"]):
        raise AppError("Username: 3-50 letters, digits, '_' or '.'.")
    d["email"] = clean(form.get("email")).lower()
    if not EMAIL_RE.match(d["email"]) or len(d["email"]) > 100:
        raise AppError("Enter a valid e-mail address.")
    phone = clean(form.get("phone"))
    digits = security.normalise_phone(phone)
    if not digits or len(digits) != 10 or not PHONE_RE.match(digits):
        raise AppError("Enter a valid 10-digit mobile number.")
    d["phone"] = digits
    d["dob"] = parse_date(clean(form.get("dob")), "Date of birth")
    if d["dob"] >= date.today():
        raise AppError("Date of birth must be in the past.")
    if years_between(d["dob"], date.today()) < 18:
        raise AppError("A voter must be at least 18 years old.")
    if years_between(d["dob"], date.today()) > 120:
        raise AppError("Please check the date of birth.")
    d["gender"] = clean(form.get("gender"))
    if d["gender"] not in GENDERS:
        raise AppError("Choose a gender from the list.")
    d["address"] = clean(form.get("address"))[:255] or None
    try:
        d["constituency_id"] = int(form.get("constituency_id", ""))
    except ValueError:
        raise AppError("Choose your constituency.")
    if not db.scalar("SELECT COUNT(*) FROM Constituency WHERE ConstituencyID = %s", (d["constituency_id"],)):
        raise AppError("Choose your constituency.")
    d["identity"] = security.check_identity_format(form.get("identity_number"))
    if require_password:
        pw = form.get("password") or ""
        if pw != (form.get("password2") or ""):
            raise AppError("The two passwords do not match.")
        check_password_strength(pw, d["username"], d["email"])
        d["password"] = pw
    return d


def _duplicate_checks(d, exclude_voter=None):
    """Friendly pre-checks. The UNIQUE indexes in the database are the real guarantee; these give a clear message."""
    ex = exclude_voter or 0
    h = security.hash_identity(d["identity"])
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE NationalIDHash = %s AND VoterID <> %s", (h, ex)):
        security.log_event("DUPLICATE_BLOCKED", "voter", d.get("username"), "Identity number already registered")
        raise AppError("This identity number is already registered to a voter account. "
                       "One person can hold only one voter account.")
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE LOWER(Username) = LOWER(%s) AND VoterID <> %s", (d["username"], ex)):
        raise AppError("That username is already taken.")
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE EmailNormalized = %s AND VoterID <> %s",
                 (security.normalise_email(d["email"]), ex)):
        security.log_event("DUPLICATE_BLOCKED", "voter", d.get("username"), "E-mail (or alias) already registered")
        raise AppError("This e-mail address (or a variation of it, such as a '+tag' or dots in a Gmail address) "
                       "is already registered.")
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE PhoneNormalized = %s AND VoterID <> %s", (d["phone"], ex)):
        security.log_event("DUPLICATE_BLOCKED", "voter", d.get("username"), "Phone number already registered")
        raise AppError("This phone number is already registered to another voter.")


def _same_person_suspect(d, exclude_voter=None):
    """Soft rule: same name + same date of birth as an existing voter -> goes to the officer as 'Flagged'."""
    row = db.query("SELECT VoterID FROM Voter WHERE LOWER(TRIM(Name)) = LOWER(%s) AND DOB = %s AND VoterID <> %s "
                   "ORDER BY VoterID LIMIT 1", (d["name"], d["dob"], exclude_voter or 0), one=True)
    return row["VoterID"] if row else None


def _photo_or_error(file_storage, exclude_voter=None):
    info = photos.process_upload(file_storage)
    dup = photos.find_duplicate(info["dhash"], info["sha256"], exclude_voter)
    if dup:
        security.log_event("DUPLICATE_BLOCKED", "voter", None, "Same photograph already registered")
        raise AppError("This photograph is already registered to another voter account.")
    return info


# ---------------------------------------------------------------- registration
def register_voter(form, photo_file, *, officer_id=None):
    """Create a voter. Public sign-up -> status Pending (an officer must verify). Officer-created -> Verified.
    The row + its audit trail are written in ONE transaction; the photograph file is removed again if it fails."""
    d = _clean_common(form, require_password=True)
    if not security.registration_allowed() and not officer_id:
        raise AppError("Too many registrations from this connection. Please try again later.")
    _duplicate_checks(d)
    info = _photo_or_error(photo_file)
    suspect = _same_person_suspect(d)

    if suspect:
        status, note, by, at = "Flagged", f"Same name and date of birth as voter #{suspect} - officer review needed", None, None
    elif officer_id:
        status, note, by, at = "Verified", "Registered and verified by an officer", officer_id, "NOW"
    else:
        status, note, by, at = "Pending", None, None, None

    fname = photos.save_voter_photo(info["data"])
    try:
        row = dict(Username=d["username"], Password=hash_password(d["password"]), AccountStatus="Active",
                   Name=d["name"], DOB=d["dob"], Gender=d["gender"], Address=d["address"], Email=d["email"],
                   Phone=d["phone"], RegistrationDate=date.today(), ConstituencyID=d["constituency_id"],
                   NationalIDHash=security.hash_identity(d["identity"]), NationalIDLast4=d["identity"][-4:],
                   IdentitySource="Registered", PhotoFile=fname, PhotoSha256=info["sha256"],
                   PhotoDHash=info["dhash"], VerificationStatus=status, VerifiedBy=by,
                   VerifiedAt=None, VerificationNote=note)
        with db.transaction(locks=[db.id_lock("Voter")]) as tx:
            vid = tx.insert_row("Voter", "VoterID", row)
            if at == "NOW":
                tx.execute("UPDATE Voter SET VerifiedAt = NOW() WHERE VoterID = %s", (vid,))
    except BaseException:
        photos.delete_voter_photo(fname)
        raise
    security.log_event("REGISTER", "voter", d["username"], "by officer" if officer_id else "self registration")
    return vid, status


# ---------------------------------------------------------------- photograph
def change_photo(voter_id, photo_file, *, by_officer=False):
    info = _photo_or_error(photo_file, exclude_voter=voter_id)
    fname = photos.save_voter_photo(info["data"])
    old = None
    try:
        with db.transaction() as tx:
            cur = tx.one("SELECT PhotoFile, VerificationStatus, Username FROM Voter WHERE VoterID = %s FOR UPDATE",
                         (voter_id,))
            if not cur:
                raise AppError("Voter not found.")
            old = cur["PhotoFile"]
            tx.execute("UPDATE Voter SET PhotoFile = %s, PhotoSha256 = %s, PhotoDHash = %s WHERE VoterID = %s",
                       (fname, info["sha256"], info["dhash"], voter_id))
            if not by_officer and cur["VerificationStatus"] == "Verified":
                tx.execute("UPDATE Voter SET VerificationStatus = 'Pending', VerificationNote = %s, "
                           "VerifiedBy = NULL, VerifiedAt = NULL WHERE VoterID = %s",
                           ("Photograph changed by the voter - verify again", voter_id))
    except BaseException:
        photos.delete_voter_photo(fname)
        raise
    photos.delete_voter_photo(old)
    return fname


# ---------------------------------------------------------------- identity number (for imported accounts)
def set_identity(voter_id, raw_identity):
    ident = security.check_identity_format(raw_identity)
    h = security.hash_identity(ident)
    cur = db.query("SELECT NationalIDHash, IdentitySource FROM Voter WHERE VoterID = %s", (voter_id,), one=True)
    if not cur:
        raise AppError("Voter not found.")
    if cur["NationalIDHash"] and cur["IdentitySource"] == "Registered":
        raise AppError("The identity number of a registered voter cannot be changed.")
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE NationalIDHash = %s AND VoterID <> %s", (h, voter_id)):
        security.log_event("DUPLICATE_BLOCKED", "voter", None, "Identity number already registered")
        raise AppError("This identity number is already registered to a voter account. "
                       "One person can hold only one voter account.")
    db.execute("UPDATE Voter SET NationalIDHash = %s, NationalIDLast4 = %s, IdentitySource = 'Registered' "
               "WHERE VoterID = %s", (h, ident[-4:], voter_id))


# ---------------------------------------------------------------- verification (stored procedure = one atomic step)
def verify(voter_id, officer_id, decision, note=""):
    if decision not in ("Verified", "Rejected", "Flagged", "Pending"):
        raise AppError("Invalid decision.")
    db.call_proc("sp_verify_voter", [voter_id, officer_id, decision, (note or "")[:255]])


# ---------------------------------------------------------------- officer edit
def update_details(voter_id, form):
    """Officer corrects a voter's details. Identity number and photograph have their own actions."""
    row = db.query("SELECT * FROM Voter WHERE VoterID = %s", (voter_id,), one=True)
    if not row:
        raise AppError("Voter not found.")
    d = dict(form)
    d["identity_number"] = d.get("identity_number") or "ABC0000000"      # not edited here (format placeholder)
    d["username"] = row["Username"]
    c = _clean_common(d, require_password=False)
    if years_between(c["dob"], row["RegistrationDate"]) < 18:
        raise AppError("That date of birth would make the voter younger than 18 on the registration date.")
    ex = voter_id
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE EmailNormalized = %s AND VoterID <> %s",
                 (security.normalise_email(c["email"]), ex)):
        raise AppError("This e-mail address (or a variation of it) is already registered.")
    if db.scalar("SELECT COUNT(*) FROM Voter WHERE PhoneNormalized = %s AND VoterID <> %s", (c["phone"], ex)):
        raise AppError("This phone number is already registered to another voter.")
    status = clean(form.get("account_status")) or row["AccountStatus"]
    if status not in ("Active", "Suspended", "Inactive"):
        raise AppError("Invalid account status.")
    sets = {"Name": c["name"], "DOB": c["dob"], "Gender": c["gender"], "Address": c["address"],
            "Email": c["email"], "Phone": c["phone"], "ConstituencyID": c["constituency_id"],
            "AccountStatus": status}
    pw = form.get("password") or ""
    if pw:
        check_password_strength(pw, row["Username"], c["email"])
        sets["Password"] = hash_password(pw)
    cols = ", ".join(f"{k} = %s" for k in sets)
    db.execute(f"UPDATE Voter SET {cols} WHERE VoterID = %s", list(sets.values()) + [voter_id])


def delete_voter(voter_id):
    row = db.query("SELECT PhotoFile FROM Voter WHERE VoterID = %s", (voter_id,), one=True)
    if not row:
        raise AppError("Voter not found.")
    n = db.execute("DELETE FROM Voter WHERE VoterID = %s", (voter_id,))
    if n:
        photos.delete_voter_photo(row["PhotoFile"])
    return n
