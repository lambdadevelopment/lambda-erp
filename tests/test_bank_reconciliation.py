#!/usr/bin/env python3
"""End-to-end bank reconciliation checks with wholly synthetic data."""

import os
from pathlib import Path
import tempfile


FIXTURE = Path(__file__).parent / "fixtures" / "camt053_synthetic.xml"


def _database_path():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        fd, path = tempfile.mkstemp(suffix=".db", prefix="lambda_bank_reconciliation_")
        os.close(fd)
        return path
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def _seed(db):
    from lambda_erp.utils import _dict

    db.insert("Company", _dict(
        name="Synthetic Co", company_name="Synthetic Co", default_currency="CHF",
        default_receivable_account="Receivable - SYNT",
        default_payable_account="Payable - SYNT",
        default_income_account="Income - SYNT",
        default_expense_account="Expense - SYNT",
    ))
    for name, root, account_type in (
        ("Bank - SYNT", "Asset", "Bank"),
        ("USD Bank - SYNT", "Asset", "Bank"),
        ("Receivable - SYNT", "Asset", "Receivable"),
        ("Payable - SYNT", "Liability", "Payable"),
        ("Income - SYNT", "Income", None),
        ("Expense - SYNT", "Expense", None),
    ):
        db.insert("Account", _dict(
            name=name, account_name=name.split(" - ")[0], company="Synthetic Co",
            root_type=root, report_type="Profit and Loss" if root in {"Income", "Expense"} else "Balance Sheet",
            account_type=account_type,
            account_currency="USD" if name == "USD Bank - SYNT" else "CHF",
            is_group=0, disabled=0,
        ))
    db.insert("Customer", _dict(
        name="CUST-001", customer_name="Example Customer", default_currency="CHF",
    ))
    db.insert("Supplier", _dict(
        name="SUPP-001", supplier_name="Example Supplier A", default_currency="CHF",
    ))
    db.insert("Item", _dict(
        name="ITEM-001", item_name="Synthetic service", stock_uom="Nos",
        is_stock_item=0, standard_rate=1,
    ))

    from lambda_erp.accounting.bank_account import BankAccount
    BankAccount({
        "account_name": "Synthetic operating account", "company": "Synthetic Co",
        "account": "Bank - SYNT", "iban": "CH3600000000000000000",
        "currency": "CHF", "bank_name": "Example Bank",
    }).save()
    BankAccount({
        "account_name": "Synthetic USD account", "company": "Synthetic Co",
        "account": "USD Bank - SYNT", "iban": "CH9300762011623852957",
        "currency": "USD", "bank_name": "Example Bank",
    }).save()


def _invoice_documents():
    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice
    from lambda_erp.accounting.sales_invoice import SalesInvoice

    sales = SalesInvoice({
        "customer": "CUST-001", "company": "Synthetic Co", "currency": "CHF",
        "posting_date": "2025-02-01", "debit_to": "Receivable - SYNT",
        "items": [{"item_code": "ITEM-001", "qty": 1, "rate": 250,
                   "income_account": "Income - SYNT"}],
    }).submit()
    purchase = PurchaseInvoice({
        "supplier": "SUPP-001", "company": "Synthetic Co", "currency": "CHF",
        "posting_date": "2025-03-01", "credit_to": "Payable - SYNT",
        "items": [{"item_code": "ITEM-001", "qty": 1, "rate": 150,
                   "expense_account": "Expense - SYNT"}],
    }).submit()
    return sales, purchase


def check_reconciliation():
    path = _database_path()
    backend = "postgres" if path.startswith("postgres") else "sqlite (temp file)"
    from lambda_erp.database import setup
    db = setup(path)
    _seed(db)
    sales, purchase = _invoice_documents()

    from lambda_erp.accounting.camt import parse_camt_upload
    from lambda_erp.accounting.bank_statement_import import import_documents, preview_documents
    docs = parse_camt_upload(FIXTURE.read_bytes(), FIXTURE.name)
    preview = preview_documents(docs)["statements"][0]
    import_documents(docs, {preview["statement_key"]: preview["matched_bank_account"]["name"]})
    transactions = db.sql(
        'SELECT name, deposit, withdrawal FROM "Bank Transaction" '
        'WHERE status != ? ORDER BY posting_date', ["Informational"],
    )
    deposit = next(row["name"] for row in transactions if row["deposit"])
    withdrawal = next(row["name"] for row in transactions if row["withdrawal"])

    # Imported rows are bank evidence, not editable drafts. Reconciliation uses
    # the dedicated audited service below rather than generic document saves.
    from lambda_erp.accounting.bank_transaction import BankTransaction
    imported = BankTransaction.load(deposit)
    imported.description = "tampered"
    try:
        imported.save()
        raise AssertionError("Imported Bank Transactions must be immutable")
    except Exception as exc:
        assert "read-only" in str(exc).lower()

    from lambda_erp.accounting.bank_reconciliation import (
        reconcile_with_existing_voucher,
        reconcile_with_existing_voucher_group,
        reconcile_with_journal,
        reconcile_with_payment,
        suggest_matches,
        undo_reconciliation,
    )

    suggestions = suggest_matches(deposit)
    assert suggestions["invoices"][0]["reference_name"] == sales.name
    assert suggestions["invoices"][0]["score"] >= 100

    try:
        reconcile_with_payment(deposit, [{
            "reference_doctype": "Sales Invoice", "reference_name": sales.name,
            "allocated_amount": 250,
        }])
        raise AssertionError("Unconfirmed payment reconciliation should fail")
    except Exception as exc:
        assert "confirmation" in str(exc).lower()

    result = reconcile_with_payment(deposit, [{
        "reference_doctype": "Sales Invoice", "reference_name": sales.name,
        "allocated_amount": 250,
    }], user="USER-001", confirmed=True)
    assert result["status"] == "Reconciled" and result["on_account_amount"] == 0
    payment = db.get_value("Payment Entry", result["voucher_no"], ["docstatus", "bank_reconciliation"])
    assert payment["docstatus"] == 1 and payment["bank_reconciliation"] == result["reconciliation"]
    assert db.get_value("Bank Transaction", deposit, "status") == "Reconciled"
    assert db.get_value("Sales Invoice", sales.name, "outstanding_amount") == 0

    try:
        reconcile_with_payment(deposit, [{
            "reference_doctype": "Sales Invoice", "reference_name": sales.name,
            "allocated_amount": 250,
        }], confirmed=True)
        raise AssertionError("A second active reconciliation should fail")
    except Exception as exc:
        assert "already reconciled" in str(exc).lower()

    undone = undo_reconciliation(deposit, user="USER-001", confirmed=True)
    assert undone["voucher_cancelled"] is True
    assert db.get_value("Payment Entry", result["voucher_no"], "docstatus") == 2
    assert db.get_value("Bank Transaction", deposit, "status") == "Unreconciled"
    assert db.get_value("Sales Invoice", sales.name, "outstanding_amount") == 250

    # An already-booked voucher is linked without posting or cancelling it.
    from lambda_erp.accounting.payment_entry import PaymentEntry
    existing = PaymentEntry({
        "payment_type": "Receive", "posting_date": "2025-02-03",
        "company": "Synthetic Co", "party_type": "Customer", "party": "CUST-001",
        "paid_from": "Receivable - SYNT", "paid_to": "Bank - SYNT",
        "paid_amount": 250, "received_amount": 250, "currency": "CHF",
        "references": [{"reference_doctype": "Sales Invoice", "reference_name": sales.name,
                        "allocated_amount": 250}],
    }).submit()
    vouchers = suggest_matches(deposit)["existing_vouchers"]
    assert any(item["voucher_no"] == existing.name for item in vouchers)
    reconcile_with_existing_voucher(
        deposit, "Payment Entry", existing.name, user="USER-001", confirmed=True,
    )
    second_undo = undo_reconciliation(deposit, user="USER-001", confirmed=True)
    assert second_undo["voucher_cancelled"] is False
    assert db.get_value("Payment Entry", existing.name, "docstatus") == 1
    # Cancelling a merely-linked voucher through its normal document page must
    # also remove the active bank match; otherwise the queue would claim a
    # cancelled posting still reconciles the statement.
    reconcile_with_existing_voucher(
        deposit, "Payment Entry", existing.name, user="USER-001", confirmed=True,
    )
    existing.reload()
    existing.cancel()
    assert db.get_value("Bank Transaction", deposit, "status") == "Unreconciled"
    assert db.get_value("Sales Invoice", sales.name, "outstanding_amount") == 250

    # Non-invoice cash movements can be classified against a safe GL account.
    journal_result = reconcile_with_journal(
        withdrawal, "Expense - SYNT", remarks="Synthetic bank charge",
        user="USER-001", confirmed=True,
    )
    assert db.get_value("Journal Entry", journal_result["voucher_no"], "docstatus") == 1
    assert db.get_value("Bank Transaction", withdrawal, "status") == "Reconciled"
    bank_leg = db.sql(
        'SELECT debit, credit FROM "GL Entry" WHERE voucher_type = ? AND voucher_no = ? '
        'AND account = ? AND is_cancelled = 0',
        ["Journal Entry", journal_result["voucher_no"], "Bank - SYNT"],
    )[0]
    assert bank_leg["debit"] == 0 and bank_leg["credit"] == 150
    undo_reconciliation(withdrawal, user="USER-001", confirmed=True)
    assert db.get_value("Journal Entry", journal_result["voucher_no"], "docstatus") == 2
    assert db.get_value("Bank Transaction", withdrawal, "status") == "Unreconciled"

    active = db.sql('SELECT COUNT(*) AS c FROM "Bank Reconciliation" WHERE status = ?', ["Active"])[0]["c"]
    assert active == 0

    # REST and chat surfaces expose the same guarded workflow.
    os.environ["LAMBDA_ERP_DB"] = path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as client:
        response = client.post("/api/auth/register", json={
            "email": "admin@example.com", "full_name": "Admin",
            "password": "test-password-123",
        })
        assert response.status_code == 200, response.text
        response = client.get("/api/bank-reconciliation/transactions")
        assert response.status_code == 200 and len(response.json()["rows"]) == 2
        response = client.get(f"/api/bank-reconciliation/transactions/{deposit}/suggestions")
        assert response.status_code == 200, response.text
        response = client.post("/api/bank-reconciliation/journal", json={
            "bank_transaction": withdrawal,
            "counterparty_account": "Expense - SYNT",
            "confirmed": False,
        })
        assert response.status_code == 422 and "confirmation" in response.text.lower()

        from api.chat import (
            TOOLS,
            _handle_list_bank_reconciliation_queue,
            _handle_reconcile_bank_transaction,
            _handle_suggest_bank_reconciliation,
        )
        user = client.get("/api/auth/me").json()
        reconcile_schema = next(
            tool["function"]["parameters"] for tool in TOOLS
            if tool.get("function", {}).get("name") == "reconcile_bank_transaction"
        )
        assert "bank_transaction" not in reconcile_schema["properties"]
        assert reconcile_schema["properties"]["bank_transactions"]["minItems"] == 1
        assert "bank_transactions" in reconcile_schema["required"]
        assert len(_handle_list_bank_reconciliation_queue({}, user)["rows"]) == 2
        assert _handle_suggest_bank_reconciliation(
            {"bank_transaction": deposit}, user,
        )["transaction"]["name"] == deposit
        assert "error" in _handle_reconcile_bank_transaction({
            "bank_transactions": [withdrawal], "mode": "journal", "confirmed": False,
        }, user)

    # A single existing journal may contain independent movements on several
    # bank accounts. Each bank leg can be reconciled once, but cannot be reused
    # for another transaction on the same account.
    primary_bank_id = db.get_value("Bank Account", {"account": "Bank - SYNT"}, "name")
    usd_bank_id = db.get_value("Bank Account", {"account": "USD Bank - SYNT"}, "name")
    for import_name, bank_id, source_hash, currency in (
        ("BSI-MULTI-CHF", primary_bank_id, "a" * 64, "CHF"),
        ("BSI-MULTI-USD", usd_bank_id, "b" * 64, "USD"),
    ):
        db.insert("Bank Statement Import", {
            "name": import_name, "bank_account": bank_id, "company": "Synthetic Co",
            "source_sha256": source_hash, "statement_index": 1, "currency": currency,
            "status": "Imported",
        })
    for name, account, bank_id, import_name, amount, currency in (
        ("BT-MULTI-CHF", "Bank - SYNT", primary_bank_id, "BSI-MULTI-CHF", 3.30, "CHF"),
        ("BT-MULTI-USD", "USD Bank - SYNT", usd_bank_id, "BSI-MULTI-USD", 0.41, "USD"),
        ("BT-MULTI-USD-DUP", "USD Bank - SYNT", usd_bank_id, "BSI-MULTI-USD", 0.41, "USD"),
    ):
        db.insert("Bank Transaction", {
            "name": name, "bank_account": account, "bank_account_id": bank_id,
            "bank_statement_import": import_name, "posting_date": "2025-12-31",
            "withdrawal": amount, "currency": currency, "external_id": f"test:{name}",
            "allocated_amount": 0, "unallocated_amount": amount, "status": "Unreconciled",
        })

    from lambda_erp.accounting.journal_entry import JournalEntry
    multi_bank_journal = JournalEntry({
        "posting_date": "2025-12-31", "company": "Synthetic Co",
        "voucher_type": "Bank Entry", "remark": "Combined CHF and USD bank fees",
        "accounts": [
            {"account": "Expense - SYNT", "debit": 3.65, "credit": 0,
             "debit_in_account_currency": 3.65, "credit_in_account_currency": 0},
            {"account": "Bank - SYNT", "debit": 0, "credit": 3.30,
             "debit_in_account_currency": 0, "credit_in_account_currency": 3.30},
            {"account": "USD Bank - SYNT", "debit": 0, "credit": 0.35,
             "debit_in_account_currency": 0, "credit_in_account_currency": 0.41},
        ],
    }).submit()
    assert any(
        item["voucher_no"] == multi_bank_journal.name
        for item in suggest_matches("BT-MULTI-CHF")["existing_vouchers"]
    )
    chat_single = _handle_reconcile_bank_transaction({
        "bank_transactions": ["BT-MULTI-CHF"],
        "mode": "existing_voucher",
        "voucher_type": "Journal Entry",
        "voucher_no": multi_bank_journal.name,
        "confirmed": True,
    }, user)
    assert chat_single.get("status") == "Reconciled", chat_single
    assert chat_single["bank_transactions"] == ["BT-MULTI-CHF"]
    # Matching the CHF leg must not hide the still-available USD leg.
    assert any(
        item["voucher_no"] == multi_bank_journal.name
        for item in suggest_matches("BT-MULTI-USD")["existing_vouchers"]
    )
    reconcile_with_existing_voucher(
        "BT-MULTI-USD", "Journal Entry", multi_bank_journal.name,
        user="USER-001", confirmed=True,
    )
    matches = db.sql(
        'SELECT bank_transaction, bank_account FROM "Bank Reconciliation" '
        'WHERE voucher_type = ? AND voucher_no = ? AND status = ? ORDER BY bank_transaction',
        ["Journal Entry", multi_bank_journal.name, "Active"],
    )
    assert [(row["bank_transaction"], row["bank_account"]) for row in matches] == [
        ("BT-MULTI-CHF", "Bank - SYNT"),
        ("BT-MULTI-USD", "USD Bank - SYNT"),
    ]
    try:
        reconcile_with_existing_voucher(
            "BT-MULTI-USD-DUP", "Journal Entry", multi_bank_journal.name,
            user="USER-001", confirmed=True,
        )
        raise AssertionError("A voucher's bank-account movement must not be reused")
    except Exception as exc:
        assert "already reconciled" in str(exc).lower()

    # Cancelling a multi-bank voucher must release every linked transaction,
    # not just whichever audit row happens to be returned first.
    multi_bank_journal.cancel()
    assert db.get_value("Bank Transaction", "BT-MULTI-CHF", "status") == "Unreconciled"
    assert db.get_value("Bank Transaction", "BT-MULTI-USD", "status") == "Unreconciled"
    assert db.sql(
        'SELECT COUNT(*) AS c FROM "Bank Reconciliation" '
        'WHERE voucher_type = ? AND voucher_no = ? AND status = ?',
        ["Journal Entry", multi_bank_journal.name, "Active"],
    )[0]["c"] == 0

    # One legacy voucher may consolidate several statement movements on the
    # same bank account into one GL bank leg. The deterministic suggestion must
    # identify an exact same-day subset, excluding unrelated same-day noise,
    # and the complete group must be linked or reversed atomically.
    for name, amount in (
        ("BT-GROUP-SMALL", 147.20),
        ("BT-GROUP-LARGE", 130811.90),
        ("BT-GROUP-NOISE", 25.00),
    ):
        db.insert("Bank Transaction", {
            "name": name, "bank_account": "Bank - SYNT", "bank_account_id": primary_bank_id,
            "bank_statement_import": "BSI-MULTI-CHF", "posting_date": "2025-01-07",
            "deposit": amount, "currency": "CHF", "external_id": f"test:{name}",
            "allocated_amount": 0, "unallocated_amount": amount, "status": "Unreconciled",
        })
    grouped_journal = JournalEntry({
        "posting_date": "2025-01-07", "company": "Synthetic Co",
        "voucher_type": "Bank Entry", "remark": "Consolidated same-account deposits",
        "accounts": [
            {"account": "Bank - SYNT", "debit": 130959.10, "credit": 0,
             "debit_in_account_currency": 130959.10, "credit_in_account_currency": 0},
            {"account": "Income - SYNT", "debit": 0, "credit": 130959.10,
             "debit_in_account_currency": 0, "credit_in_account_currency": 130959.10},
        ],
    }).submit()
    grouped_candidates = [
        item for item in suggest_matches("BT-GROUP-LARGE")["existing_vouchers"]
        if item["voucher_no"] == grouped_journal.name
    ]
    assert len(grouped_candidates) == 1
    grouped_candidate = grouped_candidates[0]
    assert grouped_candidate["kind"] == "existing_voucher_group"
    assert {row["name"] for row in grouped_candidate["bank_transactions"]} == {
        "BT-GROUP-SMALL", "BT-GROUP-LARGE",
    }
    for invalid_group in (
        ["BT-GROUP-LARGE"],
        ["BT-GROUP-LARGE", "BT-GROUP-SMALL", "BT-GROUP-NOISE"],
    ):
        try:
            reconcile_with_existing_voucher_group(
                invalid_group, "Journal Entry", grouped_journal.name,
                user="USER-001", confirmed=True,
            )
            raise AssertionError("Partial or excessive voucher groups must fail")
        except Exception as exc:
            assert "does not equal grouped transaction movement" in str(exc).lower()
    assert all(
        db.get_value("Bank Transaction", name, "status") == "Unreconciled"
        for name in ("BT-GROUP-SMALL", "BT-GROUP-LARGE", "BT-GROUP-NOISE")
    )
    grouped_result = reconcile_with_existing_voucher_group(
        ["BT-GROUP-LARGE", "BT-GROUP-SMALL"],
        "Journal Entry", grouped_journal.name,
        user="USER-001", confirmed=True,
    )
    assert grouped_result["amount"] == 130959.10
    assert all(
        db.get_value("Bank Transaction", name, "status") == "Reconciled"
        for name in ("BT-GROUP-SMALL", "BT-GROUP-LARGE")
    )
    group_audits = db.sql(
        'SELECT bank_transaction, group_id, group_head FROM "Bank Reconciliation" '
        'WHERE voucher_type = ? AND voucher_no = ? AND status = ? ORDER BY bank_transaction',
        ["Journal Entry", grouped_journal.name, "Active"],
    )
    assert len(group_audits) == 2
    assert len({row["group_id"] for row in group_audits}) == 1
    assert sorted(row["group_head"] for row in group_audits) == [0, 1]
    try:
        reconcile_with_existing_voucher(
            "BT-GROUP-NOISE", "Journal Entry", grouped_journal.name,
            user="USER-001", confirmed=True,
        )
        raise AssertionError("A grouped voucher bank leg must not be reused")
    except Exception as exc:
        assert "already reconciled" in str(exc).lower()

    group_undo = undo_reconciliation("BT-GROUP-SMALL", user="USER-001", confirmed=True)
    assert set(group_undo["bank_transactions"]) == {"BT-GROUP-SMALL", "BT-GROUP-LARGE"}
    assert all(
        db.get_value("Bank Transaction", name, "status") == "Unreconciled"
        for name in ("BT-GROUP-SMALL", "BT-GROUP-LARGE")
    )

    # REST and chat both accept the exact group returned by the read-only
    # suggestion and pass it to the same atomic service.
    with TestClient(app) as group_client:
        login = group_client.post("/api/auth/login", json={
            "email": "admin@example.com", "password": "test-password-123",
        })
        assert login.status_code == 200, login.text
        response = group_client.post("/api/bank-reconciliation/match-existing", json={
            "bank_transaction": "BT-GROUP-LARGE",
            "bank_transactions": ["BT-GROUP-LARGE", "BT-GROUP-SMALL"],
            "voucher_type": "Journal Entry",
            "voucher_no": grouped_journal.name,
            "confirmed": True,
        })
        assert response.status_code == 200, response.text
        assert set(response.json()["bank_transactions"]) == {
            "BT-GROUP-SMALL", "BT-GROUP-LARGE",
        }
    undo_reconciliation("BT-GROUP-LARGE", user="USER-001", confirmed=True)

    chat_group = _handle_reconcile_bank_transaction({
        "bank_transactions": ["BT-GROUP-LARGE", "BT-GROUP-SMALL"],
        "mode": "existing_voucher",
        "voucher_type": "Journal Entry",
        "voucher_no": grouped_journal.name,
        "confirmed": True,
    }, user)
    assert chat_group.get("status") == "Reconciled", chat_group
    grouped_journal.cancel()
    assert all(
        db.get_value("Bank Transaction", name, "status") == "Unreconciled"
        for name in ("BT-GROUP-SMALL", "BT-GROUP-LARGE")
    )

    print(f"  [bank reconciliation] suggestions/payment/existing/journal/undo OK on {backend}")

    db.close()
    if not os.environ.get("LAMBDA_ERP_TEST_DB"):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass


def main():
    print("Bank reconciliation checks")
    check_reconciliation()
    print("All bank reconciliation checks passed.")


if __name__ == "__main__":
    main()
