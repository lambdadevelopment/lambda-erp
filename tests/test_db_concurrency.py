"""Concurrency tests / benchmarks for the SQLite database layer.

Run with:
    python -m tests.test_db_concurrency

Three sections:
  1. Correctness — many threads doing inserts/reads on a file DB must not
     produce errors or lost writes.
  2. Throughput — chat-shaped mixed read/write at increasing thread counts.
  3. Read latency — quick reads should not stall behind a slow scanner.

The first section asserts; the others print numbers for comparison.
"""

import os
import sys
import tempfile
import threading
import time

from lambda_erp.database import Database


def _run_threads(target, args_for_thread):
    """Start one thread per args tuple, join all, return wall-clock seconds."""
    threads = [threading.Thread(target=target, args=a) for a in args_for_thread]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.time() - t0


def _seed(db, n_customers=500, n_sessions=64):
    for i in range(n_customers):
        db.insert("Customer", {
            "name": f"C{i}",
            "customer_name": f"Cust {i}",
            "customer_group": "g",
        })
    for i in range(n_sessions):
        db.insert("Chat Session", {"id": f"S{i}", "title": "demo", "user_id": "u"})


def test_threading_correctness():
    print("\n--- 1. Threading correctness (file DB, 8 threads x 20 inserts each) ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "correct.db"))
        errors = []

        def worker(uid):
            try:
                for j in range(20):
                    name = f"u{uid}-{j}"
                    db.insert("User", {
                        "name": name,
                        "email": f"{name}@x",
                        "full_name": name,
                        "hashed_password": "h",
                        "role": "viewer",
                    })
                    got = db.get_value("User", name, "email")
                    assert got == f"{name}@x", got
            except Exception as e:
                errors.append(e)

        elapsed = _run_threads(worker, [(i,) for i in range(8)])
        n_rows = db.sql('SELECT COUNT(*) AS c FROM "User"')[0]["c"]

        assert not errors, f"errors: {errors[:3]}"
        assert n_rows == 8 * 20, f"expected 160, got {n_rows}"
        print(f"  OK: {n_rows} rows in {elapsed:.2f}s, no errors")


def test_chat_shaped_throughput():
    print("\n--- 2. Chat-shaped throughput (50 ops/user: 3 reads + 1 write per op) ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "thr.db"))
        _seed(db)

        def chat_turn(uid, n_ops=50):
            for j in range(n_ops):
                db.sql(
                    'SELECT * FROM "Customer" WHERE customer_name LIKE ? LIMIT 50',
                    [f"%{uid}%"],
                )
                db.get_value("Customer", f"C{j % 500}", "customer_name")
                db.get_all("Customer", filters={"customer_group": "g"}, limit=20)
                db.insert("Chat Message", {
                    "session_id": f"S{uid}",
                    "role": "user",
                    "content": f"msg {j}",
                })

        print(f"  {'users':>6}  {'elapsed':>9}")
        for n_threads in [1, 2, 4, 8, 16]:
            elapsed = _run_threads(chat_turn, [(i,) for i in range(n_threads)])
            print(f"  {n_threads:>6}  {elapsed:>7.2f}s")


def test_quick_reads_under_slow_scan():
    print("\n--- 3. Quick reads must not stall under a slow scanner ---")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "lat.db"))
        for i in range(2000):
            db.insert("Customer", {
                "name": f"C{i}",
                "customer_name": f"Cust {i}",
                "customer_group": "g",
            })

        def slow_scanner():
            for _ in range(5):
                db.sql(
                    'SELECT * FROM "Customer" WHERE customer_name LIKE ?',
                    ["%5%"],
                )

        latencies_ms = []

        def quick_reader():
            for _ in range(20):
                t0 = time.time()
                db.get_value("Customer", "C100", "customer_name")
                latencies_ms.append((time.time() - t0) * 1000)

        threads = [threading.Thread(target=slow_scanner)]
        threads += [threading.Thread(target=quick_reader) for _ in range(8)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        latencies_ms.sort()
        p50 = latencies_ms[len(latencies_ms) // 2]
        p99 = latencies_ms[int(len(latencies_ms) * 0.99)]
        print(f"  1 slow scanner + 8 quick readers: {elapsed:.2f}s total")
        print(f"  quick-read p50: {p50:.1f}ms, p99: {p99:.1f}ms (n={len(latencies_ms)})")


def main():
    print("Database concurrency tests / benchmarks")
    test_threading_correctness()
    test_chat_shaped_throughput()
    test_quick_reads_under_slow_scan()
    print("\nDone.")


if __name__ == "__main__":
    main()
