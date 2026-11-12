"""Record scope: a user restricted to assigned customers or warehouses gets
nothing of the others over any supported path, and cannot write outside.

Like the access profile suite this is a walk: every REST read route and every
chat / MCP read tool is called as a restricted user, and no answer may name a
record outside the user's scope or contain one of its amounts. The same walk
as an admin must contain them, which proves the walk reaches the data.

Synthetic data only. Run:
    python -m tests.test_record_scope
    LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_record_scope
"""
import json
import os
import sys

# Everything outside the scope is named ZZOUT...; its sales are priced 913.77
# and its stock was received at 571.13, figures nothing inside produces.
OUTSIDE = "ZZOUT"
OUTSIDE_SALE, OUTSIDE_COST = "913.77", "571.13"
INSIDE_SALE = "20.77"


def check_record_scope():
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
    from api import auth, services
    from lambda_erp import permissions
    from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
    from lambda_erp.database import get_db
    from lambda_erp.exceptions import PermissionDenied
    from lambda_erp.utils import now
    from tests.access_walk import build_walks

    print(f"Record scope checks ({backend})")

    with TestClient(app) as admin:
        r = admin.post("/api/auth/register", json={"email": "admin@example.com", "full_name": "Admin",
                                                    "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]
        db = get_db()

        # ---- data: the same business twice, inside and outside ----------------
        db.insert("Company", {"name": "Test Co", "company_name": "Test Co", "default_currency": "USD"})
        setup_chart_of_accounts("Test Co", "USD")
        setup_cost_center("Test Co")
        db.insert("Supplier", {"name": "SUP", "supplier_name": "Supplier", "default_currency": "USD"})
        db.insert("Item", {"name": "S", "item_name": "Stock item", "is_stock_item": 1, "standard_rate": 20.77})
        db.insert("Item", {"name": "D", "item_name": "Device", "is_stock_item": 1, "standard_rate": 20.77,
                           "has_serial_no": 1})
        sides = {"in": ("C-IN", "Inside Customer", "W-IN", "CON-IN", 20.77, 7.5),
                 "out": (f"{OUTSIDE}-CUST", f"{OUTSIDE} Customer", f"{OUTSIDE}-WH", f"{OUTSIDE}-CON", 913.77, 571.13)}
        for customer, customer_name, warehouse, consignment, _, _ in sides.values():
            db.insert("Customer", {"name": customer, "customer_name": customer_name, "default_currency": "USD"})
            db.insert("Warehouse", {"name": warehouse, "warehouse_name": warehouse, "company": "Test Co"})
            db.insert("Warehouse", {"name": consignment, "warehouse_name": consignment, "company": "Test Co",
                                    "consignment_customer": customer, "replenishment_warehouse": warehouse})
        db.commit()

        def post(slug, data):
            doc = services.create_document(slug, data)
            return services.submit_document(slug, doc["name"])

        receivable = db.get_value("Company", "Test Co", "default_receivable_account")
        bank = db.get_value("Account", {"company": "Test Co", "account_type": "Bank", "is_group": 0}, "name") or \
            db.get_value("Account", {"company": "Test Co", "account_type": "Cash", "is_group": 0}, "name")
        docs = {}
        for side, (customer, _, warehouse, consignment, price, cost) in sides.items():
            made = docs[side] = {}
            made["receipt"] = post("purchase-receipt", {
                "company": "Test Co", "supplier": "SUP", "posting_date": "2026-11-12",
                "items": [{"item_code": "S", "qty": 20, "rate": cost, "warehouse": warehouse},
                          {"item_code": "D", "qty": 1, "rate": cost, "warehouse": warehouse,
                           "serial_no": f"SN-{side.upper()}-1"}]})
            made["stocking"] = post("stock-entry", {
                "company": "Test Co", "stock_entry_type": "Material Transfer", "posting_date": "2026-11-12",
                "items": [{"item_code": "S", "qty": 4, "s_warehouse": warehouse, "t_warehouse": consignment}]})
            made["quotation"] = services.create_document("quotation", {
                "company": "Test Co", "customer": customer, "transaction_date": "2026-11-12",
                "items": [{"item_code": "S", "qty": 1, "rate": price, "warehouse": warehouse}]})
            made["order"] = post("sales-order", {
                "company": "Test Co", "customer": customer, "transaction_date": "2026-11-12",
                "delivery_date": "2026-11-30", "items": [{"item_code": "S", "qty": 5, "rate": price,
                                                          "warehouse": warehouse}]})
            made["invoice"] = post("sales-invoice", {
                "company": "Test Co", "customer": customer, "posting_date": "2026-11-12", "update_stock": 1,
                "items": [{"item_code": "S", "qty": 2, "rate": price, "warehouse": warehouse}]})
            made["payment"] = post("payment-entry", {
                "company": "Test Co", "payment_type": "Receive", "party_type": "Customer", "party": customer,
                "paid_from": receivable, "paid_to": bank, "paid_amount": price, "posting_date": "2026-11-12",
                "references": [{"reference_doctype": "Sales Invoice", "reference_name": made["invoice"]["name"],
                                "allocated_amount": price}]})
            made["count"] = services.create_document("stock-count", {
                "warehouse": warehouse, "items": [{"item_code": "S", "counted_qty": 14}]})
        inside, outside = docs["in"], docs["out"]

        def make_user(email, role):
            name = f"U-{email.split('@')[0]}"
            db.insert("User", {"name": name, "email": email, "full_name": email, "role": role, "enabled": 1,
                               "hashed_password": auth.hash_password("test-password-123"),
                               "creation": now(), "modified": now()})
            db.commit()
            return name

        def login(email):
            client = TestClient(app)
            r = client.post("/api/auth/login", json={"email": email, "password": "test-password-123"})
            assert r.status_code == 200, r.text
            return client

        rep_id, keeper_id, both_id, plain_id = (make_user(f"{who}@example.com", "manager")
                                                for who in ("rep", "keeper", "both", "plain"))
        me_admin = admin.get("/api/auth/me").json()

        # ---- assigning a scope -------------------------------------------------
        def assign(user_id, scope, expect=200):
            r = admin.put(f"/api/auth/users/{user_id}/record-scope", json={"record_scope": scope})
            assert r.status_code == expect, (scope, r.status_code, r.text)
            return r

        assert "Unknown scope dimension" in assign(rep_id, {"colour": ["red"]}, 422).text
        assert "Unknown Customer: NOPE" in assign(rep_id, {"customer": ["C-IN", "NOPE"]}, 422).text
        assign(rep_id, {"customer": "C-IN"}, 422)
        assign(me_admin["name"], {"customer": ["C-IN"]}, 409)
        assign("U-nobody", {"customer": ["C-IN"]}, 404)
        assign(rep_id, {"customer": ["C-IN"]})
        assign(keeper_id, {"warehouse": ["W-IN", "CON-IN"]})
        assign(both_id, {"customer": ["C-IN"], "warehouse": ["W-IN"]})
        listed = {u["name"]: u for u in admin.get("/api/auth/users").json()}
        assert listed[rep_id]["record_scope"] == {"customer": ["C-IN"]} and listed[plain_id]["record_scope"] is None

        rep, keeper, both, plain = (login(f"{who}@example.com") for who in ("rep", "keeper", "both", "plain"))
        assert rep.put(f"/api/auth/users/{rep_id}/record-scope", json={"record_scope": None}).status_code == 403
        me = rep.get("/api/auth/me").json()
        assert me["restrictions"]["record_scope"] == {"customer": ["C-IN"]}, me
        assert me["restrictions"]["hidden_classes"] == [] and me["access_profile"] is None, me
        assert plain.get("/api/auth/me").json()["restrictions"] is None

        # ---- the walk ----------------------------------------------------------
        def extra_rest(get):
            for report in ("stock-balance", "stock-availability", "stock-detail", "batch-stock", "sales-backlog",
                           "purchase-backlog", "ar-aging", "ap-aging"):
                for side in sides.values():
                    get(f"/api/reports/{report}", warehouse=side[2])
            for side in sides.values():
                get("/api/reports/sales-backlog", customer=side[0])
                get("/api/reports/serial-trace", serial_no=f"SN-{'IN' if side[0] == 'C-IN' else 'OUT'}-1")
                get("/api/proposals/cover-default", company="Test Co", customer=side[0])

        def extra_tools(call):
            for side in sides.values():
                call("search_masters", {"master_type": "customer", "query": side[1].split()[0]})
                call("search_masters", {"master_type": "warehouse", "query": side[2]})
                call("get_report", {"report_type": "stock-detail", "filters": {"warehouse": side[3]}})
                call("get_report", {"report_type": "serial-trace",
                                    "filters": {"serial_no": f"SN-{'IN' if side[0] == 'C-IN' else 'OUT'}-1"}})

        rest_walk, tool_walk = build_walks(db, all_pdfs=True, extra_rest=extra_rest, extra_tools=extra_tools)

        def leaks(seen, secrets):
            found = []
            for what, status, text in seen:
                asked_for_it = OUTSIDE in what
                for secret in secrets:
                    if secret in text and not (secret.startswith(OUTSIDE) and asked_for_it and
                                               (status is True or (isinstance(status, int) and status >= 400))):
                        found.append((what, secret))
            return found

        admin_rest, admin_tools = rest_walk(admin), tool_walk(me_admin["name"])
        for label, seen in (("REST", admin_rest), ("tools", admin_tools)):
            text = "\n".join(entry[2] for entry in seen)
            missing = [secret for secret in (OUTSIDE, OUTSIDE_SALE, OUTSIDE_COST) if secret not in text]
            assert not missing, f"the {label} walk never reaches {missing}: it cannot prove anything about them"
        assert leaks(rest_walk(plain), (OUTSIDE,)), "an unrestricted user should still see every customer"

        # A customer-restricted user still sees stock in the company's own
        # warehouses (so the cost of goods received there), never another
        # customer or their sales. A warehouse-restricted user still sees every
        # customer and what they paid, never another warehouse or what lies or
        # moved there. Restricted in both, a user sees neither.
        for who, client, user_id, secrets in (("rep", rep, rep_id, (OUTSIDE + "-CUST", OUTSIDE + " Customer",
                                                                    OUTSIDE + "-CON", OUTSIDE_SALE)),
                                              ("keeper", keeper, keeper_id, (OUTSIDE + "-WH", OUTSIDE + "-CON", OUTSIDE_COST)),
                                              ("both", both, both_id, (OUTSIDE, OUTSIDE_SALE, OUTSIDE_COST))):
            rest_seen, tool_seen = rest_walk(client), tool_walk(user_id)
            found = leaks(rest_seen, secrets) + leaks(tool_seen, secrets)
            assert not found, f"{who}: {len(found)} leak(s), first: {found[:6]}"
            failed = [entry[:2] for entry in rest_seen if entry[1] >= 500]
            assert not failed, f"{who}: server errors {failed[:5]}"
            assert any(INSIDE_SALE in text for _, _, text in rest_seen), f"{who} lost the assigned customer's sales"
            assert any(INSIDE_SALE in text for _, _, text in tool_seen), f"{who} lost them in tools"
        print(f"  [ok] nothing outside the scope over {len(admin_rest)} REST calls and {len(admin_tools)} tool calls")

        # ---- reading -----------------------------------------------------------
        def names(client, path, **params):
            r = client.get(path, params=params or None)
            assert r.status_code == 200, (path, r.status_code, r.text[:300])
            body = r.json()
            rows = body.get("data") or body.get("rows") or body.get("items") or body.get("results") or [] \
                if isinstance(body, dict) else body
            return sorted(row.get("name") or row.get("warehouse") or "" for row in rows)

        assert names(rep, "/api/masters/customer") == ["C-IN"]
        assert names(rep, "/api/masters/warehouse") == ["CON-IN", "W-IN", f"{OUTSIDE}-WH"]
        assert names(keeper, "/api/masters/warehouse") == ["CON-IN", "W-IN"]
        assert names(both, "/api/masters/warehouse") == ["W-IN"]
        assert names(rep, "/api/documents/sales-order") == [inside["order"]["name"]]
        assert names(keeper, "/api/documents/sales-order") == [inside["order"]["name"]]
        assert names(keeper, "/api/documents/stock-entry") == [inside["stocking"]["name"]]
        # CON-IN is not assigned to `both`, so the transfer into it is not theirs.
        assert names(both, "/api/documents/stock-entry") == []
        assert names(rep, "/api/documents/payment-entry") == [inside["payment"]["name"]]
        for client in (rep, keeper, both):
            r = client.get(f"/api/documents/sales-order/{outside['order']['name']}")
            assert r.status_code == 403, r.text
            assert client.get(f"/api/documents/sales-order/{inside['order']['name']}").status_code == 200
            assert client.get(f"/api/documents/sales-invoice/{outside['invoice']['name']}/pdf").status_code in (403, 404)
        assert rep.get(f"/api/masters/customer/{OUTSIDE}-CUST").status_code == 403
        assert keeper.get(f"/api/masters/warehouse/{OUTSIDE}-WH").status_code == 403
        assert rep.get(f"/api/masters/warehouse/{OUTSIDE}-CON").status_code == 403
        count = rep.get("/api/documents/sales-order", params={"limit": 1}).json()
        assert count.get("total", 1) == 1, count

        # ---- reports, datasets, metrics ----------------------------------------
        stock = keeper.get("/api/reports/stock-balance").json()["rows"]
        assert stock and {row["warehouse"] for row in stock} == {"W-IN", "CON-IN"}, stock
        stock = rep.get("/api/reports/stock-balance").json()["rows"]
        assert {row["warehouse"] for row in stock} == {"W-IN", "CON-IN", f"{OUTSIDE}-WH"}, stock
        aging = rep.get("/api/reports/ar-aging").json()
        assert {row["customer"] for row in aging["rows"]} <= {"C-IN"}, aging
        assert keeper.get("/api/reports/ar-aging").status_code == 200  # receivables are not per warehouse
        backlog = rep.get("/api/reports/sales-backlog").json()["rows"]
        assert backlog and {row["customer"] for row in backlog} == {"C-IN"}, backlog
        for client in (rep, keeper, both):
            for report in ("trial-balance", "general-ledger", "profit-and-loss", "balance-sheet", "dashboard-summary"):
                r = client.get(f"/api/reports/{report}")
                assert r.status_code == 403 and "cannot be limited" in r.text, (report, r.status_code, r.text)
            assert client.get("/api/reports/batch-movements", params={"batch_no": "X"}).status_code == 403
            assert client.get("/api/reports/serial-trace", params={"serial_no": "SN-IN-1"}).status_code == 403
        r = rep.post("/api/reports/runtime/data", json={"requests": [{"dataset": "sales_invoices", "limit": 500}]})
        assert r.status_code == 200 and "C-IN" in r.text and OUTSIDE not in r.text, r.text[:400]
        r = rep.post("/api/reports/runtime/data", json={"requests": [{"dataset": "gl_entries"}]})
        assert r.status_code == 403, r.text
        datasets = {d["dataset"] for d in rep.get("/api/reports/runtime/datasets").json()["datasets"]}
        assert "sales_invoices" in datasets and "gl_entries" not in datasets and "bank_transactions" not in datasets
        metrics = [m["metric"] for m in rep.get("/api/reports/analytics/metrics").json()["metrics"]]
        assert "sales_revenue" not in metrics and "purchases" in metrics, metrics
        assert rep.get("/api/reports/analytics", params={"metric": "sales_revenue", "group_by": "customer"}).status_code == 403
        # An aggregate cannot be used to add up what is outside.
        from api.routers import mcp
        principal = auth.make_auth_principal(
            dict(db.get_value("User", rep_id, ["name", "email", "full_name", "role", "enabled"])), auth.SESSION_CREDENTIAL)
        total = json.dumps(mcp._call("query_dataset", {"dataset": "sales_invoices",
                                                       "measures": {"x": ["sum", "grand_total"]}}, principal))
        assert "41.54" in total and "1827" not in total and "1869" not in total, total

        # ---- writing -----------------------------------------------------------
        def order(customer, warehouse):
            return {"company": "Test Co", "customer": customer, "transaction_date": "2026-11-12",
                    "delivery_date": "2026-11-30", "items": [{"item_code": "S", "qty": 1, "rate": 20.77,
                                                              "warehouse": warehouse}]}

        r = rep.post("/api/documents/sales-order", json=order(f"{OUTSIDE}-CUST", "W-IN"))
        assert r.status_code == 403 and "assigned Customer" in r.text, (r.status_code, r.text)
        r = keeper.post("/api/documents/sales-order", json=order("C-IN", f"{OUTSIDE}-WH"))
        assert r.status_code == 403 and "assigned Warehouse" in r.text, (r.status_code, r.text)
        r = rep.post("/api/documents/sales-order", json=order("C-IN", "W-IN"))
        assert r.status_code == 200, r.text
        mine = r.json()["name"]
        r = rep.put(f"/api/documents/sales-order/{mine}", json={**order(f"{OUTSIDE}-CUST", "W-IN")})
        assert r.status_code == 403, r.text
        assert db.get_value("Sales Order", mine, "customer") == "C-IN"
        r = keeper.put(f"/api/documents/sales-order/{mine}", json={"items": [
            {"item_code": "S", "qty": 1, "rate": 20.77, "warehouse": f"{OUTSIDE}-WH"}]})
        assert r.status_code == 403, r.text
        assert rep.put(f"/api/documents/sales-order/{outside['quotation']['name']}", json={"remarks": "x"}).status_code in (403, 404)
        assert rep.post(f"/api/documents/sales-order/{outside['order']['name']}/cancel").status_code == 403
        assert rep.post(f"/api/documents/quotation/{outside['quotation']['name']}/convert",
                        json={"target_doctype": "Sales Order"}).status_code == 403
        # Goods cannot be moved into, or counted in, a place that is not assigned.
        move = {"company": "Test Co", "stock_entry_type": "Material Transfer", "posting_date": "2026-11-12",
                "items": [{"item_code": "S", "qty": 1, "s_warehouse": "W-IN", "t_warehouse": f"{OUTSIDE}-WH"}]}
        assert keeper.post("/api/documents/stock-entry", json=move).status_code == 403
        move["items"][0]["t_warehouse"] = f"{OUTSIDE}-CON"
        r = rep.post("/api/documents/stock-entry", json=move)
        assert r.status_code == 403 and "assigned Customer" in r.text, (r.status_code, r.text)
        move["items"][0]["t_warehouse"] = "CON-IN"
        assert keeper.post("/api/documents/stock-entry", json=move).status_code == 200
        r = keeper.post("/api/actions/prepare_stock_count", json={"warehouse": f"{OUTSIDE}-WH"})
        assert r.status_code == 403, (r.status_code, r.text)
        r = keeper.post("/api/actions/suggest_picks", json={"item_code": "S", "warehouse": f"{OUTSIDE}-WH", "qty": 1})
        assert r.status_code == 403, (r.status_code, r.text)
        r = rep.post("/api/actions/explain_price", json={"doctype": "sales-order", "item_code": "S",
                                                         "customer": f"{OUTSIDE}-CUST"})
        assert r.status_code == 403, (r.status_code, r.text)
        # The assigned records themselves cannot be extended by the user.
        r = rep.post("/api/masters/customer", json={"customer_name": "Another"})
        assert r.status_code == 403, (r.status_code, r.text)
        r = keeper.post("/api/masters/warehouse", json={"warehouse_name": "Mine too", "company": "Test Co"})
        assert r.status_code == 403, (r.status_code, r.text)
        assert rep.put("/api/masters/customer/C-IN", json={"customer_name": "Inside Customer AG"}).status_code == 200
        assert keeper.post("/api/masters/storage-location",
                           json={"warehouse": f"{OUTSIDE}-WH", "location": "A-01"}).status_code == 403

        # ---- every credential of the user carries the scope ----------------------
        assert admin.put("/api/auth/settings", json={"rest_api_enabled": "1", "chat_api_enabled": "1"}).status_code == 200
        key = rep.post("/api/auth/api-keys", json={"name": "rep key", "role": "manager"})
        assert key.status_code == 200, key.text
        headers = {"Authorization": f"Bearer {key.json()['token']}"}
        with TestClient(app) as keyed:
            assert keyed.get(f"/api/documents/sales-order/{inside['order']['name']}", headers=headers).status_code == 200
            assert keyed.get(f"/api/documents/sales-order/{outside['order']['name']}", headers=headers).status_code == 403
            for slug, doctype in services.SLUG_TO_DOCTYPE.items():
                for row in db.sql(f'SELECT name FROM "{doctype}"'):
                    r = keyed.get(f"/api/v1/documents/{slug}/{row['name']}", headers=headers)
                    assert f"{OUTSIDE}-CUST" not in r.text and OUTSIDE_SALE not in r.text, (slug, row["name"], r.text[:300])
            r = keyed.get(f"/api/v1/documents/sales-order/{inside['order']['name']}", headers=headers)
            assert r.status_code == 200 and "C-IN" in r.text, r.text[:300]

        # ---- outside a request, and when the scope cannot be read ---------------
        principal_keeper = auth.make_auth_principal(
            dict(db.get_value("User", keeper_id, ["name", "email", "full_name", "role", "enabled"])),
            auth.SESSION_CREDENTIAL)
        with permissions.acting_as(principal_keeper):
            try:
                services.load_document("sales-order", outside["order"]["name"])
                raise AssertionError("a warehouse-restricted principal read an order outside")
            except PermissionDenied:
                pass
            assert permissions.scope_sql("warehouse", "w.name") and permissions.scope_sql("customer", "c.name") is None
        assert services.load_document("sales-order", outside["order"]["name"])["customer"] == f"{OUTSIDE}-CUST"

        db.set_value("User", plain_id, {"record_scope": "not json"})
        db.commit()
        broken = login("plain@example.com")
        assert broken.get("/api/documents/sales-order").status_code == 403
        assert broken.get("/api/masters/item").status_code == 403
        assign(plain_id, None)
        assert login("plain@example.com").get("/api/documents/sales-order").status_code == 200
        # An empty list is not "everything": it is nothing.
        assign(plain_id, {"customer": []})
        nothing = login("plain@example.com")
        assert names(nothing, "/api/masters/customer") == [] and names(nothing, "/api/documents/sales-order") == []
        assert names(nothing, "/api/masters/item") != []
        print("  [ok] reads, reports, datasets and writes stay inside the assigned records")

        # ---- stored content: conversations and report drafts ----------------------
        from api import chat
        from lambda_erp.permissions import stamp_within

        def opens(client, session):
            # The route answers 200 with a "not found" detail for a session that is not the caller's.
            body = client.get(f"/api/chat/sessions/{session['id']}").json()
            return body.get("id") == session["id"]

        assign(plain_id, None)
        free = login("plain@example.com")
        old_session = free.post("/api/chat/sessions").json()
        assert "access_stamp" not in old_session, old_session
        chat.save_chat_message(old_session["id"], "assistant", f"{OUTSIDE} Customer bought for {OUTSIDE_SALE}")
        draft = {"title": "Sales", "description": f"{OUTSIDE} Customer leads with {OUTSIDE_SALE}",
                 "data_requests": [{"name": "sales", "dataset": "sales_invoices", "fields": ["customer", "net_total"]}],
                 "report": {"version": 1, "tables": [{
                     "id": "t", "title": "Sales", "source": "sales", "dimensions": [{"field": "customer"}],
                     "measures": [{"key": "revenue", "op": "sum", "field": "net_total", "type": "currency"}],
                     "columns": [{"key": "customer", "label": "Customer", "type": "string"},
                                 {"key": "revenue", "label": "Revenue", "type": "currency"}]}]}}
        r = free.post("/api/reports/runtime/drafts", json=draft)
        assert r.status_code == 200, r.text
        old_draft = r.json()["id"]
        assert opens(free, old_session)
        # Now the user is restricted: what they were shown before is not given back.
        assign(plain_id, {"customer": ["C-IN"]})
        limited = login("plain@example.com")
        assert not opens(limited, old_session)
        assert old_session["id"] not in [row["id"] for row in limited.get("/api/chat/sessions").json()]
        r = limited.get(f"/api/reports/runtime/drafts/{old_draft}")
        assert r.status_code == 403 and "before your access was restricted" in r.text, (r.status_code, r.text)
        listed_drafts = limited.get("/api/reports/runtime/drafts")
        assert old_draft not in listed_drafts.text and OUTSIDE not in listed_drafts.text, listed_drafts.text
        # What they write from now on stays theirs, also if the scope grows.
        new_session = limited.post("/api/chat/sessions").json()
        assert opens(limited, new_session)
        r = limited.post("/api/reports/runtime/drafts", json={**draft, "description": "mine"})
        assert r.status_code == 200, r.text
        new_draft = r.json()["id"]
        assign(plain_id, {"customer": ["C-IN", f"{OUTSIDE}-CUST"]})
        wider = login("plain@example.com")
        assert opens(wider, new_session)
        assert wider.get(f"/api/reports/runtime/drafts/{new_draft}").status_code == 200
        assert not opens(wider, old_session)
        # ... and not if it shrinks or changes.
        assign(plain_id, {"customer": []})
        assert not opens(login("plain@example.com"), new_session)
        # The admin still reads all of it, and lifting the restriction gives it back.
        assert opens(admin, old_session)
        assert admin.get(f"/api/reports/runtime/drafts/{old_draft}").status_code == 200
        assign(plain_id, None)
        assert opens(login("plain@example.com"), old_session)
        # The comparison itself.
        narrow = json.dumps({"hidden": ["cost", "margin"], "permissions": {"default": None, "doctypes": {}},
                             "scope": {"customer": ["C-IN"]}})
        wide = json.dumps({"hidden": ["margin"], "permissions": {"default": None, "doctypes": {}}, "scope": {}})
        other = json.dumps({"hidden": [], "permissions": {"default": None, "doctypes": {"Item": []}}, "scope": {}})
        assert stamp_within(narrow, wide) and not stamp_within(wide, narrow)
        assert stamp_within(narrow, None) and not stamp_within(None, narrow) and stamp_within(None, None)
        assert not stamp_within(wide, other) and not stamp_within(other, wide)
        assert stamp_within("not json", None) and not stamp_within(wide, "not json")
        print("  [ok] conversations and drafts from before a restriction are not given back")

        # ---- every document and master type is declared, or known to be free ----
        unrelated = {"customer": {"Purchase Order", "Purchase Receipt", "Purchase Invoice", "Budget", "Price List",
                                  "Bank Account", "Account", "Batch", "Company", "Cost Center", "Item", "Supplier"},
                     "warehouse": {"Proposal", "Payment Entry", "Subscription", "Journal Entry", "Budget", "Price List",
                                   "Item Price", "Pricing Rule", "Bank Account", "Bank Transaction", "Account", "Batch",
                                   "Company", "Cost Center", "Customer", "Item", "Supplier"}}
        tables = set(services.SLUG_TO_DOCTYPE.values()) | {table for table, _ in services.MASTER_TABLES.values()}
        for dimension in ("customer", "warehouse"):
            for table in sorted(tables):
                declared = dimension in permissions.RECORD_SCOPES.get(table, {})
                assert declared or table in unrelated[dimension], (
                    f"{table} says nothing about the {dimension} scope: declare it with "
                    f"permissions.register_record_scope or list it here as unrelated")

    print("All record scope checks passed.")


if __name__ == "__main__":
    try:
        check_record_scope()
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
