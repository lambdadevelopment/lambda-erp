#!/usr/bin/env python3
"""Tests for the two plugin seams behind the CRM upgrade (ADR-0001):

  Seam #1 — plugin schema/migration registration
    api.services.register_table / register_migration / apply_plugin_schema,
    plus db.ensure_column. A plugin declares its own table and a one-shot
    migration; the core creates the table and runs the migration exactly once,
    recorded in _PluginMigrations, idempotent across reboots.

  Seam #2 — arbitrary-field filter + ordering on GET /documents/{slug}
    Any query param naming a real column becomes an equality filter (this is
    what lets the CRM fetch `activity?lead_id=X`); an unknown column is a 400,
    never SQL. order_by/order sort by a validated column.

Simulates a plugin by registering a fictional `Gadget` doctype before the app
starts (apply_plugin_schema runs in the lifespan). Drives the real FastAPI app
as an admin (cookie auth).

Run:  python -m tests.test_plugin_schema
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_plugin_schema   # CI runs both
"""
import os
import sys


def _reset_db():
    """db_path for setup(); reset Postgres to a clean schema. SQLite uses a temp
    *file* so state persists across the two lifespans this test opens (a
    :memory: path would start empty each time and defeat the idempotency check)."""
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_plugin_schema_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


# --- Fictional plugin: a Gadget doctype registered through the public seams. ---
GADGET_TABLE = """CREATE TABLE IF NOT EXISTS "Gadget" (
    name TEXT PRIMARY KEY,
    gadget_name TEXT,
    owner_ref TEXT,
    docstatus INTEGER DEFAULT 0,
    creation TEXT,
    modified TEXT
)"""


def _gadget_add_color(db):
    # The migration adds a column the base DDL never had, plus a backfill — so
    # its effect is observable (create a Gadget with `color`, read it back).
    db.ensure_column("Gadget", "color", "TEXT")
    db.sql('UPDATE "Gadget" SET color = ? WHERE color IS NULL', ["unset"])


def _register_gadget_plugin():
    from lambda_erp.model import Document
    from api.services import register_doctype, register_table, register_migration

    class Gadget(Document):
        DOCTYPE = "Gadget"
        CHILD_TABLES = {}
        PREFIX = "GDG"

        def validate(self):
            pass

    register_doctype("Gadget", Gadget)
    register_table(GADGET_TABLE)
    register_migration("test:0001_gadget_color", _gadget_add_color)


def check_plugin_schema():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    _register_gadget_plugin()  # module-level registries; picked up by apply_plugin_schema

    from fastapi.testclient import TestClient
    from lambda_erp.database import get_db
    from api.main import app

    # --- First boot: table created, migration applied once. -----------------
    with TestClient(app) as client:
        db = get_db()
        cols = db._get_table_columns("Gadget")
        assert "color" in cols, f"migration didn't add column; cols={cols}"
        applied = db.sql('SELECT migration_id FROM "_PluginMigrations"')
        assert any(r["migration_id"] == "test:0001_gadget_color" for r in applied), applied

        # Become admin (first registrant) → cookie set on this client.
        r = client.post("/api/auth/register",
                        json={"email": "admin@example.com", "full_name": "Admin",
                              "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

        # Seed a few gadgets across two owners; the migration column is writable.
        for gn, owner, color in [("Alpha", "u1", "red"), ("Bravo", "u1", "blue"),
                                 ("Ceta", "u2", "green")]:
            r = client.post("/api/documents/gadget",
                            json={"gadget_name": gn, "owner_ref": owner, "color": color})
            assert r.status_code == 200, f"create → {r.status_code}: {r.text[:200]}"

        # Seam #2: ad-hoc column filter returns only the matching subset.
        rows = client.get("/api/documents/gadget?owner_ref=u1").json()["rows"]
        assert len(rows) == 2 and {x["gadget_name"] for x in rows} == {"Alpha", "Bravo"}, rows
        # The migration column round-trips.
        assert all(x.get("color") in ("red", "blue") for x in rows), rows

        # Unknown filter field → 400, not a silent empty list or SQL.
        assert client.get("/api/documents/gadget?nope=1").status_code == 400
        # Reserved params still behave (limit is not treated as a column filter).
        assert client.get("/api/documents/gadget?limit=1").status_code == 200

        # Ordering by a validated column, both directions.
        asc = [x["gadget_name"] for x in
               client.get("/api/documents/gadget?order_by=gadget_name&order=asc").json()["rows"]]
        assert asc == sorted(asc) and asc[0] == "Alpha", asc
        desc = [x["gadget_name"] for x in
                client.get("/api/documents/gadget?order_by=gadget_name&order=desc").json()["rows"]]
        assert desc == sorted(desc, reverse=True), desc
        # Bad order_by / order → 400.
        assert client.get("/api/documents/gadget?order_by=bogus").status_code == 400
        assert client.get("/api/documents/gadget?order_by=gadget_name&order=sideways").status_code == 400

    # --- Second boot (same DB): migration is not re-run, nothing breaks. -----
    with TestClient(app) as client:
        # Fresh client → log in as the admin persisted from boot 1.
        r = client.post("/api/auth/login",
                        json={"email": "admin@example.com", "password": "test-password-123"})
        assert r.status_code == 200, r.text[:200]
        db = get_db()
        applied = db.sql(
            'SELECT COUNT(*) AS c FROM "_PluginMigrations" WHERE migration_id = ?',
            ["test:0001_gadget_color"],
        )
        assert applied[0]["c"] == 1, f"migration recorded {applied[0]['c']} times, want 1"
        # Data from boot 1 survived; the app still serves.
        rows = client.get("/api/documents/gadget?owner_ref=u2").json()["rows"]
        assert len(rows) == 1 and rows[0]["gadget_name"] == "Ceta", rows

    print(f"  [plugin schema] register_table/migration + list filter/order OK on {backend}")

    if not os.environ.get("LAMBDA_ERP_TEST_DB"):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("Plugin schema + list-filter checks")
    check_plugin_schema()
    print("All plugin schema checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
