"""Safe, auditable reconciliation of imported bank transactions.

An imported Bank Transaction is evidence from the bank, not a ledger posting.
Reconciliation either links that evidence to an existing submitted voucher or
creates and submits the appropriate Payment Entry / Journal Entry. One exact
voucher bank leg may cover a group of imported transactions, but it can only be
consumed by one active group. Every transaction keeps its own audit row and
every reversal remains in ``Bank Reconciliation`` as history.
"""

from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import re
import unicodedata

from lambda_erp.accounting.camt import mask_iban
from lambda_erp.accounting.journal_entry import JournalEntry
from lambda_erp.accounting.payment_entry import PaymentEntry
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import _dict, flt, new_name, now


_VOUCHER_TYPES = {"Payment Entry", "Journal Entry"}


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _transaction(name: str) -> dict:
    rows = get_db().sql(
        'SELECT bt.*, ba.company AS company, ba.account AS mapped_bank_account, '
        'ba.currency AS bank_currency, c.default_currency AS base_currency '
        'FROM "Bank Transaction" bt '
        'LEFT JOIN "Bank Account" ba ON ba.name = bt.bank_account_id '
        'LEFT JOIN "Company" c ON c.name = ba.company '
        'WHERE bt.name = ?',
        [name],
    )
    if not rows:
        raise ValidationError(f"Bank Transaction {name!r} does not exist")
    tx = dict(rows[0])
    if not tx.get("bank_statement_import"):
        raise ValidationError("Only imported bank transactions can use bank reconciliation")
    if not tx.get("bank_account_id") or not tx.get("company") or not tx.get("bank_account"):
        raise ValidationError("The bank transaction has no complete Bank Account mapping")
    return tx


def _amount(tx: dict) -> float:
    return flt(tx.get("deposit") or tx.get("withdrawal"), 2)


def _is_deposit(tx: dict) -> bool:
    return flt(tx.get("deposit")) > 0


def _signed_amount(tx: dict) -> float:
    return _amount(tx) if _is_deposit(tx) else -_amount(tx)


def _active_reconciliation(bank_transaction: str) -> dict | None:
    rows = get_db().sql(
        'SELECT * FROM "Bank Reconciliation" '
        'WHERE bank_transaction = ? AND status = ? LIMIT 1',
        [bank_transaction, "Active"],
    )
    return dict(rows[0]) if rows else None


def _group_rows(group_id: str | None, *, status: str = "Active") -> list[dict]:
    if not group_id:
        return []
    return [dict(row) for row in get_db().sql(
        'SELECT * FROM "Bank Reconciliation" '
        'WHERE group_id = ? AND status = ? ORDER BY group_head DESC, name',
        [group_id, status],
    )]


def _public_active_reconciliation(active: dict | None) -> dict | None:
    if not active:
        return None
    result = dict(active)
    rows = _group_rows(result.get("group_id"))
    result["bank_transactions"] = [row["bank_transaction"] for row in rows]
    return result


def _ensure_available(tx: dict) -> None:
    if _amount(tx) <= 0 or tx.get("status") == "Informational":
        raise ValidationError("Informational or zero-value bank transactions cannot be reconciled")
    active = _active_reconciliation(tx["name"])
    if active:
        raise ValidationError(
            f"Bank Transaction {tx['name']} is already reconciled with "
            f"{active['voucher_type']} {active['voucher_no']}"
        )
    if tx.get("status") not in (None, "Unreconciled"):
        raise ValidationError(
            f"Bank Transaction {tx['name']} has status {tx.get('status')!r}, not Unreconciled"
        )


def _public_transaction(tx: dict) -> dict:
    return {
        "name": tx.get("name"),
        "bank_account": tx.get("bank_account"),
        "bank_account_id": tx.get("bank_account_id"),
        "bank_statement_import": tx.get("bank_statement_import"),
        "company": tx.get("company"),
        "base_currency": tx.get("base_currency"),
        "bank_currency": tx.get("bank_currency"),
        "posting_date": tx.get("posting_date"),
        "value_date": tx.get("value_date"),
        "deposit": flt(tx.get("deposit"), 2),
        "withdrawal": flt(tx.get("withdrawal"), 2),
        "amount": _amount(tx),
        "currency": tx.get("currency"),
        "description": tx.get("description"),
        "remittance_information": tx.get("remittance_information"),
        "reference_number": tx.get("reference_number"),
        "counterparty_name": tx.get("counterparty_name"),
        "counterparty_iban_masked": mask_iban(tx.get("counterparty_iban") or ""),
        "structured_reference": tx.get("structured_reference"),
        "status": tx.get("status"),
        "reference_doctype": tx.get("reference_doctype"),
        "reference_name": tx.get("reference_name"),
        "reconciled_by": tx.get("reconciled_by"),
        "reconciled_at": tx.get("reconciled_at"),
    }


def list_bank_transactions(*, status: str = "Unreconciled", limit: int = 100) -> dict:
    """Return a compact reconciliation queue (never expose full counterparty IBANs)."""
    limit = max(1, min(int(limit or 100), 500))
    params: list[object] = []
    where = 'WHERE bt.bank_statement_import IS NOT NULL AND bt.status != \'Informational\''
    if status and status != "All":
        where += " AND bt.status = ?"
        params.append(status)
    rows = get_db().sql(
        'SELECT bt.*, ba.company AS company, ba.account AS mapped_bank_account, '
        'ba.currency AS bank_currency, c.default_currency AS base_currency '
        'FROM "Bank Transaction" bt '
        'LEFT JOIN "Bank Account" ba ON ba.name = bt.bank_account_id '
        'LEFT JOIN "Company" c ON c.name = ba.company '
        f'{where} ORDER BY bt.posting_date DESC, bt.name DESC LIMIT ?',
        [*params, limit],
    )
    return {"rows": [_public_transaction(dict(row)) for row in rows]}


def _invoice_profile(tx: dict, doctype: str, invoice: dict) -> tuple[str, str, str, str] | None:
    """Return payment type, party type, party id, party-ledger account."""
    is_return = bool(invoice.get("is_return"))
    deposit = _is_deposit(tx)
    if doctype == "Sales Invoice":
        if deposit != (not is_return):
            return None
        return (
            "Receive" if deposit else "Pay",
            "Customer",
            invoice.get("customer"),
            invoice.get("debit_to"),
        )
    if doctype == "Purchase Invoice":
        if deposit != is_return:
            return None
        return (
            "Receive" if deposit else "Pay",
            "Supplier",
            invoice.get("supplier"),
            invoice.get("credit_to"),
        )
    return None


def _invoice_candidates(tx: dict, limit: int) -> list[dict]:
    db = get_db()
    total = _amount(tx)
    haystack = _normalise(" ".join(str(tx.get(key) or "") for key in (
        "description", "remittance_information", "reference_number",
        "structured_reference", "end_to_end_id", "counterparty_name",
    )))
    candidates: list[dict] = []
    for doctype, party_field, party_name_field, ledger_field in (
        ("Sales Invoice", "customer", "customer_name", "debit_to"),
        ("Purchase Invoice", "supplier", "supplier_name", "credit_to"),
    ):
        rows = db.sql(
            f'SELECT name, {party_field}, {party_name_field}, posting_date, due_date, '
            f'company, currency, grand_total, outstanding_amount, is_return, {ledger_field} '
            f'FROM "{doctype}" WHERE docstatus = 1 AND company = ? AND currency = ? '
            f'AND ABS(COALESCE(outstanding_amount, 0)) > 0.009 '
            f'ORDER BY posting_date DESC LIMIT 500',
            [tx["company"], tx.get("currency")],
        )
        for raw in rows:
            invoice = dict(raw)
            profile = _invoice_profile(tx, doctype, invoice)
            if not profile:
                continue
            payment_type, party_type, party, ledger_account = profile
            outstanding = abs(flt(invoice.get("outstanding_amount"), 2))
            reasons: list[str] = []
            score = 0
            delta = abs(outstanding - total)
            if delta <= 0.01:
                score += 60
                reasons.append("exact_amount")
            elif outstanding >= total:
                score += 20
                reasons.append("possible_partial_payment")
            else:
                score += 10
                reasons.append("possible_multi_invoice_payment")

            invoice_token = _normalise(invoice["name"])
            if invoice_token and invoice_token in haystack:
                score += 55
                reasons.append("document_reference")

            party_name = invoice.get(party_name_field) or ""
            tx_party = tx.get("counterparty_name") or ""
            similarity = SequenceMatcher(None, _normalise(party_name), _normalise(tx_party)).ratio()
            if similarity >= 0.92:
                score += 40
                reasons.append("counterparty_exact")
            elif similarity >= 0.60:
                score += round(similarity * 30)
                reasons.append("counterparty_similar")

            try:
                days = abs((date.fromisoformat(tx["posting_date"]) - date.fromisoformat(invoice["posting_date"])).days)
            except (TypeError, ValueError):
                days = 9999
            if days <= 7:
                score += 12
                reasons.append("date_near")
            elif days <= 90:
                score += 5

            candidates.append({
                "kind": "invoice",
                "reference_doctype": doctype,
                "reference_name": invoice["name"],
                "party_type": party_type,
                "party": party,
                "party_name": party_name,
                "payment_type": payment_type,
                "party_ledger_account": ledger_account,
                "posting_date": invoice.get("posting_date"),
                "due_date": invoice.get("due_date"),
                "currency": invoice.get("currency"),
                "grand_total": flt(invoice.get("grand_total"), 2),
                "outstanding_amount": outstanding,
                "suggested_allocation": min(outstanding, total),
                "score": score,
                "reasons": reasons,
            })
    candidates.sort(key=lambda item: (-item["score"], item["posting_date"] or "", item["reference_name"]))
    return candidates[:limit]


def _compact_group_transaction(tx: dict) -> dict:
    return {
        "name": tx.get("name"),
        "posting_date": tx.get("posting_date"),
        "amount": _amount(tx),
        "deposit": flt(tx.get("deposit"), 2),
        "withdrawal": flt(tx.get("withdrawal"), 2),
        "counterparty_name": tx.get("counterparty_name"),
        "description": tx.get("description"),
    }


def _same_day_group_pool(tx: dict, voucher_date: str) -> list[dict] | None:
    rows = [dict(row) for row in get_db().sql(
        'SELECT name, posting_date, deposit, withdrawal, counterparty_name, description '
        'FROM "Bank Transaction" WHERE bank_account = ? AND currency = ? '
        'AND posting_date = ? AND status = ? AND bank_statement_import IS NOT NULL '
        'ORDER BY name LIMIT 101',
        [tx["bank_account"], tx.get("currency"), voucher_date, "Unreconciled"],
    )]
    return None if len(rows) > 100 else rows


def _same_day_transaction_group(tx: dict, target_movement: float,
                                voucher_date: str | None,
                                pool: list[dict] | None) -> list[dict] | None:
    """Find a deterministic exact same-day subset that includes ``tx``.

    Group suggestions intentionally stay conservative: automatic grouping is
    limited to imported, unreconciled transactions on the exact voucher date,
    bank account, currency, and movement direction. Explicit reconciliation
    still validates the complete supplied group independently.
    """
    if not voucher_date or tx.get("posting_date") != voucher_date:
        return None
    if (target_movement > 0) != _is_deposit(tx):
        return None
    target_cents = round(abs(target_movement) * 100)
    selected_cents = round(_amount(tx) * 100)
    if selected_cents <= 0 or selected_cents >= target_cents:
        return None

    if pool is None:
        return None
    selected = next((row for row in pool if row["name"] == tx["name"]), None)
    if not selected:
        return None
    companions = [
        row for row in pool
        if row["name"] != tx["name"]
        and (_is_deposit(row) == _is_deposit(tx))
        and 0 < round(_amount(row) * 100) <= target_cents - selected_cents
    ]
    remainder = target_cents - selected_cents

    if companions and sum(round(_amount(row) * 100) for row in companions) == remainder:
        return [selected, *companions]

    # Retain up to two ways to reach each subtotal. A suggestion is emitted only
    # when the exact subset is unique; ambiguity is left for manual review.
    states: dict[int, list[tuple[dict, ...]]] = {0: [()]}
    for row in companions:
        cents = round(_amount(row) * 100)
        additions: dict[int, list[tuple[dict, ...]]] = {}
        for subtotal, variants in list(states.items()):
            for members in variants:
                if len(members) >= 19:
                    continue
                new_total = subtotal + cents
                if new_total > remainder:
                    continue
                bucket = additions.setdefault(new_total, [])
                candidate = (*members, row)
                if candidate not in bucket and len(bucket) < 2:
                    bucket.append(candidate)
        for subtotal, variants in additions.items():
            bucket = states.setdefault(subtotal, [])
            for variant in variants:
                if variant not in bucket and len(bucket) < 2:
                    bucket.append(variant)
        # Refuse an expensive or ambiguous search instead of guessing.
        if len(states) > 50_000:
            return None
    matches = states.get(remainder, [])
    return [selected, *matches[0]] if len(matches) == 1 else None


def _voucher_candidates(tx: dict, limit: int) -> list[dict]:
    db = get_db()
    rows = db.sql(
        'SELECT gle.voucher_type, gle.voucher_no, MIN(gle.posting_date) AS posting_date, '
        'SUM(gle.debit) AS debit, SUM(gle.credit) AS credit, '
        'SUM(gle.debit_in_account_currency) AS debit_ccy, '
        'SUM(gle.credit_in_account_currency) AS credit_ccy '
        'FROM "GL Entry" gle '
        'WHERE gle.account = ? AND gle.is_cancelled = 0 '
        'AND gle.voucher_type IN (?, ?) '
        'AND NOT EXISTS (SELECT 1 FROM "Bank Reconciliation" br '
        '  WHERE br.voucher_type = gle.voucher_type AND br.voucher_no = gle.voucher_no '
        '  AND br.status = ? AND br.bank_account = ? AND br.group_head = 1) '
        'GROUP BY gle.voucher_type, gle.voucher_no '
        'ORDER BY MIN(gle.posting_date) DESC LIMIT 500',
        [tx["bank_account"], "Payment Entry", "Journal Entry", "Active", tx["bank_account"]],
    )
    expected = _amount(tx)
    deposit = _is_deposit(tx)
    out = []
    same_day_pool: list[dict] | None = None
    same_day_pool_loaded = False
    for row in rows:
        item = dict(row)
        debit_ccy = flt(item.get("debit_ccy"), 2)
        credit_ccy = flt(item.get("credit_ccy"), 2)
        movement = debit_ccy - credit_ccy
        if not tx.get("currency") or tx.get("currency") == tx.get("base_currency"):
            movement = flt(item.get("debit"), 2) - flt(item.get("credit"), 2)
        if (movement > 0) != deposit:
            continue
        try:
            days = abs((date.fromisoformat(tx["posting_date"]) - date.fromisoformat(item["posting_date"])).days)
        except (TypeError, ValueError):
            days = 9999
        if days > 45:
            continue
        exact = abs(abs(movement) - expected) <= 0.01
        group = None
        if not exact and item.get("posting_date") == tx.get("posting_date"):
            if not same_day_pool_loaded:
                same_day_pool = _same_day_group_pool(tx, item["posting_date"])
                same_day_pool_loaded = True
            group = _same_day_transaction_group(
                tx, movement, item.get("posting_date"), same_day_pool,
            )
        if not exact and not group:
            continue
        score = 80 + (20 if days == 0 else max(0, 15 - days))
        out.append({
            "kind": "existing_voucher" if exact else "existing_voucher_group",
            "voucher_type": item["voucher_type"],
            "voucher_no": item["voucher_no"],
            "posting_date": item["posting_date"],
            "amount": abs(flt(movement, 2)),
            "currency": tx.get("currency"),
            "score": score,
            "reasons": [
                "exact_bank_movement" if exact else "exact_grouped_bank_movement",
                "same_date" if days == 0 else "date_near",
            ],
            "bank_transactions": [
                _compact_group_transaction(member)
                for member in (group or [tx])
            ],
        })
    out.sort(key=lambda item: (-item["score"], item["voucher_no"]))
    return out[:limit]


def suggest_matches(bank_transaction: str, *, limit: int = 12) -> dict:
    tx = _transaction(bank_transaction)
    active = _active_reconciliation(bank_transaction)
    return {
        "transaction": _public_transaction(tx),
        "active_reconciliation": _public_active_reconciliation(active),
        "existing_vouchers": [] if active else _voucher_candidates(tx, limit),
        "invoices": [] if active else _invoice_candidates(tx, limit),
    }


def _new_audit(tx: dict, *, mode: str, voucher_type: str, voucher_no: str,
               user: str | None, group_id: str | None = None,
               group_head: bool = True) -> str:
    name = new_name("BRC")
    get_db().insert("Bank Reconciliation", {
        "name": name,
        "bank_transaction": tx["name"],
        "bank_account": tx["bank_account"],
        "group_id": group_id or name,
        "group_head": 1 if group_head else 0,
        "mode": mode,
        "voucher_type": voucher_type,
        "voucher_no": voucher_no,
        "amount": _amount(tx),
        "status": "Active",
        "created_by": user,
        "created_at": now(),
    })
    return name


def activate_generated_reconciliation(reconciliation: str | None, *, voucher_type: str,
                                      voucher_no: str) -> None:
    """Document submit hook: atomically mark its source bank row reconciled."""
    if not reconciliation:
        return
    db = get_db()
    row = db.get_value(
        "Bank Reconciliation", reconciliation,
        ["bank_transaction", "voucher_type", "voucher_no", "amount", "status", "created_by", "created_at"],
    )
    if not row or row.get("status") != "Active":
        raise ValidationError("Bank reconciliation audit row is unavailable")
    if row.get("voucher_type") != voucher_type or row.get("voucher_no") != voucher_no:
        raise ValidationError("Bank reconciliation voucher does not match the submitted document")
    tx = _transaction(row["bank_transaction"])
    if _active_reconciliation(tx["name"])["name"] != reconciliation:
        raise ValidationError("Another reconciliation is already active for this bank transaction")
    db.set_value("Bank Transaction", tx["name"], {
        "reference_doctype": voucher_type,
        "reference_name": voucher_no,
        "allocated_amount": flt(row["amount"], 2),
        "unallocated_amount": 0,
        "status": "Reconciled",
        "reconciled_by": row.get("created_by"),
        "reconciled_at": row.get("created_at") or now(),
        "modified": now(),
    })


def reverse_generated_reconciliation(reconciliation: str | None, *, voucher_type: str,
                                     voucher_no: str) -> None:
    """Document cancel hook: unlink the bank row in the same transaction."""
    db = get_db()
    if reconciliation:
        row = db.get_value(
            "Bank Reconciliation", reconciliation,
            ["name", "bank_transaction", "group_id", "voucher_type", "voucher_no", "status", "reversed_by"],
        )
        rows = _group_rows(row.get("group_id")) if row else []
    else:
        rows = db.sql(
            'SELECT name, bank_transaction, group_id, voucher_type, voucher_no, status, reversed_by '
            'FROM "Bank Reconciliation" WHERE voucher_type = ? AND voucher_no = ? '
            'AND status = ?',
            [voucher_type, voucher_no, "Active"],
        )
    for row in rows:
        if row.get("status") != "Active":
            continue
        if row.get("voucher_type") != voucher_type or row.get("voucher_no") != voucher_no:
            raise ValidationError("Bank reconciliation voucher does not match the cancelled document")
        tx = _transaction(row["bank_transaction"])
        db.set_value("Bank Transaction", tx["name"], {
            "reference_doctype": None,
            "reference_name": None,
            "allocated_amount": 0,
            "unallocated_amount": _amount(tx),
            "status": "Unreconciled",
            "reconciled_by": None,
            "reconciled_at": None,
            "modified": now(),
        })
        db.set_value("Bank Reconciliation", row["name"], {
            "status": "Reversed",
            "reversed_by": row.get("reversed_by"),
            "reversed_at": now(),
        })


def _allocation_profile(tx: dict, allocations: list[dict]) -> tuple[tuple[str, str, str, str], list[dict]]:
    if not isinstance(allocations, list) or not allocations:
        raise ValidationError("At least one invoice allocation is required")
    db = get_db()
    prepared = []
    profile = None
    seen = set()
    allocated_total = 0.0
    for item in allocations:
        if not isinstance(item, dict):
            raise ValidationError("Each allocation must be an object")
        doctype = item.get("reference_doctype")
        name = item.get("reference_name")
        if doctype not in {"Sales Invoice", "Purchase Invoice"} or not name:
            raise ValidationError("Allocations must reference a Sales Invoice or Purchase Invoice")
        if (doctype, name) in seen:
            raise ValidationError(f"Duplicate allocation for {doctype} {name}")
        seen.add((doctype, name))
        party_field = "customer" if doctype == "Sales Invoice" else "supplier"
        ledger_field = "debit_to" if doctype == "Sales Invoice" else "credit_to"
        invoice = db.get_value(
            doctype, name,
            [party_field, "company", "currency", "docstatus", "grand_total",
             "outstanding_amount", "is_return", ledger_field],
        )
        if not invoice or flt(invoice.get("docstatus")) != 1:
            raise ValidationError(f"{doctype} {name} is unavailable or not submitted")
        if invoice.get("company") != tx["company"] or invoice.get("currency") != tx.get("currency"):
            raise ValidationError(f"{doctype} {name} has a different company or currency")
        this_profile = _invoice_profile(tx, doctype, invoice)
        if not this_profile:
            raise ValidationError(f"{doctype} {name} has the wrong payment direction")
        if profile is None:
            profile = this_profile
        elif this_profile != profile:
            raise ValidationError("All allocations must belong to the same customer or supplier")
        amount = flt(item.get("allocated_amount"), 2)
        outstanding = abs(flt(invoice.get("outstanding_amount"), 2))
        if amount <= 0 or amount > outstanding + 0.01:
            raise ValidationError(
                f"Allocation for {doctype} {name} must be positive and no more than {outstanding}"
            )
        allocated_total += amount
        prepared.append(_dict(
            reference_doctype=doctype,
            reference_name=name,
            total_amount=abs(flt(invoice.get("grand_total"), 2)),
            outstanding_amount=outstanding,
            allocated_amount=amount,
        ))
    if allocated_total > _amount(tx) + 0.01:
        raise ValidationError(
            f"Invoice allocations ({flt(allocated_total, 2)}) exceed the bank amount ({_amount(tx)})"
        )
    return profile, prepared


def reconcile_with_payment(bank_transaction: str, allocations: list[dict], *,
                           conversion_rate: float | None = None,
                           user: str | None = None, confirmed: bool = False) -> dict:
    if confirmed is not True:
        raise ValidationError("Explicit confirmation is required before posting a Payment Entry")
    tx = _transaction(bank_transaction)
    _ensure_available(tx)
    profile, references = _allocation_profile(tx, allocations)
    payment_type, party_type, party, party_ledger = profile
    total = _amount(tx)
    payment = PaymentEntry({
        "payment_type": payment_type,
        "posting_date": tx.get("posting_date"),
        "company": tx["company"],
        "party_type": party_type,
        "party": party,
        "paid_from": party_ledger if payment_type == "Receive" else tx["bank_account"],
        "paid_to": tx["bank_account"] if payment_type == "Receive" else party_ledger,
        "paid_amount": total,
        "received_amount": total,
        "currency": tx.get("currency"),
        "conversion_rate": conversion_rate,
        "reference_no": tx.get("structured_reference") or tx.get("reference_number") or tx.get("account_service_reference"),
        "reference_date": tx.get("posting_date"),
        "remarks": f"Bank reconciliation for {tx['name']}: {tx.get('description') or tx.get('remittance_information') or ''}".strip(),
        "references": references,
    })
    db = get_db()
    db._in_transaction = True
    try:
        reconciliation = _new_audit(
            tx, mode="Created Payment Entry", voucher_type="Payment Entry",
            voucher_no=payment.name, user=user,
        )
        payment._data["bank_reconciliation"] = reconciliation
        payment.submit()
    except Exception:
        db.conn.rollback()
        db._in_transaction = False
        raise
    return {
        "bank_transaction": bank_transaction,
        "reconciliation": reconciliation,
        "voucher_type": "Payment Entry",
        "voucher_no": payment.name,
        "status": "Reconciled",
        "allocated_to_invoices": flt(sum(row["allocated_amount"] for row in references), 2),
        "on_account_amount": flt(total - sum(row["allocated_amount"] for row in references), 2),
    }


def reconcile_with_journal(bank_transaction: str, counterparty_account: str, *,
                           conversion_rate: float | None = None, remarks: str | None = None,
                           user: str | None = None, confirmed: bool = False) -> dict:
    if confirmed is not True:
        raise ValidationError("Explicit confirmation is required before posting a Journal Entry")
    tx = _transaction(bank_transaction)
    _ensure_available(tx)
    db = get_db()
    counter = db.get_value(
        "Account", counterparty_account,
        ["name", "company", "root_type", "account_type", "account_currency", "is_group", "disabled"],
    )
    if not counter or counter.get("disabled") or counter.get("is_group"):
        raise ValidationError("Counterparty account is unavailable, disabled, or a group")
    if counter.get("company") != tx["company"] or counterparty_account == tx["bank_account"]:
        raise ValidationError("Counterparty account must be a different ledger account in the same company")
    if counter.get("account_type") in {"Bank", "Cash", "Receivable", "Payable"}:
        raise ValidationError(
            "Bank/Cash transfers and receivable/payable control accounts require a dedicated payment workflow"
        )
    rate = flt(conversion_rate)
    if tx.get("currency") == tx.get("base_currency"):
        rate = 1.0
    elif rate <= 0:
        raise ValidationError("A positive conversion_rate is required for a foreign-currency bank transaction")
    total = _amount(tx)
    base_amount = flt(total * rate, 2)
    counter_currency = counter.get("account_currency") or tx.get("base_currency")
    if counter_currency == tx.get("base_currency"):
        counter_ccy_amount = base_amount
    elif counter_currency == tx.get("currency"):
        counter_ccy_amount = total
    else:
        raise ValidationError(
            f"Counterparty account currency {counter_currency} is incompatible with {tx.get('currency')}"
        )
    if _is_deposit(tx):
        accounts = [
            _dict(account=tx["bank_account"], debit=base_amount, credit=0,
                  debit_in_account_currency=total, credit_in_account_currency=0),
            _dict(account=counterparty_account, debit=0, credit=base_amount,
                  debit_in_account_currency=0, credit_in_account_currency=counter_ccy_amount),
        ]
    else:
        accounts = [
            _dict(account=counterparty_account, debit=base_amount, credit=0,
                  debit_in_account_currency=counter_ccy_amount, credit_in_account_currency=0),
            _dict(account=tx["bank_account"], debit=0, credit=base_amount,
                  debit_in_account_currency=0, credit_in_account_currency=total),
        ]
    journal = JournalEntry({
        "posting_date": tx.get("posting_date"),
        "company": tx["company"],
        "voucher_type": "Bank Entry",
        "remark": remarks or tx.get("remittance_information") or tx.get("description") or f"Bank reconciliation {tx['name']}",
        "accounts": accounts,
    })
    db._in_transaction = True
    try:
        reconciliation = _new_audit(
            tx, mode="Created Journal Entry", voucher_type="Journal Entry",
            voucher_no=journal.name, user=user,
        )
        journal._data["bank_reconciliation"] = reconciliation
        journal.submit()
    except Exception:
        db.conn.rollback()
        db._in_transaction = False
        raise
    return {
        "bank_transaction": bank_transaction,
        "reconciliation": reconciliation,
        "voucher_type": "Journal Entry",
        "voucher_no": journal.name,
        "status": "Reconciled",
        "amount": total,
        "currency": tx.get("currency"),
    }


def _validate_existing_voucher_group(transactions: list[dict], voucher_type: str,
                                     voucher_no: str) -> None:
    if not transactions:
        raise ValidationError("At least one bank transaction is required")
    if voucher_type not in _VOUCHER_TYPES:
        raise ValidationError("Only submitted Payment Entries or Journal Entries can be matched")
    document = get_db().get_value(voucher_type, voucher_no, ["docstatus", "company"])
    if not document or flt(document.get("docstatus")) != 1:
        raise ValidationError(f"{voucher_type} {voucher_no} is unavailable or not submitted")
    first = transactions[0]
    if document.get("company") != first["company"]:
        raise ValidationError("Voucher and bank transaction belong to different companies")
    for tx in transactions[1:]:
        if tx.get("company") != first.get("company"):
            raise ValidationError("All grouped bank transactions must belong to the same company")
        if tx.get("bank_account") != first.get("bank_account"):
            raise ValidationError("All grouped bank transactions must use the same bank account")
        if tx.get("currency") != first.get("currency"):
            raise ValidationError("All grouped bank transactions must use the same currency")
        if _is_deposit(tx) != _is_deposit(first):
            raise ValidationError("Grouped bank transactions must have the same movement direction")
    db = get_db()
    used = db.sql(
        'SELECT group_id, bank_transaction FROM "Bank Reconciliation" '
        'WHERE voucher_type = ? AND voucher_no = ? AND bank_account = ? '
        'AND status = ? AND group_head = 1 LIMIT 1',
        [voucher_type, voucher_no, first["bank_account"], "Active"],
    )
    if used:
        raise ValidationError(
            f"The {voucher_type} {voucher_no} bank movement on account "
            f"{first['bank_account']} is already reconciled by group {used[0]['group_id']}"
        )
    rows = db.sql(
        'SELECT COALESCE(SUM(debit), 0) AS debit, COALESCE(SUM(credit), 0) AS credit, '
        'COALESCE(SUM(debit_in_account_currency), 0) AS debit_ccy, '
        'COALESCE(SUM(credit_in_account_currency), 0) AS credit_ccy '
        'FROM "GL Entry" WHERE voucher_type = ? AND voucher_no = ? '
        'AND account = ? AND is_cancelled = 0',
        [voucher_type, voucher_no, first["bank_account"]],
    )
    row = rows[0]
    movement = flt(row["debit_ccy"], 2) - flt(row["credit_ccy"], 2)
    if first.get("currency") == first.get("base_currency"):
        movement = flt(row["debit"], 2) - flt(row["credit"], 2)
    expected = flt(sum(_signed_amount(tx) for tx in transactions), 2)
    if abs(movement - expected) > 0.01:
        raise ValidationError(
            f"Voucher bank movement ({movement}) does not equal grouped transaction movement ({expected})"
        )


def _begin_existing_voucher_transaction(db, voucher_type: str, voucher_no: str) -> None:
    """Serialize consumers of one voucher while the group is validated."""
    db._in_transaction = True
    if db.dialect == "sqlite":
        db.conn.execute("BEGIN IMMEDIATE")
    else:
        # ``voucher_type`` is checked against the fixed allowlist before this
        # helper is called, so quoting the table name is safe.
        db.conn.execute(
            f'SELECT name FROM "{voucher_type}" WHERE name = ? FOR UPDATE',
            [voucher_no],
        ).fetchone()


def reconcile_with_existing_voucher_group(bank_transactions: list[str], voucher_type: str,
                                           voucher_no: str, *, user: str | None = None,
                                           confirmed: bool = False) -> dict:
    """Atomically link an exact group to one submitted voucher bank leg."""
    if confirmed is not True:
        raise ValidationError("Explicit confirmation is required before matching an existing voucher")
    if voucher_type not in _VOUCHER_TYPES:
        raise ValidationError("Only submitted Payment Entries or Journal Entries can be matched")
    if not isinstance(bank_transactions, list) or not bank_transactions:
        raise ValidationError("At least one bank transaction is required")
    names = []
    for value in bank_transactions:
        name = str(value or "").strip()
        if not name:
            raise ValidationError("Every grouped bank transaction needs a name")
        if name in names:
            raise ValidationError(f"Bank Transaction {name} occurs more than once in the group")
        names.append(name)

    db = get_db()
    try:
        _begin_existing_voucher_transaction(db, voucher_type, voucher_no)
        transactions = [_transaction(name) for name in names]
        for tx in transactions:
            _ensure_available(tx)
        _validate_existing_voucher_group(transactions, voucher_type, voucher_no)

        reconciliations: list[str] = []
        group_id = None
        for index, tx in enumerate(transactions):
            reconciliation = _new_audit(
                tx, mode="Existing Voucher", voucher_type=voucher_type,
                voucher_no=voucher_no, user=user, group_id=group_id,
                group_head=index == 0,
            )
            if group_id is None:
                group_id = reconciliation
            reconciliations.append(reconciliation)
        for reconciliation in reconciliations:
            activate_generated_reconciliation(
                reconciliation, voucher_type=voucher_type, voucher_no=voucher_no,
            )
        db.commit()
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db._in_transaction = False
    return {
        "bank_transaction": names[0],
        "bank_transactions": names,
        "reconciliation": reconciliations[0],
        "reconciliations": reconciliations,
        "group_id": group_id,
        "voucher_type": voucher_type,
        "voucher_no": voucher_no,
        "status": "Reconciled",
        "amount": flt(sum(_amount(tx) for tx in transactions), 2),
        "currency": transactions[0].get("currency"),
    }


def reconcile_with_existing_voucher(bank_transaction: str, voucher_type: str, voucher_no: str, *,
                                    user: str | None = None, confirmed: bool = False) -> dict:
    return reconcile_with_existing_voucher_group(
        [bank_transaction], voucher_type, voucher_no,
        user=user, confirmed=confirmed,
    )


def undo_reconciliation(bank_transaction: str, *, user: str | None = None,
                        confirmed: bool = False) -> dict:
    if confirmed is not True:
        raise ValidationError("Explicit confirmation is required before reversing a reconciliation")
    tx = _transaction(bank_transaction)
    audit = _active_reconciliation(bank_transaction)
    if not audit:
        raise ValidationError(f"Bank Transaction {bank_transaction} has no active reconciliation")
    db = get_db()
    if audit["mode"] in {"Created Payment Entry", "Created Journal Entry"}:
        document_cls = PaymentEntry if audit["voucher_type"] == "Payment Entry" else JournalEntry
        document = document_cls.load(audit["voucher_no"])
        db._in_transaction = True
        db.set_value("Bank Reconciliation", audit["name"], "reversed_by", user)
        try:
            document.cancel()
        except Exception:
            db.conn.rollback()
            db._in_transaction = False
            raise
    else:
        db._in_transaction = True
        try:
            group = _group_rows(audit.get("group_id")) or [audit]
            for member in group:
                member_tx = _transaction(member["bank_transaction"])
                db.set_value("Bank Reconciliation", member["name"], {
                    "status": "Reversed", "reversed_by": user, "reversed_at": now(),
                })
                db.set_value("Bank Transaction", member_tx["name"], {
                    "reference_doctype": None, "reference_name": None,
                    "allocated_amount": 0, "unallocated_amount": _amount(member_tx),
                    "status": "Unreconciled", "reconciled_by": None,
                    "reconciled_at": None, "modified": now(),
                })
            db.commit()
        except Exception:
            db.conn.rollback()
            raise
        finally:
            db._in_transaction = False
    return {
        "bank_transaction": bank_transaction,
        "bank_transactions": [
            row["bank_transaction"]
            for row in (_group_rows(audit.get("group_id"), status="Reversed") or [audit])
        ],
        "reversed_reconciliation": audit["name"],
        "voucher_type": audit["voucher_type"],
        "voucher_no": audit["voucher_no"],
        "voucher_cancelled": audit["mode"] != "Existing Voucher",
        "status": "Unreconciled",
    }
