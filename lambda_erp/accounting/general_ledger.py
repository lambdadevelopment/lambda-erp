"""
General Ledger entry creation engine.

The heart of double-entry bookkeeping. Every financial transaction ultimately
calls make_gl_entries() to post debit/credit entries to the General Ledger.

Key invariant: total debits MUST equal total credits for every voucher.

The flow:
  Document.on_submit() -> make_gl_entries(gl_map)
    -> process_gl_map() (merge similar entries)
    -> toggle_debit_credit_if_negative()
    -> process_debit_credit_difference() (round-off adjustment)
    -> save_entries() (persist to DB)
"""

import copy
from lambda_erp.utils import _dict, flt, new_name, now
from lambda_erp.database import get_db
from lambda_erp.exceptions import DebitCreditNotEqual, InvalidAccountError

def make_gl_entries(gl_map, cancel=False, merge_entries=True):
    """Create GL entries from a list of GL entry dicts.

    This is the main entry point, called by every document that affects
    the accounting ledger (invoices, payments, journal entries, stock entries
    with perpetual inventory, etc.).

    Args:
        gl_map: list of _dict with keys like account, debit, credit,
                voucher_type, voucher_no, posting_date, company, etc.
        cancel: if True, create reversing entries instead
        merge_entries: if True, merge entries with same account/party/etc.
    """
    if not gl_map:
        return

    if cancel:
        make_reverse_gl_entries(gl_map)
        return

    gl_map = process_gl_map(gl_map, merge_entries)

    if gl_map and len(gl_map) > 1:
        process_debit_credit_difference(gl_map)
        validate_disabled_accounts(gl_map)

        # Budget check — validate expense entries before saving
        from lambda_erp.accounting.budget import validate_expense_against_budget
        for entry in gl_map:
            validate_expense_against_budget(entry)

        save_entries(gl_map)
    elif gl_map:
        raise InvalidAccountError(
            "Incorrect number of General Ledger Entries found. "
            "You might have selected a wrong Account in the transaction."
        )

def process_gl_map(gl_map, merge_entries=True):
    """Process the GL entry map: merge similar entries, fix negative values."""
    if not gl_map:
        return []

    if merge_entries:
        gl_map = merge_similar_entries(gl_map)

    gl_map = toggle_debit_credit_if_negative(gl_map)

    return gl_map

def merge_similar_entries(gl_map):
    """Merge GL entries that have the same account + party + voucher_detail.

    This is important because a single invoice might have multiple items
    hitting the same income account - we want one consolidated GL entry.
    """
    merged = []
    merge_keys = [
        "account", "cost_center", "party", "party_type",
        "against_voucher", "against_voucher_type", "voucher_no"
    ]

    for entry in gl_map:
        key = tuple(entry.get(k, "") for k in merge_keys)
        entry["_merge_key"] = key

        same_head = None
        for existing in merged:
            if existing.get("_merge_key") == key:
                same_head = existing
                break

        if same_head:
            same_head["debit"] = flt(same_head["debit"]) + flt(entry.get("debit", 0))
            same_head["credit"] = flt(same_head["credit"]) + flt(entry.get("credit", 0))
            same_head["debit_in_account_currency"] = (
                flt(same_head.get("debit_in_account_currency", 0))
                + flt(entry.get("debit_in_account_currency", 0))
            )
            same_head["credit_in_account_currency"] = (
                flt(same_head.get("credit_in_account_currency", 0))
                + flt(entry.get("credit_in_account_currency", 0))
            )
        else:
            merged.append(entry)

    # Filter zero entries
    merged = [e for e in merged if flt(e.get("debit"), 2) != 0 or flt(e.get("credit"), 2) != 0]

    return merged

def toggle_debit_credit_if_negative(gl_map):
    """If debit is negative, move it to credit and vice versa.

    Ensures all GL entries have non-negative debit and credit values.
    """
    for entry in gl_map:
        debit = flt(entry.get("debit", 0))
        credit = flt(entry.get("credit", 0))

        if debit < 0:
            credit = credit - debit
            debit = 0.0

        if credit < 0:
            debit = debit - credit
            credit = 0.0

        entry["debit"] = flt(debit, 2)
        entry["credit"] = flt(credit, 2)

        # Same for account currency amounts
        debit_acc = flt(entry.get("debit_in_account_currency", 0))
        credit_acc = flt(entry.get("credit_in_account_currency", 0))

        if debit_acc < 0:
            credit_acc = credit_acc - debit_acc
            debit_acc = 0.0
        if credit_acc < 0:
            debit_acc = debit_acc - credit_acc
            credit_acc = 0.0

        entry["debit_in_account_currency"] = flt(debit_acc, 2)
        entry["credit_in_account_currency"] = flt(credit_acc, 2)

    return gl_map

def process_debit_credit_difference(gl_map):
    """Ensure total debits == total credits, adding round-off entry if needed.

    This is the double-entry bookkeeping integrity check. Due to rounding,
    there can be small differences which are posted to a round-off account.
    """
    precision = 2
    debit_credit_diff = sum(
        flt(e.get("debit", 0), precision) - flt(e.get("credit", 0), precision)
        for e in gl_map
    )
    debit_credit_diff = flt(debit_credit_diff, precision)

    allowance = 0.5  # the reference implementation allows 0.5 for non-JE/PE documents

    if abs(debit_credit_diff) > allowance:
        raise DebitCreditNotEqual(
            f"Debit and Credit not equal for {gl_map[0].get('voucher_type')} "
            f"#{gl_map[0].get('voucher_no')}. Difference is {debit_credit_diff}."
        )

    # Add round-off entry if there's a small difference
    if abs(debit_credit_diff) >= 0.01:
        db = get_db()
        company = gl_map[0].get("company")
        round_off_account = db.get_value("Company", company, "round_off_account")
        round_off_cost_center = db.get_value("Company", company, "round_off_cost_center")

        if not round_off_account:
            round_off_account = db.get_value("Company", company, "default_expense_account")

        if round_off_account:
            gl_map.append(
                _dict(
                    account=round_off_account,
                    cost_center=round_off_cost_center,
                    debit=abs(debit_credit_diff) if debit_credit_diff < 0 else 0,
                    credit=debit_credit_diff if debit_credit_diff > 0 else 0,
                    debit_in_account_currency=abs(debit_credit_diff) if debit_credit_diff < 0 else 0,
                    credit_in_account_currency=debit_credit_diff if debit_credit_diff > 0 else 0,
                    voucher_type=gl_map[0].get("voucher_type"),
                    voucher_no=gl_map[0].get("voucher_no"),
                    posting_date=gl_map[0].get("posting_date"),
                    company=company,
                    remarks="Round-off adjustment",
                )
            )

def validate_disabled_accounts(gl_map):
    """Check that no disabled accounts are used."""
    db = get_db()
    for entry in gl_map:
        account = entry.get("account")
        if account:
            disabled = db.get_value("Account", account, "disabled")
            if disabled:
                raise InvalidAccountError(
                    f"Cannot create accounting entries against disabled account: {account}"
                )

def save_entries(gl_map):
    """Persist GL entries to the database."""
    db = get_db()
    for entry in gl_map:
        gle = _dict(
            name=new_name("GLE"),
            posting_date=entry.get("posting_date"),
            account=entry.get("account"),
            party_type=entry.get("party_type"),
            party=entry.get("party"),
            cost_center=entry.get("cost_center"),
            debit=flt(entry.get("debit"), 2),
            credit=flt(entry.get("credit"), 2),
            debit_in_account_currency=flt(entry.get("debit_in_account_currency"), 2),
            credit_in_account_currency=flt(entry.get("credit_in_account_currency"), 2),
            account_currency=entry.get("account_currency", "USD"),
            voucher_type=entry.get("voucher_type"),
            voucher_no=entry.get("voucher_no"),
            against_voucher_type=entry.get("against_voucher_type"),
            against_voucher=entry.get("against_voucher"),
            remarks=entry.get("remarks"),
            is_opening=entry.get("is_opening", "No"),
            is_cancelled=0,
            company=entry.get("company"),
            fiscal_year=entry.get("fiscal_year"),
            creation=now(),
            modified=now(),
        )
        db.insert("GL Entry", gle)

def make_reverse_gl_entries(gl_entries=None, voucher_type=None, voucher_no=None):
    """Create reversing GL entries (swap debit/credit).

    Called on document cancellation.
    """
    db = get_db()

    if not gl_entries:
        gl_entries = db.get_all(
            "GL Entry",
            filters={"voucher_type": voucher_type, "voucher_no": voucher_no, "is_cancelled": 0},
            fields=["*"],
        )

    if not gl_entries:
        return

    # Mark original entries as cancelled
    for entry in gl_entries:
        db.set_value("GL Entry", entry["name"], "is_cancelled", 1)

    # Create reverse entries
    for entry in gl_entries:
        reverse = _dict(
            name=new_name("GLE"),
            posting_date=entry.get("posting_date"),
            account=entry.get("account"),
            party_type=entry.get("party_type"),
            party=entry.get("party"),
            cost_center=entry.get("cost_center"),
            debit=flt(entry.get("credit"), 2),          # swapped
            credit=flt(entry.get("debit"), 2),           # swapped
            debit_in_account_currency=flt(entry.get("credit_in_account_currency"), 2),
            credit_in_account_currency=flt(entry.get("debit_in_account_currency"), 2),
            account_currency=entry.get("account_currency", "USD"),
            voucher_type=entry.get("voucher_type"),
            voucher_no=entry.get("voucher_no"),
            against_voucher_type=entry.get("against_voucher_type"),
            against_voucher=entry.get("against_voucher"),
            remarks=f"On cancellation of {entry.get('voucher_no')}",
            is_opening=entry.get("is_opening", "No"),
            is_cancelled=1,
            company=entry.get("company"),
            fiscal_year=entry.get("fiscal_year"),
            creation=now(),
            modified=now(),
        )
        db.insert("GL Entry", reverse)

    db.commit()

def get_gl_balance(account, company=None, posting_date=None):
    """Get the balance of an account (debit - credit).

    Convenience function for querying account balances.
    """
    db = get_db()
    filters = {"account": account, "is_cancelled": 0}
    if company:
        filters["company"] = company

    query = """
        SELECT
            COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0) as balance
        FROM "GL Entry"
        WHERE account = ? AND is_cancelled = 0
    """
    params = [account]

    if company:
        query += " AND company = ?"
        params.append(company)
    if posting_date:
        query += " AND posting_date <= ?"
        params.append(posting_date)

    result = db.sql(query, params)
    return flt(result[0]["balance"]) if result else 0
