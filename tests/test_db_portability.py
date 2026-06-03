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
# don't false-positive.
_SQL_LINE = re.compile(r"\b(SELECT|UPDATE|DELETE|WHERE|FROM|JOIN|INSERT)\b|db\.sql\(")


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

    print(f"  [auth flow] setup-status/register/login OK on {backend}")


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
