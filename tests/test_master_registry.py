#!/usr/bin/env python3
"""Functional tests for the plugin master/doctype discovery seams.

`api.services.register_master` (and the existing `register_doctype`) let a
deployment plugin add its own master types, and they must be first-class from
day one — REST CRUD and the AI chat alike — with zero per-type teaching:
fields are introspected from the live table. This simulates a plugin
registering a fictional "Gadget" master + doctype and verifies:

  - the chat tool schemas (build_tools) widen their doctype/master enums from
    the live registries, without mutating the static TOOLS template
  - the system prompt (build_system_prompt) names the registered types
  - the chat master handlers work end-to-end on the registered type:
    get_master_fields introspects columns, create auto-names from the prefix,
    search matches substrings and misspellings (fuzzy), update and
    reference-unprotected delete round-trip
  - an unregistered type still errors
  - the REST surface serves /api/masters/gadget with the usual role guards

Run:  python -m tests.test_master_registry
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_master_registry
"""
import os
import re
import sys


def _reset_db():
    """Return a db_path for setup(); reset Postgres to a clean schema.

    Same rationale as tests/test_rest_api.py: SQLite uses a temp *file* so the
    schema survives across TestClient lifespans; Postgres is dropped clean.
    """
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_masterreg_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


# What a deployment plugin would ship (cf. the CRM Lead in a real deployment,
# here with fictional data): a table, a Document subclass, two register calls.
GADGET_TABLE = """CREATE TABLE IF NOT EXISTS "Gadget" (
    name TEXT PRIMARY KEY,
    gadget_name TEXT,
    town TEXT,
    status TEXT DEFAULT 'New',
    notes TEXT,
    docstatus INTEGER DEFAULT 0,
    creation TEXT,
    modified TEXT
)"""


def check_master_registry():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app
    from api import services
    from api import chat
    from lambda_erp.database import get_db
    from lambda_erp.model import Document

    class Gadget(Document):
        DOCTYPE = "Gadget"
        CHILD_TABLES = {}
        PREFIX = "GAD"

    try:
        with TestClient(app) as client:
            # --- Plugin startup: table + registrations. ----------------------
            db = get_db()
            db.conn.execute(db._ddl(GADGET_TABLE))
            db.conn.commit()
            services.register_master(
                "gadget", "Gadget", "gadget_name", name_prefix="GAD",
                identity_alias="gadget_code", description="A test inventory gadget.",
                fields=["gadget_name", "town"],
            )
            services.register_doctype("Gadget", Gadget)
            services.register_master(
                "random-gadget", "Gadget", "gadget_name", name_prefix="RGAD",
                random_name=True,
            )
            services.register_master(
                "wide-gadget", "Gadget", "gadget_name", name_prefix="WGAD",
                name_digits=4,
            )

            # --- Tool schemas widen from the live registries. ----------------
            tools = chat.build_tools()
            by_name = {t["function"]["name"]: t["function"] for t in tools}
            m_enum = by_name["search_masters"]["parameters"]["properties"]["master_type"]["enum"]
            assert "gadget" in m_enum, f"search_masters enum missing gadget: {m_enum}"
            assert "gadget" in by_name["get_master_fields"]["parameters"]["properties"]["master_type"]["enum"]
            assert "gadget" in by_name["create_master"]["parameters"]["properties"]["master_type"]["enum"]
            d_enum = by_name["list_documents"]["parameters"]["properties"]["doctype"]["enum"]
            assert "gadget" in d_enum, f"list_documents enum missing gadget: {d_enum}"
            # The static template must stay untouched (build_tools deep-copies).
            static = {t["function"]["name"]: t["function"] for t in chat.TOOLS}
            assert "gadget" not in static["search_masters"]["parameters"]["properties"]["master_type"]["enum"]
            assert "gadget" not in static["list_documents"]["parameters"]["properties"]["doctype"]["enum"]

            # --- System prompt names the registered types. -------------------
            prompt = chat.build_system_prompt({"full_name": "Test Admin", "role": "admin"})
            assert "gadget" in prompt, "system prompt does not mention the registered master"
            assert "Extensions (deployment-specific)" in prompt
            assert "display = `gadget_name`" in prompt
            assert "A test inventory gadget" in prompt

            # --- Field discovery is pure introspection. ----------------------
            fields = chat._handle_get_master_fields({"master_type": "gadget"})
            assert "gadget_name" in fields["fields"] and "town" in fields["fields"], fields
            assert "gadget_name" in fields["default_search_fields"]
            # `notes` is a generic bulk column: reachable, but not searched by default.
            assert "notes" in fields["bulk_text_fields"]
            assert "notes" not in fields["default_search_fields"]

            # --- Chat CRUD round-trip: auto-name, search, fuzzy, update. -----
            created = chat._handle_create_master(
                {"master_type": "gadget",
                 "data": {"gadget_name": "Nimbus Coil", "town": "Bramblewick"}})
            assert created.get("name") == "GAD-001", created
            assert "_warning" not in created, created

            hits = chat._handle_search_masters({"master_type": "gadget", "query": "nimbus"})
            assert isinstance(hits, list) and hits and hits[0]["name"] == "GAD-001", hits
            # Misspelled → fuzzy fallback still resolves it.
            fuzzy = chat._handle_search_masters({"master_type": "gadget", "query": "nimbes coil"})
            assert isinstance(fuzzy, list) and fuzzy and fuzzy[0]["name"] == "GAD-001", fuzzy
            # Narrowing to a specific live column works.
            by_town = chat._handle_search_masters(
                {"master_type": "gadget", "query": "bramblewick", "fields": ["town"]})
            assert isinstance(by_town, list) and by_town, by_town
            # The identity alias resolves to the `name` PK: fields=["gadget_code"]
            # searches the id column instead of erroring "no such field" — the
            # mistake the model kept making with fields=["item_code"].
            by_code = chat._handle_search_masters(
                {"master_type": "gadget", "query": "GAD-001", "fields": ["gadget_code"]})
            assert isinstance(by_code, list) and by_code and by_code[0]["name"] == "GAD-001", by_code
            # A bogus column no longer errors — it degrades to the default text
            # search (unknown names ignored), so the query still resolves.
            fallback = chat._handle_search_masters(
                {"master_type": "gadget", "query": "nimbus", "fields": ["no_such_column"]})
            assert isinstance(fallback, list) and fallback and fallback[0]["name"] == "GAD-001", fallback

            updated = chat._handle_update_master(
                {"master_type": "gadget", "name": "GAD-001", "data": {"status": "Qualified"}})
            assert updated.get("status") == "Qualified", updated

            # Unknown master types still error cleanly.
            unknown = chat._handle_search_masters({"master_type": "widget", "query": "x"})
            assert isinstance(unknown, dict) and "error" in unknown, unknown

            # --- Registered doctype is listable through the document path. ---
            rows = chat._handle_list_documents({"doctype": "gadget"})
            assert isinstance(rows, list) and rows and rows[0]["name"] == "GAD-001", rows

            # --- REST surface: /api/masters/gadget with the usual guards. ----
            r = client.post("/api/auth/register",
                            json={"email": "admin@example.com", "full_name": "Admin",
                                  "password": "test-password-123"})
            assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]
            r = client.get("/api/masters/gadget")
            assert r.status_code == 200, r.text[:300]
            assert any(g["name"] == "GAD-001" for g in r.json()["rows"]), r.text[:300]
            projected = client.get("/api/masters/gadget?fields=name,gadget_name").json()["rows"]
            assert projected and set(projected[0]) == {"name", "gadget_name"}, projected
            r = client.post("/api/masters/gadget", json={"gadget_name": "Vela Spring"})
            assert r.status_code == 200 and r.json()["name"] == "GAD-002", r.text[:300]
            r = client.post("/api/masters/random-gadget", json={"gadget_name": "Scale Safe"})
            assert r.status_code == 200, r.text[:300]
            assert re.fullmatch(r"RGAD-[0-9A-F]{16}", r.json()["name"]), r.json()
            r = client.post("/api/masters/wide-gadget", json={"gadget_name": "Padded Sequence"})
            assert r.status_code == 200 and r.json()["name"] == "WGAD-0001", r.text[:300]

            # --- Delete: no reference checks registered → permanent delete. --
            gone = chat._handle_delete_master({"master_type": "gadget", "name": "GAD-002"},
                                              {"full_name": "Admin", "role": "admin"})
            assert isinstance(gone, dict) and "error" not in gone, gone
            r = client.get("/api/masters/gadget/GAD-002")
            assert "not found" in r.json().get("detail", ""), r.text[:300]
    finally:
        # The registries are process-global — leave them as we found them.
        services.MASTER_TABLES.pop("gadget", None)
        services.MASTER_TABLES.pop("random-gadget", None)
        services.MASTER_TABLES.pop("wide-gadget", None)
        services.MASTER_NAME_PREFIXES.pop("gadget", None)
        services.MASTER_NAME_PREFIXES.pop("random-gadget", None)
        services.MASTER_NAME_PREFIXES.pop("wide-gadget", None)
        services.MASTER_NAME_DIGITS.pop("gadget", None)
        services.MASTER_NAME_DIGITS.pop("random-gadget", None)
        services.MASTER_NAME_DIGITS.pop("wide-gadget", None)
        services.MASTER_RANDOM_NAME_TYPES.discard("random-gadget")
        services.MASTER_METADATA.pop("gadget", None)
        services.MASTER_REFERENCE_CHECKS.pop("gadget", None)
        services.DOCUMENT_CLASSES.pop("Gadget", None)
        services.SLUG_TO_DOCTYPE.pop("gadget", None)
        services.DOCTYPE_TO_SLUG.pop("Gadget", None)
        if not db_path.startswith("postgres"):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(db_path + suffix)
                except OSError:
                    pass

    print(f"  [master registry] tools/prompt/handlers/REST discovery OK on {backend}")


def main():
    print("Master/doctype registration seam checks")
    check_master_registry()
    print("All master registry checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
