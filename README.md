# Janmat — online voting application (final version)

Flask (Python) web application on top of a MySQL 8 database named `evoting`.
Voters register with an identity number and photograph, an election officer verifies them, they vote once,
and results are published when the election closes.

**The upgrade never drops or empties anything.** It only *adds* columns, rules, tables, views,
functions, procedures and triggers, and it saves a JSON backup of every table first.

---

## 1. First-time setup (Windows commands shown; on Linux or macOS use `python3`, `source venv/bin/activate`, `cp` and `/` in paths)

Open **PowerShell inside this folder** (type `powershell` in the folder's address bar and press Enter).

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env          # enter your MySQL password
```

Your `.env` needs your own MySQL password:

```
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=evoting
SECRET_KEY=any-long-random-text
```

### Create the database (once, on a new MySQL 8.0.31+ server)

`sql/schema.sql` creates the database `evoting`, the core tables and some sample data. It never drops anything.

```powershell
mysql -u root -p < sql\schema.sql
```

### Install the application objects (once; safe to repeat)

```powershell
python scripts\upgrade_database.py
python scripts\check_setup.py        # must end with: EVERYTHING IS READY
```

### Run

```powershell
python run.py          # development  ->  http://127.0.0.1:5000
python serve.py        # production-style server (Waitress)  ->  http://127.0.0.1:8000
```

## 2. Demo accounts

| Role | Username | Password | Notes |
|---|---|---|---|
| Officer | `rajesh_off` | `officer123` | full control room |
| Voter | `karthik03` | `pass123` | Vellore Central — has **not** voted in GEN2026 |
| Voter | `lakshmi06` | `pass123` | Chennai North — has **not** voted in GEN2026 |
| Voter | `aarav01`, `priya02`, `farhan05`, `ananya04` | `pass123` | already voted in GEN2026 |
| Voter | `neha08` | `pass123` | account Inactive — cannot sign in |

Sample voters carry the sample identity numbers `VTR1000001` … `VTR1000008` (for example `VTR1000001` belongs to
`aarav01`) — use one of them to demonstrate that a second account with the same number is refused.

## 3. What is in the application

| Area | What it does |
|---|---|
| Home / results | Public elections list, candidates, results (CSV download) once an election is closed |
| Registration | Name, DOB (18+), e-mail, mobile, constituency, **identity number**, **photograph**, strong password |
| One person – one account – one vote | see section 4 |
| Voter area | Elections, two-step vote (choose → review → cast), receipt with verification code, profile, photo, password |
| Officer control room | KPIs and charts, **verification queue** with photos, voter register (search, filters, pagination, CSV), elections, nominations, eligibility, candidates (with pictures), parties, constituencies, officers |
| Reports | 48 ready-made analysis queries with CSV export |
| Security | Sign-in / lock-out / blocked-duplicate log, unlock accounts |
| Integrity | 8 live self-checks, duplicate watch, **live ACID demonstration** |
| Audit trail | Every change recorded by triggers; filter + pagination |

## 4. How duplicate / fake voters are prevented

| Attack | Defence |
|---|---|
| Same person, second account | Identity number (3 letters + 7 digits) stored only as an HMAC-SHA256 hash; database `UNIQUE` index |
| Same person, different e-mail (`a.b+x@gmail.com`, upper case, `googlemail.com`) | e-mail is normalised (case, `+tag`, Gmail dots) and the normalised value is `UNIQUE` |
| Same phone written differently (`+91 …`, `0…`, spaces) | last 10 digits are `UNIQUE` |
| Same photograph, or a resized / recompressed copy | SHA-256 `UNIQUE` plus a perceptual hash (dHash) comparison |
| Same name and date of birth | account is created **Flagged** for an officer to decide |
| Unverified account voting | `fn_can_vote`, `sp_cast_vote` and triggers require *Verified* + *Active* + *Eligible* |
| Changing the identity number later | database trigger forbids it |
| Brute-force passwords | lock-out after 5 failures per user (10 minutes), per-IP limit, generic error text, bcrypt |
| Forged forms / clickjacking / XSS | CSRF tokens on every POST, strict Content-Security-Policy (no inline scripts or styles), security headers |
| Malicious uploads | size/type checks, Pillow decoding and **re-encoding** (EXIF stripped), random file names, files served only through an authenticated route |
| Session theft / fixation | HttpOnly + SameSite cookies, new session on sign-in, 30-minute idle time-out, `Secure` flag with HTTPS |
| SQL injection | every query is parameterised |
| Information leaks | friendly error messages only; details go to `logs/app.log` |

## 5. ACID in the database

| Property | Where |
|---|---|
| **A**tomicity | `sp_cast_vote` (vote + eligibility flag + audit in one transaction), `sp_verify_voter`, registration in one transaction, rollback on any error |
| **C**onsistency | `PRIMARY/FOREIGN KEY`, `UNIQUE`, `CHECK`, triggers with `SIGNAL`, view `v_integrity_checks` (all must show 0) |
| **I**solation | `InnoDB` row locks, `SELECT … FOR UPDATE / FOR SHARE`, closing an election waits for votes in progress, deadlock retry |
| **D**urability | committed votes survive restarts (`innodb_flush_log_at_trx_commit = 1`) |

Officer → **Integrity** → *Run live transaction demo* shows all four properties working, step by step.

## 6. Tests

```powershell
python tests\run_final_tests.py     # 49 tests, runs on a throw-away copy (evoting_test), never on your data
python tests\run_negative_sql.py    # 18 statements that must FAIL (constraints / triggers / procedures)
```

SQL scripts for Workbench: `sql\queries.sql` (reports), `sql\crud_demo.sql` (CRUD story), `sql\tests_negative.sql`.

## 7. Project layout

```
app\            Flask application (routes, validators, security, photos, ACID demo, templates, static)
sql\            objects.sql, final_upgrade.sql, queries.sql, crud_demo.sql, tests_negative.sql
scripts\        upgrade_database.py, check_setup.py, install_objects.py, unlock_account.py, make_portraits.py
seed\voters\    illustrated sample portraits (no real people)
tests\          automated tests
DEPLOY.md       hosting guide (Waitress, Docker, HTTPS)
```

## 8. Troubleshooting

| Message | Fix |
|---|---|
| `No module named 'mysql'` | the virtual environment is not active: `venv\Scripts\activate`, then `pip install -r requirements.txt` |
| `Cannot connect to MySQL` | check `.env` and that the MySQL service is running |
| Locked out after wrong passwords | wait 10 minutes, or sign in as an officer → Security → Unlock (officer locked: `python scripts\unlock_account.py officer rajesh_off`) |
| Photo rejected | JPG/PNG/WEBP, 3 MB max, at least 160×160 px, and not already used by another voter |
