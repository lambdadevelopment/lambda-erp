"""
Purchase Invoice.

Mirror image of Sales Invoice for the buying side:
  Purchase Order -> Purchase Receipt -> **Purchase Invoice**

GL entries on submit:
  Debit:  Expense/Stock Account  = net_amount per item
  Debit:  Tax Account            = tax_amount per row (input tax)
  Credit: Accounts Payable       = grand_total
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate, add_days
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.stock.stock_ledger import (
    make_sl_entries,
    build_buy_side_sles,
    reverse_stock_sles,
)
from lambda_erp.exceptions import ValidationError

class PurchaseInvoice(Document):
    DOCTYPE = "Purchase Invoice"
    CHILD_TABLES = {
        "items": ("Purchase Invoice Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "PINV"

    LINK_FIELDS = {
        "supplier": "Supplier",
        "company": "Company",
        "credit_to": "Account",
    }
    CHILD_LINK_FIELDS = {
        "items": {
            "item_code": "Item",
            "warehouse": "Warehouse",
            "expense_account": "Account",
            "cost_center": "Cost Center",
        },
        "taxes": {
            "account_head": "Account",
            "cost_center": "Cost Center",
        },
    }
    ACCOUNT_TYPE_CONSTRAINTS = {
        "credit_to": {"account_type": "Payable"},
    }
    # items.expense_account intentionally omitted — it's legitimately routed
    # to Expense (services), Asset/SIH (direct-receive stock), or Asset/SRBNB
    # (PR→PI flow) depending on item type + update_stock.

    def validate(self):
        if not self.supplier:
            raise ValidationError("Supplier is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_supplier_name()
        self._set_missing_accounts()
        self._set_item_defaults()
        self._validate_stock_warehouses()
        self._validate_no_double_receipt()

        if self.is_return:
            self._validate_return()

        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        apply_pricing_rules(self)

        calculate_taxes_and_totals(self)

        if self.is_return:
            self._validate_return_value()

        self._data["outstanding_amount"] = flt(self.grand_total, 2)

        if not self.due_date:
            self._data["due_date"] = add_days(self.posting_date, 30)

    def _set_supplier_name(self):
        if not self.supplier_name and self.supplier:
            db = get_db()
            self.supplier_name = db.get_value("Supplier", self.supplier, "supplier_name")

    def _set_missing_accounts(self):
        """Route each line's expense_account based on item + update_stock.

        Three valid workflows:
          1. PR -> PI (update_stock=0, stock item): line debits SRBNB to clear
             the interim account opened by the Purchase Receipt.
          2. PI with update_stock=1 (direct receive-and-bill, stock item): line
             debits Stock In Hand directly (and SLE entries post below in
             on_submit). There is no prior PR to clear.
          3. Services / non-stock: line debits the default expense account.
        """
        db = get_db()
        if not self.company:
            return

        if not self.credit_to:
            self._data["credit_to"] = db.get_value(
                "Company", self.company, "default_payable_account"
            )
        default_expense = db.get_value("Company", self.company, "default_expense_account")
        stock_received_account = db.get_value(
            "Company", self.company, "stock_received_but_not_billed"
        )
        stock_in_hand_account = db.get_value(
            "Company", self.company, "stock_in_hand_account"
        )
        default_cc = db.get_value("Company", self.company, "default_cost_center")
        directly_receiving = flt(self.get("update_stock")) == 1

        for item in self.get("items"):
            if not item.get("expense_account"):
                is_stock = 0
                if item.get("item_code"):
                    is_stock = db.get_value("Item", item["item_code"], "is_stock_item") or 0
                if is_stock and directly_receiving and stock_in_hand_account:
                    item["expense_account"] = stock_in_hand_account
                elif is_stock and stock_received_account:
                    item["expense_account"] = stock_received_account
                else:
                    item["expense_account"] = default_expense
            if not item.get("cost_center"):
                item["cost_center"] = default_cc

    def _validate_stock_warehouses(self):
        """When update_stock=1, every stock-item line needs a warehouse so
        the SLE has somewhere to put the received goods."""
        if not flt(self.get("update_stock")):
            return
        db = get_db()
        for item in self.get("items"):
            if not item.get("item_code"):
                continue
            is_stock = db.get_value("Item", item["item_code"], "is_stock_item") or 0
            if is_stock and not item.get("warehouse"):
                raise ValidationError(
                    f"Warehouse is required for stock item {item['item_code']} "
                    f"when update_stock is checked"
                )

    def _check_no_linked_payment_entry(self):
        """Mirror of SalesInvoice._check_no_linked_payment_entry — cancelling a
        PI whose AP has been paid by a Payment Entry orphans the PE's
        allocation and leaves AP/bank in an inconsistent state."""
        db = get_db()
        rows = db.sql(
            'SELECT DISTINCT pe.name AS pe_name '
            'FROM "Payment Entry Reference" per '
            'JOIN "Payment Entry" pe ON pe.name = per.parent '
            'WHERE pe.docstatus = 1 '
            '  AND per.reference_doctype = ? AND per.reference_name = ? LIMIT 1',
            [self.DOCTYPE, self.name],
        )
        if rows:
            raise ValidationError(
                f"Cannot cancel {self.name}: Payment Entry {rows[0]['pe_name']} "
                f"is already allocated against it. Cancel the Payment Entry first."
            )

    def _validate_no_double_receipt(self):
        """Block update_stock=1 when the referenced Purchase Order already has
        a Purchase Receipt for the line. Otherwise stock arrives twice: once
        via the PR and again when this invoice submits. Returns are exempt."""
        if not flt(self.get("update_stock")) or self.is_return:
            return
        db = get_db()
        for item in self.get("items"):
            po = item.get("purchase_order")
            po_item = item.get("purchase_order_item")
            if not (po or po_item):
                continue
            pr_rows = db.sql(
                'SELECT pr.name AS pr_name '
                'FROM "Purchase Receipt Item" pri '
                'JOIN "Purchase Receipt" pr ON pr.name = pri.parent '
                'WHERE pr.docstatus = 1 '
                'AND (pri.against_purchase_order = ? OR pri.po_detail = ?) '
                'AND pri.item_code = ? LIMIT 1',
                [po or "", po_item or "", item.get("item_code")],
            )
            if pr_rows:
                raise ValidationError(
                    f"Cannot submit with update_stock=1: item {item.get('item_code')} "
                    f"was already received via Purchase Receipt {pr_rows[0]['pr_name']}. "
                    f"Either uncheck update_stock (use this as a bill only, clearing "
                    f"SRBNB) or cancel the Purchase Receipt first."
                )

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
                    item["description"] = item.get("description") or item_data.description
                    item["uom"] = item.get("uom") or item_data.stock_uom
                    if not item.get("rate"):
                        item["rate"] = flt(item_data.standard_rate)

    def _validate_return(self):
        """Validate return-specific rules."""
        if not self.return_against:
            raise ValidationError("Return Against is required for a return invoice")

        db = get_db()
        original = db.get_value(self.DOCTYPE, self.return_against, ["name", "docstatus", "grand_total"])
        if not original:
            raise ValidationError(f"Original invoice {self.return_against} not found")
        if original.docstatus != 1:
            raise ValidationError(f"Original invoice {self.return_against} must be submitted")

        # Aggregate already-returned qty per item across other submitted return
        # invoices against the same original. Without this, the same item can be
        # returned twice in separate debit notes, driving the original's
        # outstanding negative and producing phantom AP balances.
        already_returned: dict[str, float] = {}
        prev_rows = db.sql(
            """SELECT pii.item_code, COALESCE(SUM(ABS(pii.qty)), 0) AS qty
               FROM "Purchase Invoice Item" pii
               JOIN "Purchase Invoice" pi ON pi.name = pii.parent
               WHERE pi.return_against = ?
                 AND pi.docstatus = 1
                 AND pi.name != ?
               GROUP BY pii.item_code""",
            [self.return_against, self.name or ""],
        )
        for row in prev_rows:
            already_returned[row["item_code"]] = flt(row["qty"])

        original_doc = PurchaseInvoice.load(self.return_against)
        original_items = {item["item_code"]: flt(item["qty"]) for item in original_doc.get("items")}
        for item in self.get("items"):
            orig_qty = original_items.get(item.get("item_code"), 0)
            prev = already_returned.get(item.get("item_code"), 0)
            return_qty = abs(flt(item.get("qty")))
            remaining = max(0, orig_qty - prev)
            if return_qty > remaining + 0.01:
                hint = (
                    f"original qty {orig_qty}, already returned {prev}, "
                    f"remaining {remaining}"
                    if prev
                    else f"original qty {orig_qty}"
                )
                raise ValidationError(
                    f"Return qty ({return_qty}) for {item.get('item_code')} exceeds "
                    f"remaining returnable qty ({hint})"
                )

    def _update_original_outstanding(self):
        """Reduce original invoice outstanding when a return is submitted."""
        db = get_db()
        current = flt(db.get_value(self.DOCTYPE, self.return_against, "outstanding_amount"))
        reduction = abs(flt(self.grand_total, 2))
        new_outstanding = max(flt(current - reduction, 2), 0)
        db.set_value(self.DOCTYPE, self.return_against, "outstanding_amount", new_outstanding)

    def _validate_return_value(self):
        """Mirror SalesInvoice._validate_return_value on the purchase side."""
        db = get_db()
        original_total = abs(
            flt(db.get_value(self.DOCTYPE, self.return_against, "grand_total"), 2)
        )
        prev_rows = db.sql(
            """SELECT COALESCE(SUM(ABS(grand_total)), 0) AS total
               FROM "Purchase Invoice"
               WHERE return_against = ?
                 AND docstatus = 1
                 AND name != ?""",
            [self.return_against, self.name or ""],
        )
        already_returned = flt(prev_rows[0]["total"]) if prev_rows else 0
        remaining_value = max(0, flt(original_total - already_returned, 2))
        this_value = abs(flt(self.grand_total, 2))
        if this_value > remaining_value + 0.01:
            raise ValidationError(
                f"Return total ({this_value}) exceeds remaining returnable value "
                f"(original {original_total}, already returned {already_returned}, "
                f"remaining {remaining_value})"
            )

    def _reverse_original_outstanding(self):
        """Restore original invoice outstanding when a return is cancelled."""
        db = get_db()
        current = flt(db.get_value(self.DOCTYPE, self.return_against, "outstanding_amount"))
        restoration = abs(flt(self.grand_total, 2))
        db.set_value(self.DOCTYPE, self.return_against, "outstanding_amount", flt(current + restoration, 2))

    def on_submit(self):
        """Post GL entries.

        Accounting entry for a Purchase Invoice:
          Debit:  Expense Account (per item)  = net_amount
          Debit:  Tax Account (input tax)     = tax_amount
          Credit: Accounts Payable (supplier) = grand_total

        When update_stock=1, this invoice also receives the goods — stock
        items get SLE rows so Bin updates, and their expense_account was
        routed to Stock In Hand in _set_missing_accounts so the existing
        GL loop naturally books Dr Stock In Hand for those lines.
        """
        if flt(self.get("update_stock")):
            sl_entries = self._get_stock_sl_entries()
            if sl_entries:
                make_sl_entries(sl_entries)

        gl_entries = self._get_gl_entries()
        make_gl_entries(gl_entries)
        self._update_purchase_order_billing()

        if self.is_return and self.return_against:
            self._update_original_outstanding()

    def on_cancel(self):
        self._check_no_linked_payment_entry()

        if flt(self.get("update_stock")):
            reversed_sles = reverse_stock_sles(self._get_stock_sl_entries())
            if reversed_sles:
                make_sl_entries(reversed_sles, allow_negative_stock=True)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )
        db = get_db()
        db.set_value(self.DOCTYPE, self.name, "outstanding_amount", 0)

        if self.is_return and self.return_against:
            self._reverse_original_outstanding()

        self._update_purchase_order_billing(cancel=True)

    def _get_stock_sl_entries(self):
        """SLEs for direct-receive PIs. Only stock items contribute; services
        stay off the stock ledger. See stock_ledger.build_buy_side_sles for
        how incoming cost is set from the supplier rate (not moving-average).
        """
        db = get_db()
        stock_items = [
            item
            for item in self.get("items")
            if item.get("item_code")
            and item.get("warehouse")
            and (db.get_value("Item", item["item_code"], "is_stock_item") or 0)
        ]
        return build_buy_side_sles(self, stock_items)

    def _get_gl_entries(self):
        """Build GL entry map - mirror image of Sales Invoice."""
        gl_entries = []

        # 1. Credit: Accounts Payable (supplier)
        gl_entries.append(
            _dict(
                account=self.credit_to,
                party_type="Supplier",
                party=self.supplier,
                credit=flt(self.grand_total, 2),
                credit_in_account_currency=flt(self.grand_total, 2),
                debit=0,
                debit_in_account_currency=0,
                against_voucher_type=self.DOCTYPE,
                against_voucher=self.name,
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                posting_date=self.posting_date,
                company=self.company,
                remarks=self.remarks or f"Purchase Invoice {self.name}",
            )
        )

        # 2. Debit: Expense/Stock accounts (per item)
        expense_accounts = {}
        for item in self.get("items"):
            account = item.get("expense_account")
            if account not in expense_accounts:
                expense_accounts[account] = 0
            expense_accounts[account] += flt(item.get("net_amount", 0))

        for account, amount in expense_accounts.items():
            gl_entries.append(
                _dict(
                    account=account,
                    debit=flt(amount, 2),
                    debit_in_account_currency=flt(amount, 2),
                    credit=0,
                    credit_in_account_currency=0,
                    cost_center=self.get("items")[0].get("cost_center") if self.get("items") else None,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=f"Expense from {self.name}",
                )
            )

        # 3. Debit: Tax accounts (input tax / tax credit)
        for tax in self.get("taxes") or []:
            if flt(tax.get("tax_amount")):
                gl_entries.append(
                    _dict(
                        account=tax.get("account_head"),
                        debit=flt(tax["tax_amount"], 2),
                        debit_in_account_currency=flt(tax["tax_amount"], 2),
                        credit=0,
                        credit_in_account_currency=0,
                        voucher_type=self.DOCTYPE,
                        voucher_no=self.name,
                        posting_date=self.posting_date,
                        company=self.company,
                        remarks=tax.get("description") or f"Tax: {tax.get('account_head')}",
                    )
                )

        return gl_entries

    def _update_purchase_order_billing(self, cancel=False):
        db = get_db()
        for item in self.get("items"):
            if item.get("purchase_order") and item.get("purchase_order_item"):
                billed_qty = flt(item.get("qty"))
                if cancel:
                    billed_qty = -billed_qty
                current = db.get_value(
                    "Purchase Order Item", item["purchase_order_item"], "billed_qty"
                ) or 0
                db.set_value(
                    "Purchase Order Item", item["purchase_order_item"],
                    "billed_qty", flt(current) + billed_qty
                )
        db.commit()

def make_purchase_return(pinv_name):
    """Create a Debit Note (return Purchase Invoice) from an existing Purchase Invoice."""
    db = get_db()
    original = PurchaseInvoice.load(pinv_name)

    if original.docstatus != 1:
        raise ValidationError("Purchase Invoice must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_inv = PurchaseInvoice(
        supplier=original.supplier,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        credit_to=original.credit_to,
        is_return=1,
        return_against=original.name,
        # Direct-receive PI (update_stock=1) put goods into stock on submit;
        # the return must move them back out. Without this, AP reverses but
        # the inventory just sits there.
        update_stock=flt(original.get("update_stock")) or 0,
    )

    for item in original.get("items"):
        return_inv.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"),
            rate=flt(item.get("rate")),
            expense_account=item.get("expense_account"),
            cost_center=item.get("cost_center"),
            warehouse=item.get("warehouse"),
            purchase_order=item.get("purchase_order"),
            purchase_order_item=item.get("purchase_order_item"),
        ))

    for tax in original.get("taxes") or []:
        return_inv.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=flt(tax.get("rate")),
            tax_amount=0,
        ))

    return return_inv
