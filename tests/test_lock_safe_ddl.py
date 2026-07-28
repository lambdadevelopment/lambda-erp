#!/usr/bin/env python3
"""Tests for lock-safe schema DDL — db.ensure_column / db.drop_column.

Plugin migrations ALTER tables in the app lifespan, before the app serves.
During a rolling deploy the OLD revision is still querying those tables, so an
unbounded ALTER blocks forever and crash-loops the new revision (this happened
in prod). ensure_column/drop_column bound the lock wait (Postgres lock_timeout)
and retry, so the ALTER either catches a gap or FAILS FAST — never hangs.

Checks: add/drop are idempotent on both backends; and on Postgres, a held lock
makes the ALTER raise quickly (bounded) instead of hanging, then succeeds once
the lock is released.

Run:  python -m tests.test_lock_safe_ddl
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_lock_safe_ddl   # CI runs both
"""
import os
import sys
import time


def check_lock_safe_ddl():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    is_pg = bool(url)
    if is_pg:
        import psycopg
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        db_path = url
    else:
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="lambda_lockddl_")
        os.close(fd)
    backend = "postgres" if is_pg else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path

    from lambda_erp.database import setup, get_db
    setup(db_path)
    db = get_db()
    db.conn.execute('CREATE TABLE "T" (name TEXT PRIMARY KEY, a TEXT)')
    db.conn.execute('INSERT INTO "T" (name, a) VALUES (\'r1\', \'x\')')
    db.conn.commit()

    # --- Idempotent add. ----------------------------------------------------
    db.ensure_column("T", "b", "TEXT")
    assert "b" in db._get_table_columns("T"), "add failed"
    db.ensure_column("T", "b", "TEXT")  # already present -> no-op, no error
    assert "b" in db._get_table_columns("T")

    # --- Idempotent drop. ---------------------------------------------------
    db.drop_column("T", "b")
    assert "b" not in db._get_table_columns("T"), "drop failed"
    db.drop_column("T", "b")            # already gone -> no-op
    db.drop_column("T", "never_existed")  # no-op

    # --- Postgres: a held lock must make the ALTER fail FAST, not hang. ------
    if is_pg:
        import psycopg
        blocker = psycopg.connect(url)          # autocommit off -> holds a txn
        blocker.execute('SELECT * FROM "T"')    # ACCESS SHARE on T, held open
        t0 = time.monotonic()
        try:
            # Small bounds so the test is quick; the real defaults are larger.
            db._alter_table_lock_safe(
                ['ALTER TABLE "T" ADD COLUMN c TEXT'], ["T"],
                lock_timeout_ms=200, retries=2, backoff=0.05,
            )
            assert False, "ALTER should have failed under a held lock"
        except RuntimeError:
            pass
        elapsed = time.monotonic() - t0
        assert elapsed < 5, f"ALTER hung ({elapsed:.1f}s) instead of failing fast"
        assert "c" not in db._get_table_columns("T"), "column added despite contention"

        # Release the lock -> the same add now succeeds.
        blocker.rollback(); blocker.close()
        db.ensure_column("T", "c", "TEXT")
        assert "c" in db._get_table_columns("T"), "add failed after lock released"
        # lock_timeout was reset, so a normal query still works.
        assert db.sql('SELECT COUNT(*) AS n FROM "T"')[0]["n"] == 1

    print(f"  [lock-safe ddl] add/drop idempotency"
          f"{' + fail-fast-under-lock' if is_pg else ''} OK on {backend}")

    if not is_pg:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("Lock-safe DDL checks")
    check_lock_safe_ddl()
    print("All lock-safe DDL checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
