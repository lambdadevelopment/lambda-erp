"""
Stock Entry - Material movements.

Stock Entry handles all material movements:
- Material Receipt: goods coming INTO a warehouse (no source)
- Material Issue: goods going OUT of a warehouse (no target)
- Material Transfer: goods moving between warehouses
- Batch Assignment: stock without a batch gets its batch, in place

Each type creates Stock Ledger Entries (SLEs) and optionally
GL entries for perpetual inventory.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.stock.stock_ledger import make_cancel_sl_entries, make_sl_entries, reverse_stock_sles, stock_account_for
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.exceptions import ValidationError
import math

class StockEntry(Document):
    DOCTYPE = "Stock Entry"
    SUBMITTABLE = True
    CHILD_TABLES = {
        "items": ("Stock Entry Detail", None),
    }
    PREFIX = "STE"
    REQUIRED_FIELDS = ("company", "items", "stock_entry_type")
    CHILD_REQUIREMENTS = {'items': {'required': ['item_code', 'qty']}}
    CONDITIONAL_REQUIREMENTS = (
        "Receipt/opening rows require an explicit finite basic_rate >= 0 in company currency (zero means intentionally unvalued). Issues and transfers use actual inventory cost, not a supplied selling rate. Financial postings derive from posted stock values.",
        "stock_entry_type must be Opening Stock, Material Receipt, Material Issue, Material Transfer or Batch Assignment.",
        "Every items row requires an existing item_code and a positive finite qty. Receipt/opening need t_warehouse; issue needs s_warehouse; transfer needs two distinct warehouses. Warehouses must belong to company.",
        "Rows of a batch-tracked item (Item.has_batch_no) require batch_no; a new batch on a receipt/opening row also needs expiry_date when the item has expiry dates. A transfer moves the batch as it is. Items that are not batch-tracked take no batch_no.",
        "Batch Assignment gives stock that has no batch (from before the item was batch-tracked) its batch without moving it: each row needs the warehouse (s_warehouse or t_warehouse), batch_no and qty; it changes neither total quantity nor value and posts no GL.",
    )

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
        if self.stock_entry_type not in {"Opening Stock", "Material Receipt", "Material Issue", "Material Transfer",
                                         "Batch Assignment"}:
            raise ValidationError(
                "Stock Entry Type must be Opening Stock, Material Receipt, Material Issue, "
                "Material Transfer or Batch Assignment"
            )
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        if not self.company:
            raise ValidationError("Stock Entry: Company is required")
        for idx, item in enumerate(self.get("items"), 1):
            if not item.get('item_code'):
                raise ValidationError(f"Stock Entry row {idx}: Item Code is required")
            try:
                qty = float(item.get('qty'))
            except (ValueError, TypeError):
                raise ValidationError(f"Stock Entry row {idx}: Qty is required and must be positive")
            if not math.isfinite(qty) or qty <= 0:
                raise ValidationError(f"Stock Entry row {idx}: Qty must be positive and finite")
            item['qty'] = qty
            if self.stock_entry_type in {'Opening Stock', 'Material Receipt'}:
                try:
                    rate = float(item.get('basic_rate'))
                except (TypeError, ValueError):
                    raise ValidationError('Basic Rate is required for receipts/opening stock; use zero only for intentionally unvalued stock')
                if not math.isfinite(rate) or rate < 0:
                    raise ValidationError('Basic Rate must be finite and non-negative')
                item['basic_rate'] = rate

        self._validate_warehouses()
        db = get_db()
        for item in self.get('items'):
            if self.stock_entry_type == 'Material Transfer' and item.get('s_warehouse') == item.get('t_warehouse'):
                raise ValidationError('Source and Target Warehouse must be different')
            if self.stock_entry_type in ('Opening Stock', 'Material Receipt') and item.get('s_warehouse'):
                raise ValidationError('A receipt cannot have a Source Warehouse; use Material Transfer')
            if self.stock_entry_type == 'Material Issue' and item.get('t_warehouse'):
                raise ValidationError('An issue cannot have a Target Warehouse; use Material Transfer')
            for field in ('s_warehouse', 't_warehouse'):
                warehouse = item.get(field)
                if warehouse and db.exists('Warehouse', warehouse):
                    if db.get_value('Warehouse', warehouse, 'company') != self.company:
                        raise ValidationError('Stock Entry Warehouse must belong to Company')
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
            elif self.stock_entry_type == "Batch Assignment":
                # Same warehouse on both sides: the stock stays where it is.
                warehouse = (item.get("t_warehouse") or item.get("s_warehouse")
                             or self.to_warehouse or self.from_warehouse)
                if not warehouse:
                    raise ValidationError(
                        f"Warehouse is required for Batch Assignment (Item: {item.get('item_code')})")
                if item.get("s_warehouse") and item.get("t_warehouse") and item["s_warehouse"] != item["t_warehouse"]:
                    raise ValidationError(
                        "Batch Assignment does not move stock: Source and Target Warehouse must be the same")
                item["s_warehouse"] = item["t_warehouse"] = warehouse
                if not str(item.get("batch_no") or "").strip():
                    raise ValidationError(
                        f"Batch is required for Batch Assignment (Item: {item.get('item_code')})")
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
        db = get_db()
        incoming = outgoing = 0
        for item in self.get('items'):
            base = _dict(item_code=item['item_code'], voucher_type=self.DOCTYPE,
                         voucher_no=self.name, voucher_detail_no=item['name'],
                         posting_date=self.posting_date, posting_time=self.posting_time or '00:00:00',
                         company=self.company)
            rate = flt(item.get('basic_rate'))
            batch = {'batch_no': item.get('batch_no')}
            # Batch Assignment takes the quantity out of stock without a batch
            # and puts it back, at the same value, under the batch.
            source_batch = ({'batch_no': None, 'allow_unassigned_batch': True}
                            if self.stock_entry_type == 'Batch Assignment' else batch)
            if item.get('s_warehouse'):
                make_sl_entries([{**base, **source_batch, 'warehouse': item['s_warehouse'], 'actual_qty': -flt(item['qty']), 'outgoing_rate': 0}])
                posted = db.get_value('Stock Ledger Entry', {'voucher_type': self.DOCTYPE, 'voucher_no': self.name, 'voucher_detail_no': item['name'], 'warehouse': item['s_warehouse']}, ['stock_value_difference'])
                value = -flt(posted.stock_value_difference)
                rate = value / flt(item['qty'])
                outgoing += value
            if item.get('t_warehouse'):
                make_sl_entries([{**base, **batch, 'expiry_date': item.get('expiry_date'), 'warehouse': item['t_warehouse'], 'actual_qty': flt(item['qty']), 'incoming_rate': rate, 'incoming_rate_is_explicit': True}])
                incoming += flt(item['qty']) * rate
            item['basic_rate'] = rate
            item['basic_amount'] = item['amount'] = flt(flt(item['qty']) * rate, 2)
        self.total_incoming_value = flt(incoming, 2)
        self.total_outgoing_value = flt(outgoing, 2)
        self.value_difference = flt(incoming - outgoing, 2)
        self.total_amount = flt(sum(row['basic_amount'] for row in self.get('items')), 2)
        self._persist(commit=False)
        entries = self._get_gl_entries()
        if entries:
            make_gl_entries(entries)

    def on_cancel(self):
        # Reverse the actual posted cost, even if moving-average cost changed.
        rows = get_db().get_all('Stock Ledger Entry', filters={
            'voucher_type': self.DOCTYPE, 'voucher_no': self.name, 'is_cancelled': 0}, fields=['*'])
        reversals = reverse_stock_sles(rows)
        for row in reversals:
            row['incoming_rate_is_explicit'] = True
            row['outgoing_rate_is_explicit'] = True
        make_cancel_sl_entries(reversals)
        make_reverse_gl_entries(voucher_type=self.DOCTYPE, voucher_no=self.name)

    def _get_gl_entries(self):
        """One stock leg per warehouse; all values come from posted SLEs."""
        db = get_db()
        rows = db.sql('SELECT warehouse, SUM(stock_value_difference) AS value FROM "Stock Ledger Entry" '
                      'WHERE voucher_type = ? AND voucher_no = ? AND is_cancelled = 0 GROUP BY warehouse', [self.DOCTYPE, self.name])
        amounts = {}
        for row in rows:
            value = flt(row.value, 2)
            if not value:
                continue
            account = stock_account_for(self.company, row.warehouse)
            if not account:
                raise ValidationError('Stock account is required before posting a valued stock movement')
            amounts[account] = amounts.get(account, 0) + value
        if self.stock_entry_type == 'Batch Assignment':
            if abs(sum(amounts.values())) > 0.01:
                raise ValidationError('Batch Assignment must not change stock value')
            return []
        if self.stock_entry_type != 'Material Transfer':
            total = sum(amounts.values())
            if total:
                field = 'default_opening_balance_equity' if self.stock_entry_type == 'Opening Stock' else 'stock_adjustment_account'
                contra = db.get_value('Company', self.company, field)
                if not contra:
                    raise ValidationError(f'{field} is required before posting a valued stock movement')
                amounts[contra] = amounts.get(contra, 0) - total
        elif abs(sum(amounts.values())) > 0.01:
            raise ValidationError('Transfer must preserve stock value across warehouses')
        cost_center = db.get_value('Company', self.company, 'default_cost_center')
        return [_dict(account=account, debit=max(0, flt(value, 2)), credit=max(0, -flt(value, 2)),
                      debit_in_account_currency=max(0, flt(value, 2)), credit_in_account_currency=max(0, -flt(value, 2)),
                      cost_center=cost_center, voucher_type=self.DOCTYPE, voucher_no=self.name,
                      posting_date=self.posting_date, company=self.company, remarks=f'{self.stock_entry_type} via {self.name}')
                for account, value in amounts.items() if flt(value, 2)]
