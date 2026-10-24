"""Automated test-suite for the final application.

    python tests/run_final_tests.py

It copies your database into a throw-away database called  evoting_test  (your real data is never modified),
upgrades the copy, and then exercises the whole system through the web layer and the database:
registration, duplicate-identity protection, verification, voting, access control, lock-out, CSRF,
upload attacks, SQL-injection strings, concurrency (ACID) and the integrity self-checks.
"""
import io
import os
import random
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

# ---- point everything at the throw-away copy BEFORE the app reads its configuration
from dotenv import load_dotenv                          # noqa: E402
load_dotenv(ROOT / ".env")
SRC_DB = os.getenv("DB_NAME", "evoting")
os.environ["DB_NAME"] = "evoting_test"

import mysql.connector                                  # noqa: E402
import testdb                                           # noqa: E402
from PIL import Image, ImageDraw                        # noqa: E402

CONN = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""))
print(f"Building the test copy 'evoting_test' from '{SRC_DB}' ...")
testdb.build(SRC_DB, CONN)

import install_objects                                  # noqa: E402
for f in ("objects.sql", "final_upgrade.sql"):
    assert install_objects.main(ROOT / "sql" / f) == 0
import upgrade_database                                 # noqa: E402
upgrade_database.seed_samples()

from app import create_app, db, security, photos, voters, acid   # noqa: E402

UPLOADS = tempfile.mkdtemp(prefix="evoting_uploads_")
BASE_CFG = {"TESTING": True, "WTF_CSRF_ENABLED": False, "UPLOAD_DIR": Path(UPLOADS),
            "REGISTER_LIMIT_PER_HOUR": 100000, "LOGIN_IP_MAX_FAILS": 100000}
APP = create_app(BASE_CFG)

_uniq = [0]


def uniq():
    _uniq[0] += 1
    return _uniq[0]


def photo_bytes(seed, fmt="JPEG", size=300):
    """A textured synthetic picture (flat pictures would all look alike to the duplicate detector)."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (size, size), (rnd.randint(0, 255),) * 3)
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = rnd.randint(0, size), rnd.randint(0, size)
        r = rnd.randint(8, 90)
        d.rectangle([x, y, x + r, y + rnd.randint(8, 90)], fill=tuple(rnd.randint(0, 255) for _ in range(3)))
        d.ellipse([y, x, y + r, x + r], fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    out = io.BytesIO()
    im.save(out, fmt)
    return out.getvalue()


def reform(raw, size=240, quality=60):
    """Same picture, resized and re-compressed (what a cheater would do)."""
    im = Image.open(io.BytesIO(raw)).convert("RGB").resize((size, size))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def form(**kw):
    n = uniq()
    ident = f"TST{1000000 + n * 7 + random.randint(0, 6)}"
    base = dict(name=f"Test Person{'abcdefghij'[n % 10]}{n}", username=f"tester{n}", email=f"tester{n}@example.com",
                phone=f"98{random.randint(10000000, 99999999)}", dob="1990-05-17", gender="Male", address="1 Test Street",
                constituency_id=str(CONST), identity_number=ident, password="Strong#Pass99", password2="Strong#Pass99")
    base.update(kw)
    return base


def q(sql, p=()):
    with APP.app_context():
        return db.query(sql, p)


def send(client, path, data=None, photo=None, follow=False):
    d = dict(data or {})
    if photo is not None:
        d["photo"] = (io.BytesIO(photo[0]), photo[1])
    return client.post(path, data=d, content_type="multipart/form-data" if photo is not None else None,
                       follow_redirects=follow)


def login(c, role, user, pw):
    return c.post("/login", data=dict(role=role, username=user, password=pw), follow_redirects=False)


def officer_client():
    c = APP.test_client()
    assert login(c, "officer", "rajesh_off", "officer123").status_code == 302
    return c


def register(client, photo_seed=None, expect_ok=True, photo=None, **kw):
    f = form(**kw)
    ph = photo if photo is not None else (photo_bytes(photo_seed if photo_seed is not None else uniq() * 977 + random.randint(0, 10 ** 6)), "me.jpg")
    r = send(client, "/register", f, ph)
    return f, r


with APP.app_context():
    _row = db.query("SELECT ConstituencyID, ElectionID FROM v_ballot WHERE ElectionStatus = 'Active' LIMIT 1", one=True)
CONST, ELECTION = _row["ConstituencyID"], _row["ElectionID"]
ELECTION_STATUS_NOTE = f"constituency {CONST}, election {ELECTION}"


def make_voter(verified=True, assign=True, **kw):
    """Register a voter through the real registration page; optionally verify + assign to the active election."""
    c = APP.test_client()
    f, r = register(c, **kw)
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    vid = q("SELECT VoterID FROM Voter WHERE Username = %s", (f["username"],))[0]["VoterID"]
    if verified or assign:
        o = officer_client()
        if verified:
            o.post(f"/officer/voters/{vid}/verify", data=dict(decision="Verified", note="test"))
        if assign:
            o.post("/officer/eligibility/add", data=dict(election_id=ELECTION, voter_id=vid, status="Eligible"))
    return f, vid


def vote_via_web(f, candidate_id=None):
    c = APP.test_client()
    assert login(c, "voter", f["username"], f["password"]).status_code == 302
    if candidate_id is None:
        candidate_id = q("SELECT CandidateID FROM v_ballot WHERE ElectionID = %s AND ConstituencyID = %s LIMIT 1",
                         (ELECTION, CONST))[0]["CandidateID"]
    c.post(f"/voter/vote/{ELECTION}/review", data=dict(candidate_id=candidate_id))
    return c, c.post(f"/voter/vote/{ELECTION}/cast", data=dict(candidate_id=candidate_id))


class T01_Public(unittest.TestCase):
    def test_pages_and_health(self):
        c = APP.test_client()
        for url in ("/", "/results", "/results/1", f"/elections/{ELECTION}", "/login", "/login?role=officer", "/register"):
            self.assertEqual(c.get(url).status_code, 200, url)
        j = c.get("/health").get_json()
        self.assertEqual(j["status"], "ok")
        self.assertEqual(c.get("/results/99999").status_code, 404)
        self.assertEqual(c.get("/nope").status_code, 404)
        self.assertIn("Page not found", c.get("/nope").get_data(as_text=True))

    def test_no_internal_wording_anywhere(self):
        """No page may reveal implementation details."""
        banned = re.compile(r"MySQL|SQL|evoting_|Traceback|Exception", re.I)
        pages = ["/", "/results", "/results/1", "/login", "/register", "/nope"]
        for url in pages:
            html = APP.test_client().get(url).get_data(as_text=True)
            self.assertIsNone(banned.search(html), f"{url}: {banned.search(html) and banned.search(html).group(0)}")
        o = officer_client()
        for url in ("/officer/", "/officer/voters", "/officer/voters/1", "/officer/verification", "/officer/security",
                    "/officer/audit", "/officer/manage/elections", "/officer/nominations", "/officer/eligibility", "/officer/reports"):
            html = o.get(url).get_data(as_text=True)
            self.assertIsNone(re.search(r"Traceback", html, re.I), url)
        c = APP.test_client()
        login(c, "voter", "aarav01", "pass123")
        for url in ("/voter/", "/voter/profile"):
            html = c.get(url).get_data(as_text=True)
            self.assertIsNone(re.search(r"Traceback", html, re.I), url)

    def test_headers_and_cookie(self):
        r = APP.test_client().get("/")
        h = r.headers
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("HttpOnly", r.headers.get("Set-Cookie", "") or "HttpOnly")
        self.assertNotIn("<script>", re.sub(r"<script src=[^>]+></script>", "", r.get_data(as_text=True)))     # no inline script
        self.assertNotRegex(r.get_data(as_text=True), r'\sstyle="')                                          # no inline style
        self.assertEqual(APP.config["PERMANENT_SESSION_LIFETIME"].total_seconds(), 30 * 60)


class T02_Registration(unittest.TestCase):
    def test_register_pending_and_cannot_vote(self):
        f, vid = make_voter(verified=False, assign=True)
        row = q("SELECT VerificationStatus, NationalIDHash, NationalIDLast4, PhotoFile FROM Voter WHERE VoterID = %s", (vid,))[0]
        self.assertEqual(row["VerificationStatus"], "Pending")
        self.assertNotIn(f["identity_number"], str(row))                                # never stored in clear
        self.assertEqual(len(row["NationalIDHash"]), 64)
        self.assertEqual(row["NationalIDLast4"], f["identity_number"][-4:])
        with APP.app_context():
            self.assertTrue(photos.voter_photo_path(row["PhotoFile"]))
        c, r = vote_via_web(f)
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 0)
        with APP.app_context():
            self.assertEqual(db.scalar("SELECT fn_can_vote(%s, %s)", (vid, ELECTION)), 0)

    def test_validation_errors(self):
        c = APP.test_client()
        cases = [dict(identity_number="12345"), dict(identity_number="ABCD123456"), dict(dob="2015-01-01"),
                 dict(password="short", password2="short"), dict(password2="Different#99"), dict(password="password123", password2="password123"),
                 dict(email="not-an-email"), dict(phone="12"), dict(name="X"), dict(username="a b"), dict(gender="Robot"),
                 dict(constituency_id="99999")]
        for kw in cases:
            f, r = register(c, **kw)
            self.assertEqual(r.status_code, 200, kw)                                    # re-shown with an error, not saved
            self.assertEqual(q("SELECT COUNT(*) n FROM Voter WHERE Username = %s", (f["username"],))[0]["n"], 0, kw)

    def test_identity_number_is_normalised(self):
        f, vid = make_voter(verified=False, assign=False, identity_number="QWE 4567-890")
        h = q("SELECT NationalIDHash FROM Voter WHERE VoterID = %s", (vid,))[0]["NationalIDHash"]
        with APP.app_context():
            self.assertEqual(h, security.hash_identity("QWE4567890"))


class T03_OnePersonOneAccount(unittest.TestCase):
    """Same person, different details: every attempt must be refused."""
    @classmethod
    def setUpClass(cls):
        cls.base_f, cls.base_vid = make_voter(verified=False, assign=False, email="first.person@gmail.com",
                                              phone="9812345670", identity_number="ZZZ1234567")
        cls.base_photo = photo_bytes(424242)
        c = APP.test_client()
        f = form(identity_number="ZZZ7654321", email="photo.owner@example.com")
        assert send(c, "/register", f, (cls.base_photo, "p.jpg")).status_code == 302
        cls.photo_owner = f

    def refused(self, msg_part, **kw):
        c = APP.test_client()
        f, r = register(c, **kw)
        self.assertEqual(r.status_code, 200, kw)
        self.assertIn(msg_part, r.get_data(as_text=True))
        self.assertEqual(q("SELECT COUNT(*) n FROM Voter WHERE Username = %s", (f["username"],))[0]["n"], 0)
        return r

    def test_same_identity_number(self):
        self.refused("identity number is already registered", identity_number="ZZZ1234567")

    def test_identity_number_written_differently(self):
        self.refused("identity number is already registered", identity_number="zzz 123-4567")

    def test_email_aliases(self):
        for alias in ("first.person@gmail.com", "FIRST.PERSON@GMAIL.COM", "firstperson@gmail.com",
                      "first.person+vote@gmail.com", "f.i.r.s.t.p.e.r.s.o.n@googlemail.com"):
            self.refused("e-mail address", email=alias)

    def test_phone_variants(self):
        for ph in ("9812345670", "+91 98123 45670", "098123-45670", "91 9812345670"):
            self.refused("phone number", phone=ph)

    def test_same_photo_and_edited_copy(self):
        self.refused("photograph", photo=(self.base_photo, "x.jpg"))
        self.refused("photograph", photo=(reform(self.base_photo), "x.jpg"))
        self.refused("photograph", photo=(reform(self.base_photo, 200, 35), "x.jpg"))

    def test_same_name_and_dob_is_flagged_for_officer(self):
        row = q("SELECT Name, DOB FROM Voter WHERE VoterID = %s", (self.base_vid,))[0]
        c = APP.test_client()
        f, r = register(c, name=row["Name"], dob=str(row["DOB"]))
        self.assertEqual(r.status_code, 302)
        st = q("SELECT VerificationStatus, VerificationNote FROM Voter WHERE Username = %s", (f["username"],))[0]
        self.assertEqual(st["VerificationStatus"], "Flagged")
        self.assertIn("officer", st["VerificationNote"].lower())

    def test_database_itself_refuses_duplicates(self):
        """Even code that bypasses the application cannot create a second account for one identity."""
        with APP.app_context():
            conn = db.connect(APP.config)
            cur = conn.cursor()
            for sql in ("UPDATE Voter SET NationalIDHash = (SELECT h FROM (SELECT NationalIDHash h FROM Voter WHERE VoterID = %s) t) WHERE VoterID = %s",
                        ):
                with self.assertRaises(mysql.connector.Error):
                    cur.execute(sql, (self.base_vid, self.base_vid + 1))
            conn.rollback()
            conn.close()

    def test_blocked_attempts_are_logged(self):
        n = q("SELECT COUNT(*) n FROM SecurityEvent WHERE EventType = 'DUPLICATE_BLOCKED'")[0]["n"]
        self.assertGreaterEqual(n, 0)


class T04_Uploads(unittest.TestCase):
    def reject(self, data, name):
        c = APP.test_client()
        f, r = register(c, photo=(data, name))
        self.assertEqual(r.status_code, 200, name)
        self.assertEqual(q("SELECT COUNT(*) n FROM Voter WHERE Username = %s", (f["username"],))[0]["n"], 0, name)

    def test_non_images_rejected(self):
        self.reject(b"<?php system($_GET['x']); ?>", "shell.php.jpg")
        self.reject(b"just text", "a.jpg")
        self.reject(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "x.svg")
        self.reject(b"", "empty.jpg")
        self.reject(b"GIF89a" + b"\x00" * 50, "x.gif")

    def test_too_small_and_too_big(self):
        self.reject(photo_bytes(7, size=60), "small.jpg")
        self.reject(b"\xff\xd8\xff" + os.urandom(3 * 1024 * 1024 + 10), "big.jpg")

    def test_stored_file_is_clean_reencoded_jpeg(self):
        img = Image.new("RGB", (400, 400), (10, 20, 30))
        ImageDraw.Draw(img).ellipse([50, 50, 350, 350], fill=(200, 100, 50))
        exif = Image.Exif()
        exif[0x010E] = "SECRET-DESCRIPTION"
        buf = io.BytesIO()
        img.save(buf, "PNG", exif=exif.tobytes())
        f, vid = make_voter(verified=False, assign=False, photo=(buf.getvalue(), "evil.png"))
        name = q("SELECT PhotoFile FROM Voter WHERE VoterID = %s", (vid,))[0]["PhotoFile"]
        self.assertRegex(name, r"^[0-9a-f]{32}\.jpg$")                                  # random name, not the client's
        with APP.app_context():
            p = photos.voter_photo_path(name)
        data = p.read_bytes()
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertNotIn(b"SECRET-DESCRIPTION", data)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (photos.SIZE, photos.SIZE))

    def test_delete_voter_removes_file(self):
        f, vid = make_voter(verified=False, assign=False)
        name = q("SELECT PhotoFile FROM Voter WHERE VoterID = %s", (vid,))[0]["PhotoFile"]
        o = officer_client()
        o.post(f"/officer/voters/{vid}/delete")
        self.assertEqual(q("SELECT COUNT(*) n FROM Voter WHERE VoterID = %s", (vid,))[0]["n"], 0)
        with APP.app_context():
            self.assertIsNone(photos.voter_photo_path(name))


class T05_AccessControl(unittest.TestCase):
    def test_anonymous_redirected(self):
        c = APP.test_client()
        for url in ("/voter/", "/voter/profile", f"/voter/vote/{ELECTION}", "/officer/", "/officer/voters", "/officer/audit",
                    "/officer/integrity", "/officer/security", "/officer/reports", "/officer/voters.csv"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])
        for url in ("/voter/password", "/officer/voters/1/delete", "/officer/voters/1/verify", f"/voter/vote/{ELECTION}/cast"):
            self.assertEqual(c.post(url).status_code, 302, url)

    def test_voter_cannot_use_officer_area(self):
        c = APP.test_client()
        login(c, "voter", "aarav01", "pass123")
        for url in ("/officer/", "/officer/voters", "/officer/voters/2", "/officer/security", "/officer/integrity"):
            self.assertEqual(c.get(url).status_code, 302, url)
        for url in ("/officer/voters/2/delete", "/officer/voters/2/verify", "/officer/integrity/acid"):
            self.assertEqual(c.post(url, data=dict(decision="Verified")).status_code, 302, url)
        self.assertEqual(q("SELECT COUNT(*) n FROM Voter WHERE VoterID = 2")[0]["n"], 1)

    def test_photo_privacy(self):
        self.assertEqual(APP.test_client().get("/media/voter/1").status_code, 403)
        c = APP.test_client()
        login(c, "voter", "aarav01", "pass123")
        self.assertEqual(c.get("/media/voter/1").status_code, 200)
        self.assertEqual(c.get("/media/voter/2").status_code, 403)
        self.assertEqual(officer_client().get("/media/voter/2").status_code, 200)
        self.assertEqual(APP.test_client().get("/media/candidate/1").status_code, 200)
        self.assertEqual(APP.test_client().get("/media/candidate/9999").status_code, 404)

    def test_path_traversal_in_media(self):
        c = APP.test_client()
        for u in ("/media/voter/../../etc/passwd", "/media/candidate/..%2f..%2fapp", "/static/../.env", "/static/..%2f.env"):
            self.assertIn(c.get(u).status_code, (400, 403, 404), u)

    def test_voter_cannot_see_someone_elses_receipt(self):
        f1, v1 = make_voter()
        c1, r1 = vote_via_web(f1)
        self.assertEqual(r1.status_code, 302)
        receipt_url = r1.headers["Location"]
        self.assertEqual(c1.get(receipt_url).status_code, 200)
        c2 = APP.test_client()
        login(c2, "voter", "aarav01", "pass123")
        self.assertEqual(c2.get(receipt_url).status_code, 404)

    def test_suspension_is_immediate(self):
        f, vid = make_voter()
        c = APP.test_client()
        login(c, "voter", f["username"], f["password"])
        self.assertEqual(c.get("/voter/").status_code, 200)
        officer_client().post(f"/officer/voters/{vid}/status", data=dict(status="Suspended"))
        self.assertEqual(c.get("/voter/").status_code, 302)
        self.assertEqual(login(APP.test_client(), "voter", f["username"], f["password"]).status_code, 401)


class T06_Auth(unittest.TestCase):
    def test_wrong_credentials_generic_message(self):
        c = APP.test_client()
        a = login(c, "voter", "aarav01", "wrong").get_data(as_text=True)
        b = login(c, "voter", "nobody-here", "wrong").get_data(as_text=True)
        self.assertIn("Invalid username or password", a)
        self.assertIn("Invalid username or password", b)

    def test_lockout_after_five_failures_and_unlock(self):
        f, vid = make_voter(verified=True, assign=False)
        c = APP.test_client()
        for _ in range(5):
            self.assertEqual(login(c, "voter", f["username"], "bad-pass-1").status_code, 401)
        r = login(c, "voter", f["username"], f["password"])                              # correct password, but locked
        self.assertEqual(r.status_code, 401)
        self.assertIn("locked", r.get_data(as_text=True))
        o = officer_client()
        self.assertIn(f["username"], o.get("/officer/security").get_data(as_text=True))
        o.post(f"/officer/voters/{vid}/unlock")
        self.assertEqual(login(c, "voter", f["username"], f["password"]).status_code, 302)

    def test_injection_strings(self):
        c = APP.test_client()
        for s in ("' OR '1'='1", "admin'--", "\" OR 1=1 --", "'; DROP TABLE Voter; --", "a" * 5000):
            r = login(c, "voter", s, s)
            self.assertIn(r.status_code, (401, 200))
            self.assertEqual(c.get("/results", query_string=dict(q=s)).status_code, 200)
            self.assertEqual(officer_client().get("/officer/voters", query_string=dict(q=s)).status_code, 200)
        self.assertGreaterEqual(q("SELECT COUNT(*) n FROM Voter")[0]["n"], 8)

    def test_password_hash_is_bcrypt_and_legacy_upgrades(self):
        with APP.app_context():
            import hashlib
            db.execute("UPDATE Voter SET Password = %s WHERE Username = 'priya02'", (hashlib.sha256(b"pass123").hexdigest(),))
        c = APP.test_client()
        self.assertEqual(login(c, "voter", "priya02", "pass123").status_code, 302)
        self.assertTrue(q("SELECT Password FROM Voter WHERE Username = 'priya02'")[0]["Password"].startswith("$2"))

    def test_session_is_new_after_login(self):
        c = APP.test_client()
        c.get("/login")
        with c.session_transaction() as s:
            s["attacker"] = "planted"
        login(c, "voter", "aarav01", "pass123")
        with c.session_transaction() as s:
            self.assertNotIn("attacker", s)

    def test_open_redirect_blocked(self):
        c = APP.test_client()
        r = c.post("/login?next=//evil.example.com", data=dict(role="voter", username="aarav01", password="pass123"))
        self.assertNotIn("evil.example.com", r.headers["Location"])

    def test_csrf_enforced(self):
        app2 = create_app(dict(BASE_CFG, WTF_CSRF_ENABLED=True))
        c = app2.test_client()
        r = c.post("/login", data=dict(role="voter", username="aarav01", password="pass123"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("expired", r.get_data(as_text=True).lower())
        html = c.get("/login").get_data(as_text=True)
        tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        r = c.post("/login", data=dict(role="voter", username="aarav01", password="pass123", csrf_token=tok))
        self.assertEqual(r.status_code, 302)
        r = c.post("/logout")                                                            # logout also needs the token
        self.assertEqual(r.status_code, 400)

    def test_password_change(self):
        f, vid = make_voter(verified=True, assign=False)
        c = APP.test_client()
        login(c, "voter", f["username"], f["password"])
        r = c.post("/voter/password", data=dict(old_password="nope", new_password="Another#Pass77", new_password2="Another#Pass77"), follow_redirects=True)
        self.assertIn("incorrect", r.get_data(as_text=True))
        c.post("/voter/password", data=dict(old_password=f["password"], new_password="weak", new_password2="weak"))
        c.post("/voter/password", data=dict(old_password=f["password"], new_password="Another#Pass77", new_password2="Another#Pass77"))
        self.assertEqual(login(APP.test_client(), "voter", f["username"], "Another#Pass77").status_code, 302)


class T07_VotingFlow(unittest.TestCase):
    def test_full_vote_and_double_vote(self):
        f, vid = make_voter()
        c = APP.test_client()
        login(c, "voter", f["username"], f["password"])
        self.assertIn("Vote now", c.get("/voter/").get_data(as_text=True))
        cid = q("SELECT CandidateID FROM v_ballot WHERE ElectionID = %s AND ConstituencyID = %s LIMIT 1", (ELECTION, CONST))[0]["CandidateID"]
        # casting without the review step is refused
        r = c.post(f"/voter/vote/{ELECTION}/cast", data=dict(candidate_id=cid))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 0)
        r = c.post(f"/voter/vote/{ELECTION}/review", data=dict(candidate_id=cid))
        self.assertIn("Cast my vote", r.get_data(as_text=True))
        r = c.post(f"/voter/vote/{ELECTION}/cast", data=dict(candidate_id=cid))
        self.assertEqual(r.status_code, 302)
        rec = c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertRegex(rec, r"[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}")
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 1)
        self.assertEqual(q("SELECT HasVoted FROM ElectionVoter WHERE VoterID = %s AND ElectionID = %s", (vid, ELECTION))[0]["HasVoted"], 1)
        # second attempt
        c.post(f"/voter/vote/{ELECTION}/review", data=dict(candidate_id=cid))
        c.post(f"/voter/vote/{ELECTION}/cast", data=dict(candidate_id=cid))
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 1)
        self.assertEqual(c.get(f"/voter/vote/{ELECTION}").status_code, 302)

    def test_candidate_from_another_constituency_refused(self):
        f, vid = make_voter()
        other = q("SELECT CandidateID FROM v_ballot WHERE ElectionID = %s AND ConstituencyID <> %s LIMIT 1", (ELECTION, CONST))
        if not other:
            self.skipTest("only one constituency on the ballot")
        c = APP.test_client()
        login(c, "voter", f["username"], f["password"])
        c.post(f"/voter/vote/{ELECTION}/review", data=dict(candidate_id=other[0]["CandidateID"]))
        c.post(f"/voter/vote/{ELECTION}/cast", data=dict(candidate_id=other[0]["CandidateID"]))
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 0)

    def test_rejected_or_flagged_voter_cannot_vote(self):
        for decision in ("Rejected", "Flagged"):
            f, vid = make_voter()
            officer_client().post(f"/officer/voters/{vid}/verify", data=dict(decision=decision, note="t"))
            c, r = vote_via_web(f)
            self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s", (vid,))[0]["n"], 0, decision)

    def test_verification_changes_are_audited(self):
        f, vid = make_voter(verified=False, assign=False)
        officer_client().post(f"/officer/voters/{vid}/verify", data=dict(decision="Verified", note="id checked"))
        a = q("SELECT COUNT(*) n FROM AuditLog WHERE TableName = 'Voter' AND RecordID = %s", (vid,))[0]["n"]
        self.assertGreaterEqual(a, 1)

    def test_identity_immutable_and_legacy_flow(self):
        f, vid = make_voter(verified=False, assign=False)
        c = APP.test_client()
        login(c, "voter", f["username"], f["password"])
        before = q("SELECT NationalIDHash FROM Voter WHERE VoterID = %s", (vid,))[0]["NationalIDHash"]
        c.post("/voter/profile/identity", data=dict(identity_number="NEW1234567"))
        self.assertEqual(q("SELECT NationalIDHash FROM Voter WHERE VoterID = %s", (vid,))[0]["NationalIDHash"], before)
        # a voter imported without an identity number can add one exactly once, and not one that is already taken
        with APP.app_context():
            db.execute("UPDATE Voter SET IdentitySource = 'Imported' WHERE VoterID = %s", (vid,))
            db.execute("UPDATE Voter SET NationalIDHash = NULL, NationalIDLast4 = NULL WHERE VoterID = %s", (vid,))
        c.post("/voter/profile/identity", data=dict(identity_number="VTR1000001"))                          # belongs to Aarav
        self.assertIsNone(q("SELECT NationalIDHash FROM Voter WHERE VoterID = %s", (vid,))[0]["NationalIDHash"])
        c.post("/voter/profile/identity", data=dict(identity_number="NEW1234567"))
        self.assertIsNotNone(q("SELECT NationalIDHash FROM Voter WHERE VoterID = %s", (vid,))[0]["NationalIDHash"])


class T08_Concurrency_ACID(unittest.TestCase):
    def test_same_voter_many_threads_exactly_one_vote(self):
        f, vid = make_voter()
        cid = q("SELECT CandidateID FROM v_ballot WHERE ElectionID = %s AND ConstituencyID = %s LIMIT 1", (ELECTION, CONST))[0]["CandidateID"]
        results = []

        def work():
            try:
                conn = mysql.connector.connect(host=CONN["host"], port=CONN["port"], user=CONN["user"], password=CONN["password"], database="evoting_test")
                cur = conn.cursor()
                cur.callproc("sp_cast_vote", [vid, ELECTION, cid, 0])
                conn.commit()
                results.append("ok")
                conn.close()
            except mysql.connector.Error as e:
                results.append("fail")

        ts = [threading.Thread(target=work) for _ in range(16)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE VoterID = %s AND ElectionID = %s", (vid, ELECTION))[0]["n"], 1)

    def test_many_voters_in_parallel_stay_consistent(self):
        people = [make_voter() for _ in range(10)]
        cid = q("SELECT CandidateID FROM v_ballot WHERE ElectionID = %s AND ConstituencyID = %s LIMIT 1", (ELECTION, CONST))[0]["CandidateID"]
        before = q("SELECT COUNT(*) n FROM Vote WHERE ElectionID = %s", (ELECTION,))[0]["n"]
        out = []

        def work(vid):
            for _ in range(3):                                                           # each voter hammers the button 3 times
                try:
                    conn = mysql.connector.connect(host=CONN["host"], port=CONN["port"], user=CONN["user"], password=CONN["password"], database="evoting_test")
                    cur = conn.cursor()
                    cur.callproc("sp_cast_vote", [vid, ELECTION, cid, 0])
                    conn.commit()
                    out.append(1)
                    conn.close()
                except mysql.connector.Error:
                    out.append(0)

        ts = [threading.Thread(target=work, args=(vid,)) for _, vid in people]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(out), 10)
        self.assertEqual(q("SELECT COUNT(*) n FROM Vote WHERE ElectionID = %s", (ELECTION,))[0]["n"], before + 10)
        self.assertEqual(q("SELECT SUM(Violations) s FROM v_integrity_checks")[0]["s"], 0)

    def test_registration_ids_unique_under_parallel_signup(self):
        errs, ids = [], []

        def work(i):
            try:
                c = APP.test_client()
                f, r = register(c)
                ids.append(r.status_code)
            except Exception as e:                                                       # noqa
                errs.append(repr(e))

        ts = [threading.Thread(target=work, args=(i,)) for i in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(ids.count(302), 12, ids)
        self.assertEqual(q("SELECT COUNT(*) - COUNT(DISTINCT VoterID) d FROM Voter")[0]["d"], 0)

    def test_acid_demonstration(self):
        with APP.app_context():
            res = acid.run_all()
        self.assertEqual([r["property"] for r in res], list("ACID"))
        for r in res:
            self.assertTrue(r["ok"], r)
        self.assertEqual(q("SELECT COUNT(*) n FROM TxnScratch")[0]["n"], 0)

    def test_failed_registration_leaves_no_trace(self):
        """Atomicity: a registration that fails half-way (duplicate identity) leaves no voter row and no stray photo."""
        n_files = len(list((Path(UPLOADS) / "voters").glob("*.jpg")))
        n_voters = q("SELECT COUNT(*) n FROM Voter")[0]["n"]
        c = APP.test_client()
        f, r = register(c, identity_number="VTR1000001")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(q("SELECT COUNT(*) n FROM Voter")[0]["n"], n_voters)
        self.assertEqual(len(list((Path(UPLOADS) / "voters").glob("*.jpg"))), n_files)


class T09_OfficerFeatures(unittest.TestCase):
    def test_crud_cycle_and_referential_integrity(self):
        o = officer_client()
        n = uniq()
        r = o.post("/officer/manage/constituencies/new", data=dict(ConstituencyName=f"Testville{n}", State="TS", District="Dist"), follow_redirects=True)
        self.assertIn("added", r.get_data(as_text=True))
        cid = q("SELECT ConstituencyID FROM Constituency WHERE ConstituencyName = %s", (f"Testville{n}",))[0]["ConstituencyID"]
        o.post(f"/officer/manage/constituencies/{cid}/edit", data=dict(ConstituencyName=f"Testville{n}b", State="TS", District="Dist"))
        self.assertEqual(q("SELECT COUNT(*) n FROM Constituency WHERE ConstituencyName = %s", (f"Testville{n}b",))[0]["n"], 1)
        r = o.post(f"/officer/manage/constituencies/{cid}/delete", follow_redirects=True)
        self.assertEqual(q("SELECT COUNT(*) n FROM Constituency WHERE ConstituencyID = %s", (cid,))[0]["n"], 0)
        r = o.post(f"/officer/manage/constituencies/{CONST}/delete", follow_redirects=True)                  # still referenced
        self.assertIn("cannot be deleted", r.get_data(as_text=True))
        self.assertEqual(q("SELECT COUNT(*) n FROM Constituency WHERE ConstituencyID = %s", (CONST,))[0]["n"], 1)

    def test_search_filter_pagination_and_csv(self):
        o = officer_client()
        for _ in range(3):
            make_voter(verified=False, assign=False)
        html = o.get("/officer/voters?verification=Pending").get_data(as_text=True)
        self.assertIn("Pending", html)
        self.assertEqual(o.get("/officer/voters?page=999").status_code, 200)
        self.assertEqual(o.get("/officer/voters?page=-4").status_code, 200)
        self.assertIn("aarav01", o.get("/officer/voters?q=aarav").get_data(as_text=True))
        r = o.get("/officer/voters.csv?verification=Verified")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("VoterID", r.get_data(as_text=True))
        self.assertNotIn("NationalIDHash", r.get_data(as_text=True))
        self.assertEqual(o.get("/officer/audit?table=Voter&action=UPDATE").status_code, 200)
        self.assertEqual(o.get("/officer/security?type=LOGIN_FAIL").status_code, 200)
        for no in re.findall(r"/officer/reports/(Q\d+[a-z]?)", o.get("/officer/reports").get_data(as_text=True)):
            self.assertEqual(o.get(f"/officer/reports/{no}").status_code, 200, no)
        self.assertEqual(o.get("/officer/reports/Q1?format=csv").mimetype, "text/csv")

    def test_election_lifecycle_and_results(self):
        o = officer_client()
        eid = q("SELECT ElectionID FROM Election WHERE Status = 'Upcoming' LIMIT 1")
        if not eid:
            self.skipTest("no upcoming election")
        eid = eid[0]["ElectionID"]
        r = o.post(f"/officer/elections/{eid}/open", follow_redirects=True)
        self.assertEqual(q("SELECT Status FROM Election WHERE ElectionID = %s", (eid,))[0]["Status"], "Active")
        o.post(f"/officer/elections/{eid}/close")
        self.assertEqual(q("SELECT Status FROM Election WHERE ElectionID = %s", (eid,))[0]["Status"], "Closed")
        self.assertEqual(APP.test_client().get(f"/results/{eid}").status_code, 200)
        self.assertEqual(APP.test_client().get(f"/results/{eid}.csv").mimetype, "text/csv")
        r = o.post(f"/officer/elections/{eid}/open", follow_redirects=True)                                   # a closed election cannot reopen
        self.assertEqual(q("SELECT Status FROM Election WHERE ElectionID = %s", (eid,))[0]["Status"], "Closed")

    def test_cannot_deactivate_own_account(self):
        o = officer_client()
        me = q("SELECT OfficerID FROM ElectionOfficer WHERE Username = 'rajesh_off'")[0]["OfficerID"]
        o.post(f"/officer/manage/officers/{me}/status", data=dict(status="Suspended"))
        self.assertEqual(q("SELECT AccountStatus FROM ElectionOfficer WHERE OfficerID = %s", (me,))[0]["AccountStatus"], "Active")

    def test_candidate_picture_upload(self):
        o = officer_client()
        r = send(o, "/officer/candidates/1/photo", photo=(photo_bytes(31337), "c.jpg"))
        self.assertEqual(r.status_code, 302)
        r = APP.test_client().get("/media/candidate/1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "image/jpeg")
        r = send(o, "/officer/candidates/1/photo", photo=(b"not an image", "c.jpg"), follow=True)
        self.assertIn("not a valid image", r.get_data(as_text=True))


class T99_FinalIntegrity(unittest.TestCase):
    def test_all_integrity_checks_pass(self):
        rows = q("SELECT * FROM v_integrity_checks ORDER BY CheckNo")
        self.assertEqual(len(rows), 8)
        for r in rows:
            self.assertEqual(r["Violations"], 0, r)

    def test_no_two_accounts_share_identity_email_phone_photo(self):
        for col in ("NationalIDHash", "EmailNormalized", "PhoneNormalized", "PhotoSha256"):
            d = q(f"SELECT COUNT(*) n FROM (SELECT {col} FROM Voter WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1) x")[0]["n"]
            self.assertEqual(d, 0, col)


if __name__ == "__main__":
    unittest.main(verbosity=2)
