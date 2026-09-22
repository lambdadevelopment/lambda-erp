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
        assert [{k: v for k, v in row.items() if k != "view_url"} for row in mcp_rows] == rest_rows, (mcp_rows, rest_rows)
        assert [row["customer_name"] for row in mcp_rows] == ["MCP Test AG"], mcp_rows
        assert mcp_rows[0]["view_url"] == f"/masters/customer/{mcp_rows[0]['name']}", mcp_rows

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

        # Reproduce the reported Item projection over the actual MCP transport.
        for code in ("ALIAS-001", "ALIAS-002"):
            made = api.post("/api/masters/item", headers=mgr_h, json={
                "name": code, "item_code": code, "item_name": "Philips Everflo Pädiatrisch",
                "standard_rate": 125, "is_stock_item": 0, "disabled": 1,
            })
            assert made.status_code == 200, made.text

        def item_tool(tool, args, error=False):
            result = rpc(api, {"jsonrpc": "2.0", "id": 620, "method": "tools/call",
                               "params": {"name": tool, "arguments": {"master_type": "item", **args}}},
                         mgr_h).json()["result"]
            assert result["isError"] is error, result
            return json.loads(result["content"][0]["text"])

        projection = ["item_code", "item_name", "standard_rate", "is_stock_item", "stock_uom", "disabled"]
        rows = item_tool("search_masters", {"query": "Philips Everflo Pädiatrisch",
                                           "include_disabled": True, "result_fields": projection})
        assert [row["item_code"] for row in rows] == ["ALIAS-001", "ALIAS-002"], rows
        assert all(row["item_code"] == row["name"] and row["standard_rate"] == 125 for row in rows), rows
        assert item_tool("search_masters", {"query": "Philips Everflo Pädiatrisch", "result_fields": projection}) == []
        schema = item_tool("get_master_fields", {})
        assert schema["field_aliases"] == {"item_code": "name"} and "item_code" not in schema["fields"], schema
        canonical = item_tool("search_masters", {"include_disabled": True,
                              "filters": {"name": "ALIAS-001"}, "result_fields": ["name"]})
        assert canonical[0]["name"] == "ALIAS-001" and "item_code" not in canonical[0], canonical

        rest_args = {"include_disabled": "true", "fields": "item_code,item_name",
                     "item_code__contains": "ALIAS-", "search": "ALIAS", "search_fields": "item_code",
                     "order_by": "item_code", "order": "desc", "limit": 1, "offset": 1}
        rest = api.get("/api/masters/item", params=rest_args, headers=mgr_h)
        assert rest.status_code == 200 and rest.json()["rows"][0]["item_code"] == "ALIAS-001", rest.text
        page = item_tool("search_masters", {"include_disabled": True, "query": "ALIAS", "fields": ["item_code"],
                         "filters": {"item_code": ["contains", "ALIAS-"]}, "order_by": "item_code", "order": "desc",
                         "result_fields": ["item_code", "item_name"], "include_meta": True, "limit": 1, "offset": 1})
        assert page["total"] == 2 and page["rows"][0]["item_code"] == "ALIAS-001", page
        assert {k: v for k, v in page["rows"][0].items() if k != "view_url"} == rest.json()["rows"][0]
        adjacent = api.get("/api/masters/item/ALIAS-002/adjacent", params=rest_args, headers=mgr_h)
        assert adjacent.status_code == 200 and adjacent.json() == {"prev": None, "next": "ALIAS-001"}, adjacent.text
        values = api.get("/api/masters/item/filter-values", params={"field": "item_code", "q": "ALIAS-"}, headers=mgr_h)
        assert values.status_code == 200 and values.json()["values"] == ["ALIAS-001", "ALIAS-002"], values.text

        for args in ({"result_fields": ["invented_field"]}, {"filters": {"name": "ALIAS-001", "item_code": "ALIAS-002"}}):
            item_tool("search_masters", args, error=True)
        conflict = api.get("/api/masters/item", params={"name": "ALIAS-001", "item_code": "ALIAS-002"}, headers=mgr_h)
        assert conflict.status_code == 400, conflict.text
        unknown = api.get("/api/masters/item", params={"fields": "invented_field"}, headers=mgr_h)
        assert unknown.status_code == 400, unknown.text
        item_tool("create_master", {"data": {"name": "ALIAS-CONFLICT", "item_code": "OTHER",
                                              "item_name": "Must not exist"}}, error=True)
        assert item_tool("search_masters", {"filters": {"name": "ALIAS-CONFLICT"}}) == []
        item_tool("update_master", {"name": "ALIAS-001", "data": {"item_code": "OTHER"}}, error=True)

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
        assert [{k: v for k, v in row.items() if k != "view_url"} for row in mcp_docs] == rest_docs.json()["rows"], (mcp_docs, rest_docs.json())
        assert [row["name"] for row in mcp_docs] == ["QTN-MCP-1"], mcp_docs
        assert mcp_docs[0]["view_url"] == "/app/quotation/QTN-MCP-1", mcp_docs
        doc_meta_call = rpc(api, {"jsonrpc": "2.0", "id": 64, "method": "tools/call",
                                  "params": {"name": "get_document_fields", "arguments": {
                                      "doctype": "quotation",
                                  }}}, mgr_h).json()["result"]
        doc_meta = json.loads(doc_meta_call["content"][0]["text"])
        assert "customer_name" in doc_meta["text_fields"], doc_meta
        assert "customer_name" in doc_meta["default_search_fields"], doc_meta

        # Stock lookup regression: the operator-shaped IN used by the chat
        # must return the same six SimplyGo units as the legacy flat list.
        # Synthetic Bin positions isolate this read contract from stock posting.
        codes = ["PHI-SGM-PORT-LARGE", "PHI-SGM-PORT-SMALL"]
        for code, qty in [(codes[0], 3), (codes[1], 3), ("OTHER-DEVICE", 8), ("in", 1)]:
            db.insert("Bin", {"name": code + "-WH-001", "item_code": code,
                              "warehouse": "WH-001", "actual_qty": qty})
        db.conn.commit()

        def dataset_call(filters, dataset="stock_balances", error=False):
            result = rpc(api, {"jsonrpc": "2.0", "id": 650, "method": "tools/call",
                               "params": {"name": "query_dataset", "arguments": {
                                   "dataset": dataset, "group_by": ["item_code", "warehouse"],
                                   "measures": {"qty": ["sum", "actual_qty"]}, "filters": filters,
                                   "order_by": [{"field": "item_code", "direction": "asc"}],
                               }}}, mgr_h).json()["result"]
            assert result["isError"] is error, result
            return json.loads(result["content"][0]["text"])

        def runtime_call(filters, dataset="stock_balances"):
            return api.post("/api/reports/runtime/data", headers=mgr_h, json={"requests": [{
                "dataset": dataset, "fields": ["item_code", "warehouse", "actual_qty"], "filters": filters,
            }]})

        expected = [{"item_code": code, "warehouse": "WH-001", "qty": 3} for code in codes]
        for value in (codes, ["in", codes], ["IN", codes]):
            filters = {"item_code": value, "warehouse": "WH-001"}
            assert dataset_call(filters)["rows"] == expected
            rest = runtime_call(filters)
            assert rest.status_code == 200, rest.text
            rows = rest.json()["datasets"][0]["rows"]
            assert {r["item_code"] for r in rows} == set(codes) and sum(r["actual_qty"] for r in rows) == 6, rows
        assert dataset_call({"item_code": codes[0]})["rows"] == expected[:1]
        assert dataset_call({"item_code": "' OR 1=1 --"})["rows"] == []
        assert dataset_call({"item_code": ["not in", ["OTHER-DEVICE", "in"]]})["rows"] == expected
        # A flat list remains literal, even when a real code is named "in".
        assert {r["item_code"] for r in dataset_call({"item_code": ["in", codes[0]]})["rows"]} == {"in", codes[0]}
        for value in ([], ["in", []]):
            assert dataset_call({"item_code": value})["rows"] == []
            assert runtime_call({"item_code": value}).json()["datasets"][0]["rows"] == []
        assert len(dataset_call({"item_code": ["not in", []]})["rows"]) == 4

        invalid_filters = [
            {"item_code": ["in", [codes]]}, {"item_code": ["in", codes, "extra"]},
            {"item_code": ["unknown-op", codes]}, {"item_code": [codes[0], None]},
            {"item_code": [{"code": codes[0]}]}, {"item_code": codes * 251},
            {"item_code": {"operator": "in", "value": codes}}, {"item_code": {}},
            {"item_code": {"from": codes}}, {"item_code": {"from": "Z", "to": "A"}},
            {"item_code": {"from": 1, "to": "A"}}, {"invented_field": codes},
        ]
        for filters in invalid_filters:
            failure = dataset_call(filters, error=True)
            assert "error" in failure, failure
            rest = runtime_call(filters)
            assert rest.status_code == 400, (filters, rest.text)
        dataset_call([], error=True)

        for code, day in zip(codes, ("2026-09-22", "2026-09-23")):
            db.insert("Stock Ledger Entry", {"name": code + "-MOVEMENT", "item_code": code,
                      "warehouse": "WH-001", "posting_date": day, "actual_qty": 3, "is_cancelled": 0})
        db.conn.commit()
        assert dataset_call({"posting_date": {"from": "2026-09-22", "to": "2026-09-22"}},
                            dataset="stock_movements")["rows"] == expected[:1]
        for value in ({"from": "2026-09-23", "to": "2026-09-22"},
                      {"from": "2026-02-30"}, {"from": "2026-09-22", "until": "2026-09-23"},
                      {"from": {"date": "2026-09-22"}}, "2026-99-99", ["in", ["20260922"]]):
            filters = {"posting_date": value}
            dataset_call(filters, dataset="stock_movements", error=True)
            rest = runtime_call(filters, dataset="stock_movements")
            assert rest.status_code == 400, (filters, rest.text)
        for value in ({"from": "2026-09-22", "to": "2026-09-22"},
                      {"from": "2026-09-22"}, {"to": "2026-09-22"}, {"from": "", "to": None}):
            dataset_call({"posting_date": value}, dataset="stock_movements")

        # Chat and MCP use the same handler; advertised filters must also
        # describe the accepted shape for custom report data requests.
        from api import chat
        chat_tools = {tool["function"]["name"]: tool["function"] for tool in chat.build_tools()}
        assert '"in", [' in tools_by_name["query_dataset"]["inputSchema"]["properties"]["filters"]["description"]
        for tool_name in ("query_dataset", "create_custom_analytics_report", "update_custom_analytics_report"):
            schema = chat_tools[tool_name]["parameters"]["properties"]
            if tool_name != "query_dataset":
                schema = schema["data_requests"]["items"]["properties"]
            assert '"in", [' in schema["filters"]["description"], schema

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
