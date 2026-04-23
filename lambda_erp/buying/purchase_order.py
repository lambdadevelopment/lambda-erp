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

    def on_submit(self):
        """Update ordered_qty in Bin for MRP planning."""
        self._update_ordered_qty(1)

    def on_cancel(self):
        self._update_ordered_qty(-1)

    def _update_ordered_qty(self, direction=1):
        db = get_db()
        for item in self.get("items"):
            if item.get("warehouse") and item.get("item_code"):
                qty = flt(item.get("qty", 0)) * direction
                bin_data = db.get_value(
                    "Bin",
                    {"item_code": item["item_code"], "warehouse": item["warehouse"]},
                    ["name", "ordered_qty"],
                )
                if bin_data:
                    new_ordered = flt(bin_data.ordered_qty) + qty
                    db.set_value("Bin", bin_data.name, "ordered_qty", max(0, new_ordered))
                elif direction > 0:
                    db.insert("Bin", _dict(
                        name=f"{item['item_code']}-{item['warehouse']}",
                        item_code=item["item_code"],
                        warehouse=item["warehouse"],
                        ordered_qty=qty,
                    ))
        db.commit()

    def update_receipt_status(self):
        """Update per_received based on received quantities."""
        total_qty = sum(flt(item.get("qty")) for item in self.get("items"))
        received_qty = sum(flt(item.get("received_qty")) for item in self.get("items"))
        if total_qty:
            self._data["per_received"] = flt(received_qty / total_qty * 100, 2)
        self._persist()

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
