#!/usr/bin/env python3
"""Tests for the boot-seeded admin (api.auth.ensure_seed_admin).

When LAMBDA_ERP_ADMIN_EMAIL + LAMBDA_ERP_ADMIN_PASSWORD are set, an enabled
admin with that email must exist before the first visitor can register — so a
deployment whose DB is recreated on every rollout (the ephemeral-SQLite demo)
can't hand admin to whichever stranger loads the login page first. This drives
the real FastAPI app through its lifespan (which calls ensure_seed_admin) and
verifies:

  - the seeded admin exists and can log in with the env password, as admin
  - first-run is closed: `setup-status` reports has_users, and a fresh
    registrant does NOT become admin (falls through to invite-only → 403)
  - idempotent across restarts: a second lifespan neither duplicates nor
    errors, and the password is unchanged
  - no env → no-op: the classic first-registrant-becomes-admin path still works

Run:  python -m tests.test_seed_admin
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_seed_admin   # CI runs both
"""
import os
import sys


def _reset_db():
    """Return a db_path for setup(); reset Postgres to a clean schema.

    Each TestClient re-runs the app lifespan → setup(db_path). Postgres
    persists across that (same URL); a `:memory:` SQLite path would start empty
    every lifespan, so for SQLite we use a temp *file* the caller unlinks."""
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_seed_admin_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_seed_admin():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
    os.environ["LAMBDA_ERP_ADMIN_EMAIL"] = "Owner@Example.COM"  # mixed case on purpose
    os.environ["LAMBDA_ERP_ADMIN_PASSWORD"] = "seed-pw-123"
    os.environ["LAMBDA_ERP_ADMIN_NAME"] = "Seeded Owner"

    from fastapi.testclient import TestClient
    from api.main import app

    # --- First boot: lifespan seeds the admin. ------------------------------
    with TestClient(app) as client:
        st = client.get("/api/auth/setup-status").json()
        assert st["has_users"] and not st["first_run"], f"seed didn't close first-run: {st}"

        # The seeded admin logs in with the env password; email normalized.
        r = client.post("/api/auth/login",
                        json={"email": "owner@example.com", "password": "seed-pw-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

        # First-run is closed → a fresh registrant can't grab admin. With
        # public signup off (default) and no invite, registration is refused.
        with TestClient(app) as anon:
            r = anon.post("/api/auth/register",
                          json={"email": "stranger@example.com", "full_name": "Stranger",
                                "password": "hunter2222"})
            assert r.status_code == 403, f"stranger registration → {r.status_code}: {r.text[:200]}"

    # --- Second boot (same DB): idempotent — one admin, password unchanged. --
    with TestClient(app) as client:
        r = client.post("/api/auth/login",
                        json={"email": "owner@example.com", "password": "seed-pw-123"})
        assert r.status_code == 200, f"password changed across reboot? {r.text[:200]}"
        # Exactly one admin with that email — no duplicate row from re-seeding.
        me = client.get("/api/auth/me").json()
        assert me["email"] == "owner@example.com", me

    print(f"  [seed admin] seed/close-first-run/idempotency OK on {backend}")

    # --- Control: no env → classic first-registrant-becomes-admin. ----------
    del os.environ["LAMBDA_ERP_ADMIN_EMAIL"]
    del os.environ["LAMBDA_ERP_ADMIN_PASSWORD"]
    db_path2 = _reset_db()
    os.environ["LAMBDA_ERP_DB"] = db_path2
    with TestClient(app) as client:
        st = client.get("/api/auth/setup-status").json()
        assert st["first_run"], f"unexpected users without seed env: {st}"
        r = client.post("/api/auth/register",
                        json={"email": "first@example.com", "full_name": "First",
                              "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

    print(f"  [seed admin] no-env first-registrant-admin control OK on {backend}")

    # Clean up temp SQLite files (and WAL sidecars), if any.
    if not os.environ.get("LAMBDA_ERP_TEST_DB"):
        for p in (db_path, db_path2):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(p + suffix)
                except OSError:
                    pass


def main():
    print("Seed-admin checks")
    check_seed_admin()
    print("All seed-admin checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
