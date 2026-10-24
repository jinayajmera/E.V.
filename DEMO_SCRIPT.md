# Demonstration script (about 12 minutes)

Keep three windows ready: the website (`python run.py`), MySQL Workbench with `sql\queries.sql`, and a terminal.

## 1. Home page and design (1 min)
Open `http://127.0.0.1:5000`. Scroll: hero, how-it-works cards, "Here's what we offer", live elections.

## 2. Registration with identity + photo (2 min)
1. **Register** → fill the form, identity number e.g. `DEM2000001`, upload a photograph → *Registration received*.
2. Sign in as that user → dashboard shows **Identity pending** and no *Vote now* button (unverified accounts cannot vote).

## 3. One person – one account (2 min) — the security requirement
Try to register again and show each refusal:
| Try | Result |
|---|---|
| same identity number `dem 200-0001` | "identity number is already registered" |
| same person, new e-mail `Dem.O+x@gmail.com` variant of the first | "e-mail address (or a variation) is already registered" |
| same phone as `+91 …` | "phone number is already registered" |
| same photograph (or a resized copy) | "photograph is already registered" |
| same name + date of birth | accepted but **Flagged** for the officer |
Then Officer → **Security** shows the blocked attempts.

## 4. Officer: verification (1 min)
Sign in `rajesh_off / officer123` → **Verification** → compare photo/details → **Verify**. → **Eligibility** → add the voter to GEN2026.

## 5. Voting and receipt (2 min)
Sign in as the new voter (or `karthik03 / pass123`) → **Vote now** → choose → **Review** → **Cast** → receipt code. Try to vote again → refused.

## 6. Dashboard, search, reports (1 min)
Officer **Overview** (KPIs, turnout bars), **Voters** (search + filters + CSV), **Reports** (run one, export CSV), **Audit**.

## 7. Database integrity and ACID (2 min)
Officer → **Integrity**: all 8 checks show 0. Click **Run live transaction demo** and explain A, C, I, D.
In Workbench run `sql\tests_negative.sql` statements: every one fails with its protective message.

## 8. Security close-up (1 min)
Five wrong passwords → account locked (even the right password is refused) → Officer → Security → **Unlock**.

## Tests
`python tests\run_final_tests.py` → 49 tests OK.
