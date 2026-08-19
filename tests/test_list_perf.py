#!/usr/bin/env python3
"""List-performance framework fixes:
  A) every table with a `creation` column gets a btree index on it (lists default
     to ORDER BY creation DESC; without this a large table full-scans+sorts);
  C) count_documents caches the exact count per (doctype, filters) briefly;
  B) list_documents projects to the requested columns instead of every column.

Run:  python -m tests.test_list_perf
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_list_perf
"""
import os


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_list_perf():
    from lambda_erp.database import setup
    import api.services as services

    db = setup(_reset_db())

    # --- A: every table with a `creation` column has an index on it. -----------
    def _index_columns(table):
        if db.dialect == "postgres":
            rows = db.sql("SELECT indexdef FROM pg_indexes WHERE tablename = ?", [table])
            return " ".join(r["indexdef"] for r in rows)
        cols = []
        for r in db.sql(f'PRAGMA index_list("{table}")'):
            for i in db.sql(f'PRAGMA index_info("{r["name"]}")'):
                cols.append(str(i["name"]))
        return " ".join(cols)

    # Document tables list by `creation DESC`, so they get the index. (Masters
    # like Customer have no `creation` column — they sort by the `name` PK — so
    # they are correctly skipped.)
    for table in ("Quotation", "Sales Invoice", "Sales Order", "Payment Entry"):
        assert "creation" in _index_columns(table), f"no creation index on {table}"
    assert "creation" not in _index_columns("Customer"), "master should not be indexed on creation"
    print("  A: creation index present on document tables, skipped on masters")

    db._ensure_list_indexes()  # idempotent — a second reconcile must not error
    print("  A: _ensure_list_indexes is idempotent")

    # --- C: exact count is cached, but a write invalidates it immediately. -----
    # Raw inserts into a core doctype (Quotation: name PK, rest nullable) — this
    # tests the list/count plumbing, not document validation.
    db.insert("Quotation", {"name": "QTN-1", "customer_name": "Alpha AG", "creation": "2026-08-19 10:00:00"})
    db.insert("Quotation", {"name": "QTN-2", "customer_name": "Beta GmbH", "creation": "2026-08-19 11:00:00"})
    db.conn.commit()
    assert services.count_documents("quotation") == 2
    assert services.count_documents("quotation") == 2  # repeat read: served from cache
    # A write bumps the DB write-generation, so the next count is fresh (not stale).
    db.insert("Quotation", {"name": "QTN-3", "customer_name": "Gamma SA", "creation": "2026-08-19 12:00:00"})
    db.conn.commit()
    assert services.count_documents("quotation") == 3, "insert must invalidate the cached count"
    db.delete("Quotation", "QTN-3")
    db.conn.commit()
    assert services.count_documents("quotation") == 2, "delete must invalidate the cached count"
    print("  C: count is cached but invalidates immediately on any write")

    # --- B: fields projection returns only name + the requested columns. -------
    rows = services.list_documents("quotation", fields=["customer_name"])
    assert rows, "expected quotation rows"
    assert set(rows[0].keys()) == {"name", "customer_name"}, set(rows[0].keys())
    print("  B: projection returns only name + requested columns")

    print("PASS")


if __name__ == "__main__":
    check_list_perf()
