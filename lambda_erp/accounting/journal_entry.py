"""
Journal Entry.

Journal Entry is the manual bookkeeping tool - allows direct debit/credit
entries to any accounts. Used for:
- Opening balances
- Adjustments
- Expense accruals
- Company-specific adjustments
- Write-offs

GL entries are created directly from the Journal Entry Account rows.
The key constraint: total debits MUST equal total credits.
"""

import math
from collections import defaultdict
from lambda_erp.accounting.settlement import journal_reduction, validate_reduction
from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.exceptions import ValidationError, DebitCreditNotEqual

class JournalEntry(Document):
    DOCTYPE = "Journal Entry"
    CHILD_TABLES = {
        "accounts": ("Journal Entry Account", None),
    }
    PREFIX = "JV"
    REQUIRED_FIELDS = ('company', 'accounts')
    CONDITIONAL_REQUIREMENTS = (
        'Invoice allocations are summed per invoice, must reduce its signed outstanding, and require the original company, party and receivable/payable account. Use account-currency amounts and the booked exchange rate for foreign-currency settlement.',
        'Account rows must balance debit and credit. Submission requires at least one nonzero line.',
        'Invoice reference type and name must be provided together and match the row party and receivable/payable account.',
    )

    LINK_FIELDS = {
        "company": "Company",
        "bank_reconciliation": "Bank Reconciliation",
    }
    CHILD_LINK_FIELDS = {
        "accounts": {
            "account": "Account",
            "cost_center": "Cost Center",
        },
    }
    CHILD_DYNAMIC_LINK_FIELDS = {
        "accounts": {
            "party": ("party_type", {"Customer": "Customer", "Supplier": "Supplier"}),
            "reference_name": ("reference_doctype", {
                "Sales Invoice": "Sales Invoice",
                "Purchase Invoice": "Purchase Invoice",
                "POS Invoice": "POS Invoice",
            }),
        },
    }

    _REFERENCE_META = {
        "Sales Invoice": {
            "party_field": "customer",
            "allowed_party_type": "Customer",
        },
        "POS Invoice": {
            "party_field": "customer",
            "allowed_party_type": "Customer",
        },
        "Purchase Invoice": {
            "party_field": "supplier",
            "allowed_party_type": "Supplier",
        },
    }

    def validate(self):
        if not self.get("accounts"):
            raise ValidationError("At least one account row is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._normalize_amounts()
        self._validate_debit_credit()
        self._validate_references()
        self._set_totals()

    def _normalize_amounts(self):
        """Back-fill only missing base/account-currency amounts.

        ``None`` or an absent field means the caller supplied only the matching
        amount in the other currency. An explicit zero is meaningful: period-end
        valuation entries move the base carrying value without changing the
        foreign-currency quantity, so their *_in_account_currency amount must
        remain zero.
        """
        for row in self.get("accounts") or []:
            for field in ('debit', 'credit', 'debit_in_account_currency', 'credit_in_account_currency'):
                if row.get(field) is not None:
                    try:
                        value = float(row[field])
                    except (TypeError, ValueError):
                        raise ValidationError(f'Journal Entry {field} must be a finite number')
                    if not math.isfinite(value):
                        raise ValidationError(f'Journal Entry {field} must be a finite number')
                    row[field] = value
            debit = row.get("debit")
            credit = row.get("credit")
            dr_ac = row.get("debit_in_account_currency")
            cr_ac = row.get("credit_in_account_currency")
            if debit is None and dr_ac is not None:
                row["debit"] = flt(dr_ac)
            if credit is None and cr_ac is not None:
                row["credit"] = flt(cr_ac)
            if dr_ac is None and debit is not None:
                row["debit_in_account_currency"] = flt(debit)
            if cr_ac is None and credit is not None:
                row["credit_in_account_currency"] = flt(credit)

    def _validate_debit_credit(self):
        """Ensure total debits == total credits.

        This is the fundamental double-entry bookkeeping check.
        """
        total_debit = sum(flt(row.get("debit")) for row in self.get("accounts"))
        total_credit = sum(flt(row.get("credit")) for row in self.get("accounts"))

        diff = abs(total_debit - total_credit)
        if diff > 0.01:
            raise DebitCreditNotEqual(
                f"Total Debit ({total_debit}) must equal Total Credit ({total_credit}). "
                f"Difference: {diff}"
            )

    def _set_totals(self):
        self._data["total_debit"] = flt(
            sum(flt(row.get("debit")) for row in self.get("accounts")), 2
        )
        self._data["total_credit"] = flt(
            sum(flt(row.get("credit")) for row in self.get("accounts")), 2
        )

    def on_submit(self):
        """Post GL entries directly from account rows."""
        gl_entries = self._get_gl_entries()
        if not gl_entries:
            raise ValidationError(
                "Journal Entry has no non-zero lines to post — every account row is "
                "0/0. Set a debit or credit amount before submitting."
            )
        make_gl_entries(gl_entries)
        self._update_referenced_outstanding()
        from lambda_erp.accounting.bank_reconciliation import activate_generated_reconciliation
        activate_generated_reconciliation(
            self.bank_reconciliation,
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

    def on_cancel(self):
        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )
        self._update_referenced_outstanding(cancel=True)
        from lambda_erp.accounting.bank_reconciliation import reverse_generated_reconciliation
        reverse_generated_reconciliation(
            self.bank_reconciliation,
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

    def _validate_references(self):
        """Referenced party-ledger rows must be coherent with the invoice they
        claim to settle. Otherwise the JE can post a valid GL but corrupt the
        subledger by driving the invoice past zero or tagging the wrong party.
        """
        db = get_db()
        grouped = defaultdict(float)
        invoices = {}
        for idx, row in enumerate(self.get("accounts") or [], start=1):
            if row.get('reference_doctype') and row.get('reference_type') and row['reference_doctype'] != row['reference_type']:
                raise ValidationError('reference_doctype and legacy reference_type must agree')
            ref_dt = row.get("reference_doctype") or row.get("reference_type")
            ref_name = row.get("reference_name")
            if not ref_dt and not ref_name:
                continue
            if not ref_dt or not ref_name:
                raise ValidationError(
                    f"Journal Entry: row {idx} must set both reference_doctype and reference_name"
                )

            meta = self._REFERENCE_META.get(ref_dt)
            if not meta:
                raise ValidationError(
                    f"Journal Entry: row {idx} cannot reference unsupported doctype {ref_dt}"
                )

            row['reference_doctype'] = ref_dt

            invoice = db.get_value(
                ref_dt,
                ref_name,
                [meta["party_field"], "docstatus", "outstanding_amount", "company", "currency", "conversion_rate", "is_return", "debit_to", "credit_to"],
            )
            if not invoice:
                raise ValidationError(
                    f"Journal Entry: row {idx} reference {ref_dt} {ref_name} does not exist"
                )
            if flt(invoice.get("docstatus")) != 1:
                raise ValidationError(
                    f"Journal Entry: row {idx} reference {ref_dt} {ref_name} is not submitted"
                )

            if row.get("party_type") != meta["allowed_party_type"]:
                raise ValidationError(
                    f"Journal Entry: row {idx} reference {ref_dt} requires party_type "
                    f"{meta['allowed_party_type']}"
                )

            expected_party = invoice.get(meta["party_field"])
            if row.get("party") != expected_party:
                raise ValidationError(
                    f"Journal Entry: row {idx} reference {ref_dt} {ref_name} belongs to "
                    f"{meta['allowed_party_type']} '{expected_party}', not '{row.get('party')}'"
                )

            if invoice.company != self.company:
                raise ValidationError('Journal Entry and referenced invoice must belong to the same Company')
            reduction = journal_reduction(row, ref_dt, invoice)
            grouped[(ref_dt, ref_name)] += reduction
            invoices[(ref_dt, ref_name)] = invoice
        for (kind, name), reduction in grouped.items():
            invoice = invoices[(kind, name)]
            validate_reduction(kind, name, reduction, flt(invoice.outstanding_amount), is_return=bool(flt(invoice.is_return)))

    def _update_referenced_outstanding(self, cancel=False):
        """When a Journal Entry row carries a reference_doctype + reference_name
        (e.g. a write-off against a Sales Invoice), mirror the accounting
        effect onto the invoice's outstanding_amount. Without this, the GL
        is correct but AR/AP aging stays stale — a write-off JE that credits
        AR would zero the account while the invoice still shows outstanding.
        """
        db = get_db()
        grouped = defaultdict(float)
        for row in self.get('accounts') or []:
            kind, name = row.get('reference_doctype') or row.get('reference_type'), row.get('reference_name')
            if not kind or not name:
                continue
            invoice = db.get_value(kind, name, ['company', 'currency', 'conversion_rate', 'debit_to', 'credit_to'])
            grouped[(kind, name)] += journal_reduction(row, kind, invoice)
        for (kind, name), reduction in grouped.items():
            current = flt(db.get_value(kind, name, 'outstanding_amount'))
            value = current - reduction * (-1 if cancel else 1)
            db.set_value(kind, name, 'outstanding_amount', flt(value, 2))
        if not db._in_transaction:
            db.commit()

    def _get_gl_entries(self):
        """Build GL entries from Journal Entry Account rows.

        Unlike invoices where GL entries are computed from item/tax totals,
        journal entries map 1:1 from account rows to GL entries.
        """
        gl_entries = []
        db = get_db()
        base_currency = db.get_value("Company", self.company, "default_currency") or "USD"

        for row in self.get("accounts"):
            if not (flt(row.get("debit")) or flt(row.get("credit"))):
                continue

            debit = flt(row.get("debit"), 2)
            credit = flt(row.get("credit"), 2)
            dr_ac = row.get("debit_in_account_currency")
            cr_ac = row.get("credit_in_account_currency")
            account_currency = (
                db.get_value("Account", row.get("account"), "account_currency")
                or base_currency
            )

            gl_entries.append(
                _dict(
                    account=row.get("account"),
                    party_type=row.get("party_type"),
                    party=row.get("party"),
                    cost_center=row.get("cost_center"),
                    debit=debit,
                    credit=credit,
                    debit_in_account_currency=flt(debit if dr_ac is None else dr_ac, 2),
                    credit_in_account_currency=flt(credit if cr_ac is None else cr_ac, 2),
                    account_currency=account_currency,
                    against_voucher_type=row.get("reference_doctype") or row.get("reference_type"),
                    against_voucher=row.get("reference_name"),
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remark or "Journal Entry",
                )
            )

        return gl_entries
