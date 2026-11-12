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
