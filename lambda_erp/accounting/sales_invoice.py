"""
Sales Invoice.

The Sales Invoice is the most complex document in the reference implementation (~2,500 lines).
It's where the sales cycle culminates in actual financial impact:
  Quotation -> Sales Order -> Delivery Note -> **Sales Invoice**

Key behaviors on submit:
1. Creates GL entries (Debit: Receivable, Credit: Income + Tax accounts)
2. Updates outstanding_amount
3. Updates Sales Order billing status
4. Optionally updates stock (if update_stock is checked)

This is a simplified port focusing on the core GL posting logic.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate, add_days
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.controllers.defaults import set_default_currency
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries, to_base_currency
from lambda_erp.stock.stock_ledger import (
    make_sl_entries,
    build_sell_side_sles,
    build_cost_basis_gl,
    reverse_stock_sles,
)
from lambda_erp.exceptions import ValidationError

class SalesInvoice(Document):
    DOCTYPE = "Sales Invoice"
    SUBMITTABLE = True
    CHILD_TABLES = {
        "items": ("Sales Invoice Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "SINV"

    LINK_FIELDS = {
        "subscription": "Subscription",
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
        """Validate the sales invoice.

        Mirrors the reference implementation's SalesInvoice.validate() which runs through
        the full controller chain:
          SellingController.validate()
            -> StockController.validate()
              -> AccountsController.validate()
                -> set_missing_values
                -> calculate_taxes_and_totals
                -> validate_party
        """
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_customer_name()
        self._set_missing_accounts()
        self._set_item_defaults()
        self._validate_no_double_shipment()

        if self.is_return:
            self._validate_return()

        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        apply_pricing_rules(self)

        set_default_currency(self, "Customer", "customer")

        calculate_taxes_and_totals(self)

        if self.is_return:
            self._validate_return_value()

        # Set outstanding = grand total (before any payments)
        self._data["outstanding_amount"] = flt(self.grand_total, 2)

        if not self.due_date:
            self._data["due_date"] = add_days(self.posting_date, 30)

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

    def _set_missing_accounts(self):
        """Set default accounts from Company if not specified.

        In the reference implementation, this is handled by AccountsController.set_missing_values()
        which pulls defaults from Company settings.
        """
        db = get_db()
        if self.company:
            if not self.debit_to:
                self._data["debit_to"] = db.get_value(
                    "Company", self.company, "default_receivable_account"
                )

            # Set income account on items
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
                    item["description"] = item.get("description") or item_data.description
                    item["uom"] = item.get("uom") or item_data.stock_uom
                    if item.get("rate") is None and item.get("price_list_rate") is None:
                        item["rate"] = flt(item_data.standard_rate)

    def _validate_no_double_shipment(self):
        """Block update_stock=1 when the referenced Sales Order already has a
        Delivery Note for the line. Otherwise stock ships twice: once via the
        DN and again when this invoice submits. Returns are exempt (they
        reverse the SI's own earlier shipment, not a separate DN)."""
        if not flt(self.get("update_stock")) or self.is_return:
            return
        db = get_db()
        for item in self.get("items"):
            so_item = item.get("sales_order_item") or item.get("so_detail")
            so = item.get("sales_order")
            if not (so or so_item):
                continue
            dn_rows = db.sql(
                'SELECT dn.name AS dn_name '
                'FROM "Delivery Note Item" dni '
                'JOIN "Delivery Note" dn ON dn.name = dni.parent '
                'WHERE dn.docstatus = 1 '
                'AND (dni.against_sales_order = ? OR dni.so_detail = ?) '
                'AND dni.item_code = ? LIMIT 1',
                [so or "", so_item or "", item.get("item_code")],
            )
            if dn_rows:
                raise ValidationError(
                    f"Cannot submit with update_stock=1: item {item.get('item_code')} "
                    f"was already shipped via Delivery Note {dn_rows[0]['dn_name']}. "
                    f"Either uncheck update_stock (use this as a bill only) or "
                    f"cancel the Delivery Note first."
                )

    def _validate_return(self):
        from lambda_erp.workflow import validate_return
        validate_return(self)

    def _update_original_outstanding(self):
        """Reduce original invoice outstanding when a return is submitted."""
        db = get_db()
        current = flt(db.get_value(self.DOCTYPE, self.return_against, "outstanding_amount"))
        reduction = abs(flt(self.grand_total, 2))
        new_outstanding = max(flt(current - reduction, 2), 0)
        db.set_value(self.DOCTYPE, self.return_against, "outstanding_amount", new_outstanding)

    def _validate_return_value(self):
        """Cap the return's value at what is still economically returnable from
        the original document. Quantity-only validation is insufficient because
        a user could keep qty within bounds but edit rate/tax upward and create
        an oversized credit note.
        """
        db = get_db()
        original_total = abs(
            flt(db.get_value(self.DOCTYPE, self.return_against, "grand_total"), 2)
        )
        prev_rows = db.sql(
            """SELECT COALESCE(SUM(ABS(grand_total)), 0) AS total
               FROM "Sales Invoice"
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

    def _check_no_linked_payment_entry(self):
        """Block cancel if a submitted Payment Entry allocates against this
        invoice. Otherwise the reversal orphans the PE's allocation — AR
        swings into credit balance and the cash is in the bank with nothing
        backing it. Recourse: cancel the PE first."""
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

    def on_submit(self):
        """Post GL entries on submission.

        This is the core accounting logic. In the reference implementation, this is handled by
        AccountsController.make_gl_entries() which builds a gl_map and
        then calls general_ledger.make_gl_entries().

        The accounting entry for a Sales Invoice:
          Debit:  Accounts Receivable (customer)    = grand_total
          Credit: Income Account (per item)         = net_amount per item
          Credit: Tax Account (per tax row)         = tax_amount per row

        If update_stock is checked, this invoice also ships goods directly:
          - Creates SLE entries (stock leaves the warehouse)
          - Posts Dr COGS / Cr Stock In Hand on top of the revenue entries
        """
        gl_entries = self._get_gl_entries()
        # Revenue side (AR/income/tax) is built in document currency; convert to
        # base before posting. Cost-of-goods entries below are already in base
        # (moving-average cost), so they're appended after the conversion.
        to_base_currency(gl_entries, self.get("conversion_rate"))

        if flt(self.get("update_stock")):
            sl_entries = self._get_stock_sl_entries()
            if sl_entries:
                make_sl_entries(sl_entries)
            gl_entries.extend(
                build_cost_basis_gl(self, remarks=f"Direct shipment via {self.name}")
            )

        make_gl_entries(gl_entries)

        # Shared lifecycle recalculates order progress after ledger hooks.

        # Update original invoice outstanding for returns
        if self.is_return and self.return_against:
            self._update_original_outstanding()

    def on_cancel(self):
        """Reverse GL entries on cancellation."""
        self._check_no_linked_payment_entry()

        if flt(self.get("update_stock")):
            reversed_sles = reverse_stock_sles(self._get_stock_sl_entries())
            if reversed_sles:
                make_sl_entries(reversed_sles, allow_negative_stock=True)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

        # Reset outstanding
        db = get_db()
        db.set_value(self.DOCTYPE, self.name, "outstanding_amount", 0)

        # Restore original invoice outstanding for returns
        if self.is_return and self.return_against:
            self._reverse_original_outstanding()

        # Shared lifecycle recalculates order progress after ledger hooks.

    def _get_gl_entries(self):
        """Build the GL entry map for this invoice.

        AccountsController.get_gl_entries().

        This is the heart of the accounting: building the list of
        debit/credit entries that maintain the double-entry invariant.
        """
        gl_entries = []

        # 1. Debit: Accounts Receivable
        #    In the reference implementation: debit_to account with party_type=Customer
        gl_entries.append(
            _dict(
                account=self.debit_to,
                party_type="Customer",
                party=self.customer,
                debit=flt(self.grand_total, 2),
                debit_in_account_currency=flt(self.grand_total, 2),
                credit=0,
                credit_in_account_currency=0,
                against_voucher_type=self.DOCTYPE,
                against_voucher=self.name,
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                posting_date=self.posting_date,
                company=self.company,
                remarks=self.remarks or f"Sales Invoice {self.name} against {self.customer_name}",
            )
        )

        # 2. Credit: Income accounts (per item)
        #    In the reference implementation, items with the same income_account are grouped
        income_accounts = {}
        for item in self.get("items"):
            account = item.get("income_account")
            if account not in income_accounts:
                income_accounts[account] = 0
            income_accounts[account] += flt(item.get("net_amount", 0))

        for account, amount in income_accounts.items():
            gl_entries.append(
                _dict(
                    account=account,
                    credit=flt(amount, 2),
                    credit_in_account_currency=flt(amount, 2),
                    debit=0,
                    debit_in_account_currency=0,
                    cost_center=self.get("items")[0].get("cost_center") if self.get("items") else None,
                    voucher_type=self.DOCTYPE,
                    voucher_no=self.name,
                    posting_date=self.posting_date,
                    company=self.company,
                    remarks=self.remarks or f"Income from {self.name}",
                )
            )

        # 3. Credit: Tax accounts (per tax row)
        for tax in self.get("taxes") or []:
            if flt(tax.get("tax_amount")):
                gl_entries.append(
                    _dict(
                        account=tax.get("account_head"),
                        credit=flt(tax["tax_amount"], 2),
                        credit_in_account_currency=flt(tax["tax_amount"], 2),
                        debit=0,
                        debit_in_account_currency=0,
                        cost_center=self.get("items")[0].get("cost_center") if self.get("items") else None,
                        voucher_type=self.DOCTYPE,
                        voucher_no=self.name,
                        posting_date=self.posting_date,
                        company=self.company,
                        remarks=tax.get("description") or f"Tax: {tax.get('account_head')}",
                    )
                )

        return gl_entries

    def _get_stock_sl_entries(self):
        """SLEs for direct-ship invoices. Shared helper handles both normal
        ship-out and returns via the qty sign."""
        return build_sell_side_sles(self, self.get("items"))

    def _update_sales_order_billing(self, cancel=False):
        from lambda_erp.workflow import refresh_order_progress
        refresh_order_progress(self)

def make_sales_return(sinv_name):
    """Create a Credit Note (return Sales Invoice) from an existing Sales Invoice."""
    db = get_db()
    original = SalesInvoice.load(sinv_name)

    if original.docstatus != 1:
        raise ValidationError("Sales Invoice must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_inv = SalesInvoice(
        customer=original.customer,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        debit_to=original.debit_to,
        is_return=1,
        return_against=original.name,
        # If the original invoice shipped goods directly (update_stock=1),
        # the return must put them back; otherwise AR reverses but stock
        # stays stranded. Non-direct-ship invoices keep the default of 0.
        update_stock=flt(original.get("update_stock")) or 0,
    )

    from lambda_erp.workflow import returnable_rows
    for item in returnable_rows(original):
        return_inv.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"),
            rate=flt(item.get("rate")),
            income_account=item.get("income_account"),
            cost_center=item.get("cost_center"),
            warehouse=item.get("warehouse"),
            sales_order=item.get("sales_order"),
            sales_order_item=item.get("sales_order_item"),
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
