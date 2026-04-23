"""
Purchase Receipt.

Purchase Receipt records goods received from a supplier against a Purchase Order:
  Purchase Order -> Purchase Receipt -> Purchase Invoice

Key behaviors:
- Creates Stock Ledger Entries (stock IN to warehouse)
- Creates GL entries (Dr: Stock In Hand, Cr: Stock Received But Not Billed)
- Updates received_qty on the linked Purchase Order
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.stock_ledger import make_sl_entries
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries

class PurchaseReceipt(Document):
    DOCTYPE = "Purchase Receipt"
    CHILD_TABLES = {
        "items": ("Purchase Receipt Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "PREC"

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
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_supplier_name()
        self._set_item_defaults()

        if self.is_return:
            self._validate_return()

        calculate_taxes_and_totals(self)
        self._set_status()

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

    def _validate_return(self):
        """Validate return-specific rules."""
        if not self.return_against:
            raise ValidationError("Return Against is required for a return Purchase Receipt")

        db = get_db()
        original = db.get_value(self.DOCTYPE, self.return_against, ["name", "docstatus"])
        if not original:
            raise ValidationError(f"Original Purchase Receipt {self.return_against} not found")
        if original.docstatus != 1:
            raise ValidationError(f"Original Purchase Receipt {self.return_against} must be submitted")

        original_doc = PurchaseReceipt.load(self.return_against)
        original_items = {item["item_code"]: flt(item["qty"]) for item in original_doc.get("items")}
        for item in self.get("items"):
            orig_qty = original_items.get(item.get("item_code"), 0)
            return_qty = abs(flt(item.get("qty")))
            if return_qty > orig_qty:
                raise ValidationError(
                    f"Return qty ({return_qty}) for {item.get('item_code')} exceeds "
                    f"original qty ({orig_qty})"
                )

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

        self._update_purchase_order_received()

    def on_cancel(self):
        # Must be the first thing — if we've already touched the ledgers,
        # raising here leaves them half-reversed until the outer transaction
        # rolls back. Cheap check first, expensive work after.
        self._check_no_linked_purchase_invoice()

        sl_entries = self._get_sl_entries()
        for sle in sl_entries:
            sle["actual_qty"] = -flt(sle["actual_qty"])
            incoming = sle.get("incoming_rate", 0)
            outgoing = sle.get("outgoing_rate", 0)
            sle["incoming_rate"] = outgoing
            sle["outgoing_rate"] = incoming
        make_sl_entries(sl_entries, allow_negative_stock=True)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

        self._update_purchase_order_received(cancel=True)

    def _check_no_linked_purchase_invoice(self):
        """Block cancel if a submitted Purchase Invoice for the same PO has
        already cleared this receipt's SRBNB entry. Otherwise the reversal
        here (Cr SIH / Dr SRBNB) runs against a SRBNB account the PI already
        zeroed, producing an orphan SRBNB debit with no real payable to match.
        User's recourse: cancel the PI first, then retry the PR cancel.
        """
        db = get_db()
        pos = {
            item.get("against_purchase_order")
            for item in self.get("items")
            if item.get("against_purchase_order")
        }
        if not pos:
            return

        placeholders = ",".join("?" * len(pos))
        rows = db.sql(
            f'SELECT DISTINCT pi_parent.name AS pi_name '
            f'FROM "Purchase Invoice Item" pii '
            f'JOIN "Purchase Invoice" pi_parent ON pi_parent.name = pii.parent '
            f'WHERE pi_parent.docstatus = 1 '
            f'  AND pii.purchase_order IN ({placeholders})',
            list(pos),
        )
        if rows:
            raise ValidationError(
                f"Cannot cancel {self.name}: Purchase Invoice {rows[0]['pi_name']} "
                f"is already submitted against the same Purchase Order. Cancel "
                f"the Purchase Invoice first so SRBNB returns to its pre-bill state."
            )

    def _get_sl_entries(self):
        sl_entries = []
        for item in self.get("items"):
            warehouse = item.get("warehouse")
            if not warehouse:
                continue
            actual_qty = flt(item["qty"])  # Normal: positive (in). Return: negative (out).
            rate = flt(item.get("rate", 0))
            sl_entries.append(_dict(
                item_code=item["item_code"],
                warehouse=warehouse,
                actual_qty=actual_qty,
                incoming_rate=rate if actual_qty > 0 else 0,
                outgoing_rate=rate if actual_qty < 0 else 0,
                voucher_type=self.DOCTYPE,
                voucher_no=self.name,
                voucher_detail_no=item.get("name"),
                posting_date=self.posting_date,
                company=self.company,
            ))
        return sl_entries

    def _get_gl_entries(self):
        db = get_db()
        gl_entries = []

        stock_account = db.get_value("Company", self.company, "stock_in_hand_account")
        srbnb_account = db.get_value("Company", self.company, "stock_received_but_not_billed")

        if not stock_account or not srbnb_account:
            return []

        total = sum(flt(item.get("amount", 0)) for item in self.get("items"))
        if not total:
            return []

        # Dr: Stock In Hand
        gl_entries.append(_dict(
            account=stock_account,
            debit=flt(total, 2),
            debit_in_account_currency=flt(total, 2),
            credit=0,
            credit_in_account_currency=0,
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
            posting_date=self.posting_date,
            company=self.company,
        ))

        # Cr: Stock Received But Not Billed
        gl_entries.append(_dict(
            account=srbnb_account,
            debit=0,
            debit_in_account_currency=0,
            credit=flt(total, 2),
            credit_in_account_currency=flt(total, 2),
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
            posting_date=self.posting_date,
            company=self.company,
        ))

        return gl_entries

    def _update_purchase_order_received(self, cancel=False):
        db = get_db()
        updated_pos = set()
        po_details = set()
        for item in self.get("items"):
            if item.get("against_purchase_order"):
                updated_pos.add(item["against_purchase_order"])
            if item.get("po_detail"):
                po_details.add(item["po_detail"])

        for po_detail in po_details:
            result = db.sql(
                """SELECT COALESCE(SUM(qty), 0) as total_received
                   FROM "Purchase Receipt Item"
                   WHERE po_detail = ?
                     AND parent IN (
                         SELECT name FROM "Purchase Receipt" WHERE docstatus = 1
                     )""",
                [po_detail],
            )
            received = flt(result[0]["total_received"]) if result else 0
            db.set_value("Purchase Order Item", po_detail, "received_qty", received)

        for po_name in updated_pos:
            from lambda_erp.buying.purchase_order import PurchaseOrder
            po = PurchaseOrder.load(po_name)
            po.update_receipt_status()

def make_purchase_receipt(purchase_order_name):
    """Convert a Purchase Order into a Purchase Receipt."""
    from lambda_erp.buying.purchase_order import PurchaseOrder

    po = PurchaseOrder.load(purchase_order_name)
    if po.docstatus != 1:
        raise ValidationError("Purchase Order must be submitted before creating Purchase Receipt")

    pr = PurchaseReceipt(
        supplier=po.supplier,
        supplier_name=po.supplier_name,
        company=po.company,
        currency=po.currency,
        conversion_rate=po.conversion_rate,
        posting_date=nowdate(),
    )

    for item in po.get("items"):
        unreceived = flt(item.get("qty")) - flt(item.get("received_qty"))
        if unreceived <= 0:
            continue
        pr.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=unreceived,
            uom=item.get("uom"),
            rate=item.get("rate"),
            warehouse=item.get("warehouse"),
            against_purchase_order=po.name,
            po_detail=item.get("name"),
        ))

    for tax in po.get("taxes") or []:
        pr.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
        ))

    return pr

def make_purchase_receipt_return(prec_name):
    """Create a return Purchase Receipt (stock back out) from an existing Purchase Receipt."""
    original = PurchaseReceipt.load(prec_name)

    if original.docstatus != 1:
        raise ValidationError("Purchase Receipt must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_pr = PurchaseReceipt(
        supplier=original.supplier,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        is_return=1,
        return_against=original.name,
    )

    for item in original.get("items"):
        return_pr.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"),
            rate=flt(item.get("rate")),
            warehouse=item.get("warehouse"),
            against_purchase_order=item.get("against_purchase_order"),
            po_detail=item.get("po_detail"),
        ))

    return return_pr
