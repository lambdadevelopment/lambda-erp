"""
Journal Entry.

Journal Entry is the manual bookkeeping tool - allows direct debit/credit
entries to any accounts. Used for:
- Opening balances
- Adjustments
- Expense accruals
- Inter-company transactions
- Write-offs

GL entries are created directly from the Journal Entry Account rows.
The key constraint: total debits MUST equal total credits.
"""

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

    LINK_FIELDS = {
        "company": "Company",
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
            "reduction": lambda row: flt(row.get("credit")) - flt(row.get("debit")),
        },
        "POS Invoice": {
            "party_field": "customer",
            "allowed_party_type": "Customer",
            "reduction": lambda row: flt(row.get("credit")) - flt(row.get("debit")),
        },
        "Purchase Invoice": {
            "party_field": "supplier",
            "allowed_party_type": "Supplier",
            "reduction": lambda row: flt(row.get("debit")) - flt(row.get("credit")),
        },
    }

    def validate(self):
        if not self.get("accounts"):
            raise ValidationError("At least one account row is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._validate_debit_credit()
        self._validate_references()
        self._set_totals()

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
        make_gl_entries(gl_entries)
        self._update_referenced_outstanding()

    def on_cancel(self):
        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )
        self._update_referenced_outstanding(cancel=True)

    def _validate_references(self):
        """Referenced party-ledger rows must be coherent with the invoice they
        claim to settle. Otherwise the JE can post a valid GL but corrupt the
        subledger by driving the invoice past zero or tagging the wrong party.
        """
        db = get_db()
        for idx, row in enumerate(self.get("accounts") or [], start=1):
            ref_dt = row.get("reference_doctype")
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

            invoice = db.get_value(
                ref_dt,
                ref_name,
                [meta["party_field"], "docstatus", "outstanding_amount"],
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

            reduction = meta["reduction"](row)
            if not reduction:
                continue

            current = flt(invoice.get("outstanding_amount"))
            if reduction > abs(current) + 0.01:
                raise ValidationError(
                    f"Journal Entry: row {idx} reduces {ref_dt} {ref_name} by {reduction}, "
                    f"which exceeds its remaining outstanding ({abs(current)})"
                )

    def _update_referenced_outstanding(self, cancel=False):
        """When a Journal Entry row carries a reference_doctype + reference_name
        (e.g. a write-off against a Sales Invoice), mirror the accounting
        effect onto the invoice's outstanding_amount. Without this, the GL
        is correct but AR/AP aging stays stale — a write-off JE that credits
        AR would zero the account while the invoice still shows outstanding.
        """
        db = get_db()
        for row in self.get("accounts") or []:
            ref_dt = row.get("reference_doctype")
            ref_name = row.get("reference_name")
            if not ref_dt or not ref_name:
                continue
            meta = self._REFERENCE_META.get(ref_dt)
            if not meta:
                continue

            reduction = meta["reduction"](row)
            if cancel:
                reduction = -reduction
            if not reduction:
                continue

            current = flt(db.get_value(ref_dt, ref_name, "outstanding_amount"))
            new_outstanding = current - reduction
            if abs(new_outstanding) < 0.01:
                new_outstanding = 0
            db.set_value(ref_dt, ref_name, "outstanding_amount", flt(new_outstanding, 2))
        db.commit()

    def _get_gl_entries(self):
        """Build GL entries from Journal Entry Account rows.

        Unlike invoices where GL entries are computed from item/tax totals,
        journal entries map 1:1 from account rows to GL entries.
        """
        gl_entries = []

        for row in self.get("accounts"):
            if not (flt(row.get("debit")) or flt(row.get("credit"))):
                continue

            gl_entries.append(
                _dict(
                    account=row.get("account"),
                    party_type=row.get("party_type"),
                    party=row.get("party"),
                    cost_center=row.get("cost_center"),
                    debit=flt(row.get("debit"), 2),
                    credit=flt(row.get("credit"), 2),
                    debit_in_account_currency=flt(row.get("debit_in_account_currency") or row.get("debit"), 2),
                    credit_in_account_currency=flt(row.get("credit_in_account_currency") or row.get("credit"), 2),
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
