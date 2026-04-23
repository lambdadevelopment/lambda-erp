"""
POS Invoice.

POS Invoice is a Sales Invoice with immediate payment. It combines:
- Sales Invoice GL entries (Dr: Receivable, Cr: Income)
- Payment GL entries (Dr: Cash/Bank, Cr: Receivable)
- Optional stock update (Dr: COGS, Cr: Stock In Hand)

The receivable is created and immediately settled in one transaction.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.stock_ledger import (
    make_sl_entries,
    build_sell_side_sles,
    build_cost_basis_gl,
    reverse_stock_sles,
)
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries

class POSInvoice(Document):
    DOCTYPE = "POS Invoice"
    CHILD_TABLES = {
        "items": ("POS Invoice Item", None),
        "taxes": ("Sales Taxes and Charges", None),
        "payments": ("POS Invoice Payment", None),
    }
    PREFIX = "POS"

    LINK_FIELDS = {
        "customer": "Customer",
        "company": "Company",
        "debit_to": "Account",
    }
    CHILD_LINK_FIELDS = {
        "items": {
            "item_code": "Item",
            "warehouse": "Warehouse",
            "income_account": "Account",
            "cost_center": "Cost Center",
        },
        "taxes": {
            "account_head": "Account",
            "cost_center": "Cost Center",
        },
    }
    ACCOUNT_TYPE_CONSTRAINTS = {
        "debit_to": {"account_type": "Receivable"},
    }
    CHILD_ACCOUNT_TYPE_CONSTRAINTS = {
        "items": {"income_account": {"root_type": "Income"}},
    }

    def validate(self):
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_customer_name()
        self._set_missing_accounts()
        self._set_item_defaults()
        if self.is_return:
            self._validate_return()
        calculate_taxes_and_totals(self)
        self._calculate_payments()
        self._set_status()

    def _validate_return(self):
        if not self.return_against:
            raise ValidationError("return_against is required for a POS return")
        db = get_db()
        original = db.get_value(self.DOCTYPE, self.return_against, ["name", "docstatus"])
        if not original:
            raise ValidationError(f"Original POS Invoice {self.return_against} not found")
        if original.docstatus != 1:
            raise ValidationError(f"Original POS Invoice {self.return_against} must be submitted")

        original_doc = POSInvoice.load(self.return_against)
        original_items = {
            item["item_code"]: flt(item["qty"]) for item in original_doc.get("items")
        }
        for item in self.get("items"):
            orig_qty = original_items.get(item.get("item_code"), 0)
            return_qty = abs(flt(item.get("qty")))
            if return_qty > orig_qty:
                raise ValidationError(
                    f"Return qty ({return_qty}) for {item.get('item_code')} exceeds "
                    f"original qty ({orig_qty})"
                )

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

    def _set_missing_accounts(self):
        db = get_db()
        if self.company:
            if not self.debit_to:
                self._data["debit_to"] = db.get_value(
                    "Company", self.company, "default_receivable_account"
                )
            default_income = db.get_value("Company", self.company, "default_income_account")
            default_cc = db.get_value("Company", self.company, "default_cost_center")
            for item in self.get("items"):
                if not item.get("income_account"):
                    item["income_account"] = default_income
                if not item.get("cost_center"):
                    item["cost_center"] = default_cc

    def _set_item_defaults(self):
        db = get_db()
        for item in self.get("items"):
            if item.get("item_code") and not item.get("item_name"):
                item_data = db.get_value(
                    "Item", item["item_code"],
                    ["item_name", "description", "stock_uom", "standard_rate"]
                )
                if item_data:
                    item["item_name"] = item_data.item_name
                    item["uom"] = item.get("uom") or item_data.stock_uom
                    if not item.get("rate"):
                        item["rate"] = flt(item_data.standard_rate)

    def _calculate_payments(self):
        paid = sum(flt(p.get("amount", 0)) for p in self.get("payments") or [])
        self._data["paid_amount"] = flt(paid, 2)
        grand = flt(self.grand_total, 2)
        self._data["change_amount"] = flt(max(0, paid - grand), 2)
        self._data["outstanding_amount"] = flt(max(0, grand - paid), 2)

    def _set_status(self):
        if self.docstatus == 0:
            self._data["status"] = "Draft"
        elif self.docstatus == 2:
            self._data["status"] = "Cancelled"
        elif self.docstatus == 1:
            if flt(self.outstanding_amount) <= 0:
                self._data["status"] = "Paid"
            else:
                self._data["status"] = "Unpaid"

    def on_submit(self):
        # POS sales must be paid at time of submit. Returns are the exception:
        # refund payments are optional — the user can settle the refund later
        # via a Payment Entry or off-ledger.
        if not self.is_return and not self.get("payments"):
            raise ValidationError("At least one payment is required for POS Invoice")

        gl_entries = self._get_gl_entries()

        # When update_stock=1, post SLEs first so stock_value_difference is
        # available on the persisted rows, then append the matching stock-side
        # GL entries (Dr COGS / Cr Stock In Hand at cost). Without this, stock
        # leaves physically but the balance sheet never reflects it.
        if flt(self._data.get("update_stock")):
            sl_entries = self._get_sl_entries()
            if sl_entries:
                make_sl_entries(sl_entries)
            gl_entries.extend(build_cost_basis_gl(self, remarks=f"POS sale via {self.name}"))

        if gl_entries:
            make_gl_entries(gl_entries)

    def on_cancel(self):
        if flt(self._data.get("update_stock")):
            reversed_sles = reverse_stock_sles(self._get_sl_entries())
            if reversed_sles:
                make_sl_entries(reversed_sles, allow_negative_stock=True)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

    def _get_gl_entries(self):
        db = get_db()
        gl_entries = []

        grand_total = flt(self.grand_total, 2)
        if not grand_total:
            return []

        # 1. Dr: Accounts Receivable
        gl_entries.append(_dict(
            account=self.debit_to,
            party_type="Customer",
            party=self.customer,
            debit=grand_total,
            debit_in_account_currency=grand_total,
            credit=0,
            credit_in_account_currency=0,
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
            posting_date=self.posting_date,
            company=self.company,
        ))

        # 2. Cr: Income accounts
        income_accounts = {}
        for item in self.get("items"):
            account = item.get("income_account")
            if not account:
                continue
            income_accounts[account] = income_accounts.get(account, 0) + flt(item.get("net_amount", 0))

        for account, amount in income_accounts.items():
            gl_entries.append(_dict(
                account=account,
                credit=flt(amount, 2),
                credit_in_account_currency=flt(amount, 2),
                debit=0,
                debit_in_account_currency=0,
                cost_center=db.get_value("Company", self.company, "default_cost_center"),
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                posting_date=self.posting_date,
                company=self.company,
            ))

        # 3. Cr: Tax accounts
        for tax in self.get("taxes") or []:
            if flt(tax.get("tax_amount")):
                gl_entries.append(_dict(
                    account=tax.get("account_head"),
                    credit=flt(tax["tax_amount"], 2),
                    credit_in_account_currency=flt(tax["tax_amount"], 2),
                    debit=0,
                    debit_in_account_currency=0,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                ))

        # 4. Payment entries — Dr: Cash/Bank, Cr: Receivable
        for payment in self.get("payments") or []:
            pay_amount = flt(payment.get("amount", 0), 2)
            if not pay_amount:
                continue
            pay_account = payment.get("account")
            if not pay_account:
                pay_account = db.get_value("Company", self.company, "default_bank_account")
            if not pay_account:
                continue

            # Dr: Cash/Bank
            gl_entries.append(_dict(
                account=pay_account,
                debit=pay_amount,
                debit_in_account_currency=pay_amount,
                credit=0,
                credit_in_account_currency=0,
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                posting_date=self.posting_date,
                company=self.company,
            ))

            # Cr: Receivable (settles the debit from step 1)
            gl_entries.append(_dict(
                account=self.debit_to,
                party_type="Customer",
                party=self.customer,
                debit=0,
                debit_in_account_currency=0,
                credit=pay_amount,
                credit_in_account_currency=pay_amount,
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                posting_date=self.posting_date,
                company=self.company,
            ))

        return gl_entries

    def _get_sl_entries(self):
        """SLEs for direct-ship POS sales. See stock_ledger.build_sell_side_sles
        for semantics (rates passed as 0 so moving-average cost is used)."""
        return build_sell_side_sles(self, self.get("items"))

def make_pos_return(posi_name):
    """Create a return POS Invoice from an existing one. Carries update_stock
    so the return also reverses inventory when the original was direct-ship.
    Refund payments are optional — a POS return with no payment row leaves
    the refund as an AR balance the original customer can draw from, or the
    user can post a Payment Entry separately."""
    original = POSInvoice.load(posi_name)

    if original.docstatus != 1:
        raise ValidationError("POS Invoice must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_pos = POSInvoice(
        customer=original.customer,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        debit_to=original.debit_to,
        is_return=1,
        return_against=original.name,
        update_stock=flt(original.get("update_stock")) or 0,
    )

    for item in original.get("items"):
        return_pos.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"),
            rate=flt(item.get("rate")),
            income_account=item.get("income_account"),
            cost_center=item.get("cost_center"),
            warehouse=item.get("warehouse"),
        ))

    for tax in original.get("taxes") or []:
        return_pos.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=flt(tax.get("rate")),
            tax_amount=-flt(tax.get("tax_amount")),
        ))

    return return_pos
