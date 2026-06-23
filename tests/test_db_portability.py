#!/usr/bin/env python3
"""Database-portability tests — guard against SQLite-only SQL slipping in.

Two checks:
  1. A static scan for double-quoted string *literals* in SQL. SQLite quietly
     treats an unknown double-quoted token as a string literal; Postgres treats
     `"..."` strictly as an identifier and errors ("column ... does not exist").
     So `WHERE x = "value"` / `IN ("a","b")` is a portability bug — it must be
     single-quoted. This catches the class everywhere, including paths no
     functional test exercises (e.g. chat history queries).
  2. A functional auth smoke test (setup-status -> register -> login, plus an
     unauthenticated protected request) run against whatever LAMBDA_ERP_TEST_DB
     points at. On Postgres this exercises the real query paths and the
     connection-reuse behaviour after an error.

Run:  python -m tests.test_db_portability
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_db_portability   # CI runs both
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A double-quoted token used as a VALUE: after =, !=, <>, or inside IN (...).
_BAD_LITERAL = re.compile(r'(?:=|!=|<>|\bIN\s*\()\s*"[A-Za-z_][\w ]*"')
# Only flag lines that are clearly SQL, so Python tuples like `x in ("a","b")`
# don't false-positive. Includes clause/expression keywords (CASE/WHEN/THEN,
# AND/OR, aggregates) so a bad literal on a CONTINUATION line of a multi-line
# query is still caught — e.g. `WHEN role = "public_manager"` whose SELECT/FROM
# live on other lines. These are matched as whole uppercase tokens, which don't
# occur in idiomatic Python (which uses lowercase and/or), keeping false
# positives negligible.
_SQL_LINE = re.compile(
    r"\b(SELECT|UPDATE|DELETE|WHERE|FROM|JOIN|INSERT|CASE|WHEN|THEN|ELSE|END"
    r"|AND|OR|HAVING|COALESCE|SUM|COUNT|GROUP\s+BY|ORDER\s+BY|VALUES|SET)\b"
    r"|db\.sql\("
)


def check_no_double_quoted_literals():
    offenders = []
    files = (glob.glob(f"{ROOT}/lambda_erp/**/*.py", recursive=True)
             + glob.glob(f"{ROOT}/api/**/*.py", recursive=True))
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if _BAD_LITERAL.search(line) and _SQL_LINE.search(line):
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{n}: {line.strip()}")
    if offenders:
        raise AssertionError(
            "Double-quoted string literal(s) in SQL — breaks on Postgres "
            "(use single quotes for values):\n  " + "\n  ".join(offenders)
        )
    print("  [static] no double-quoted SQL literals — OK")


def _reset_db():
    """Return a db_path for setup(); reset Postgres to a clean schema."""
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_auth_flow():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (:memory:)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")

    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        # Unauthenticated hit on a protected endpoint exercises the
        # get_current_user -> public_manager fallback query. Must be 401, NOT a
        # 500 from a SQLite-only query, and must NOT poison the connection.
        r = client.get("/api/accounting/currencies")
        assert r.status_code == 401, f"unauth protected -> {r.status_code} (expected 401): {r.text[:200]}"

        # The connection must still be usable after that error (Postgres aborts a
        # transaction on a failed statement; a poisoned pooled conn would 500 here).
        r = client.get("/api/auth/setup-status")
        assert r.status_code == 200, f"setup-status -> {r.status_code}: {r.text[:200]}"
        assert r.json()["has_users"] is False, r.json()

        r = client.post("/api/auth/register",
                        json={"email": "admin@example.com", "full_name": "Admin",
                              "password": "test-password-123"})
        assert r.status_code == 200, f"register -> {r.status_code}: {r.text[:300]}"
        assert r.json()["role"] == "admin", r.json()

        r = client.get("/api/auth/setup-status")
        assert r.json()["has_users"] is True, r.json()

        # Authenticated request (register set the cookie) -> get_current_user via
        # the token path (get_value with a cross-doctype field list).
        r = client.get("/api/accounting/currencies")
        assert r.status_code == 200, f"authed currencies -> {r.status_code}: {r.text[:200]}"

        # Change own password: wrong current -> 403, correct -> 200, then the
        # new password logs in and the old one does not.
        r = client.post("/api/auth/change-password",
                        json={"current_password": "WRONG", "new_password": "test-password-456"})
        assert r.status_code == 403, f"change-pw wrong current -> {r.status_code}: {r.text[:200]}"
        r = client.post("/api/auth/change-password",
                        json={"current_password": "test-password-123", "new_password": "test-password-456"})
        assert r.status_code == 200, f"change-pw -> {r.status_code}: {r.text[:200]}"
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login",
                           json={"email": "admin@example.com", "password": "test-password-123"}
                           ).status_code == 401, "old password still works after change"
        r = client.post("/api/auth/login",
                        json={"email": "admin@example.com", "password": "test-password-456"})
        assert r.status_code == 200, f"login with new password -> {r.status_code}: {r.text[:200]}"

        # Public-signup toggle (admin cookie is active from register above).
        # With the toggle OFF (default), a no-invite registration is refused.
        r = client.post("/api/auth/register",
                        json={"email": "nope@example.com", "full_name": "Nope",
                              "password": "test-password-123"})
        assert r.status_code == 403, f"no-invite register (closed) -> {r.status_code}: {r.text[:200]}"
        s = client.get("/api/auth/setup-status").json()
        assert s["public_signup"] is False and s["registration_open"] is False, s

        # Enable open signup; a no-invite registrant then joins as a viewer.
        r = client.put("/api/auth/settings", json={"allow_public_signup": "1"})
        assert r.status_code == 200, f"enable public signup -> {r.status_code}: {r.text[:200]}"
        s = client.get("/api/auth/setup-status").json()
        assert s["public_signup"] and s["registration_open"] and not s["first_run"], s
        r = client.post("/api/auth/register",
                        json={"email": "viewer@example.com", "full_name": "Viewer",
                              "password": "test-password-123"})
        assert r.status_code == 200, f"public register -> {r.status_code}: {r.text[:300]}"
        assert r.json()["role"] == "viewer", r.json()

    print(f"  [auth flow] setup-status/register/login + public-signup OK on {backend}")

    # db.sql() used for writes must return [] (not raise). psycopg errors with
    # "the last operation didn't produce records" if fetchall() is called after
    # an INSERT/UPDATE/DELETE; SQLite returns []. The chat/WebSocket path relies
    # on this. (This was the cause of the WS disconnect/reconnect loop.)
    from lambda_erp.database import get_db
    db = get_db()
    db.sql('CREATE TABLE IF NOT EXISTS "_PortTest" (k TEXT PRIMARY KEY, v INTEGER)')
    assert db.sql('INSERT INTO "_PortTest" (k, v) VALUES (?, ?)', ["x", 1]) == []
    assert db.sql('UPDATE "_PortTest" SET v = ? WHERE k = ?', [2, "x"]) == []
    assert db.sql('SELECT v FROM "_PortTest" WHERE k = ?', ["x"])[0]["v"] == 2
    assert db.sql('DELETE FROM "_PortTest" WHERE k = ?', ["x"]) == []
    db.sql('DROP TABLE "_PortTest"')
    db.conn.commit()
    print(f"  [sql writes] INSERT/UPDATE/DELETE via db.sql() return [] on {backend}")


def check_master_search():
    """search_masters must be case-insensitive, search all text fields (incl.
    address/city), and fuzzy-match misspellings — on BOTH backends. Bare LIKE is
    case-insensitive on SQLite but case-sensitive on Postgres, so this only fails
    on Postgres if the case-folding regresses. Relies on the DB set up by
    check_auth_flow()."""
    from lambda_erp.database import get_db
    from api.chat import _handle_search_masters, _handle_get_master_fields

    db = get_db()
    db.insert("Customer", {
        "name": "CUST-PORT1",
        "customer_name": "Zorblax AG",
        "city": "Frimbleton",
        "disabled": 0,
    })
    db.conn.commit()

    def names(query, **kw):
        return {r["name"] for r in _handle_search_masters(
            {"master_type": "customer", "query": query, **kw})}

    # Case-insensitive substring match on the display name (the prod regression).
    assert "CUST-PORT1" in names("zorblax"), "lowercase query did not match 'Zorblax AG'"
    assert "CUST-PORT1" in names("ZORBLAX"), "uppercase query did not match 'Zorblax AG'"
    # Address fields are searched, not just name/customer_name.
    assert "CUST-PORT1" in names("frimbleton"), "city query did not match"
    # Fuzzy fallback catches misspellings that no substring would.
    assert "CUST-PORT1" in names("zorlax"), "fuzzy query did not match misspelled name"
    # A genuine non-match still returns nothing (fuzzy must not match everything).
    assert "CUST-PORT1" not in names("zzqxnomatch"), "unrelated query unexpectedly matched"

    # `fields` narrows to specific columns: matches when the value is there...
    assert "CUST-PORT1" in names("frimbleton", fields=["city"]), "targeted city search missed"
    # ...and does not match when searching a column that lacks the value.
    assert "CUST-PORT1" not in names("frimbleton", fields=["customer_name"]), \
        "targeted name search wrongly matched a city value"

    # Large free-text columns are skipped by default but reachable via `fields`.
    db.insert("Item", {
        "name": "ITEM-PORT1",
        "item_name": "Widget",
        "description": "ships with a qwobble bracket",
        "disabled": 0,
    })
    db.conn.commit()

    def item_names(query, **kw):
        return {r["name"] for r in _handle_search_masters(
            {"master_type": "item", "query": query, **kw})}

    assert "ITEM-PORT1" not in item_names("qwobble"), "description should not be searched by default"
    assert "ITEM-PORT1" in item_names("qwobble", fields=["description"]), \
        "description should be searchable when named in fields"

    # Unknown field names yield a clear error rather than matching everything.
    err = _handle_search_masters({"master_type": "customer", "query": "x", "fields": ["no_such_col"]})
    assert isinstance(err, dict) and "error" in err, f"bad fields should error, got {err!r}"

    # get_master_fields exposes real columns so the model can target them.
    meta = _handle_get_master_fields({"master_type": "item"})
    assert "description" in meta["fields"], meta
    assert "description" not in meta["default_search_fields"], meta
    assert "description" in meta["bulk_text_fields"], meta
    assert "item_name" in meta["default_search_fields"], meta

    backend = "postgres" if os.environ.get("LAMBDA_ERP_TEST_DB", "").startswith("postgres") else "sqlite"
    print(f"  [master search] case-insensitive + address + fuzzy + fields OK on {backend}")


def main():
    print("DB portability checks")
    check_no_double_quoted_literals()
    check_auth_flow()
    check_master_search()
    print("All portability checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
