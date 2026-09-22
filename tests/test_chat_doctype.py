#!/usr/bin/env python3
"""Tests for register_chat_doctype — teaching the AI chat about a plugin doctype.

The document tools (create/update/list/get_document) already accept any
registered doctype and run its validate(); what was missing is the chat KNOWING
what a custom doctype is and how it links. register_chat_doctype(slug,
description=…) makes build_system_prompt surface: the description, key fields,
and the Document class's LINK_FIELDS relationships, plus the rule "drive these
with the document tools, attach to a parent via its link field." Also checks the
list_documents chat tool gained order_by/order.

Run:  python -m tests.test_chat_doctype
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_chat_doctype   # CI runs both
"""
from api.pdf_profiles import DisabledPDF
import os
import sys


def check_chat_doctype():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if url:
        import psycopg
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        db_path = url
    else:
        import tempfile
        fd, db_path = tempfile.mkstemp(suffix=".db", prefix="lambda_chat_doctype_")
        os.close(fd)
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")

    from fastapi.testclient import TestClient
    from lambda_erp.model import Document
    import api.services as services
    import api.chat as chat
    from api.main import app

    class Gadget(Document):
        DOCTYPE = "Gadget"; CHILD_TABLES = {}; PREFIX = "GAD"
        def validate(self):
            pass

    class Widget(Document):
        DOCTYPE = "Widget"; CHILD_TABLES = {}; PREFIX = "WID"
        LINK_FIELDS = {"gadget_id": "Gadget"}
        def validate(self):
            pass

    with TestClient(app) as client:
        services.register_doctype("Gadget", Gadget, pdf_profile=DisabledPDF("Synthetic non-printable test fixture"))
        services.register_doctype("Widget", Widget, pdf_profile=DisabledPDF("Synthetic non-printable test fixture"))
        services.register_chat_doctype("gadget", description="A gadget.")  # page="self" default
        services.register_chat_doctype(
            "widget", description="A widget attached to a gadget.",
            fields=["label", "gadget_id"], page="gadget_id",  # page-less, opens via gadget
        )
        prompt = chat.build_system_prompt({"full_name": "Jon", "role": "manager"})

        assert "## Custom record types" in prompt, "no custom-types section"
        assert "A widget attached to a gadget." in prompt, "description missing"
        assert "`gadget_id` → the Gadget's name" in prompt, "LINK_FIELDS relationship not surfaced"
        assert "`label`" in prompt, "key fields missing"
        assert "NOT the master tools" in prompt, "document-tools rule missing"
        assert "widget" in prompt, "slug not listed among doctypes"

        # Per-doctype link rules (register_chat_doctype `page`).
        assert "Open a record at `/app/gadget/<name>`." in prompt, "self page-rule missing"
        assert "open it via its parent: `/app/gadget/<gadget_id>`" in prompt, "via page-rule missing"
        assert services.chat_doctype_page_info("widget") == {
            "kind": "via", "link_field": "gadget_id", "parent_slug": "gadget"}

        # Tools supply the actual destination, including plugin parent pages.
        # Exercise saved results so view_url cannot become persisted input.
        from lambda_erp.database import get_db
        db = get_db()
        for table in ("Gadget", "Widget"):
            db.sql(f'''CREATE TABLE "{table}" (
                name TEXT PRIMARY KEY, label TEXT, gadget_id TEXT,
                docstatus INTEGER DEFAULT 0, discarded INTEGER DEFAULT 0,
                creation TEXT, modified TEXT, owner TEXT)''')
            db._col_cache.pop(table, None)
            db._text_col_cache.pop(table, None)
        db.conn.commit()
        gadget_name = "GAD / Zürich?#(1)"
        expected_url = "/app/gadget/GAD%20%2F%20Z%C3%BCrich%3F%23%281%29"
        created = chat.TOOL_HANDLERS["create_document"]({
            "doctype": "gadget", "data": {"name": gadget_name, "label": "Before"}})
        assert created["view_url"] == expected_url, created
        updated = chat.TOOL_HANDLERS["update_document"]({
            "doctype": "gadget", "name": gadget_name, "data": {"label": "After"}})
        assert updated["view_url"] == expected_url, updated
        assert "view_url" not in services.load_document("gadget", gadget_name)
        widget = chat.TOOL_HANDLERS["create_document"]({
            "doctype": "widget", "data": {"gadget_id": gadget_name, "label": "Child"}})
        assert widget["view_url"] == expected_url, widget
        fetched = chat.TOOL_HANDLERS["get_document"]({"doctype": "widget", "name": widget["name"]})
        assert fetched["view_url"] == expected_url, fetched
        for meta in (False, True):
            listed = chat.TOOL_HANDLERS["list_documents"]({"doctype": "widget", "include_meta": meta})
            assert (listed["rows"] if meta else listed)[0]["view_url"] == expected_url, listed
        projected = chat.TOOL_HANDLERS["list_documents"]({"doctype": "widget", "fields": ["name"]})
        assert projected[0]["view_url"] is None, projected
        batch = chat.TOOL_HANDLERS["batch_update_documents"]({"doctype": "gadget", "updates": [
            {"name": gadget_name, "data": {"label": "Batch"}},
            {"name": "missing", "data": {"label": "Failure"}},
        ]})
        assert batch["results"][0]["view_url"] == expected_url, batch
        assert "view_url" not in batch["results"][1], batch
        services.register_chat_doctype("widget", description="No standalone page.", page=None)
        assert chat.TOOL_HANDLERS["get_document"]({"doctype": "widget", "name": widget["name"]})["view_url"] is None
        services.register_chat_doctype("widget", description="A widget.", page="gadget_id")

        from unittest.mock import patch
        # A conversion's link must point at the target, never the source type.
        with patch.object(services, "convert_document", return_value={"name": "SINV-001"}):
            converted = chat.TOOL_HANDLERS["convert_document"]({
                "doctype": "sales-order", "name": "SO-001", "target_doctype": "Sales Invoice"})
        assert converted["view_url"] == "/app/sales-invoice/SINV-001", converted
        from api.chat_links import record_view_url
        assert record_view_url("customer", {"name": "CUST-001"}, master=True) == "/masters/customer/CUST-001"
        assert record_view_url("company", {"name": "My Co"}, master=True) == "/masters/company/My%20Co"
        assert record_view_url("account", {"name": "Sales & Services"}, master=True) == "/reports/general-ledger?account=Sales+%26+Services"
        assert record_view_url("cost-center", {"name": "Main"}, master=True) is None
        assert record_view_url("unknown", {"name": "Missing"}) is None
        assert "copy that exact value" in prompt and "Never construct a record URL" in prompt
        api_prompt = chat.build_system_prompt({"role": "manager"}, channel="api")
        assert "Do not paste `/app/...`" in api_prompt

        # /api/chat-doctypes exposes the resolved page info for the frontend.
        rows = {d["slug"]: d for d in client.get("/api/chat-doctypes").json()["doctypes"]}
        assert rows["gadget"]["page"]["kind"] == "self"
        assert rows["widget"]["page"] == {"kind": "via", "link_field": "gadget_id", "parent_slug": "gadget"}

        # list_documents chat tool gained order_by/order.
        tool = next(t for t in chat.build_tools() if t["function"]["name"] == "list_documents")
        props = tool["function"]["parameters"]["properties"]
        assert "order_by" in props and "order" in props, "order_by/order not on list tool"

        # The widget slug is in the create_document enum too (widened from the registry),
        # so the chat can actually create one through the validated path.
        ctool = next(t for t in chat.build_tools() if t["function"]["name"] == "create_document")
        assert "widget" in ctool["function"]["parameters"]["properties"]["doctype"]["enum"]

    print(f"  [chat doctype] register_chat_doctype prompt + list order_by OK on {backend}")

    if not url:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("Chat-doctype seam checks")
    check_chat_doctype()
    print("All chat-doctype checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
