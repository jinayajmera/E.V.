"""Declarative description of the entities that get the generic officer CRUD screens.
Each entity maps to ONE table of evoting. Adding a field here adds it to the
list, the add-form, the edit-form, the validation and the INSERT / UPDATE."""
from datetime import date
from .db import AppError
from .validators import (EMAIL_RE, PHONE_RE, USER_RE, check_password_strength, years_between)

GENDERS = [("Male", "Male"), ("Female", "Female"), ("Other", "Other")]
STATUSES = [("Active", "Active"), ("Suspended", "Suspended"), ("Inactive", "Inactive")]


def F(name, label, type="text", required=True, maxlen=None, choices=None, sql=None,
      default=None, edit=True, show_on_new=True, help=None):
    return dict(name=name, label=label, type=type, required=required, maxlen=maxlen,
                choices=choices, sql=sql, default=default, edit=edit,
                show_on_new=show_on_new, help=help)


# ---------------------------------------------------------------- hooks
def _unused_voter_hook(data, is_new, ctx):
    if not USER_RE.match(data.get("Username") or ""):
        raise AppError("Username: 3-50 letters, digits, '_' or '.'.")
    if not EMAIL_RE.match(data["Email"]):
        raise AppError("Enter a valid e-mail address.")
    if data.get("Phone") and not PHONE_RE.match(data["Phone"]):
        raise AppError("Phone must be 10-15 digits.")
    if data["DOB"] >= date.today():
        raise AppError("Date of birth must be in the past.")
    reg = data.get("RegistrationDate") or date.today()
    if years_between(data["DOB"], reg) < 18:
        raise AppError("A voter must be at least 18 years old on the registration date.")


def _officer_hook(data, is_new, ctx):
    if not USER_RE.match(data.get("Username") or ""):
        raise AppError("Username: 3-50 letters, digits, '_' or '.'.")
    if not EMAIL_RE.match(data["Email"]):
        raise AppError("Enter a valid e-mail address.")
    if not PHONE_RE.match(data["Phone"]):
        raise AppError("Phone must be 10-15 digits.")
    if not is_new and ctx.get("id") == ctx.get("current_officer") and data.get("AccountStatus") != "Active":
        raise AppError("You cannot deactivate your own account.")


def _candidate_hook(data, is_new, ctx):
    if not USER_RE.match(data.get("Username") or ""):
        raise AppError("Username: 3-50 letters, digits, '_' or '.'.")
    if data["DOB"] >= date.today():
        raise AppError("Date of birth must be in the past.")
    if years_between(data["DOB"], date.today()) < 18:
        raise AppError("A candidate must be an adult (18+).")


def _election_hook(data, is_new, ctx):
    if data["EndDate"] < data["StartDate"]:
        raise AppError("End date cannot be before the start date.")
    from . import db
    st = db.scalar("SELECT AccountStatus FROM ElectionOfficer WHERE OfficerID = %s", (data["OfficerID"],))
    cur = None if is_new else db.scalar("SELECT OfficerID FROM Election WHERE ElectionID = %s", (ctx.get("id"),))
    if st != "Active" and data["OfficerID"] != cur:
        raise AppError("The responsible officer must have an Active account.")


def _party_hook(data, is_new, ctx):
    y = data.get("FoundedYear")
    if y is not None and not (1800 <= y <= 2100):
        raise AppError("Founded year must be between 1800 and 2100.")


def _delete_guard_officer(id_, ctx):
    if id_ == ctx.get("current_officer"):
        raise AppError("You cannot delete the account you are logged in with.")


ENTITIES = {
    "constituencies": dict(
        table="Constituency", pk="ConstituencyID", title="Constituency", plural="Constituencies",
        icon="geo-alt",
        list_sql="SELECT c.ConstituencyID, c.ConstituencyName, c.State, c.District, "
                 "(SELECT COUNT(*) FROM Voter v WHERE v.ConstituencyID = c.ConstituencyID) AS Voters "
                 "FROM Constituency c",
        columns=[("ConstituencyID", "ID"), ("ConstituencyName", "Name"), ("State", "State"),
                 ("District", "District"), ("Voters", "Voters")],
        search=["ConstituencyName", "State", "District"],
        fields=[F("ConstituencyName", "Constituency name", maxlen=100),
                F("State", "State", maxlen=100),
                F("District", "District", maxlen=100)],
    ),
    "parties": dict(
        table="PoliticalParty", pk="PartyID", title="Political party", plural="Political parties",
        icon="flag",
        list_sql="SELECT p.PartyID, p.PartyName, p.Symbol, p.FoundedYear, p.Description, "
                 "(SELECT COUNT(*) FROM Candidate c WHERE c.PartyID = p.PartyID) AS Candidates "
                 "FROM PoliticalParty p",
        columns=[("PartyID", "ID"), ("PartyName", "Party"), ("Symbol", "Symbol"),
                 ("FoundedYear", "Founded"), ("Candidates", "Candidates"), ("Description", "Description")],
        search=["PartyName", "Symbol", "Description"],
        fields=[F("PartyName", "Party name", maxlen=100),
                F("Symbol", "Symbol", maxlen=100),
                F("FoundedYear", "Founded year", type="number", required=False),
                F("Description", "Description", type="textarea", required=False, maxlen=255)],
        hook=_party_hook,
    ),
    "officers": dict(
        table="ElectionOfficer", pk="OfficerID", title="Election officer", plural="Election officers",
        icon="person-badge",
        list_sql="SELECT o.OfficerID, o.Name, o.Email, o.Phone, o.Username, o.AccountStatus, "
                 "(SELECT COUNT(*) FROM Election e WHERE e.OfficerID = o.OfficerID) AS Elections "
                 "FROM ElectionOfficer o",
        columns=[("OfficerID", "ID"), ("Name", "Name"), ("Email", "E-mail"), ("Phone", "Phone"),
                 ("Username", "Username"), ("AccountStatus", "Status"), ("Elections", "Elections")],
        search=["Name", "Email", "Username"],
        fields=[F("Name", "Full name", maxlen=100),
                F("Email", "E-mail", type="email", maxlen=100),
                F("Phone", "Phone", maxlen=15),
                F("Username", "Username", maxlen=50, edit=False),
                F("Password", "Password", type="password", maxlen=72,
                  help="Minimum 8 characters, letters + numbers. Leave blank when editing to keep the current one."),
                F("AccountStatus", "Account status", type="select", choices=STATUSES, default="Active")],
        hook=_officer_hook, delete_guard=_delete_guard_officer,
    ),
    "candidates": dict(
        table="Candidate", pk="CandidateID", title="Candidate", plural="Candidates", icon="person-vcard",
        list_sql="SELECT ca.CandidateID, ca.Username, ca.Name, ca.Gender, ca.DOB, p.PartyName, "
                 "ca.AccountStatus FROM Candidate ca JOIN PoliticalParty p ON p.PartyID = ca.PartyID",
        columns=[("CandidateID", "ID"), ("Name", "Name"), ("Username", "Username"), ("Gender", "Gender"),
                 ("DOB", "DOB"), ("PartyName", "Party"), ("AccountStatus", "Status")],
        search=["Name", "Username", "PartyName"],
        fields=[F("Username", "Username", maxlen=50, edit=False),
                F("Password", "Password", type="password", maxlen=72,
                  help="Minimum 8 characters, letters + numbers. Leave blank when editing to keep the current one."),
                F("AccountStatus", "Account status", type="select", choices=STATUSES, default="Active"),
                F("Name", "Full name", maxlen=100),
                F("DOB", "Date of birth", type="date"),
                F("Gender", "Gender", type="select", choices=GENDERS),
                F("PartyID", "Party", type="select",
                  sql="SELECT PartyID, PartyName FROM PoliticalParty ORDER BY PartyName")],
        photo_upload=True,
        hook=_candidate_hook,
    ),
    "elections": dict(
        table="Election", pk="ElectionID", title="Election", plural="Elections", icon="calendar-check",
        list_sql="SELECT e.ElectionID, e.ElectionCode, e.ElectionName, e.ElectionType, e.StartDate, "
                 "e.EndDate, e.Status, o.Name AS OfficerName "
                 "FROM Election e JOIN ElectionOfficer o ON o.OfficerID = e.OfficerID",
        columns=[("ElectionID", "ID"), ("ElectionCode", "Code"), ("ElectionName", "Name"),
                 ("ElectionType", "Type"), ("StartDate", "Start"), ("EndDate", "End"),
                 ("Status", "Status"), ("OfficerName", "Officer")],
        search=["ElectionCode", "ElectionName", "ElectionType", "Status"],
        fields=[F("ElectionCode", "Election code", maxlen=30, help="Unique, e.g. GEN2027"),
                F("ElectionName", "Election name", maxlen=100),
                F("Description", "Description", type="textarea", required=False, maxlen=255),
                F("ElectionType", "Type", type="select",
                  choices=[(t, t) for t in ("General", "State", "Bypoll", "Local")]),
                F("StartDate", "Start date", type="date"),
                F("EndDate", "End date", type="date"),
                F("OfficerID", "Responsible officer", type="select", default="me",
                  sql="SELECT OfficerID, CONCAT(Name, IF(AccountStatus = 'Active', '', CONCAT(' (', AccountStatus, ')'))) "
                      "FROM ElectionOfficer ORDER BY Name")],
        hook=_election_hook,
        extra_actions="elections",
    ),
}
