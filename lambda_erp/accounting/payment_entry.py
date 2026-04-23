"""
Payment Entry.

Payment Entry handles:
- Receiving money from customers (against Sales Invoices)
- Paying money to suppliers (against Purchase Invoices)
- Internal transfers between bank/cash accounts

GL entries on submit:
  Receive from Customer:
    Debit:  Bank/Cash Account         = received_amount
    Credit: Accounts Receivable       = paid_amount

  Pay to Supplier:
    Debit:  Accounts Payable          = paid_amount
    Credit: Bank/Cash Account         = paid_amount
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.exceptions import ValidationError

class PaymentEntry(Document):
    DOCTYPE = "Payment Entry"
    CHILD_TABLES = {
        "references": ("Payment Entry Reference", None),
    }
    PREFIX = "PE"

    LINK_FIELDS = {
        "company": "Company",
        "paid_from": "Account",
        "paid_to": "Account",
    }
    # `party` and `references[].reference_name` are dynamic — target doctype
    # resolves from party_type / reference_doctype at validation time.
    DYNAMIC_LINK_FIELDS = {
        "party": ("party_type", {"Customer": "Customer", "Supplier": "Supplier"}),
    }
    CHILD_DYNAMIC_LINK_FIELDS = {
        "references": {
            "reference_name": ("reference_doctype", {
                "Sales Invoice": "Sales Invoice",
                "Purchase Invoice": "Purchase Invoice",
                "POS Invoice": "POS Invoice",
            }),
        },
    }

    _PARTY_LEDGER_BY_TYPE = {
        "Customer": "default_receivable_account",
        "Supplier": "default_payable_account",
    }
    _ALLOWED_PARTY_TYPES = {"Customer", "Supplier"}

    def validate(self):
        if not self.payment_type:
            raise ValidationError("Payment Type is required (Receive, Pay, or Internal Transfer)")
        if not self.posting_date:
            self.posting_date = nowdate()
        if not self.paid_amount or flt(self.paid_amount) <= 0:
            raise ValidationError("Paid Amount must be greater than zero")

        if self.payment_type != "Internal Transfer":
            if self.party_type not in self._ALLOWED_PARTY_TYPES:
                raise ValidationError("Party Type is required and must be Customer or Supplier")
            if not self.party:
                raise ValidationError("Party is required for Receive and Pay entries")

        self._set_missing_values()
        self._validate_references()

    def _set_missing_values(self):
        db = get_db()

        if self.payment_type == "Receive":
            # For external receipts, money lands in Bank and the contra is the
            # party ledger for the selected party type:
            # - Customer -> AR (normal receipt)
            # - Supplier -> AP (supplier refund)
            if not self.paid_from and self.company:
                self._data["paid_from"] = self._get_default_party_ledger_account()
            if not self.paid_to and self.company:
                self._data["paid_to"] = self._get_default_bank_account()

        elif self.payment_type == "Pay":
            # For external payments, money leaves Bank and the contra is the
            # party ledger for the selected party type:
            # - Supplier -> AP (normal supplier payment)
            # - Customer -> AR (customer refund)
            if not self.paid_from and self.company:
                self._data["paid_from"] = self._get_default_bank_account()
            if not self.paid_to and self.company:
                self._data["paid_to"] = self._get_default_party_ledger_account()

        if not self.received_amount:
            self._data["received_amount"] = self.paid_amount

        if self.party and not self.party_name:
            if self.party_type == "Customer":
                self._data["party_name"] = db.get_value("Customer", self.party, "customer_name")
            elif self.party_type == "Supplier":
                self._data["party_name"] = db.get_value("Supplier", self.party, "supplier_name")

        if not self.cost_center and self.company:
            self._data["cost_center"] = db.get_value("Company", self.company, "default_cost_center")

    def _get_default_bank_account(self):
        db = get_db()
        bank = db.get_all(
            "Account",
            filters={"company": self.company, "account_type": "Bank", "is_group": 0},
            fields=["name"],
            limit=1,
        )
        return bank[0]["name"] if bank else None

    def _get_default_party_ledger_account(self):
        if not self.company:
            return None
        fieldname = self._PARTY_LEDGER_BY_TYPE.get(self.party_type)
        if not fieldname:
            return None
        return get_db().get_value("Company", self.company, fieldname)

    # Allowed (party_type, reference_doctype) combinations. Customer-side
    # payments settle sales documents; Supplier-side payments settle purchase
    # documents. Returns (credit/debit notes) use the same tables.
    _ALLOWED_REFERENCE_DOCTYPES = {
        "Customer": {"Sales Invoice", "POS Invoice"},
        "Supplier": {"Purchase Invoice"},
    }

    def _validate_references(self):
        """Validate each reference row individually, then the aggregate.

        Per-row checks catch the real footguns: cross-party allocation,
        settling a cancelled invoice, over-allocation past remaining
        outstanding, and mismatched direction (Customer PE referencing a
        Purchase Invoice).
        """
        db = get_db()
        total_allocated = 0.0
        allowed_dts = self._ALLOWED_REFERENCE_DOCTYPES.get(self.party_type, set())

        for ref in self.get("references") or []:
            doctype = ref.get("reference_doctype")
            docname = ref.get("reference_name")
            allocated = flt(ref.get("allocated_amount"))
            if not doctype or not docname:
                continue
            if allocated <= 0:
                raise ValidationError(
                    f"Allocated amount on {doctype} {docname} must be positive"
                )

            if doctype not in allowed_dts:
                raise ValidationError(
                    f"Cannot allocate a {self.party_type} payment against "
                    f"{doctype} {docname}"
                )

            invoice = db.get_value(
                doctype,
                docname,
                ["customer", "supplier", "docstatus", "outstanding_amount", "is_return"],
            )
            if not invoice:
                raise ValidationError(f"{doctype} {docname} does not exist")
            if flt(invoice.get("docstatus")) != 1:
                raise ValidationError(
                    f"{doctype} {docname} is not submitted (docstatus="
                    f"{invoice.get('docstatus')}); cannot allocate a payment to it"
                )

            expected_party = invoice.get("customer") if self.party_type == "Customer" else invoice.get("supplier")
            if expected_party != self.party:
                raise ValidationError(
                    f"{doctype} {docname} belongs to {self.party_type} "
                    f"'{expected_party}', not '{self.party}'"
                )

            current = flt(invoice.get("outstanding_amount"))
            # Return invoices carry a negative outstanding. Allocations against
            # them (refund flow) always reduce |outstanding| — compare on
            # absolute value and allow a $0.01 tolerance for rounding noise.
            if allocated > abs(current) + 0.01:
                raise ValidationError(
                    f"Allocation {allocated} on {doctype} {docname} exceeds its "
                    f"remaining outstanding ({abs(current)})"
                )

            total_allocated += allocated

        if total_allocated > flt(self.paid_amount) + 0.01:
            raise ValidationError(
                f"Total allocated amount ({total_allocated}) exceeds paid amount ({self.paid_amount})"
            )

    def on_submit(self):
        """Post GL entries and update outstanding on referenced invoices.

        This is the core payment logic from the reference implementation. The GL entries depend
        on the payment_type:

        Receive: Debit Bank, Credit Receivable (with party)
        Pay:     Debit Payable (with party), Credit Bank
        """
        gl_entries = self._get_gl_entries()
        make_gl_entries(gl_entries)
        self._update_outstanding()

    def on_cancel(self):
        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )
        self._update_outstanding(cancel=True)

    def _get_gl_entries(self):
        gl_entries = []

        if self.payment_type == "Receive":
            # Debit: Bank/Cash (paid_to)
            gl_entries.append(
                _dict(
                    account=self.paid_to,
                    debit=flt(self.received_amount, 2),
                    debit_in_account_currency=flt(self.received_amount, 2),
                    credit=0,
                    credit_in_account_currency=0,
                    cost_center=self.cost_center,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or f"Payment received from {self.party_name}",
                )
            )
            # Credit: party ledger (paid_from). Uses self.party_type so that
            # "Receive + Supplier" (supplier refund) posts against AP with the
            # supplier tagged, rather than forcing a Customer tag.
            gl_entries.append(
                _dict(
                    account=self.paid_from,
                    party_type=self.party_type,
                    party=self.party,
                    credit=flt(self.paid_amount, 2),
                    credit_in_account_currency=flt(self.paid_amount, 2),
                    debit=0,
                    debit_in_account_currency=0,
                    against_voucher_type=self.DOCTYPE,
                    against_voucher=self.name,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or f"Payment received from {self.party_name}",
                )
            )

        elif self.payment_type == "Pay":
            # Debit: party ledger (paid_to). Uses self.party_type so that
            # "Pay + Customer" (customer refund) posts against AR with the
            # customer tagged, rather than forcing a Supplier tag.
            gl_entries.append(
                _dict(
                    account=self.paid_to,
                    party_type=self.party_type,
                    party=self.party,
                    debit=flt(self.paid_amount, 2),
                    debit_in_account_currency=flt(self.paid_amount, 2),
                    credit=0,
                    credit_in_account_currency=0,
                    against_voucher_type=self.DOCTYPE,
                    against_voucher=self.name,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or f"Payment to {self.party_name}",
                )
            )
            # Credit: Bank/Cash (paid_from)
            gl_entries.append(
                _dict(
                    account=self.paid_from,
                    credit=flt(self.paid_amount, 2),
                    credit_in_account_currency=flt(self.paid_amount, 2),
                    debit=0,
                    debit_in_account_currency=0,
                    cost_center=self.cost_center,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or f"Payment to {self.party_name}",
                )
            )

        elif self.payment_type == "Internal Transfer":
            # Debit: paid_to, Credit: paid_from
            gl_entries.append(
                _dict(
                    account=self.paid_to,
                    debit=flt(self.received_amount, 2),
                    debit_in_account_currency=flt(self.received_amount, 2),
                    credit=0,
                    credit_in_account_currency=0,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or "Internal Transfer",
                )
            )
            gl_entries.append(
                _dict(
                    account=self.paid_from,
                    credit=flt(self.paid_amount, 2),
                    credit_in_account_currency=flt(self.paid_amount, 2),
                    debit=0,
                    debit_in_account_currency=0,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or "Internal Transfer",
                )
            )

        return gl_entries

    def _update_outstanding(self, cancel=False):
        """Update outstanding_amount on referenced invoices.

        Sign convention: every allocation brings |outstanding| closer to zero.
        - A normal invoice starts with positive outstanding; allocation subtracts.
        - A return invoice (credit/debit note) starts with negative outstanding;
          allocation adds (refund flow).
        Rounding residues below $0.01 snap to 0 so invoices don't linger in an
        "almost paid" state after 100/3-style splits.
        """
        db = get_db()
        for ref in self.get("references") or []:
            doctype = ref.get("reference_doctype")
            docname = ref.get("reference_name")
            allocated = flt(ref.get("allocated_amount"))

            if not doctype or not docname or not allocated:
                continue

            current = flt(db.get_value(doctype, docname, "outstanding_amount"))
            direction = 1 if current >= 0 else -1  # +: reduce, -: add (refund)
            delta = allocated if not cancel else -allocated
            new_outstanding = current - direction * delta

            if abs(new_outstanding) < 0.01:
                new_outstanding = 0

            db.set_value(doctype, docname, "outstanding_amount", flt(new_outstanding, 2))

        db.commit()
