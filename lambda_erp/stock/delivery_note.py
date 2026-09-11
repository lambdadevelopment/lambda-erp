"""
Delivery Note.

Delivery Note records goods shipped to a customer from a Sales Order:
  Sales Order -> Delivery Note -> Sales Invoice

Key behaviors:
- Creates Stock Ledger Entries (stock OUT from warehouse)
- Creates GL entries (Dr: COGS, Cr: Stock In Hand)
- Updates delivered_qty on the linked Sales Order
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.controllers.defaults import set_default_currency
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.stock_ledger import (
    make_sl_entries,
    build_sell_side_sles,
    build_cost_basis_gl,
    reverse_stock_sles,
)
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries

class DeliveryNote(Document):
    DOCTYPE = "Delivery Note"
    CHILD_TABLES = {
        "items": ("Delivery Note Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "DN"

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
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_customer_name()
        self._set_item_defaults()

        if self.is_return:
            self._validate_return()

        set_default_currency(self, "Customer", "customer")
        calculate_taxes_and_totals(self)
        self._set_status()

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

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

    def _validate_return(self):
        from lambda_erp.workflow import validate_return
        validate_return(self)

    def _set_status(self):
        if self.docstatus == 0:
            self._data["status"] = "Draft"
        elif self.docstatus == 2:
            self._data["status"] = "Cancelled"
        elif self.docstatus == 1:
            if flt(self.per_billed) >= 100:
                self._data["status"] = "Completed"
            else:
                self._data["status"] = "To Bill"

    def on_submit(self):
        sl_entries = self._get_sl_entries()
        make_sl_entries(sl_entries)

        gl_entries = self._get_gl_entries()
        if gl_entries:
            make_gl_entries(gl_entries)

        # Shared lifecycle recalculates order progress after ledger hooks.

    def on_cancel(self):
        reversed_sles = reverse_stock_sles(self._get_sl_entries())
        if reversed_sles:
            make_sl_entries(reversed_sles, allow_negative_stock=True)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

        # Shared lifecycle recalculates order progress after ledger hooks.

    def _get_sl_entries(self):
        return build_sell_side_sles(self, self.get("items"))

    def _get_gl_entries(self):
        return build_cost_basis_gl(self, remarks=f"Delivery Note {self.name}")

    def _update_sales_order_delivered(self, cancel=False):
        from lambda_erp.workflow import refresh_order_progress
        refresh_order_progress(self)

def make_delivery_note(sales_order_name):
    """Convert a Sales Order into a Delivery Note."""
    from lambda_erp.selling.sales_order import SalesOrder

    so = SalesOrder.load(sales_order_name)
    if so.docstatus != 1:
        raise ValidationError("Sales Order must be submitted before creating Delivery Note")

    dn = DeliveryNote(
        customer=so.customer,
        customer_name=so.customer_name,
        company=so.company,
        currency=so.currency,
        conversion_rate=so.conversion_rate,
        posting_date=nowdate(),
    )

    for item in so.get("items"):
        undelivered = flt(item.get("qty")) - flt(item.get("delivered_qty"))
        if undelivered <= 0:
            continue
        dn.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=undelivered,
            uom=item.get("uom"),
            rate=item.get("rate"),
            warehouse=item.get("warehouse"),
            against_sales_order=so.name,
            so_detail=item.get("name"),
        ))

    for tax in so.get("taxes") or []:
        dn.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
        ))

    return dn

def make_delivery_return(dn_name):
    """Create a return Delivery Note (stock back in) from an existing Delivery Note."""
    original = DeliveryNote.load(dn_name)

    if original.docstatus != 1:
        raise ValidationError("Delivery Note must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_dn = DeliveryNote(
        customer=original.customer,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        is_return=1,
        return_against=original.name,
    )

    from lambda_erp.workflow import returnable_rows
    for item in returnable_rows(original):
        return_dn.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"),
            rate=flt(item.get("rate")),
            warehouse=item.get("warehouse"),
            against_sales_order=item.get("against_sales_order"),
            so_detail=item.get("so_detail"),
        ))

    return return_dn
