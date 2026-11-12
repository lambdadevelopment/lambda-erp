"""Files kept with a document or master record.

Synthetic data only. Run:
    python -m tests.test_attachments
    LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_attachments
"""
import json
import os
import sys

PDF = b"%PDF-1.4\n% synthetic order\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def check_attachments():
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
    from api import auth, record_attachments, services
    from api.routers import mcp
    from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
    from lambda_erp.database import get_db
    from lambda_erp.utils import now

    print(f"Attachment checks ({backend})")

    with TestClient(app) as admin:
        r = admin.post("/api/auth/register", json={"email": "admin@example.com", "full_name": "Admin",
                                                    "password": "test-password-123"})
        assert r.status_code == 200, r.text[:300]
        db = get_db()
        db.insert("Company", {"name": "Test Co", "company_name": "Test Co", "default_currency": "USD"})
        setup_chart_of_accounts("Test Co", "USD")
        setup_cost_center("Test Co")
        for customer in ("C-1", "C-2"):
            db.insert("Customer", {"name": customer, "customer_name": customer, "default_currency": "USD"})
        db.insert("Warehouse", {"name": "A", "warehouse_name": "A", "company": "Test Co"})
        db.insert("Item", {"name": "S", "item_name": "Stock item", "is_stock_item": 1, "standard_rate": 20})
        db.commit()

        def order(customer):
            return services.create_document("sales-order", {
                "company": "Test Co", "customer": customer, "transaction_date": "2026-11-12",
                "delivery_date": "2026-12-30", "items": [{"item_code": "S", "qty": 1, "rate": 20, "warehouse": "A"}]})

        mine, other = order("C-1"), order("C-2")

        def make_user(email, role, **fields):
            name = f"U-{email.split('@')[0]}"
            db.insert("User", {"name": name, "email": email, "full_name": email, "role": role, "enabled": 1,
                               "hashed_password": auth.hash_password("test-password-123"),
                               "creation": now(), "modified": now(), **fields})
            db.commit()
            client = TestClient(app)
            assert client.post("/api/auth/login", json={"email": email, "password": "test-password-123"}).status_code == 200
            return name, client

        _, viewer = make_user("viewer@example.com", "viewer")
        rep_id, rep = make_user("rep@example.com", "manager", record_scope=json.dumps({"customer": ["C-1"]}))

        def upload(client, record_type, record_id, filename="order.pdf", data=PDF, **form):
            return client.post("/api/record-attachments", data={"record_type": record_type, "record_id": record_id, **form},
                               files={"file": (filename, data, "application/octet-stream")})

        # ---- add, list, download, remove ------------------------------------
        before = db.get_value("Sales Order", mine["name"], "modified")
        r = upload(admin, "sales-order", mine["name"], description="As received by e-mail")
        assert r.status_code == 200, r.text
        first = r.json()
        assert first["filename"] == "order.pdf" and first["content_type"] == "application/pdf" \
            and first["size"] == len(PDF) and first["description"] == "As received by e-mail", first
        assert db.get_value("Sales Order", mine["name"], "modified") == before  # the record is untouched
        listed = admin.get("/api/record-attachments", params={"record_type": "sales-order", "record_id": mine["name"]})
        assert [a["id"] for a in listed.json()["attachments"]] == [first["id"]] and "data" not in listed.text
        r = admin.get(first["url"])
        assert r.status_code == 200 and r.content == PDF, r.status_code
        assert r.headers["content-disposition"].startswith("attachment;") and r.headers["x-content-type-options"] == "nosniff"
        # On a submitted document too, and on a master record.
        services.submit_document("sales-order", mine["name"])
        assert upload(admin, "sales-order", mine["name"], "signed slip.jpg", b"\xff\xd8\xff synthetic").status_code == 200
        assert upload(admin, "item", "S", "certificate.pdf").status_code == 200
        assert len(admin.get("/api/record-attachments", params={"record_type": "item", "record_id": "S"}).json()["attachments"]) == 1

        # ---- what is refused ------------------------------------------------
        for filename, data, status in (("page.html", b"<script>alert(1)</script>", 415), ("run.exe", b"MZ", 415),
                                       ("noextension", b"x", 415), ("empty.pdf", b"", 400),
                                       ("big.pdf", b"0" * (record_attachments.MAX_SIZE + 1), 413)):
            r = upload(admin, "sales-order", mine["name"], filename, data)
            assert r.status_code == status, (filename, r.status_code, r.text[:200])
        assert upload(admin, "sales-order", "SO-NOPE").status_code == 404
        assert upload(admin, "no-such-type", "X").status_code == 404
        # A path in the name does not get anywhere.
        r = upload(admin, "sales-order", mine["name"], "../../etc/pass wd.pdf")
        assert r.status_code == 200 and r.json()["filename"] == "pass wd.pdf", r.text

        # ---- roles and access ------------------------------------------------
        r = viewer.get("/api/record-attachments", params={"record_type": "sales-order", "record_id": mine["name"]})
        assert r.status_code == 200 and len(r.json()["attachments"]) == 3, r.text
        assert viewer.get(first["url"]).status_code == 200
        assert upload(viewer, "sales-order", mine["name"]).status_code == 403
        assert viewer.delete(first["url"]).status_code == 403
        assert TestClient(app).get(first["url"]).status_code == 401
        # A user restricted to one customer gets that customer's files only.
        theirs = upload(admin, "sales-order", other["name"], "other order.pdf").json()
        assert rep.get("/api/record-attachments", params={"record_type": "sales-order", "record_id": mine["name"]}).status_code == 200
        assert rep.get(first["url"]).status_code == 200
        for r in (rep.get("/api/record-attachments", params={"record_type": "sales-order", "record_id": other["name"]}),
                  rep.get(theirs["url"]), rep.delete(theirs["url"]), upload(rep, "sales-order", other["name"])):
            assert r.status_code == 404 and "other order" not in r.text, (r.status_code, r.text)
        assert upload(rep, "sales-order", mine["name"], "note.txt", b"call before delivery").status_code == 200

        # ---- chat and MCP ----------------------------------------------------
        principal = auth.make_auth_principal(
            dict(db.get_value("User", rep_id, ["name", "email", "full_name", "role", "enabled"])), auth.SESSION_CREDENTIAL)
        seen = mcp._call("list_record_attachments", {"record_type": "sales-order", "record_id": mine["name"]}, principal)
        assert len(seen["attachments"]) == 4 and "data" not in json.dumps(seen), seen
        try:
            mcp._call("list_record_attachments", {"record_type": "sales-order", "record_id": other["name"]}, principal)
            raise AssertionError("a restricted user listed another customer's files through a tool")
        except Exception as exc:
            assert "unavailable" in str(getattr(exc, "detail", exc)), exc
        # A file uploaded to the user's own conversation can be kept with a record.
        from api import chat
        session = chat.create_session(user_id=rep_id)
        upload_dir = os.path.join(os.path.dirname(db_path) if not db_path.startswith("postgres") else "/tmp", "att-test")
        os.makedirs(upload_dir, exist_ok=True)
        path = os.path.join(upload_dir, "chat-upload.pdf")
        with open(path, "wb") as handle:
            handle.write(PDF)
        db.sql('INSERT INTO "Chat Attachment" (id, session_id, user_id, filename, mime_type, size_bytes, file_path) '
               'VALUES (?, ?, ?, ?, ?, ?, ?)', ["ATT-1", session["id"], rep_id, "customer order.pdf", "application/pdf",
                                                len(PDF), path])
        db.commit()
        kept = mcp._call("attach_chat_file", {"record_type": "sales-order", "record_id": mine["name"],
                                              "attachment_id": "ATT-1", "description": "Order as received"}, principal)
        assert kept["filename"] == "customer order.pdf" and rep.get(kept["url"]).content == PDF, kept
        admin_principal = auth.make_auth_principal(
            dict(db.get_value("User", admin.get("/api/auth/me").json()["name"],
                              ["name", "email", "full_name", "role", "enabled"])), auth.SESSION_CREDENTIAL)
        try:
            mcp._call("attach_chat_file", {"record_type": "sales-order", "record_id": mine["name"],
                                           "attachment_id": "ATT-1"}, admin_principal)
            raise AssertionError("someone else's chat upload was attached")
        except Exception as exc:
            assert "not uploaded" in str(getattr(exc, "detail", exc)), exc
        os.remove(path)

        # ---- a file cannot be redacted, so it is hidden as a whole ---------------
        # (also internal notes: both are free content on a record)
        db.insert("Supplier", {"name": "SUP", "supplier_name": "Supplier", "default_currency": "USD"})
        db.commit()
        purchase = services.create_document("purchase-order", {
            "company": "Test Co", "supplier": "SUP", "transaction_date": "2026-11-12",
            "items": [{"item_code": "S", "qty": 1, "rate": 73.91, "warehouse": "A"}]})
        assert admin.post("/api/auth/access-profiles", json={"name": "Blind", "hidden_classes": ["purchase_price"]}).status_code == 200
        blind_id, blind = make_user("blind@example.com", "manager", access_profile="Blind")
        quote = b"Supplier quotation: unit price 73.91"
        kept = {}
        for record_type, record_id in (("purchase-order", purchase["name"]), ("supplier", "SUP"), ("item", "S"),
                                       ("sales-order", mine["name"])):
            r = upload(admin, record_type, record_id, "supplier quotation.txt", quote)
            assert r.status_code == 200, r.text
            kept[record_type] = r.json()
            r = admin.post("/api/actions/add_record_note", json={
                "doctype": record_type, "record_id": record_id, "body": "Agreed 73.91 with the supplier @blind@example.com",
                "request_id": f"req-{record_type}-0001"})
            assert r.status_code == 200, r.text
        assert kept["purchase-order"]["sensitivity_class"] == kept["supplier"]["sensitivity_class"] == "purchase_price"
        assert kept["item"]["sensitivity_class"] is None and kept["sales-order"]["sensitivity_class"] is None
        # The record itself stays readable (without its prices); what hangs on it is gone.
        assert blind.get(f"/api/documents/purchase-order/{purchase['name']}").status_code == 200

        def reads(client, record_type, record_id):
            listing = client.get("/api/record-attachments", params={"record_type": record_type, "record_id": record_id})
            notes = client.post("/api/actions/list_record_notes", json={"record_type": record_type, "record_id": record_id})
            return listing, notes

        for record_type, record_id in (("purchase-order", purchase["name"]), ("supplier", "SUP")):
            listing, notes = reads(blind, record_type, record_id)
            assert listing.status_code == 200 and listing.json()["attachments"] == [], listing.text
            assert "quotation" not in listing.text and "73.91" not in notes.text, (listing.text, notes.text)
            r = blind.get(kept[record_type]["url"])
            assert r.status_code == 404 and quote not in r.content, (record_type, r.status_code)
            assert blind.delete(kept[record_type]["url"]).status_code == 404
            assert len(reads(admin, record_type, record_id)[0].json()["attachments"]) == 1
        inbox = blind.post("/api/actions/list_my_mentions", json={"view": "all"})
        assert inbox.status_code == 200 and "73.91" in inbox.text, inbox.text  # the open records' notes ...
        assert purchase["name"] not in inbox.text and '"record_id": "SUP"' not in inbox.text, inbox.text  # ... only those
        blind_principal = auth.make_auth_principal(
            dict(db.get_value("User", blind_id, ["name", "email", "full_name", "role", "enabled"])), auth.SESSION_CREDENTIAL)
        seen = mcp._call("list_record_attachments", {"record_type": "supplier", "record_id": "SUP"}, blind_principal)
        assert seen["attachments"] == [], seen
        # Files on records that are not about purchasing stay open, until someone says what is in one.
        assert blind.get(kept["item"]["url"]).content == quote
        r = admin.put(kept["item"]["url"], json={"sensitivity": "purchase_price"})
        assert r.status_code == 200 and r.json()["sensitivity_class"] == "purchase_price", r.text
        assert blind.get(kept["item"]["url"]).status_code == 404
        assert reads(blind, "item", "S")[0].json()["attachments"][0]["filename"] == "certificate.pdf"
        # A file on a purchase order that holds nothing sensitive can be opened up, by someone who sees prices.
        slip = upload(admin, "purchase-order", purchase["name"], "delivery slip.pdf", sensitivity="none")
        assert slip.status_code == 200 and slip.json()["sensitivity_class"] is None, slip.text
        assert blind.get(slip.json()["url"]).content == PDF
        assert [a["filename"] for a in reads(blind, "purchase-order", purchase["name"])[0].json()["attachments"]] == ["delivery slip.pdf"]
        # The restricted user can add a file there, but not decide what it counts as, nor reopen a hidden one.
        own = upload(blind, "purchase-order", purchase["name"], "signed slip.pdf")
        assert own.status_code == 200, own.text
        assert blind.get(own.json()["url"]).status_code == 404  # it counts as purchasing content like the rest
        assert upload(blind, "purchase-order", purchase["name"], "x.pdf", sensitivity="none").status_code == 403
        assert blind.put(slip.json()["url"], json={"sensitivity": "purchase_price"}).status_code == 403
        assert upload(admin, "purchase-order", purchase["name"], "x.pdf", sensitivity="secret").status_code == 422
        # A type a plugin registers says what hangs on it.
        from lambda_erp import permissions
        permissions.register_content_class("Sales Order", "purchase_price")
        try:
            assert blind.get(kept["sales-order"]["url"]).status_code == 404
            assert "73.91" not in reads(blind, "sales-order", mine["name"])[1].text
        finally:
            permissions.RECORD_CONTENT_CLASSES.pop("Sales Order", None)
        assert blind.get(kept["sales-order"]["url"]).status_code == 200

        # A mention does not open a record: a note on another customer's order stays out of the inbox.
        for target, request in ((other["name"], "req-scope-other-01"), (mine["name"], "req-scope-mine-001")):
            r = admin.post("/api/actions/add_record_note", json={
                "doctype": "sales-order", "record_id": target, "body": f"Please look at {target} @rep@example.com",
                "request_id": request})
            assert r.status_code == 200, r.text
        inbox = rep.post("/api/actions/list_my_mentions", json={"view": "all"}).json()
        assert [row["record_id"] for row in inbox["rows"]] == [mine["name"]] and inbox["unread_count"] == 1, inbox
        # The currency revaluation lists open balances and posts to the ledger.
        for client in (blind, rep):
            r = client.post("/api/accounting/revaluation", json={"company": "Test Co"})
            assert r.status_code == 403, (r.status_code, r.text)
        assert admin.post("/api/accounting/revaluation", json={"company": "Test Co"}).status_code == 200
        revalued = mcp._call("revalue_currencies", {"company": "Test Co"}, blind_principal)
        assert "error" in revalued and "access profile" in revalued["error"], revalued

        # What reaches the model is data too: the prompt of a restricted user says nothing
        # about what is hidden from them, and the history tool only knows its own conversation.
        from api import chat as chat_module
        services.submit_document("purchase-invoice", services.create_document("purchase-invoice", {
            "company": "Test Co", "supplier": "SUP", "posting_date": "2026-11-12",
            "items": [{"item_code": "S", "qty": 1, "rate": 73.91, "warehouse": "A"}]})["name"])
        assert "purchase invoices: 1 submitted" in chat_module.build_system_prompt(admin_principal)
        for restricted in (blind_principal, principal):
            prompt = chat_module.build_system_prompt(restricted)
            assert "purchase invoices:" not in prompt and "73.91" not in prompt, prompt[-600:]
        assert "sales invoices:" in chat_module.build_system_prompt(blind_principal)
        assert "error" in chat_module._handle_retrieve_chat_history({})
        assert "error" in chat_module.TOOL_HANDLERS["retrieve_chat_history"]({"num_messages": 50})

        # ---- removing ---------------------------------------------------------
        assert admin.delete(first["url"]).status_code == 200
        assert admin.get(first["url"]).status_code == 404
        # A deleted master record takes its files with it.
        db.insert("Customer", {"name": "C-3", "customer_name": "Gone soon", "default_currency": "USD"})
        db.commit()
        gone = upload(admin, "customer", "C-3").json()
        assert admin.delete("/api/masters/customer/C-3").status_code == 200
        assert not db.sql('SELECT 1 FROM "Record Attachment" WHERE name = ?', [gone["id"]])
        # The tracking reference of a delivery.
        note = services.create_document("delivery-note", {
            "company": "Test Co", "customer": "C-1", "posting_date": "2026-11-12", "tracking_reference": "99.00.123456.78",
            "items": [{"item_code": "S", "qty": 1, "rate": 20, "warehouse": "A"}]})
        assert note["tracking_reference"] == "99.00.123456.78", note

    print("  [ok] files are kept, served as downloads, limited by type, size, role and record access")
    print("All attachment checks passed.")


if __name__ == "__main__":
    try:
        check_attachments()
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
