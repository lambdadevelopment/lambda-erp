#!/usr/bin/env python3
"""Tests for GET /documents/{slug}/{name}/adjacent — prev/next record navigation.

Steps through records in the list's order (creation DESC, name tie-break) and
respects the same filters, so a detail page's DocPager follows the list the user
came from. Uses a fictional Widget doctype (register_table + register_doctype).

Run:  python -m tests.test_adjacent
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_adjacent   # CI runs both
"""
import os
import sys

WIDGET_TABLE = """CREATE TABLE IF NOT EXISTS "Widget" (
    name TEXT PRIMARY KEY, label TEXT, kind TEXT, made_on TEXT,
    discarded INTEGER DEFAULT 0,
    docstatus INTEGER DEFAULT 0, creation TEXT, modified TEXT
)"""


def _register():
    from lambda_erp.model import Document
    from api.services import register_doctype, register_table

    class Widget(Document):
        DOCTYPE = "Widget"; CHILD_TABLES = {}; PREFIX = "WID"
        def validate(self):
            pass

    register_doctype("Widget", Widget)
    register_table(WIDGET_TABLE)


def check_adjacent():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if url:
        import psycopg
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE"); conn.execute("CREATE SCHEMA public")
        db_path = url
    else:
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="lambda_adjacent_")
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
        # 5 widgets, controlled distinct creation (w0 oldest .. w4 newest),
        # alternating kind. creation DESC list: w4,w3,w2,w1,w0 (top->bottom).
        names = []
        for i in range(5):
            n = client.post("/api/documents/widget",
                            json={"label": f"W{i}", "kind": "A" if i % 2 == 0 else "B"}).json()["name"]
            db.set_value("Widget", n, {"creation": f"2026-07-2{i}T00:00:00",
                                       "made_on": f"2026-07-2{i}"})
            names.append(n)

        def adj(name, **params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get(f"/api/documents/widget/{name}/adjacent" + (f"?{qs}" if qs else "")).json()

        # middle w2: prev is w3 (up), next is w1 (down)
        assert adj(names[2]) == {"prev": names[3], "next": names[1]}, adj(names[2])
        # ends
        assert adj(names[4]) == {"prev": None, "next": names[3]}, "top should have no prev"
        assert adj(names[0]) == {"prev": names[1], "next": None}, "bottom should have no next"
        # filter kind=A (w0,w2,w4) -> w2 steps to w4 (up) / w0 (down), skipping B
        assert adj(names[2], kind="A") == {"prev": names[4], "next": names[0]}, adj(names[2], kind="A")
        # unknown filter field -> 400
        assert client.get(f"/api/documents/widget/{names[2]}/adjacent?bogus=1").status_code == 400

        # --- date_field override. Widget isn't in the server's built-in
        # DATE_FIELDS map, so date filtering only kicks in when the caller passes
        # its declared dateField (the frontend sends config.dateField).
        def wlist(**params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get("/api/documents/widget" + (f"?{qs}" if qs else "")).json()

        # no date_field -> range silently ignored (unmapped doctype, all 5)
        assert wlist(from_date="2026-07-22", to_date="2026-07-23")["total"] == 5
        # with date_field -> filters + counts on made_on (w2,w3,w4 >= the 22nd)
        r = wlist(date_field="made_on", from_date="2026-07-22")
        assert r["total"] == 3, r
        assert {row["name"] for row in r["rows"]} == {names[2], names[3], names[4]}, r
        # both bounds
        assert wlist(date_field="made_on", from_date="2026-07-22", to_date="2026-07-23")["total"] == 2
        # a date_field that isn't a real column degrades to no date filter (not a
        # 400 — a config with a synthetic dateField must not break the whole list)
        assert wlist(date_field="not_a_column", from_date="2026-07-22")["total"] == 5
        # adjacent honors the same window: inside [22..24] (w4,w3,w2), w3 steps w4/w2
        assert adj(names[3], date_field="made_on", from_date="2026-07-22", to_date="2026-07-24") \
            == {"prev": names[4], "next": names[2]}, "date_field window not respected by adjacent"

        # --- Masters: /masters/{type}/{name}/adjacent, name ASC (list order).
        cust = []
        for label in ("Aster Trading AG", "Borea Logistik GmbH", "Cirrus Metall AG"):
            r = client.post("/api/masters/customer", json={"customer_name": label})
            assert r.status_code == 200, r.text
            cust.append(r.json()["name"])
        cust.sort()

        def madj(name, **params):
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return client.get(f"/api/masters/customer/{name}/adjacent" + (f"?{qs}" if qs else "")).json()

        assert madj(cust[1]) == {"prev": cust[0], "next": cust[2]}, madj(cust[1])
        assert madj(cust[0])["prev"] is None
        assert madj(cust[2])["next"] is None
        # Custom sorting and search use the same list context on detail pages.
        assert madj(cust[1], order_by="customer_name", order="desc") == {
            "prev": cust[2], "next": cust[0]
        }
        assert madj(cust[1], search="Borea", search_fields="customer_name") == {
            "prev": None, "next": None
        }
        # NULL sort values stay last and keyset navigation crosses the
        # non-NULL/NULL boundary in the same order as the list.
        client.put(f"/api/masters/customer/{cust[0]}", json={"territory": "Zurich"})
        client.put(f"/api/masters/customer/{cust[1]}", json={"territory": "Aargau"})
        client.put(f"/api/masters/customer/{cust[2]}", json={"territory": None})
        assert madj(cust[0], order_by="territory", order="asc") == {
            "prev": cust[1], "next": cust[2]
        }
        assert madj(cust[2], order_by="territory", order="asc") == {
            "prev": cust[0], "next": None
        }
        # disabled records are included by default (list parity), excludable
        client.put(f"/api/masters/customer/{cust[2]}", json={"disabled": 1})
        assert madj(cust[1])["next"] == cust[2]
        assert madj(cust[1], include_disabled="false")["next"] is None
        # the list itself pages in the same deterministic order
        rows = client.get("/api/masters/customer?include_disabled=1").json()["rows"]
        listed = [r["name"] for r in rows if r["name"] in set(cust)]
        assert listed == cust, f"list order != name ASC: {listed}"
        # unknown master type -> 404
        assert client.get(f"/api/masters/nope/{cust[0]}/adjacent").status_code == 404

    print(f"  [adjacent] documents + masters prev/next OK on {backend}")

    if not url:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("Adjacent (prev/next) checks")
    check_adjacent()
    print("All adjacent checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
