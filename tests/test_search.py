#!/usr/bin/env python3
"""Tests for free-text list search — GET /documents/{slug}?search=&search_fields=.

Covers same-table column search (case-insensitive substring), the
register_search_expansion seam (match a doctype via a related table the core
doesn't know — e.g. a Widget by one of its Gadgets), count parity, and that
prev/next (adjacent) tracks the searched list.

Run:  python -m tests.test_search
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_search   # CI runs both
"""
import os
import sys

WIDGET_TABLE = """CREATE TABLE IF NOT EXISTS "Widget" (
    name TEXT PRIMARY KEY, label TEXT, kind TEXT, discarded INTEGER DEFAULT 0,
    docstatus INTEGER DEFAULT 0, creation TEXT, modified TEXT
)"""

# A related table keyed by widget name — stands in for the internal CRM's
# Contact(lead_id) so the expansion seam is exercised the way it's used in prod.
GADGET_TABLE = """CREATE TABLE IF NOT EXISTS "Gadget" (
    name TEXT PRIMARY KEY, widget_id TEXT, gadget_name TEXT,
    docstatus INTEGER DEFAULT 0, creation TEXT, modified TEXT
)"""


def _register():
    from lambda_erp.model import Document
    from api.services import register_doctype, register_table, register_search_expansion

    class Widget(Document):
        DOCTYPE = "Widget"; CHILD_TABLES = {}; PREFIX = "WID"
        def validate(self):
            pass

    register_doctype("Widget", Widget)
    register_table(WIDGET_TABLE)
    register_table(GADGET_TABLE)

    def widget_gadget_search(q, db):
        like = f"%{q}%"
        rows = db.sql('SELECT DISTINCT widget_id FROM "Gadget" WHERE LOWER(gadget_name) LIKE LOWER(?)', [like])
        return [r["widget_id"] for r in rows if r["widget_id"]]

    register_search_expansion("widget", widget_gadget_search)


def check_search():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if url:
        import psycopg
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE"); conn.execute("CREATE SCHEMA public")
        db_path = url
    else:
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="lambda_search_")
        os.close(fd)
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")

    _register()
    from fastapi.testclient import TestClient
    from lambda_erp.database import get_db
    from api.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/register",
                        json={"email": "a@b.c", "full_name": "A", "password": "pw-123456"})
        assert r.status_code == 200, r.text
        db = get_db()

        labels = ["Acme Anvil", "Acme Rocket", "Globex Widget", "Initech Stapler"]
        names = []
        for i, lbl in enumerate(labels):
            n = client.post("/api/documents/widget", json={"label": lbl, "kind": "A"}).json()["name"]
            db.set_value("Widget", n, {"creation": f"2026-07-2{i}T00:00:00"})
            names.append(n)
        # A gadget under Initech's widget, named so it only matches via expansion.
        db.sql('INSERT INTO "Gadget" (name, widget_id, gadget_name, creation, modified) '
               "VALUES ('G1', ?, 'Zephyr Module', '2026-07-01', '2026-07-01')", [names[3]])
        db.commit()  # raw insert -> commit so the request connection sees the gadget

        def wlist(**params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get("/api/documents/widget" + (f"?{qs}" if qs else "")).json()

        # same-table substring, case-insensitive, across the declared fields
        r = wlist(search="acme", search_fields="label")
        assert r["total"] == 2, r
        assert {x["name"] for x in r["rows"]} == {names[0], names[1]}, r
        # no match -> empty (and total agrees)
        assert wlist(search="nonesuch", search_fields="label")["total"] == 0
        # search is ANDed with other filters, not OR-ed over the whole table
        assert wlist(search="rocket", search_fields="label", kind="A")["total"] == 1

        # --- expansion seam: 'zephyr' matches no Widget column, only a Gadget,
        # and still returns the parent Widget.
        r = wlist(search="zephyr", search_fields="label")
        assert r["total"] == 1 and r["rows"][0]["name"] == names[3], r
        # union of same-table + expansion, de-duplicated: 'e' hits several labels;
        # ensure the Initech row (only reachable via gadget for 'zephyr') isn't
        # double-counted when it also matches directly.
        both = wlist(search="Initech", search_fields="label")  # direct label hit
        assert both["total"] == 1 and both["rows"][0]["name"] == names[3]

        # unknown search field -> 400 (same contract as filters/order_by)
        assert client.get("/api/documents/widget?search=x&search_fields=bogus").status_code == 400

        # --- adjacent tracks the searched list: within {Acme Anvil, Acme Rocket}
        # (creation DESC -> Rocket, Anvil), Rocket's next is Anvil, no prev.
        def adj(name, **params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get(f"/api/documents/widget/{name}/adjacent?{qs}").json()

        a = adj(names[1], search="acme", search_fields="label")  # Acme Rocket
        assert a == {"prev": None, "next": names[0]}, a
        a0 = adj(names[0], search="acme", search_fields="label")  # Acme Anvil
        assert a0 == {"prev": names[1], "next": None}, a0

    print(f"  [search] same-table + expansion seam + count + adjacent OK on {backend}")

    if not url:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("List search checks")
    check_search()
    print("All search checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
