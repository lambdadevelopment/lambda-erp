"""
Bank Transaction.

Bank Transaction represents a single entry from a bank statement.
It can be matched to Payment Entries or Invoices for reconciliation.
When matched, it sets clearance_date on the referenced document.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

class BankTransaction(Document):
    DOCTYPE = "Bank Transaction"
    CHILD_TABLES = {}
    PREFIX = "BT"

    def validate(self):
        deposit = flt(self.deposit)
        withdrawal = flt(self.withdrawal)
        if not deposit and not withdrawal:
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
        if unallocated <= 0 and total > 0:
            self._data["status"] = "Reconciled"
        elif flt(self.allocated_amount) > 0:
            self._data["status"] = "Partially Reconciled"
        else:
            self._data["status"] = "Unreconciled"

def reconcile_bank_transaction(bank_transaction_name, reference_doctype, reference_name):
    """Match a bank transaction to a payment entry or invoice.

    Sets clearance_date on the referenced document and updates the
    bank transaction's allocated amount and status.
    """
    db = get_db()

    bt = BankTransaction.load(bank_transaction_name)
    total = flt(bt.deposit) or flt(bt.withdrawal)
    unallocated = flt(bt._data.get("unallocated_amount", total))

    if unallocated <= 0:
        raise ValidationError("Bank Transaction is already fully reconciled")

    # Set reference on bank transaction
    bt._data["reference_doctype"] = reference_doctype
    bt._data["reference_name"] = reference_name
    bt._data["allocated_amount"] = total
    bt._calculate_unallocated()
    bt._set_status()
    bt._persist()

    # Set clearance_date on the referenced document
    if db.exists(reference_doctype, reference_name):
        db.set_value(reference_doctype, reference_name, "clearance_date", bt.posting_date)
        db.commit()

    return bt.as_dict()
