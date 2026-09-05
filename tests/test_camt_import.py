#!/usr/bin/env python3
"""CAMT parser, manual import, audit, deduplication, and chat attachment tests.

The fixture is wholly synthetic. No customer, bank, account, or transaction
data from the production-like Raiffeisen sample is stored in this repository.

Run:  python -m tests.test_camt_import
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_camt_import
"""

from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile


FIXTURE = Path(__file__).parent / "fixtures" / "camt053_synthetic.xml"


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_camt_test_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_parser():
    from lambda_erp.accounting.camt import CamtError, parse_camt_upload, parse_camt_xml

    raw = FIXTURE.read_bytes()
    document = parse_camt_xml(raw, FIXTURE.name)
    statement = document.statements[0]
    assert statement.schema_version == "camt.053.001.08"
    assert statement.from_date == "2025-01-01" and statement.to_date == "2025-12-31"
    assert str(statement.opening_balance) == "1000.00"
    assert str(statement.closing_balance) == "1100.00"
    assert len(statement.entries) == 3
    assert len(statement.entries[1].details) == 2
    assert statement.entries[1].batch_transaction_count == 2
    assert statement.entries[0].details[0].creditor_reference_type == "QRR"
    assert statement.entries[2].amount == 0
    assert statement.warnings == []

    zipped = BytesIO()
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("safe/statement.xml", raw)
    assert len(parse_camt_upload(zipped.getvalue(), "statements.zip")) == 1

    malicious = raw.replace(
        b'<Document xmlns=',
        b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Document xmlns=',
        1,
    )
    try:
        parse_camt_xml(malicious, "unsafe.xml")
        raise AssertionError("DTD/entity document should have been rejected")
    except CamtError:
        pass
    print("  [camt] parser/ZIP/XML-safety OK")


def check_existing_database_upgrade():
    """The external-id index must be created only after its column exists."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_camt_upgrade_")
    os.close(fd)
    try:
        with sqlite3.connect(path) as conn:
            conn.execute(
                'CREATE TABLE "Bank Transaction" ('
                'name TEXT PRIMARY KEY, bank_account TEXT, posting_date TEXT, '
                'deposit REAL DEFAULT 0, withdrawal REAL DEFAULT 0, '
                'description TEXT, allocated_amount REAL DEFAULT 0, '
                'unallocated_amount REAL DEFAULT 0, reference_doctype TEXT, '
                'reference_name TEXT, status TEXT, docstatus INTEGER DEFAULT 0, '
                'creation TEXT, modified TEXT)'
            )
            conn.execute(
                'CREATE TABLE "_SchemaMigrations" ('
                'version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)'
            )
            conn.executemany(
                'INSERT INTO "_SchemaMigrations" VALUES (?, ?, ?)',
                [(version, f"existing_{version}", "2026-01-01T00:00:00") for version in range(1, 23)],
            )
        from lambda_erp.database import Database
        upgraded = Database(path)
        assert "external_id" in upgraded._get_table_columns("Bank Transaction")
        assert "reconciled_at" in upgraded._get_table_columns("Bank Transaction")
        assert "bank_reconciliation" in upgraded._get_table_columns("Payment Entry")
        assert "bank_reconciliation" in upgraded._get_table_columns("Journal Entry")
        assert "reference_doctype" in upgraded._get_table_columns("Journal Entry Account")
        assert "bank_transaction" in upgraded._get_table_columns("Bank Reconciliation")
        indexes = upgraded.sql('PRAGMA index_list("Bank Transaction")', as_dict=False)
        assert any(row[1] == "ux_bank_transaction_external_id" for row in indexes)
        upgraded.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass
    print("  [camt] existing-database migration order OK")


def check_v24_reconciliation_upgrade():
    """A deployed v0.8.24 database must replace its voucher-wide index safely."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_reconciliation_upgrade_")
    os.close(fd)
    try:
        from lambda_erp.database import Database
        initial = Database(path)
        initial.close()
        with sqlite3.connect(path) as conn:
            conn.execute('DROP INDEX "ux_bank_reconciliation_active_voucher_account"')
            conn.execute('ALTER TABLE "Bank Reconciliation" DROP COLUMN bank_account')
            conn.execute('DELETE FROM "_SchemaMigrations" WHERE version = 25')
            conn.execute(
                'CREATE UNIQUE INDEX "ux_bank_reconciliation_active_voucher" '
                'ON "Bank Reconciliation" (voucher_type, voucher_no) WHERE status = \'Active\''
            )
            conn.execute(
                'INSERT INTO "Bank Transaction" '
                '(name, bank_account, withdrawal, currency, status) VALUES (?, ?, ?, ?, ?)',
                ["BT-LEGACY", "Legacy Bank", 3.30, "CHF", "Reconciled"],
            )
            conn.execute(
                'INSERT INTO "Bank Reconciliation" '
                '(name, bank_transaction, mode, voucher_type, voucher_no, amount, status) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                ["BRC-LEGACY", "BT-LEGACY", "Existing Voucher", "Journal Entry",
                 "JV-LEGACY", 3.30, "Active"],
            )

        upgraded = Database(path)
        assert upgraded.get_value(
            "Bank Reconciliation", "BRC-LEGACY", "bank_account"
        ) == "Legacy Bank"
        indexes = upgraded.sql('PRAGMA index_list("Bank Reconciliation")', as_dict=False)
        index_names = {row[1] for row in indexes}
        assert "ux_bank_reconciliation_active_voucher" not in index_names
        assert "ux_bank_reconciliation_active_voucher_account" in index_names
        upgraded.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass
    print("  [camt] v0.8.24 reconciliation index upgrade OK")


def check_api_and_chat():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (temp file)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app
    from lambda_erp.database import get_db
    from lambda_erp.utils import _dict

    raw = FIXTURE.read_bytes()
    upload = [("files", (FIXTURE.name, raw, "application/xml"))]
    with TestClient(app) as client:
        response = client.post("/api/auth/register", json={
            "email": "admin@example.com", "full_name": "Admin", "password": "test-password-123",
        })
        assert response.status_code == 200 and response.json()["role"] == "admin", response.text

        db = get_db()
        db.insert("Company", _dict(
            name="Synthetic Co", company_name="Synthetic Co", default_currency="CHF",
        ))
        db.insert("Account", _dict(
            name="Synthetic Bank - SYNT", account_name="Synthetic Bank",
            company="Synthetic Co", root_type="Asset", report_type="Balance Sheet",
            account_type="Bank", account_currency="CHF", is_group=0,
        ))

        response = client.post("/api/documents/bank-account", json={
            "account_name": "Synthetic operating account",
            "company": "Synthetic Co",
            "account": "Synthetic Bank - SYNT",
            "iban": "ch36 0000 0000 0000 0000 0",
            "currency": "CHF",
            "bank_name": "Example Bank",
        })
        assert response.status_code == 200, response.text
        bank_account = response.json()
        assert bank_account["iban"] == "CH3600000000000000000"

        response = client.post("/api/bank-statements/preview", files=upload)
        assert response.status_code == 200, response.text
        preview = response.json()["statements"][0]
        assert "account_iban" not in preview
        assert preview["account_iban_masked"] == "CH36 … 0000"
        assert preview["matched_bank_account"]["name"] == bank_account["name"]
        assert preview["entry_count"] == 3 and preview["detail_count"] == 4
        assert preview["batch_entry_count"] == 1 and preview["qr_reference_count"] == 1
        assert preview["opening_balance"] == "1000.00" and preview["closing_balance"] == "1100.00"

        mappings = json.dumps({preview["statement_key"]: bank_account["name"]})
        response = client.post("/api/bank-statements/import", files=upload, data={"mappings": mappings})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["imports"][0]["imported_entry_count"] == 3
        import_name = result["imports"][0]["name"]

        tx = db.sql('SELECT * FROM "Bank Transaction" ORDER BY posting_date')
        details = db.sql('SELECT * FROM "Bank Transaction Detail" ORDER BY parent, idx')
        assert len(tx) == 3 and len(details) == 4
        assert sum(row["deposit"] or 0 for row in tx) == 250
        assert sum(row["withdrawal"] or 0 for row in tx) == 150
        assert sum(row["status"] == "Informational" for row in tx) == 1
        assert sum(row["detail_count"] > 1 for row in tx) == 1
        assert sum(row["creditor_reference_type"] == "QRR" for row in details) == 1

        response = client.get(f"/api/bank-statements/{import_name}/source")
        assert response.status_code == 200 and response.content == raw

        response = client.post("/api/bank-statements/import", files=upload, data={"mappings": mappings})
        assert response.status_code == 200, response.text
        assert response.json()["imports"] == []
        assert response.json()["exact_duplicate_imports"] == [import_name]

        # Selecting the same source twice in one import request must not race
        # the unique source index or create a second audit record.
        duplicate_upload = [
            ("files", (FIXTURE.name, raw, "application/xml")),
            ("files", ("same-again.xml", raw, "application/xml")),
        ]
        response = client.post(
            "/api/bank-statements/import", files=duplicate_upload, data={"mappings": mappings},
        )
        assert response.status_code == 200, response.text
        assert response.json()["imports"] == []
        assert response.json()["exact_duplicate_imports"] == [import_name]

        # A newly generated export has a new source hash, but the bank's stable
        # AcctSvcrRef values still prevent duplicate Bank Transactions.
        regenerated = raw.replace(b"SYNTHETIC-MESSAGE-2025", b"SYNTHETIC-MESSAGE-NEW!", 1)
        regenerated_upload = [("files", ("regenerated.xml", regenerated, "application/xml"))]
        response = client.post("/api/bank-statements/preview", files=regenerated_upload)
        second_preview = response.json()["statements"][0]
        assert second_preview["duplicate_entry_count"] == 3
        second_mapping = json.dumps({second_preview["statement_key"]: bank_account["name"]})
        response = client.post(
            "/api/bank-statements/import", files=regenerated_upload, data={"mappings": second_mapping},
        )
        assert response.status_code == 200, response.text
        assert response.json()["imports"][0]["imported_entry_count"] == 0
        assert response.json()["imports"][0]["duplicate_entry_count"] == 3
        assert db.sql('SELECT COUNT(*) c FROM "Bank Transaction"')[0]["c"] == 3

        # XML is a normal chat attachment, but its raw contents are deliberately
        # not injected into the LLM context; the compact parser tool handles it.
        session = client.post("/api/chat/sessions").json()
        response = client.post(
            "/api/chat/attachments",
            files={"file": (FIXTURE.name, raw, "application/xml")},
            data={"session_id": session["id"]},
        )
        assert response.status_code == 200, response.text
        attachment = response.json()
        from api.attachments import build_multimodal_content, get_attachments_by_ids
        from api.chat import (
            _handle_import_bank_statement_attachments,
            _handle_preview_bank_statement_attachments,
        )
        # The helper is scoped by real user id; fetch it from the auth response.
        user_name = client.get("/api/auth/me").json()["name"]
        stored = get_attachments_by_ids([attachment["id"]], user_name)
        content = build_multimodal_content(stored[0])
        assert content["type"] == "text" and "dedicated bank-statement preview tool" in content["text"]
        tool_preview = _handle_preview_bank_statement_attachments(
            {"attachment_ids": [attachment["id"]]}, user_name,
        )
        assert tool_preview["statements"][0]["entry_count"] == 3
        denied = _handle_import_bank_statement_attachments(
            {"attachment_ids": [attachment["id"]], "confirmed": False},
            {"name": user_name, "role": "admin"},
        )
        assert "error" in denied
        client.delete(f"/api/chat/sessions/{session['id']}")
        assert get_attachments_by_ids([attachment["id"]], user_name) == []
        # The imported accounting source is an independent audit record and
        # must outlive deletion of the chat attachment it originally came from.
        response = client.get(f"/api/bank-statements/{import_name}/source")
        assert response.status_code == 200 and response.content == raw

    if not os.environ.get("LAMBDA_ERP_TEST_DB"):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + suffix)
            except OSError:
                pass
    print(f"  [camt] API/import/audit/dedup/chat OK on {backend}")


def main():
    print("CAMT bank statement checks")
    check_parser()
    check_existing_database_upgrade()
    check_v24_reconciliation_upgrade()
    check_api_and_chat()
    print("All CAMT checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)
