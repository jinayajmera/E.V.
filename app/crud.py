"""Generic, table-driven CRUD helpers used by the officer screens."""
from datetime import date
from . import db
from .db import AppError
from .auth import hash_password
from .validators import clean, parse_date, check_password_strength


def load_choices(spec):
    """{field_name: [(value, label), ...]} for every select field."""
    out = {}
    for f in spec["fields"]:
        if f["type"] != "select":
            continue
        if f["choices"] is not None:
            out[f["name"]] = list(f["choices"])
        else:
            rows = db.query(f["sql"])
            out[f["name"]] = [tuple(r.values()) for r in rows]
    return out


def blank_values(spec, ctx):
    vals = {}
    for f in spec["fields"]:
        d = f["default"]
        if d == "today":
            d = date.today().isoformat()
        elif d == "me":
            d = str(ctx.get("current_officer", ""))
        vals[f["name"]] = "" if d is None else str(d)
    return vals


def row_to_values(spec, row):
    vals = {}
    for f in spec["fields"]:
        v = row.get(f["name"])
        vals[f["name"]] = "" if (v is None or f["type"] == "password") else str(v)
    return vals


def parse_form(spec, form, is_new, choices):
    """Validate + convert form input -> dict ready for SQL. Raises AppError."""
    data = {}
    for f in spec["fields"]:
        if not is_new and not f["edit"]:
            continue
        raw = clean(form.get(f["name"]))
        label = f["label"]
        if f["type"] == "password":
            if not raw:
                if is_new:
                    raise AppError(f"{label} is required.")
                continue                      # edit: blank = keep the old password
            check_password_strength(raw)
            data[f["name"]] = hash_password(raw)
            continue
        if not raw:
            if f["required"]:
                raise AppError(f"{label} is required.")
            data[f["name"]] = None
            continue
        if f["maxlen"] and len(raw) > f["maxlen"]:
            raise AppError(f"{label} is too long (max {f['maxlen']} characters).")
        if f["type"] == "number":
            try:
                data[f["name"]] = int(raw)
            except ValueError:
                raise AppError(f"{label} must be a whole number.")
        elif f["type"] == "date":
            data[f["name"]] = parse_date(raw, label)
        elif f["type"] == "select":
            allowed = {str(v): v for v, _ in choices[f["name"]]}
            if raw not in allowed:
                raise AppError(f"{label}: choose a value from the list.")
            data[f["name"]] = allowed[raw]
        else:
            data[f["name"]] = raw
    return data


def list_rows(spec, q):
    sql = spec["list_sql"]
    params = []
    if q:
        cond = " OR ".join(f"CAST(t.{c} AS CHAR) LIKE %s" for c in spec["search"])
        sql = f"SELECT t.* FROM ({sql}) t WHERE {cond} ORDER BY t.{spec['pk']}"
        params = [f"%{q}%"] * len(spec["search"])
    else:
        sql = f"SELECT t.* FROM ({sql}) t ORDER BY t.{spec['pk']}"
    return db.query(sql, params)
