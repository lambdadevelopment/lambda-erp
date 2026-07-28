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
        services.register_doctype("Gadget", Gadget)
        services.register_doctype("Widget", Widget)
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
