"""
Stock Entry - Material movements.

Stock Entry handles all material movements:
- Material Receipt: goods coming INTO a warehouse (no source)
- Material Issue: goods going OUT of a warehouse (no target)
- Material Transfer: goods moving between warehouses

Each type creates Stock Ledger Entries (SLEs) and optionally
GL entries for perpetual inventory.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.stock.stock_ledger import make_sl_entries
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.exceptions import ValidationError

class StockEntry(Document):
    DOCTYPE = "Stock Entry"
    CHILD_TABLES = {
        "items": ("Stock Entry Detail", None),
    }
    PREFIX = "STE"

    LINK_FIELDS = {
        "company": "Company",
        "from_warehouse": "Warehouse",
        "to_warehouse": "Warehouse",
    }
    CHILD_LINK_FIELDS = {
        "items": {
            "item_code": "Item",
            "s_warehouse": "Warehouse",
            "t_warehouse": "Warehouse",
        },
    }

    def validate(self):
        if not self.stock_entry_type:
            raise ValidationError(
                "Stock Entry Type is required (Material Receipt, Material Issue, Material Transfer)"
            )
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._validate_warehouses()
        self._set_item_defaults()
        self._calculate_totals()

    def _validate_warehouses(self):
        """Validate source/target warehouses based on entry type."""
        for item in self.get("items"):
            if self.stock_entry_type in ("Material Receipt", "Opening Stock"):
                if not item.get("t_warehouse"):
                    item["t_warehouse"] = self.to_warehouse
                if not item.get("t_warehouse"):
                    raise ValidationError(
                        f"Target Warehouse is required for {self.stock_entry_type} (Item: {item.get('item_code')})"
                    )
            elif self.stock_entry_type == "Material Issue":
                if not item.get("s_warehouse"):
                    item["s_warehouse"] = self.from_warehouse
                if not item.get("s_warehouse"):
                    raise ValidationError(
                        f"Source Warehouse is required for Material Issue (Item: {item.get('item_code')})"
                    )
            elif self.stock_entry_type == "Material Transfer":
                if not item.get("s_warehouse"):
                    item["s_warehouse"] = self.from_warehouse
                if not item.get("t_warehouse"):
                    item["t_warehouse"] = self.to_warehouse
                if not item.get("s_warehouse") or not item.get("t_warehouse"):
                    raise ValidationError(
                        f"Both Source and Target Warehouse required for Transfer (Item: {item.get('item_code')})"
                    )

    def _set_item_defaults(self):
        db = get_db()
        for item in self.get("items"):
            if item.get("item_code") and not item.get("item_name"):
                item_data = db.get_value(
                    "Item", item["item_code"], ["item_name", "stock_uom", "standard_rate"]
                )
                if item_data:
                    item["item_name"] = item_data.item_name
                    item["uom"] = item.get("uom") or item_data.stock_uom

    def _calculate_totals(self):
        """Calculate total values for the stock entry."""
        total_incoming = 0
        total_outgoing = 0
        total_amount = 0

        for item in self.get("items"):
            qty = flt(item.get("qty", 0))
            rate = flt(item.get("basic_rate", 0))
            item["basic_amount"] = flt(qty * rate, 2)
            item["amount"] = item["basic_amount"]

            if item.get("t_warehouse"):
                total_incoming += item["basic_amount"]
            if item.get("s_warehouse"):
                total_outgoing += item["basic_amount"]
            total_amount += item["basic_amount"]

        self._data["total_incoming_value"] = flt(total_incoming, 2)
        self._data["total_outgoing_value"] = flt(total_outgoing, 2)
        self._data["value_difference"] = flt(total_incoming - total_outgoing, 2)
        self._data["total_amount"] = flt(total_amount, 2)

    def on_submit(self):
        """Create Stock Ledger Entries and GL entries.

        - update_stock_ledger() -> make_sl_entries()
        - make_gl_entries() (for perpetual inventory)
        """
        sl_entries = self._get_sl_entries()
        make_sl_entries(sl_entries)

        # GL entries for perpetual inventory
        gl_entries = self._get_gl_entries()
        if gl_entries:
            make_gl_entries(gl_entries)

    def on_cancel(self):
        """Reverse SLEs and GL entries."""
        # Reverse SLEs by creating negative entries
        sl_entries = self._get_sl_entries()
        for sle in sl_entries:
            sle["actual_qty"] = -flt(sle["actual_qty"])
            # Swap incoming/outgoing rates
            incoming = sle.get("incoming_rate", 0)
            outgoing = sle.get("outgoing_rate", 0)
            sle["incoming_rate"] = outgoing
            sle["outgoing_rate"] = incoming
        make_sl_entries(sl_entries, allow_negative_stock=True)

        # Reverse GL entries
        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

    def _get_sl_entries(self):
        """Build Stock Ledger Entry list.

        For Material Transfer, each item creates TWO SLEs:
        1. Negative from source warehouse
        2. Positive to target warehouse
        """
        sl_entries = []

        for item in self.get("items"):
            # Outgoing (from source warehouse)
            if item.get("s_warehouse"):
                sl_entries.append(
                    _dict(
                        item_code=item["item_code"],
                        warehouse=item["s_warehouse"],
                        actual_qty=-flt(item["qty"]),
                        outgoing_rate=flt(item.get("basic_rate") or item.get("valuation_rate", 0)),
                        incoming_rate=0,
                        voucher_type=self.DOCTYPE,
                        voucher_no=self.name,
                        voucher_detail_no=item.get("name"),
                        posting_date=self.posting_date,
                        posting_time=self.posting_time or "00:00:00",
                        company=self.company,
                    )
                )

            # Incoming (to target warehouse)
            if item.get("t_warehouse"):
                sl_entries.append(
                    _dict(
                        item_code=item["item_code"],
                        warehouse=item["t_warehouse"],
                        actual_qty=flt(item["qty"]),
                        incoming_rate=flt(item.get("basic_rate") or item.get("valuation_rate", 0)),
                        outgoing_rate=0,
                        voucher_type=self.DOCTYPE,
                        voucher_no=self.name,
                        voucher_detail_no=item.get("name"),
                        posting_date=self.posting_date,
                        posting_time=self.posting_time or "00:00:00",
                        company=self.company,
                    )
                )

        return sl_entries

    def _get_gl_entries(self):
        """Build GL entries for perpetual inventory.

        In perpetual inventory, stock movements also create accounting entries:
        - Opening Stock:   Debit Stock In Hand, Credit Opening Balance Equity
                           (one-time seed of inventory at company setup)
        - Material Receipt: Debit Stock In Hand, Credit Stock Adjustment
                           (manual adjustments / found stock, not supplier deliveries)
        - Material Issue:  Debit Stock Adjustment, Credit Stock In Hand
                           (write-offs / internal consumption)
        - Material Transfer: No GL impact (same Stock In Hand account)
        """
        db = get_db()
        if not self.company:
            return []

        gl_entries = []
        stock_account = None
        expense_account = None

        # Get stock and expense accounts from warehouse or company defaults
        for item in self.get("items"):
            if item.get("t_warehouse"):
                stock_account = (
                    db.get_value("Warehouse", item["t_warehouse"], "account")
                    or db.get_value("Account",
                                    {"company": self.company, "account_type": "Stock", "is_group": 0},
                                    "name")
                )
            if item.get("s_warehouse"):
                stock_account = stock_account or (
                    db.get_value("Warehouse", item["s_warehouse"], "account")
                    or db.get_value("Account",
                                    {"company": self.company, "account_type": "Stock", "is_group": 0},
                                    "name")
                )

        if not stock_account:
            return []  # No perpetual inventory

        cost_center = db.get_value("Company", self.company, "default_cost_center")

        if self.stock_entry_type == "Opening Stock":
            # One-time seed of inventory at company setup. Contra to equity
            # (Opening Balance Equity) so the P&L is not distorted by stock
            # that the business had on day one but didn't "earn".
            contra_account = db.get_value(
                "Company", self.company, "default_opening_balance_equity"
            )
            if stock_account and contra_account:
                gl_entries = [
                    _dict(
                        account=stock_account,
                        debit=flt(self.total_incoming_value, 2),
                        debit_in_account_currency=flt(self.total_incoming_value, 2),
                        credit=0, credit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Opening stock via {self.name}",
                    ),
                    _dict(
                        account=contra_account,
                        credit=flt(self.total_incoming_value, 2),
                        credit_in_account_currency=flt(self.total_incoming_value, 2),
                        debit=0, debit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Opening stock via {self.name}",
                    ),
                ]

        elif self.stock_entry_type == "Material Receipt":
            # Manual inventory receipts (adjustments, found stock). Contra to
            # Stock Adjustment (expense). For opening balances use the
            # dedicated "Opening Stock" type above instead.
            contra_account = db.get_value("Company", self.company, "stock_adjustment_account")
            if stock_account and contra_account:
                gl_entries = [
                    _dict(
                        account=stock_account,
                        debit=flt(self.total_incoming_value, 2),
                        debit_in_account_currency=flt(self.total_incoming_value, 2),
                        credit=0, credit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Material Receipt via {self.name}",
                    ),
                    _dict(
                        account=contra_account,
                        credit=flt(self.total_incoming_value, 2),
                        credit_in_account_currency=flt(self.total_incoming_value, 2),
                        debit=0, debit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Material Receipt via {self.name}",
                    ),
                ]

        elif self.stock_entry_type == "Material Issue":
            # Manual issues are write-offs or internal consumption, not sales.
            # Route them to Stock Adjustment rather than COGS/default expense —
            # COGS should only be credited/debited by documents that actually
            # correspond to a sale (Sales Invoice, Delivery Note, POS).
            expense_account = db.get_value("Company", self.company, "stock_adjustment_account")
            if stock_account and expense_account:
                gl_entries = [
                    _dict(
                        account=expense_account,
                        debit=flt(self.total_outgoing_value, 2),
                        debit_in_account_currency=flt(self.total_outgoing_value, 2),
                        credit=0, credit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Material Issue via {self.name}",
                    ),
                    _dict(
                        account=stock_account,
                        credit=flt(self.total_outgoing_value, 2),
                        credit_in_account_currency=flt(self.total_outgoing_value, 2),
                        debit=0, debit_in_account_currency=0,
                        cost_center=cost_center,
                        voucher_type=self.DOCTYPE, voucher_no=self.name,
                        posting_date=self.posting_date, company=self.company,
                        remarks=f"Material Issue via {self.name}",
                    ),
                ]

        # Material Transfer has no GL impact (stock stays in same Stock In Hand account)

        return gl_entries
