"""The walk used by the access tests: every REST read route and every chat /
MCP read tool, called as one user. Returns what each call answered, so a test
can look for what must not be in there (and, as an admin, for what must)."""
import json


def build_walks(db, all_pdfs=False, extra_rest=None, extra_tools=None):
    """(rest_walk(client), tool_walk(user_id)) over the data that is in the
    database now. `extra_rest(get)` and `extra_tools(call)` add calls."""
    from api import auth, services
    from api.routers import analytics, mcp

    names = {slug: [row["name"] for row in db.sql(f'SELECT name FROM "{doctype}"')]
             for slug, doctype in services.SLUG_TO_DOCTYPE.items()}
    master_names = {slug: [row["name"] for row in db.sql(f'SELECT name FROM "{table}" LIMIT 50')
                           if "/" not in row["name"]]
                    for slug, (table, _) in services.MASTER_TABLES.items()}

    def rest_walk(client):
        seen = []

        def get(path, **params):
            r = client.get(path, params=params or None)
            seen.append((f"GET {path} {params or ''}", r.status_code, r.text))
            return r

        for slug in services.SLUG_TO_DOCTYPE:
            get(f"/api/documents/{slug}")
            get(f"/api/documents/{slug}", limit=500, include_discarded="true")
            get(f"/api/documents/{slug}/fields")
            get(f"/api/documents/{slug}/search", q="S")
            get(f"/api/documents/{slug}/filter-values", field="name")
            for name in names[slug]:
                get(f"/api/documents/{slug}/{name}")
                get(f"/api/documents/{slug}/{name}/adjacent")
                if all_pdfs or slug.startswith("purchase") or slug == "stock-entry":
                    r = client.post(f"/api/documents/{slug}/{name}/pdf")
                    seen.append((f"POST pdf {slug}/{name}", r.status_code, r.text))
        for slug in services.MASTER_TABLES:
            get(f"/api/masters/{slug}")
            get(f"/api/masters/{slug}/search", q="S")
            get(f"/api/masters/{slug}/fields")
            get(f"/api/masters/{slug}/filter-values", field="name")
            for name in master_names[slug]:
                get(f"/api/masters/{slug}/{name}")
        get("/api/masters/account/tree")
        for report in ("trial-balance", "chart-of-accounts", "general-ledger", "stock-balance", "stock-availability", "stock-detail", "sales-backlog", "purchase-backlog", "batch-stock",
                       "dashboard-summary", "profit-and-loss", "balance-sheet", "ar-aging", "ap-aging"):
            get(f"/api/reports/{report}")
            get(f"/api/reports/{report}", company="Test Co")
        get("/api/reports/analytics/metrics")
        for metric, meta in analytics.METRICS.items():
            for group_by in meta["group_by"]:
                get("/api/reports/analytics", metric=metric, group_by=group_by)
        get("/api/reports/runtime/datasets")
        for dataset in analytics.SEMANTIC_DATASETS:
            r = client.post("/api/reports/runtime/data", json={"requests": [{"dataset": dataset, "limit": 500}]})
            seen.append((f"POST runtime/data {dataset}", r.status_code, r.text))
        for batch in ("LOT-NONE",):
            get("/api/reports/batch-recipients", batch_no=batch)
            get("/api/reports/batch-changes", batch_no=batch)
            get("/api/reports/batch-movements", batch_no=batch)
        get("/api/reports/serial-trace", serial_no="SN-NONE")
        get("/api/chat-doctypes")
        get("/api/reports/runtime/drafts")
        if extra_rest:
            extra_rest(get)
        return seen

    def tool_walk(user_id):
        principal = auth.make_auth_principal(
            dict(db.get_value("User", user_id, ["name", "email", "full_name", "role", "enabled"])),
            auth.SESSION_CREDENTIAL)
        seen = []

        def call(tool, args):
            try:
                result = mcp._call(tool, args, principal)
            except Exception as exc:  # the transports report errors as text
                result = {"error": f"{type(exc).__name__}: {exc}"}
            text = json.dumps(result, default=str)
            seen.append((f"{tool} {json.dumps(args, default=str)[:120]}", "error" in text[:40], text))
            return result

        call("get_erp_context", {})
        for slug in services.SLUG_TO_DOCTYPE:
            call("list_documents", {"doctype": slug, "limit": 100})
            call("get_document_fields", {"doctype": slug})
            for name in names[slug]:
                call("get_document", {"doctype": slug, "name": name})
                call("generate_document_pdf", {"doctype": slug, "name": name})
        for slug in services.MASTER_TABLES:
            call("search_masters", {"master_type": slug})
            call("search_masters", {"master_type": slug, "query": "S"})
            call("search_masters", {"master_type": slug, "query": "Suplier"})  # fuzzy path
            call("get_master_fields", {"master_type": slug})
        for report in ("trial-balance", "general-ledger", "stock-balance", "stock-availability", "stock-detail", "sales-backlog", "purchase-backlog", "batch-stock", "dashboard-summary",
                       "profit-and-loss", "balance-sheet", "ar-aging", "ap-aging"):
            call("get_report", {"report_type": report, "filters": {"company": "Test Co"}})
        for dataset, spec in analytics.SEMANTIC_DATASETS.items():
            numeric = [field for field, kind in spec["fields"].items() if kind == "number"]
            call("query_dataset", {"dataset": dataset, "measures": {"n": ["count"]}})
            for field in numeric:
                call("query_dataset", {"dataset": dataset, "measures": {"x": ["sum", field], "y": ["max", field]}})
            call("query_dataset", {"dataset": dataset, "group_by": list(spec["fields"])[:3],
                                   "measures": {"n": ["count"]}})
        if extra_tools:
            extra_tools(call)
        return seen

    return rest_walk, tool_walk
