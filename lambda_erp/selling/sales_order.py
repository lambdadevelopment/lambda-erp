"""
Sales Order.

Sales Order sits between Quotation and Invoice in the sales cycle:
  Quotation -> Sales Order -> Delivery Note -> Sales Invoice

Key behaviors:
- Does NOT create GL entries (no financial impact yet)
- Reserves stock (updates ordered_qty in Bin)
- Tracks delivery and billing status (per_delivered, per_billed)
- Can be converted to Sales Invoice via make_sales_invoice()
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.exceptions import ValidationError

class SalesOrder(Document):
    DOCTYPE = "Sales Order"
    CHILD_TABLES = {
        "items": ("Sales Order Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "SO"

    LINK_FIELDS = {
        "customer": "Customer",
        "company": "Company",
    }
    CHILD_LINK_FIELDS = {
        "items": {
            "item_code": "Item",
            "warehouse": "Warehouse",
        },
        "taxes": {
            "account_head": "Account",
            "cost_center": "Cost Center",
        },
    }

    def validate(self):
        """Validate sales order.

        Mirrors the reference implementation's SalesOrder.validate() which calls the full controller
        chain: SellingController -> StockController -> AccountsController.
        """
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.transaction_date:
            self.transaction_date = nowdate()

        self._set_customer_name()
        self._set_item_defaults()
        self._validate_delivery_date()

        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        apply_pricing_rules(self)

        # Calculate taxes and totals
        calculate_taxes_and_totals(self)

        self._set_status()

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

    def _set_item_defaults(self):
        """Fill in item names and rates from master data."""
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
                    if not item.get("rate") and not item.get("price_list_rate"):
                        item["rate"] = flt(item_data.standard_rate)

    def _validate_delivery_date(self):
        if self.delivery_date and getdate(self.delivery_date) < getdate(self.transaction_date):
            raise ValidationError("Expected delivery date cannot be before order date")

    def _set_status(self):
        """Set status based on delivery and billing."""
        if self.docstatus == 0:
            self._data["status"] = "Draft"
        elif self.docstatus == 2:
            self._data["status"] = "Cancelled"
        elif self.docstatus == 1:
            per_delivered = flt(self.per_delivered)
            per_billed = flt(self.per_billed)

            if per_delivered >= 100 and per_billed >= 100:
                self._data["status"] = "Completed"
            elif per_delivered > 0 and per_delivered < 100:
                self._data["status"] = "To Deliver"
            elif per_billed > 0 and per_billed < 100:
                self._data["status"] = "To Bill"
            else:
                self._data["status"] = "To Deliver and Bill"

    def on_submit(self):
        """On submit, update stock reservations (ordered_qty in Bin).

        In the reference implementation, submitting a Sales Order updates the Bin.ordered_qty
        so that MRP/stock planning can account for upcoming demand.
        """
        self._update_reserved_qty(1)

    def on_cancel(self):
        """Reverse stock reservations."""
        self._update_reserved_qty(-1)

    def _update_reserved_qty(self, direction=1):
        """Update Bin.reserved_qty for each item+warehouse."""
        db = get_db()
        for item in self.get("items"):
            if item.get("warehouse") and item.get("item_code"):
                qty = flt(item.get("qty", 0)) * direction
                bin_data = db.get_value(
                    "Bin",
                    {"item_code": item["item_code"], "warehouse": item["warehouse"]},
                    ["name", "reserved_qty"],
                )
                if bin_data:
                    new_reserved = flt(bin_data.reserved_qty) + qty
                    db.set_value("Bin", bin_data.name, "reserved_qty", max(0, new_reserved))
        db.commit()

    def update_delivery_status(self):
        """Update per_delivered based on delivered quantities."""
        total_qty = sum(flt(item.get("qty")) for item in self.get("items"))
        delivered_qty = sum(flt(item.get("delivered_qty")) for item in self.get("items"))
        if total_qty:
            self._data["per_delivered"] = flt(delivered_qty / total_qty * 100, 2)
        self._set_status()
        self._persist()

    def update_billing_status(self):
        """Update per_billed based on billed quantities."""
        total_qty = sum(flt(item.get("qty")) for item in self.get("items"))
        billed_qty = sum(flt(item.get("billed_qty")) for item in self.get("items"))
        if total_qty:
            self._data["per_billed"] = flt(billed_qty / total_qty * 100, 2)
        self._set_status()
        self._persist()

def make_sales_invoice(sales_order_name):
    """Convert a Sales Order into a Sales Invoice.

    This is the standard flow: Sales Order -> Sales Invoice.
    """
    from lambda_erp.accounting.sales_invoice import SalesInvoice

    db = get_db()
    so = SalesOrder.load(sales_order_name)

    if so.docstatus != 1:
        raise ValidationError("Sales Order must be submitted before creating Sales Invoice")

    si = SalesInvoice(
        customer=so.customer,
        customer_name=so.customer_name,
        company=so.company,
        currency=so.currency,
        conversion_rate=so.conversion_rate,
        posting_date=nowdate(),
        sales_order=so.name,
    )

    for item in so.get("items"):
        unbilled_qty = flt(item.get("qty")) - flt(item.get("billed_qty"))
        if unbilled_qty <= 0:
            continue

        si.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=unbilled_qty,
            uom=item.get("uom"),
            rate=item.get("rate"),
            price_list_rate=item.get("price_list_rate"),
            discount_percentage=item.get("discount_percentage"),
            warehouse=item.get("warehouse"),
            cost_center=item.get("cost_center"),
            sales_order=so.name,
            sales_order_item=item.get("name"),
        ))

    for tax in so.get("taxes") or []:
        si.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
            included_in_print_rate=tax.get("included_in_print_rate"),
        ))

    return si
