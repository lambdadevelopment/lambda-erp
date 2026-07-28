#!/usr/bin/env python3
"""Tests that Postgres connections never idle inside an implicit transaction.

With autocommit off, a plain SELECT opens a transaction; before 0.6.5 nothing
ended it, so thread-local connections sat 'idle in transaction' after every
read-only request, pinning ACCESS SHARE locks that starved lock-safe plugin
migrations (ALTER TABLE) on every boot. _PgConn._maybe_release now rolls back
right after a read when no write is pending and no explicit transaction is
active. Postgres-only semantics — a no-op on SQLite.

Run:  LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_txn_release
"""
import os
import sys


def main():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        print("SKIP: transaction-release semantics are Postgres-only "
              "(set LAMBDA_ERP_TEST_DB=postgresql://...)")
        return

    import psycopg
    from psycopg.pq import TransactionStatus

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")

    from lambda_erp.database import Database

    db = Database(url)
    status = lambda: db.conn._raw.info.transaction_status

    # Schema setup commits; the connection must start outside a transaction.
    assert status() == TransactionStatus.IDLE, status()

    # 1. A read releases its implicit transaction immediately — and the rows
    #    are still fetchable (psycopg buffers the result set client-side).
    rows = db.sql('SELECT key FROM "Settings"')
    assert status() == TransactionStatus.IDLE, f"read left txn open: {status()}"
    assert isinstance(rows, list)

    # Read helpers funnel through the same path (Customer has the standard
    # name/creation columns; Settings is a bare key/value table).
    db.exists("Customer", "CUST-does-not-exist")
    db.get_all("Customer", limit=1)
    assert status() == TransactionStatus.IDLE, f"helper left txn open: {status()}"

    # 2. A write keeps its transaction open (atomicity with the later commit),
    #    and interleaved reads must NOT roll it back.
    db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', ["txn-test", "1"])
    assert status() == TransactionStatus.INTRANS, f"write did not open txn: {status()}"
    got = db.sql('SELECT value FROM "Settings" WHERE key = ?', ["txn-test"])
    assert status() == TransactionStatus.INTRANS, "read rolled back a pending write"
    assert got and got[0]["value"] == "1"
    db.conn.commit()
    assert status() == TransactionStatus.IDLE
    got = db.sql('SELECT value FROM "Settings" WHERE key = ?', ["txn-test"])
    assert got and got[0]["value"] == "1", "committed write lost"

    # 3. Inside an explicit transaction (submit/cancel flow) reads keep the
    #    transaction — and its snapshot — open.
    db._in_transaction = True
    try:
        db.sql('SELECT 1 FROM "Settings"')
        assert status() == TransactionStatus.INTRANS, "explicit txn was released"
        db.conn.rollback()
    finally:
        db._in_transaction = False
    assert status() == TransactionStatus.IDLE

    # 4. The payoff — the production failure mode: a second thread (its own
    #    thread-local connection) does a read and then goes quiet, exactly like
    #    a pooled request thread between requests. Before the fix its idle
    #    transaction held ACCESS SHARE on "Settings" and this ALTER timed out
    #    after 8 retries; now the read released the lock and the ALTER wins.
    import threading

    read_done, release = threading.Event(), threading.Event()

    def quiet_reader():
        db.sql('SELECT * FROM "Settings"')
        read_done.set()
        release.wait(timeout=60)  # keep the thread (and its connection) alive

    t = threading.Thread(target=quiet_reader, daemon=True)
    t.start()
    assert read_done.wait(timeout=10), "reader thread never ran"
    try:
        db.ensure_column("Settings", "txn_release_probe", "TEXT")
    finally:
        release.set()
        t.join(timeout=10)
    assert "txn_release_probe" in db._get_table_columns("Settings")

    print("OK: reads release their transaction; writes stay atomic")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
