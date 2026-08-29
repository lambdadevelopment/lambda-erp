#!/usr/bin/env python3
"""Tests for the MCP endpoint (POST /api/mcp) — the fine-grained ERP tool surface
for LLM agents, over JSON-RPC 2.0.

Reuses the chat's tools + handlers and the Bearer-key auth: a key acts as its
user at the key's role, gated by `rest_api_enabled`. Covers initialize,
tools/list (role-scoped), tools/call (read + a write round-trip), viewer
write-denial, notifications, and the no-key rejection.

Run:  python -m tests.test_mcp
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_mcp
"""
import json
import os
import sys


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_mcp_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_mcp():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/register",
                        json={"email": "admin@example.com", "full_name": "Admin",
                              "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]
        mgr = client.post("/api/auth/api-keys", json={"name": "agent", "role": "manager"}).json()
        vwr = client.post("/api/auth/api-keys", json={"name": "ro", "role": "viewer"}).json()
        client.put("/api/auth/settings", json={"rest_api_enabled": "1"})

    mgr_h = {"Authorization": f"Bearer {mgr['token']}"}
    vwr_h = {"Authorization": f"Bearer {vwr['token']}"}

    def rpc(client, body, headers):
        return client.post("/api/mcp", json=body, headers=headers)

    with TestClient(app) as api:  # no cookie — Bearer is the only credential
        # No key -> 401 (never the public fallback).
        assert api.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401

        # initialize
        r = rpc(api, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, mgr_h)
        assert r.status_code == 200, r.text[:200]
        init = r.json()["result"]
        assert init["protocolVersion"] and init["serverInfo"]["name"] == "lambda-erp", init
        assert init["capabilities"].get("tools") is not None, init

        # notification (no id) -> 202, no body
        assert rpc(api, {"jsonrpc": "2.0", "method": "notifications/initialized"}, mgr_h).status_code == 202

        # tools/list — manager sees writes; viewer does not.
        listed_tools = rpc(api, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, mgr_h).json()["result"]["tools"]
        tools_by_name = {tool["name"]: tool for tool in listed_tools}
        mgr_tools = set(tools_by_name)
        vwr_tools = {t["name"] for t in rpc(api, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, vwr_h).json()["result"]["tools"]}
        assert {"list_documents", "get_document_fields", "get_document", "create_document"} <= mgr_tools, mgr_tools
        assert "list_documents" in vwr_tools and "create_document" not in vwr_tools, vwr_tools
        assert "delete_master" not in mgr_tools, "delete_master is admin-only"
        search_props = tools_by_name["search_masters"]["inputSchema"]["properties"]
        assert {"filters", "order_by", "order", "offset", "result_fields"} <= set(search_props), search_props
        # Chat-session tools are excluded from MCP.
        assert "retrieve_chat_history" not in mgr_tools

        # Each tool carries an MCP inputSchema.
        one = next(t for t in rpc(api, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, mgr_h).json()["result"]["tools"])
        assert one["inputSchema"]["type"] == "object", one

        # tools/call — a read.
        call = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "list_documents", "arguments": {"doctype": "quotation"}}}
        res = rpc(api, call, mgr_h).json()["result"]
        assert res["isError"] is False and res["content"][0]["type"] == "text", res

        # tools/call — a write round-trip (manager creates a customer master).
        create = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                  "params": {"name": "create_master",
                             "arguments": {"master_type": "customer", "data": {
                                 "customer_name": "MCP Test AG", "customer_group": "Retail",
                                 "territory": "Zurich", "credit_limit": 100,
                             }}}}
        out = rpc(api, create, mgr_h).json()["result"]
        assert out["isError"] is False, out

        # Two more records make combined filters, sorting, projection and
        # pagination observable. Use the same write tool an external MCP client
        # uses rather than reaching around the transport.
        for idx, data in enumerate((
            {"customer_name": "Bern Retail AG", "customer_group": "Retail", "territory": "Bern", "credit_limit": 100},
            {"customer_name": "Zurich Wholesale AG", "customer_group": "Wholesale", "territory": "Zurich", "credit_limit": 200},
        ), start=50):
            made = rpc(api, {"jsonrpc": "2.0", "id": idx, "method": "tools/call",
                             "params": {"name": "create_master", "arguments": {
                                 "master_type": "customer", "data": data,
                             }}}, mgr_h).json()["result"]
            assert made["isError"] is False, made

        # REST Smart Search and MCP search_masters now drive the same
        # deterministic master-list semantics. Each field has its own value;
        # text uses contains, numeric/date/bool values stay exact.
        rest = api.get(
            "/api/masters/customer?customer_group__contains=tail&territory=Zurich"
            "&credit_limit=100&order_by=customer_name&order=asc&fields=name,customer_name",
            headers=mgr_h,
        )
        assert rest.status_code == 200, rest.text[:300]
        rest_rows = rest.json()["rows"]
        call = rpc(api, {"jsonrpc": "2.0", "id": 60, "method": "tools/call",
                         "params": {"name": "search_masters", "arguments": {
                             "master_type": "customer",
                             "filters": {
                                 "customer_group": ["contains", "tail"],
                                 "territory": "Zurich",
                                 "credit_limit": 100,
                             },
                             "order_by": "customer_name", "order": "asc",
                             "result_fields": ["customer_name"],
                         }}}, mgr_h).json()["result"]
        assert call["isError"] is False, call
        mcp_rows = json.loads(call["content"][0]["text"])
        assert mcp_rows == rest_rows and [row["customer_name"] for row in mcp_rows] == ["MCP Test AG"], (mcp_rows, rest_rows)

        page_call = rpc(api, {"jsonrpc": "2.0", "id": 600, "method": "tools/call",
                              "params": {"name": "search_masters", "arguments": {
                                  "master_type": "customer",
                                  "filters": {"customer_group": ["contains", "tail"]},
                                  "order_by": "customer_name", "order": "asc",
                                  "limit": 1, "offset": 1,
                                  "result_fields": ["customer_name"],
                              }}}, mgr_h).json()["result"]
        page_rows = json.loads(page_call["content"][0]["text"])
        assert [row["customer_name"] for row in page_rows] == ["MCP Test AG"], page_rows

        # Schema discovery tells an agent which columns accept `contains`.
        meta_call = rpc(api, {"jsonrpc": "2.0", "id": 61, "method": "tools/call",
                              "params": {"name": "get_master_fields", "arguments": {
                                  "master_type": "customer",
                              }}}, mgr_h).json()["result"]
        meta = json.loads(meta_call["content"][0]["text"])
        assert "customer_name" in meta["text_fields"] and "credit_limit" not in meta["text_fields"], meta

        bad_contains = rpc(api, {"jsonrpc": "2.0", "id": 62, "method": "tools/call",
                                 "params": {"name": "search_masters", "arguments": {
                                     "master_type": "customer",
                                     "filters": {"credit_limit": ["contains", "10"]},
                                 }}}, mgr_h).json()["result"]
        assert bad_contains["isError"] is True, bad_contains

        # Bare document free-text search uses server-side defaults over both
        # REST and MCP, and get_document_fields exposes those defaults.
        from lambda_erp.database import get_db
        db = get_db()
        db.insert("Quotation", {"name": "QTN-MCP-1", "customer_name": "Needle Customer AG",
                                "status": "Draft", "docstatus": 0, "discarded": 0})
        db.insert("Quotation", {"name": "QTN-MCP-2", "customer_name": "Other Customer AG",
                                "status": "Draft", "docstatus": 0, "discarded": 0})
        db.conn.commit()
        rest_docs = api.get("/api/documents/quotation?search=needle&fields=name,customer_name", headers=mgr_h)
        assert rest_docs.status_code == 200, rest_docs.text[:300]
        doc_call = rpc(api, {"jsonrpc": "2.0", "id": 63, "method": "tools/call",
                             "params": {"name": "list_documents", "arguments": {
                                 "doctype": "quotation", "filters": {"search": "needle"},
                                 "fields": ["customer_name"],
                             }}}, mgr_h).json()["result"]
        mcp_docs = json.loads(doc_call["content"][0]["text"])
        assert mcp_docs == rest_docs.json()["rows"] and [row["name"] for row in mcp_docs] == ["QTN-MCP-1"], (mcp_docs, rest_docs.json())
        doc_meta_call = rpc(api, {"jsonrpc": "2.0", "id": 64, "method": "tools/call",
                                  "params": {"name": "get_document_fields", "arguments": {
                                      "doctype": "quotation",
                                  }}}, mgr_h).json()["result"]
        doc_meta = json.loads(doc_meta_call["content"][0]["text"])
        assert "customer_name" in doc_meta["text_fields"], doc_meta
        assert "customer_name" in doc_meta["default_search_fields"], doc_meta

        # Viewer is denied writes at call time too (defence in depth).
        denied = rpc(api, {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                           "params": {"name": "create_master", "arguments": {"master_type": "customer", "data": {"customer_name": "x"}}}}, vwr_h).json()["result"]
        assert denied["isError"] is True, denied

        # Unknown tool -> JSON-RPC error.
        err = rpc(api, {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope"}}, mgr_h).json()
        assert err.get("error", {}).get("code") == -32602, err
        # Unknown method -> method-not-found.
        err2 = rpc(api, {"jsonrpc": "2.0", "id": 8, "method": "bogus/method"}, mgr_h).json()
        assert err2.get("error", {}).get("code") == -32601, err2

    print(f"  [mcp] initialize/tools-list/tools-call + role scoping OK on {backend}")

    if not db_path.startswith("postgres"):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass


def main():
    print("MCP endpoint checks")
    check_mcp()
    print("All MCP checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
