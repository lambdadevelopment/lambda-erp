"""Access profiles: a restricted user gets no purchase price, cost or margin
over any supported path, and cannot act outside the profile.

The core of this suite is a walk: every REST read route and every chat / MCP
read tool is called as a restricted user, and no response may contain one of
the seeded purchase / cost figures. The same walk as an admin must contain
each of them, which proves the walk reaches the data.

Synthetic data only. Run:
    python -m tests.test_access_profiles
    LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_access_profiles
"""
import json
import os
import sys

# Purchase rate 73.91: 13 received = 960.83, 3 more by stock entry = 221.73,
# 2 sold = 147.82 cost of goods, 14 left = 1034.74 stock value.
SECRETS = ("73.91", "960.83", "221.73", "147.82", "1034.74")
SELLING = "20.77"

SAFE_NUMERIC = {"qty", "idx", "docstatus", "discarded", "conversion_rate", "total_qty", "billed_qty",
                "received_qty", "per_billed", "per_received", "ignore_pricing_rule", "is_return",
                "update_stock", "amount_only", "included_in_print_rate", "row_id", "rate"}


def check_access_profiles():
    from tests.test_rest_api import _reset_db
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app
    from api import auth, chat, services
    from api.routers import analytics, mcp
    from api.routers.masters import create_master_record
    from lambda_erp import permissions
    from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
    from lambda_erp.database import get_db
    from lambda_erp.exceptions import PermissionDenied
    from lambda_erp.utils import now

    print(f"Access profile checks ({backend})")

    with TestClient(app) as admin:
        r = admin.post("/api/auth/register", json={"email": "admin@example.com", "full_name": "Admin",
                                                    "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]
        db = get_db()

        # ---- data ------------------------------------------------------------
        db.insert("Company", {"name": "Test Co", "company_name": "Test Co", "default_currency": "USD"})
        setup_chart_of_accounts("Test Co", "USD")
        setup_cost_center("Test Co")
        db.insert("Customer", {"name": "C", "customer_name": "Customer", "default_currency": "USD"})
        db.insert("Supplier", {"name": "SUP", "supplier_name": "Supplier", "default_currency": "USD"})
        db.insert("Warehouse", {"name": "A", "warehouse_name": "A", "company": "Test Co"})
        db.insert("Item", {"name": "S", "item_name": "Stock item", "is_stock_item": 1, "standard_rate": 20.77})
        db.commit()

        def post(slug, data):
            doc = services.create_document(slug, data)
            return services.submit_document(slug, doc["name"])

        line = {"item_code": "S", "warehouse": "A"}
        po = post("purchase-order", {"company": "Test Co", "supplier": "SUP", "transaction_date": "2026-11-12",
                                     "items": [{**line, "qty": 13, "rate": 73.91}]})
        post("purchase-receipt", {"company": "Test Co", "supplier": "SUP", "posting_date": "2026-11-12",
                                  "items": [{**line, "qty": 13, "rate": 73.91}]})
        pinv = post("purchase-invoice", {"company": "Test Co", "supplier": "SUP", "posting_date": "2026-11-12",
                                         "items": [{**line, "qty": 13, "rate": 73.91}]})
        post("stock-entry", {"company": "Test Co", "stock_entry_type": "Material Receipt",
                             "items": [{"item_code": "S", "qty": 3, "basic_rate": 73.91, "t_warehouse": "A"}]})
        so = post("sales-order", {"company": "Test Co", "customer": "C", "transaction_date": "2026-11-12",
                                  "delivery_date": "2026-11-30", "items": [{**line, "qty": 2, "rate": 20.77}]})
        post("sales-invoice", {"company": "Test Co", "customer": "C", "posting_date": "2026-11-12", "update_stock": 1,
                               "items": [{**line, "qty": 2, "rate": 20.77}]})
        buying = services.create_document("price-list", {"price_list_name": "Buying", "buying": 1, "selling": 0,
                                                         "currency": "USD"})
        selling = services.create_document("price-list", {"price_list_name": "Selling", "buying": 0, "selling": 1,
                                                          "currency": "USD"})
        buy_price = services.create_document("item-price", {"item_code": "S", "price_list": buying["name"], "rate": 73.91})
        sell_price = services.create_document("item-price", {"item_code": "S", "price_list": selling["name"], "rate": 20.77})
        payable = db.get_value("Company", "Test Co", "default_payable_account")
        bank = db.get_value("Account", {"company": "Test Co", "account_type": "Bank", "is_group": 0}, "name") or \
            db.get_value("Account", {"company": "Test Co", "account_type": "Cash", "is_group": 0}, "name")
        supplier_payment = post("payment-entry", {
            "company": "Test Co", "payment_type": "Pay", "party_type": "Supplier", "party": "SUP",
            "paid_from": bank, "paid_to": payable, "paid_amount": 960.83, "posting_date": "2026-11-12",
            "references": [{"reference_doctype": "Purchase Invoice", "reference_name": pinv["name"],
                            "allocated_amount": 960.83}]})

        # ---- profiles and users -----------------------------------------------
        r = admin.post("/api/auth/access-profiles", json={"name": "X", "hidden_classes": ["salary"]})
        assert r.status_code == 422 and "Unknown sensitivity class" in r.text, r.text
        r = admin.post("/api/auth/access-profiles", json={"name": "X", "permissions": {"doctypes": {"Item": ["fly"]}}})
        assert r.status_code == 422, r.text
        r = admin.post("/api/auth/access-profiles", json={
            "name": "Field", "description": "No purchase prices, no suppliers",
            "hidden_classes": ["purchase_price"], "permissions": {"doctypes": {"Supplier": []}}})
        assert r.status_code == 200 and r.json()["hidden_classes"] == ["purchase_price"], r.text
        assert admin.post("/api/auth/access-profiles", json={"name": "Field"}).status_code == 409
        listed = admin.get("/api/auth/access-profiles").json()
        assert [p["name"] for p in listed["profiles"]] == ["Field"] and "cost" in listed["sensitivity_classes"], listed

        def make_user(email, role):
            name = f"U-{role}-{email.split('@')[0]}"
            db.insert("User", {"name": name, "email": email, "full_name": email, "role": role, "enabled": 1,
                               "hashed_password": auth.hash_password("test-password-123"),
                               "creation": now(), "modified": now()})
            db.commit()
            return name

        rep_id, clerk_id, plain_id = make_user("rep@example.com", "viewer"), make_user("clerk@example.com", "manager"), \
            make_user("plain@example.com", "viewer")
        for user_id in (rep_id, clerk_id):
            r = admin.put(f"/api/auth/users/{user_id}/access-profile", json={"access_profile": "Field"})
            assert r.status_code == 200, r.text
        me_admin = admin.get("/api/auth/me").json()
        assert admin.put(f"/api/auth/users/{me_admin['name']}/access-profile",
                         json={"access_profile": "Field"}).status_code == 409
        assert admin.put(f"/api/auth/users/{rep_id}/access-profile", json={"access_profile": "Nope"}).status_code == 404
        assert admin.delete("/api/auth/access-profiles/Field").status_code == 409  # in use
        assert any(u["name"] == rep_id and u["access_profile"] == "Field" for u in admin.get("/api/auth/users").json())

        def login(email):
            client = TestClient(app)
            r = client.post("/api/auth/login", json={"email": email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
            return client

        rep, clerk, plain = login("rep@example.com"), login("clerk@example.com"), login("plain@example.com")
        me = rep.get("/api/auth/me").json()
        assert me["role"] == "viewer" and me["access_profile"] == "Field", me
        assert set(me["restrictions"]["hidden_classes"]) == {"purchase_price", "cost", "supplier_terms", "margin"}, me
        assert "general-ledger" in me["restrictions"]["denied_reports"], me
        assert me_admin["restrictions"] is None and plain.get("/api/auth/me").json()["restrictions"] is None
        # Only an admin manages profiles.
        assert rep.get("/api/auth/access-profiles").status_code == 403
        assert clerk.post("/api/auth/access-profiles", json={"name": "Mine"}).status_code == 403

        # ---- the walk -----------------------------------------------------------
        names = {slug: [row["name"] for row in db.sql(f'SELECT name FROM "{doctype}"')]
                 for slug, doctype in services.SLUG_TO_DOCTYPE.items()}
        master_names = {slug: [row["name"] for row in db.sql(f'SELECT name FROM "{table}" LIMIT 5')]
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
                    if slug.startswith("purchase") or slug == "stock-entry":
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
            for report in ("trial-balance", "chart-of-accounts", "general-ledger", "stock-balance", "stock-availability", "stock-detail", "batch-stock",
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
            get("/api/chat-doctypes")
            get("/api/reports/runtime/drafts")
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
            for report in ("trial-balance", "general-ledger", "stock-balance", "stock-availability", "stock-detail", "batch-stock", "dashboard-summary",
                           "profit-and-loss", "balance-sheet", "ar-aging", "ap-aging"):
                call("get_report", {"report_type": report, "filters": {"company": "Test Co"}})
            for dataset, spec in analytics.SEMANTIC_DATASETS.items():
                numeric = [field for field, kind in spec["fields"].items() if kind == "number"]
                call("query_dataset", {"dataset": dataset, "measures": {"n": ["count"]}})
                for field in numeric:
                    call("query_dataset", {"dataset": dataset, "measures": {"x": ["sum", field], "y": ["max", field]}})
                call("query_dataset", {"dataset": dataset, "group_by": list(spec["fields"])[:3],
                                       "measures": {"n": ["count"]}})
            return seen

        def leaks(seen):
            return [(what, secret) for what, _, text in seen for secret in SECRETS if secret in text]

        admin_rest, admin_tools = rest_walk(admin), tool_walk(me_admin["name"])
        # Every path of the walk exists and works for an admin; a typo in a
        # route would otherwise look like "no leak".
        broken = [entry[:2] for entry in admin_rest if entry[1] >= 400]
        assert not broken, f"the walk calls paths that fail for an admin: {broken[:8]}"
        for label, seen in (("REST", admin_rest), ("tools", admin_tools)):
            text = "\n".join(entry[2] for entry in seen)
            missing = [secret for secret in SECRETS if secret not in text]
            assert not missing, f"the {label} walk never reaches {missing}: it cannot prove anything about them"
        # A plain viewer (no profile) still sees what viewers always saw.
        assert leaks(rest_walk(plain)), "an unrestricted viewer should still see purchase data"

        for who, client, user_id in (("rep", rep, rep_id), ("clerk", clerk, clerk_id)):
            rest_seen, tool_seen = rest_walk(client), tool_walk(user_id)
            found = leaks(rest_seen) + leaks(tool_seen)
            assert not found, f"{who}: {len(found)} leak(s), first: {found[:5]}"
            assert not [entry[0] for entry in rest_seen if entry[1] >= 500], \
                [entry[:2] for entry in rest_seen if entry[1] >= 500][:5]
            # Selling data stays visible.
            assert any(SELLING in text for _, _, text in rest_seen), f"{who} lost selling prices"
            assert any(SELLING in text for _, _, text in tool_seen), f"{who} lost selling prices in tools"
        print(f"  [ok] no leak over {len(admin_rest)} REST calls and {len(admin_tools)} tool calls per user")

        # ---- what a restricted user still gets, and how it is refused ----------
        doc = rep.get(f"/api/documents/purchase-order/{po['name']}")
        assert doc.status_code == 200, doc.text
        body = doc.json()
        assert body["supplier"] == "SUP" and body["items"][0]["qty"] == 13, body
        assert "grand_total" not in body and "rate" not in body["items"][0] and "amount" not in body["items"][0], body
        fields = rep.get("/api/documents/purchase-order/fields").json()["fields"]
        assert "supplier" in fields and "grand_total" not in fields, fields
        for params in ({"grand_total": "960.83"}, {"order_by": "grand_total"}, {"fields": "name,grand_total"}):
            r = rep.get("/api/documents/purchase-order", params=params)
            assert r.status_code == 403 and "grand_total" in r.text, (params, r.status_code, r.text)
        assert rep.get("/api/documents/purchase-order/filter-values", params={"field": "grand_total"}).status_code == 403
        assert rep.get("/api/documents/sales-order", params={"order_by": "grand_total"}).status_code == 200

        for report in ("trial-balance", "general-ledger", "profit-and-loss", "balance-sheet", "ap-aging"):
            assert rep.get(f"/api/reports/{report}").status_code == 403, report
        assert rep.get("/api/reports/ar-aging").status_code == 200
        stock = rep.get("/api/reports/stock-balance").json()["rows"]
        assert stock and stock[0]["actual_qty"] == 14 and "valuation_rate" not in stock[0], stock
        summary = rep.get("/api/reports/dashboard-summary").json()
        assert "total_revenue" in summary and "total_stock_value" not in summary and "outstanding_payable" not in summary

        r = rep.get("/api/reports/runtime/datasets")
        assert r.status_code == 200, r.text
        datasets = {d["dataset"]: d for d in r.json()["datasets"]}
        assert "purchase_invoices" not in datasets and "gl_entries" not in datasets, sorted(datasets)
        assert "stock_value" not in datasets["stock_balances"]["fields"], datasets["stock_balances"]
        r = rep.post("/api/reports/runtime/data", json={"requests": [{"dataset": "purchase_invoices"}]})
        assert r.status_code == 403, r.text
        r = rep.post("/api/reports/runtime/data", json={"requests": [{"dataset": "stock_balances", "fields": ["stock_value"]}]})
        assert r.status_code == 400 and "stock_value" in r.text, r.text
        metrics = [m["metric"] for m in rep.get("/api/reports/analytics/metrics").json()["metrics"]]
        assert "sales_revenue" in metrics and "purchases" not in metrics and "stock_value" not in metrics, metrics
        assert rep.get("/api/reports/analytics", params={"metric": "purchases", "group_by": "month"}).status_code == 403

        # Record rules: buying prices and supplier payments do not exist for them.
        listed = [row["name"] for row in rep.get("/api/documents/item-price").json()["rows"]]
        assert listed == [sell_price["name"]], listed
        assert rep.get(f"/api/documents/item-price/{buy_price['name']}").status_code == 403
        assert rep.get(f"/api/documents/price-list/{buying['name']}").status_code == 403
        assert [row["name"] for row in rep.get("/api/documents/price-list").json()["rows"]] == [selling["name"]]
        assert rep.get(f"/api/documents/payment-entry/{supplier_payment['name']}").status_code == 403
        assert rep.get("/api/documents/payment-entry").json()["total"] == 0
        assert admin.get("/api/documents/payment-entry").json()["total"] == 1  # the count cache is per visibility
        # Hidden types.
        assert rep.get("/api/documents/journal-entry").status_code == 403
        for path in ("/api/masters/supplier", "/api/masters/supplier/SUP", "/api/masters/supplier/search"):
            assert rep.get(path).status_code == 403, path
        assert rep.get("/api/masters/customer/C").status_code == 200
        # PDFs of documents with hidden fields.
        assert rep.post(f"/api/documents/purchase-order/{po['name']}/pdf").status_code == 403

        # ---- writes: a manager with the profile ---------------------------------
        r = clerk.post("/api/documents/sales-order", json={
            "company": "Test Co", "customer": "C", "transaction_date": "2026-11-12", "delivery_date": "2026-11-30",
            "items": [{**line, "qty": 1, "rate": 20.77}]})
        assert r.status_code == 200, r.text
        clerk_order = r.json()["name"]
        assert clerk.post(f"/api/documents/sales-order/{clerk_order}/submit").status_code == 200
        r = clerk.post("/api/documents/purchase-order", json={
            "company": "Test Co", "supplier": "SUP", "transaction_date": "2026-11-12",
            "items": [{**line, "qty": 1, "rate": 1}]})
        assert r.status_code == 403 and "hides fields of Purchase Order" in r.text, r.text
        r = clerk.put(f"/api/documents/purchase-order/{po['name']}", json={"remarks": "x"})
        assert r.status_code == 403, r.text
        assert clerk.post("/api/masters/supplier", json={"supplier_name": "New"}).status_code == 403
        assert clerk.post("/api/masters/customer", json={"customer_name": "New"}).status_code == 200
        # The same through the tools.
        clerk_principal = auth.make_auth_principal(
            dict(db.get_value("User", clerk_id, ["name", "email", "full_name", "role", "enabled"])), auth.SESSION_CREDENTIAL)
        try:
            mcp._call("create_document", {"doctype": "purchase-order", "data": {
                "company": "Test Co", "supplier": "SUP", "items": [{**line, "qty": 1, "rate": 1}]}}, clerk_principal)
            raise AssertionError("a restricted manager created a purchase order through a tool")
        except PermissionDenied as exc:
            assert "hides fields of Purchase Order" in str(exc), exc

        # ---- per-action permissions ----------------------------------------------
        r = admin.post("/api/auth/access-profiles", json={
            "name": "Counter", "permissions": {"default": ["read"], "doctypes": {
                "Sales Order": ["read", "create", "update"], "Quotation": []}}})
        assert r.status_code == 200, r.text
        assert admin.put(f"/api/auth/users/{clerk_id}/access-profile", json={"access_profile": "Counter"}).status_code == 200
        r = clerk.post("/api/documents/sales-order", json={
            "company": "Test Co", "customer": "C", "transaction_date": "2026-11-12", "delivery_date": "2026-11-30",
            "items": [{**line, "qty": 1, "rate": 20.77}]})
        assert r.status_code == 200, r.text
        r = clerk.post(f"/api/documents/sales-order/{r.json()['name']}/submit")
        assert r.status_code == 403 and "submit Sales Order" in r.text, r.text
        assert clerk.post(f"/api/documents/sales-order/{so['name']}/cancel").status_code == 403
        assert clerk.get("/api/documents/quotation").status_code == 403
        assert clerk.get("/api/documents/delivery-note").status_code == 200
        r = clerk.post("/api/documents/delivery-note", json={"company": "Test Co", "customer": "C", "items": [line]})
        assert r.status_code == 403, r.text
        # Without hidden classes this profile sees prices again.
        assert clerk.get(f"/api/documents/purchase-order/{po['name']}").json()["grand_total"] == 960.83

        # ---- every credential of the user carries the profile --------------------
        assert admin.put("/api/auth/settings", json={"rest_api_enabled": "1", "chat_api_enabled": "1"}).status_code == 200
        assert admin.put(f"/api/auth/users/{clerk_id}/access-profile", json={"access_profile": "Field"}).status_code == 200
        key = clerk.post("/api/auth/api-keys", json={"name": "clerk key", "role": "manager"})
        assert key.status_code == 200, key.text
        headers = {"Authorization": f"Bearer {key.json()['token']}"}
        admin_key = admin.post("/api/auth/api-keys", json={"name": "admin key", "role": "admin"}).json()
        admin_headers = {"Authorization": f"Bearer {admin_key['token']}"}
        with TestClient(app) as keyed:
            r = keyed.get(f"/api/documents/purchase-order/{po['name']}", headers=headers)
            assert r.status_code == 200 and "grand_total" not in r.json(), r.text[:300]
            assert keyed.get("/api/reports/general-ledger", headers=headers).status_code == 403
            # The chat API's own document routes (a separate authentication path).
            for slug, doc_names in names.items():
                for name in doc_names:
                    r = keyed.get(f"/api/v1/documents/{slug}/{name}", headers=headers)
                    assert not [s for s in SECRETS if s in r.text], (slug, name, r.text[:300])
            r = keyed.get(f"/api/v1/documents/purchase-order/{po['name']}", headers=admin_headers)
            assert r.status_code == 200 and "960.83" in r.text, r.text[:300]
            r = keyed.get(f"/api/v1/documents/purchase-order/{po['name']}", headers=headers)
            assert r.status_code == 200 and "960.83" not in r.text and '"qty"' in r.text, r.text[:300]
            # MCP over its real transport.
            def rpc(tool, arguments, h):
                return keyed.post("/api/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": arguments}}).text
            got = rpc("get_document", {"doctype": "purchase-order", "name": po["name"]}, admin_headers)
            assert "960.83" in got, got[:300]
            got = rpc("get_document", {"doctype": "purchase-order", "name": po["name"]}, headers)
            assert "960.83" not in got and "73.91" not in got and "SUP" in got, got[:300]
            assert "does not allow" in rpc("get_report", {"report_type": "general-ledger"}, headers)

        # ---- fail closed -----------------------------------------------------------
        db.sql('UPDATE "User" SET access_profile = ? WHERE name = ?', ["Gone", rep_id])
        db.commit()
        assert rep.get("/api/documents/sales-order").status_code == 403, "a missing profile must lock the user out"
        assert rep.get("/api/auth/me").json()["restrictions"]["profile"] == "Gone"
        db.sql('UPDATE "User" SET access_profile = ? WHERE name = ?', ["Field", rep_id])
        db.commit()
        assert admin.put("/api/auth/access-profiles/Field", json={"hidden_classes": ["purchase_price"], "disabled": True}).status_code == 200
        assert rep.get("/api/documents/sales-order").status_code == 403, "a disabled profile must lock the user out"

        # ---- business logic without a principal is not restricted ------------------
        assert permissions.current_principal() is None
        assert services.load_document("purchase-order", po["name"])["grand_total"] == 960.83
        locked = {"restrictions": permissions.load_restrictions("Gone")}
        with permissions.acting_as(locked):
            try:
                services.load_document("purchase-order", po["name"])
                raise AssertionError("locked-out principal read a document")
            except PermissionDenied:
                pass

        # ---- classification is complete ---------------------------------------------
        for doctype, policy in permissions.SENSITIVE_FIELDS.items():
            cls = services.DOCUMENT_CLASSES.get(doctype)
            tables = [("", doctype)] + [(key, value[0]) for key, value in (cls.CHILD_TABLES if cls else {}).items()]
            for key, table in tables:
                numeric = db._get_table_columns(table) - db._get_text_columns(table)
                unclassified = numeric - set(policy.get(key, {})) - SAFE_NUMERIC
                if doctype != "Asset":
                    assert not unclassified, (
                        f"{doctype} {key or '(document)'}: numeric column(s) {sorted(unclassified)} are neither "
                        f"classified in permissions.SENSITIVE_FIELDS nor known to be harmless")

    print("All access profile checks passed.")


if __name__ == "__main__":
    try:
        check_access_profiles()
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
