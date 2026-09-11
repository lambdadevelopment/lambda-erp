"""
Purchase Order.

Purchase Order is the buying-side equivalent of Sales Order:
  Purchase Order -> Purchase Receipt -> Purchase Invoice

Key behaviors:
- Does NOT create GL entries
- Updates ordered_qty in Bin (for MRP planning)
- Tracks receipt and billing status
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.controllers.defaults import set_default_currency
from lambda_erp.exceptions import ValidationError

class PurchaseOrder(Document):
    DOCTYPE = "Purchase Order"
    CHILD_TABLES = {
        "items": ("Purchase Order Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "PO"

    LINK_FIELDS = {
        "supplier": "Supplier",
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
        if not self.supplier:
            raise ValidationError("Supplier is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.transaction_date:
            self.transaction_date = nowdate()

        self._set_supplier_name()
        self._set_item_defaults()

        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        apply_pricing_rules(self)

        set_default_currency(self, "Supplier", "supplier")

        calculate_taxes_and_totals(self)

    def _set_supplier_name(self):
        if not self.supplier_name and self.supplier:
            db = get_db()
            self.supplier_name = db.get_value("Supplier", self.supplier, "supplier_name")

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

    def _update_ordered_qty(self, direction=1):
        from lambda_erp.workflow import refresh_order_progress
        refresh_order_progress(self)

    def update_receipt_status(self):
        """Refresh progress from submitted vouchers, not caller-supplied counters."""
        from lambda_erp.workflow import refresh_order_progress
        with get_db().atomic():
            refresh_order_progress(self)
        self.reload()

def make_purchase_invoice(purchase_order_name):
    """Convert a Purchase Order into a Purchase Invoice."""
    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice

    db = get_db()
    po = PurchaseOrder.load(purchase_order_name)

    if po.docstatus != 1:
        raise ValidationError("Purchase Order must be submitted before creating Purchase Invoice")

    pi = PurchaseInvoice(
        supplier=po.supplier,
        supplier_name=po.supplier_name,
        company=po.company,
        currency=po.currency,
        conversion_rate=po.conversion_rate,
        posting_date=nowdate(),
        purchase_order=po.name,
    )

    for item in po.get("items"):
        unbilled = flt(item.get("qty")) - flt(item.get("billed_qty"))
        if unbilled <= 0:
            continue

        pi.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=unbilled,
            uom=item.get("uom"),
            rate=item.get("rate"),
            warehouse=item.get("warehouse"),
            purchase_order=po.name,
            purchase_order_item=item.get("name"),
        ))

    for tax in po.get("taxes") or []:
        pi.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
            included_in_print_rate=tax.get("included_in_print_rate"),
        ))

    return pi
