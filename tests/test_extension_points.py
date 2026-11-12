"""Extension points a deployment's plugin uses: required migrations, its own
reports and datasets, stock ledger hooks, batch attributes, shipped access
profiles. Each is exercised the way a plugin would use it.

Synthetic data only. Run:
    python -m tests.test_extension_points
    LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_extension_points
"""
import json
import os
import sys


def check_extension_points():
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
    from lambda_erp import hooks, permissions
    from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
    from lambda_erp.database import get_db
    from lambda_erp.exceptions import PermissionDenied, ValidationError
    from lambda_erp.utils import now

    print(f"Extension point checks ({backend})")

    with TestClient(app) as admin:
        r = admin.post("/api/auth/register", json={"email": "admin@example.com", "full_name": "Admin",
                                                    "password": "test-password-123"})
        assert r.status_code == 200, r.text[:300]
        db = get_db()
        db.insert("Company", {"name": "Test Co", "company_name": "Test Co", "default_currency": "USD"})
        setup_chart_of_accounts("Test Co", "USD")
        setup_cost_center("Test Co")
        db.insert("Supplier", {"name": "SUP", "supplier_name": "Supplier", "default_currency": "USD"})
        for customer in ("C-1", "C-2"):
            db.insert("Customer", {"name": customer, "customer_name": customer, "default_currency": "USD"})
        for warehouse in ("A", "B"):
            db.insert("Warehouse", {"name": warehouse, "warehouse_name": warehouse, "company": "Test Co"})
        db.insert("Item", {"name": "L", "item_name": "Lot item", "is_stock_item": 1, "standard_rate": 10,
                           "has_batch_no": 1})
        db.commit()

        def make_user(email, **fields):
            name = f"U-{email.split('@')[0]}"
            db.insert("User", {"name": name, "email": email, "full_name": email, "role": "manager", "enabled": 1,
                               "hashed_password": auth.hash_password("test-password-123"),
                               "creation": now(), "modified": now(), **fields})
            db.commit()
            client = TestClient(app)
            assert client.post("/api/auth/login", json={"email": email, "password": "test-password-123"}).status_code == 200
            return name, client

        def principal(user_id):
            return auth.make_auth_principal(
                dict(db.get_value("User", user_id, ["name", "email", "full_name", "role", "enabled"])),
                auth.SESSION_CREDENTIAL)

        # ---- migrations: optional ones retry, required ones stop the start ------
        saved = list(services._PLUGIN_MIGRATIONS)
        ran = []
        try:
            services._PLUGIN_MIGRATIONS.clear()

            def broken(_db):
                _db.ensure_column("Batch", "half_done", "TEXT")
                raise RuntimeError("synthetic failure")

            services.register_migration("test:0001_optional_broken", broken)
            services.register_migration("test:0002_after", lambda _db: ran.append("after"))
            services.apply_plugin_schema()  # logs, continues
            assert ran == ["after"]
            applied = {row["migration_id"] for row in db.sql('SELECT migration_id FROM "_PluginMigrations"')}
            assert "test:0002_after" in applied and "test:0001_optional_broken" not in applied, applied

            services._PLUGIN_MIGRATIONS.clear()
            services.register_migration("test:0003_required_broken", broken, required=True)
            services.register_migration("test:0004_never", lambda _db: ran.append("never"))
            try:
                services.apply_plugin_schema()
                raise AssertionError("a failing required migration did not stop the start")
            except RuntimeError as exc:
                assert "test:0003_required_broken" in str(exc), exc
            assert "never" not in ran
            applied = {row["migration_id"] for row in db.sql('SELECT migration_id FROM "_PluginMigrations"')}
            assert not applied & {"test:0003_required_broken", "test:0004_never"}, applied

            # A batch attribute of the deployment's own.
            services._PLUGIN_MIGRATIONS.clear()
            services.register_migration("test:0005_certificate",
                                        lambda _db: _db.ensure_column("Batch", "certificate_no", "TEXT"), required=True)
            services.apply_plugin_schema()
            services.apply_plugin_schema()  # once only
        finally:
            services._PLUGIN_MIGRATIONS[:] = saved
        assert "certificate_no" in db._get_table_columns("Batch")
        print("  [ok] a required migration stops the start; an optional one is retried")

        # ---- stock ledger hooks and batch attributes ----------------------------
        seen = []

        def before_post(entry):
            seen.append((entry["item_code"], entry["warehouse"], entry["actual_qty"], entry["batch_no"]))
            if entry["warehouse"] == "B" and entry["actual_qty"] > 0 and entry["batch_no"] == "LOT-QUARANTINE":
                raise ValidationError("Synthetic rule: LOT-QUARANTINE may not be put into warehouse B")

        def batch_created(batch, entry):
            db.set_value("Batch", batch["name"], {"certificate_no": f"CERT-{batch['batch_no']}"})

        def stock_rows(rows):
            for row in rows:
                row["certificate_no"] = db.get_value(
                    "Batch", {"item_code": row["item_code"], "batch_no": row["batch_no"]}, "certificate_no") \
                    if row.get("batch_no") else None

        hooks.register_hook("Stock Ledger Entry:before_post", before_post)
        hooks.register_hook("Batch:created", batch_created)
        hooks.register_hook("Report:stock-detail", stock_rows)
        try:
            def post(slug, data):
                doc = services.create_document(slug, data)
                return services.submit_document(slug, doc["name"])

            post("purchase-receipt", {"company": "Test Co", "supplier": "SUP", "posting_date": "2026-11-12", "items": [
                {"item_code": "L", "qty": 5, "rate": 4, "warehouse": "A", "batch_no": "LOT-1"},
                {"item_code": "L", "qty": 3, "rate": 4, "warehouse": "A", "batch_no": "LOT-QUARANTINE"}]})
            assert ("L", "A", 5, "LOT-1") in seen, seen
            assert db.get_value("Batch", {"item_code": "L", "batch_no": "LOT-1"}, "certificate_no") == "CERT-LOT-1"
            # The rule refuses one leg of a transfer: nothing of the document is posted.
            move = services.create_document("stock-entry", {
                "company": "Test Co", "stock_entry_type": "Material Transfer", "posting_date": "2026-11-12", "items": [
                    {"item_code": "L", "qty": 1, "s_warehouse": "A", "t_warehouse": "B", "batch_no": "LOT-1"},
                    {"item_code": "L", "qty": 1, "s_warehouse": "A", "t_warehouse": "B", "batch_no": "LOT-QUARANTINE"}]})
            try:
                services.submit_document("stock-entry", move["name"])
                raise AssertionError("the stock rule did not refuse the transfer")
            except ValidationError as exc:
                assert "Synthetic rule" in str(exc), exc
            assert db.get_value("Bin", {"item_code": "L", "warehouse": "A"}, "actual_qty") == 8
            assert not db.get_value("Bin", {"item_code": "L", "warehouse": "B"}, "actual_qty")
            assert db.get_value("Stock Entry", move["name"], "docstatus") == 0
            # The attribute is an ordinary field of the batch master, and a column of the stock view.
            batch = db.get_value("Batch", {"item_code": "L", "batch_no": "LOT-1"}, "name")
            r = admin.put(f"/api/masters/batch/{batch}", json={"certificate_no": "CERT-CHANGED"})
            assert r.status_code == 200 and r.json()["certificate_no"] == "CERT-CHANGED", r.text
            rows = admin.get("/api/reports/stock-detail", params={"item_code": "L"}).json()["rows"]
            assert {row["batch_no"]: row["certificate_no"] for row in rows} == \
                {"LOT-1": "CERT-CHANGED", "LOT-QUARANTINE": "CERT-LOT-QUARANTINE"}, rows
        finally:
            for event, fn in (("Stock Ledger Entry:before_post", before_post), ("Batch:created", batch_created),
                              ("Report:stock-detail", stock_rows)):
                hooks._HOOKS[event].remove(fn)
        print("  [ok] stock ledger hooks see and can refuse a movement; batch attributes flow through")

        # ---- a deployment's own report ------------------------------------------
        post("sales-invoice", {"company": "Test Co", "customer": "C-1", "posting_date": "2026-11-12",
                               "items": [{"item_code": "L", "qty": 1, "rate": 30, "warehouse": "A", "batch_no": "LOT-1"}],
                               "update_stock": 1})
        post("sales-invoice", {"company": "Test Co", "customer": "C-2", "posting_date": "2026-11-12",
                               "items": [{"item_code": "L", "qty": 2, "rate": 30, "warehouse": "A", "batch_no": "LOT-1"}],
                               "update_stock": 1})

        def units_per_customer(filters):
            where, params = ["si.docstatus = 1"], []
            if filters.get("customer"):
                where.append("si.customer = ?")
                params.append(filters["customer"])
            assigned = permissions.scope_sql("customer", "si.customer")
            if assigned:
                where.append(assigned)
            rows = db.sql('SELECT si.customer, SUM(sii.qty) AS units FROM "Sales Invoice" si '
                          'JOIN "Sales Invoice Item" sii ON sii.parent = si.name '
                          f'WHERE {" AND ".join(where)} GROUP BY si.customer ORDER BY si.customer', params)
            return {"rows": [dict(row) for row in rows]}

        try:
            try:
                services.register_report("units-per-customer", units_per_customer, description="x")
                raise AssertionError("a report was registered without saying what it shows")
            except ValueError as exc:
                assert "sensitivity" in str(exc), exc
            for bad in ("Units", "stock-balance"):
                try:
                    services.register_report(bad, units_per_customer, description="x", sensitivity=None)
                    raise AssertionError(f"report name {bad} was accepted")
                except ValueError:
                    pass
            services.register_report(
                "units-per-customer", units_per_customer, sensitivity=None,
                description="Units invoiced per customer.", filters={"customer": "one customer"},
                scope={"customer": "customer", "warehouse": None})
            services.register_report("cost-per-unit", lambda filters: {"rows": [{"cost": 4}]},
                                     description="Synthetic cost report.", sensitivity="cost")

            r = admin.get("/api/reports/registered/units-per-customer")
            assert r.status_code == 200 and r.json()["rows"] == [{"customer": "C-1", "units": 1},
                                                                 {"customer": "C-2", "units": 2}], r.text
            assert admin.get("/api/reports/registered/units-per-customer",
                             params={"customer": "C-2"}).json()["rows"] == [{"customer": "C-2", "units": 2}]
            assert admin.get("/api/reports/registered/units-per-customer", params={"colour": "red"}).status_code == 422
            assert admin.get("/api/reports/registered/nope").status_code == 404
            listed = [row["name"] for row in admin.get("/api/reports/registered").json()["reports"]]
            assert listed == ["units-per-customer", "cost-per-unit"], listed
            # Chat and MCP get it through the same tool as the core reports.
            me_admin = admin.get("/api/auth/me").json()
            result = mcp._call("get_report", {"report_type": "units-per-customer", "filters": {"customer": "C-1"}},
                               principal(me_admin["name"]))
            assert result["rows"] == [{"customer": "C-1", "units": 1}], result
            tool = next(t for t in chat.build_tools() if t["function"]["name"] == "get_report")
            assert "units-per-customer" in tool["function"]["parameters"]["properties"]["report_type"]["enum"]
            assert "Units invoiced per customer" in tool["function"]["description"]

            # Restricted users: by class, and by assigned records.
            assert admin.post("/api/auth/access-profiles", json={"name": "No cost", "hidden_classes": ["cost"]}).status_code == 200
            blind_id, blind = make_user("blind@example.com", access_profile="No cost")
            rep_id, rep = make_user("rep@example.com", record_scope=json.dumps({"customer": ["C-1"]}))
            keeper_id, keeper = make_user("keeper@example.com", record_scope=json.dumps({"warehouse": ["A"]}))
            assert blind.get("/api/reports/registered/cost-per-unit").status_code == 403
            assert blind.get("/api/reports/registered/units-per-customer").status_code == 200
            assert [row["name"] for row in blind.get("/api/reports/registered").json()["reports"]] == ["units-per-customer"]
            r = rep.get("/api/reports/registered/units-per-customer")
            assert r.status_code == 200 and r.json()["rows"] == [{"customer": "C-1", "units": 1}], r.text
            assert rep.get("/api/reports/registered/units-per-customer", params={"customer": "C-2"}).json()["rows"] == []
            # It said nothing about record scope, so it cannot be limited: refused.
            r = rep.get("/api/reports/registered/cost-per-unit")
            assert r.status_code == 403 and "cannot be limited" in r.text, r.text
            assert keeper.get("/api/reports/registered/units-per-customer").status_code == 200
            assert "cost-per-unit" in rep.get("/api/auth/me").json()["restrictions"]["denied_reports"]
            try:
                mcp._call("get_report", {"report_type": "cost-per-unit"}, principal(blind_id))
                raise AssertionError("a hidden registered report was run through a tool")
            except PermissionDenied:
                pass
        finally:
            for name in ("units-per-customer", "cost-per-unit"):
                services.REGISTERED_REPORTS.pop(name, None)
                permissions.SENSITIVE_ARTIFACTS["report"].pop(name, None)
                permissions.SCOPED_ARTIFACTS["report"].pop(name, None)
        print("  [ok] a registered report runs over REST, chat and MCP under the access rules")

        # ---- a deployment's own dataset -----------------------------------------
        spec = {"label": "Invoiced units", "description": "Synthetic: invoice lines.",
                "sql_from": 'FROM "Sales Invoice Item" sii JOIN "Sales Invoice" si ON si.name = sii.parent',
                "fields": {"customer": "string", "item_code": "string", "qty": "number", "amount": "number"},
                "field_sql": {"customer": "si.customer", "item_code": "sii.item_code", "qty": "sii.qty",
                              "amount": "sii.amount"},
                "default_where": ["si.docstatus = 1"], "filter_fields": {"customer", "item_code"}}
        try:
            try:
                analytics.register_dataset("invoiced_units", spec)
                raise AssertionError("a dataset was registered without saying what it holds")
            except ValueError as exc:
                assert "sensitivity" in str(exc), exc
            try:
                analytics.register_dataset("sales_invoices", spec, sensitivity=None)
                raise AssertionError("a core dataset was replaced")
            except ValueError:
                pass
            analytics.register_dataset("invoiced_units", spec, sensitivity=None, hidden_fields={"amount": "cost"},
                                       scope={"customer": "si.customer", "warehouse": None})
            r = admin.post("/api/reports/runtime/data", json={"requests": [{"dataset": "invoiced_units"}]})
            assert r.status_code == 200 and "C-2" in r.text and "amount" in r.text, r.text[:400]
            total = mcp._call("query_dataset", {"dataset": "invoiced_units", "measures": {"units": ["sum", "qty"]}},
                              principal(me_admin["name"]))
            assert "3" in json.dumps(total), total
            r = rep.post("/api/reports/runtime/data", json={"requests": [{"dataset": "invoiced_units"}]})
            assert r.status_code == 200 and "C-1" in r.text and "C-2" not in r.text, r.text[:400]
            total = json.dumps(mcp._call("query_dataset", {"dataset": "invoiced_units",
                                                           "measures": {"units": ["sum", "qty"]}}, principal(rep_id)))
            assert '"units": 1' in total, total
            r = blind.post("/api/reports/runtime/data", json={"requests": [{"dataset": "invoiced_units"}]})
            assert r.status_code == 200 and "amount" not in r.text, r.text[:400]
            assert "invoiced_units" in [d["dataset"] for d in rep.get("/api/reports/runtime/datasets").json()["datasets"]]
        finally:
            analytics.SEMANTIC_DATASETS.pop("invoiced_units", None)
            permissions.SENSITIVE_ARTIFACT_FIELDS["dataset"].pop("invoiced_units", None)
            permissions.SCOPED_ARTIFACTS["dataset"].pop("invoiced_units", None)
        print("  [ok] a registered dataset is limited and redacted like the core ones")

        # ---- access profiles a deployment ships ---------------------------------
        assert permissions.ensure_access_profile(
            "Counter", description="Counts stock, posts nothing", hidden_classes=["cost"],
            permissions={"default": ["read"], "doctypes": {"Stock Count": ["read", "create", "update"]}}) is True
        db.commit()
        shipped = {p["name"]: p for p in admin.get("/api/auth/access-profiles").json()["profiles"]}["Counter"]
        assert shipped["hidden_classes"] == ["cost"] and shipped["permissions"]["default"] == ["read"], shipped
        # An admin's later changes are never overwritten by the next start.
        assert admin.put("/api/auth/access-profiles/Counter", json={"hidden_classes": []}).status_code == 200
        assert permissions.ensure_access_profile("Counter", hidden_classes=["cost"]) is False
        assert {p["name"]: p for p in admin.get("/api/auth/access-profiles").json()["profiles"]}["Counter"]["hidden_classes"] == []
        try:
            permissions.ensure_access_profile("Broken", hidden_classes=["salary"])
            raise AssertionError("an unknown sensitivity class was accepted")
        except ValidationError:
            pass
        print("  [ok] a shipped access profile is created once and then left to the admins")

    print("All extension point checks passed.")


if __name__ == "__main__":
    try:
        check_extension_points()
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
