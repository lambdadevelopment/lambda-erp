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


def main():
    print("DB portability checks")
    check_no_double_quoted_literals()
    check_auth_flow()
    print("All portability checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
