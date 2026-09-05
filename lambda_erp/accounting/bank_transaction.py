"""
Bank Transaction.

Bank Transaction represents a single entry from a bank statement. Imported
rows remain immutable bank evidence; the reconciliation service either links
them to an existing posting or creates the necessary posting atomically.
"""

from lambda_erp.model import Document
from lambda_erp.utils import flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

class BankTransaction(Document):
    DOCTYPE = "Bank Transaction"
    CHILD_TABLES = {
        "details": ("Bank Transaction Detail", None),
    }
    PREFIX = "BT"

    LINK_FIELDS = {
        # bank_account is the historical GL Account field. bank_account_id is
        # the real-world account/IBAN mapping introduced by CAMT imports.
        "bank_account": "Account",
        "bank_account_id": "Bank Account",
        "bank_statement_import": "Bank Statement Import",
    }
    ACCOUNT_TYPE_CONSTRAINTS = {
        "bank_account": {"account_type": "Bank"},
    }

    def validate(self):
        db = get_db()
        existing = db.get_value(self.DOCTYPE, self.name, "bank_statement_import")
        # An imported row is immutable bank evidence. Reconciliation metadata is
        # changed only by the audited reconciliation service (via db.set_value),
        # never through generic draft editing.
        if existing:
            raise ValidationError(
                "Imported Bank Transactions are read-only; use Bank Reconciliation "
                "for matching changes"
            )
        deposit = flt(self.deposit)
        withdrawal = flt(self.withdrawal)
        # CAMT statements can contain booked zero-amount informational rows
        # (for example a quarterly interest close with no interest due). Keep
        # those for a complete audit trail, while manual transactions still
        # require an actual movement.
        if not deposit and not withdrawal and not self.bank_statement_import:
            raise ValidationError("Either Deposit or Withdrawal amount is required")
        if deposit and withdrawal:
            raise ValidationError("Cannot have both Deposit and Withdrawal")

        if not self.posting_date:
            self._data["posting_date"] = nowdate()

        self._calculate_unallocated()
        self._set_status()

    def _calculate_unallocated(self):
        total = flt(self.deposit) or flt(self.withdrawal)
        allocated = flt(self.allocated_amount)
        self._data["unallocated_amount"] = flt(total - allocated, 2)

    def _set_status(self):
        unallocated = flt(self._data.get("unallocated_amount", 0))
        total = flt(self.deposit) or flt(self.withdrawal)
        if total == 0 and self.bank_statement_import:
            self._data["status"] = "Informational"
        elif unallocated <= 0 and total > 0:
            self._data["status"] = "Reconciled"
        elif flt(self.allocated_amount) > 0:
            self._data["status"] = "Partially Reconciled"
        else:
            self._data["status"] = "Unreconciled"

def reconcile_bank_transaction(bank_transaction_name, reference_doctype, reference_name):
    """Compatibility wrapper for the old one-voucher matcher.

    Direct invoice matching was unsafe: it marked the bank row reconciled but
    never posted the cash movement. The replacement therefore accepts only an
    already-submitted Payment Entry or Journal Entry whose bank leg exactly
    matches the imported transaction.
    """
    from lambda_erp.accounting.bank_reconciliation import reconcile_with_existing_voucher
    return reconcile_with_existing_voucher(
        bank_transaction_name,
        reference_doctype,
        reference_name,
        confirmed=True,
    )
