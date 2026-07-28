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
    name TEXT PRIMARY KEY, label TEXT, kind TEXT, discarded INTEGER DEFAULT 0,
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
            db.set_value("Widget", n, {"creation": f"2026-07-2{i}T00:00:00"})
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

    print(f"  [adjacent] prev/next + filter-following + ends OK on {backend}")

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
