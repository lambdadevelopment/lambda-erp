"""
Stock Ledger Entry processing.

The heart of inventory management, equivalent to what general_ledger.py is
for accounting.

Every stock movement creates Stock Ledger Entries (SLEs) that track:
- actual_qty: quantity change (+/-)
- qty_after_transaction: running balance
- incoming_rate / outgoing_rate: unit cost
- valuation_rate: weighted average cost per unit
- stock_value: total value of stock after this transaction

Valuation methods supported:
- FIFO (First In First Out)
- Moving Average (weighted average)

The flow:
  Document.on_submit() -> StockController.make_sl_entries()
    -> stock_ledger.make_sl_entries(sl_entries)
      -> create SLE records
      -> update_entries_after() (recalculate valuation)
      -> update_bin_qty() (update Bin summary)
"""

from lambda_erp.utils import _dict, flt, new_name, now, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import NegativeStockError

def make_sl_entries(sl_entries, allow_negative_stock=False):
    """Create Stock Ledger Entries from a list of SLE dicts.

    Called on every stock transaction.

    Args:
        sl_entries: list of _dict with item_code, warehouse, actual_qty,
                   incoming_rate, voucher_type, voucher_no, etc.
        allow_negative_stock: if False, raises NegativeStockError
    """
    db = get_db()

    if not sl_entries:
        return

    for sle in sl_entries:
        if not flt(sle.get("actual_qty")):
            continue

        # Create the SLE record
        sle_doc = _dict(
            name=new_name("SLE"),
            posting_date=sle.get("posting_date") or nowdate(),
            posting_time=sle.get("posting_time", "00:00:00"),
            item_code=sle["item_code"],
            warehouse=sle["warehouse"],
            actual_qty=flt(sle["actual_qty"]),
            incoming_rate=flt(sle.get("incoming_rate", 0)),
            outgoing_rate=flt(sle.get("outgoing_rate", 0)),
            voucher_type=sle.get("voucher_type"),
            voucher_no=sle.get("voucher_no"),
            voucher_detail_no=sle.get("voucher_detail_no"),
            batch_no=sle.get("batch_no"),
            serial_no=sle.get("serial_no"),
            company=sle.get("company"),
            is_cancelled=0,
            creation=now(),
            modified=now(),
        )

        # Calculate running balances
        update_stock_values(sle_doc, allow_negative_stock)

        # Persist
        db.insert("Stock Ledger Entry", sle_doc)

        # Update Bin (summary table)
        update_bin(sle_doc)

    db.commit()

def update_stock_values(sle, allow_negative_stock=False):
    """Calculate qty_after_transaction, valuation_rate, stock_value.

    This is a simplified port of the reference implementation's update_entries_after() which
    handles the full FIFO queue or moving average calculation.

    We implement Moving Average here for simplicity, which is the most
    common valuation method.
    """
    db = get_db()

    # Get current stock state from Bin
    bin_data = db.get_value(
        "Bin",
        {"item_code": sle["item_code"], "warehouse": sle["warehouse"]},
        ["actual_qty", "valuation_rate", "stock_value"],
    )

    prev_qty = flt(bin_data.actual_qty) if bin_data else 0
    prev_val_rate = flt(bin_data.valuation_rate) if bin_data else 0
    prev_stock_value = flt(bin_data.stock_value) if bin_data else 0

    new_qty = prev_qty + flt(sle["actual_qty"])

    # Check for negative stock
    if new_qty < 0 and not allow_negative_stock:
        raise NegativeStockError(
            f"Negative stock not allowed for Item {sle['item_code']} "
            f"in Warehouse {sle['warehouse']}. "
            f"Available: {prev_qty}, Requested: {abs(flt(sle['actual_qty']))}"
        )

    # Calculate valuation using Moving Average
    # (the reference implementation also supports FIFO via a stock queue, but moving average
    # is the simpler and more common method)
    if flt(sle["actual_qty"]) > 0:
        # Incoming: weighted average of existing stock + new stock.
        # If the caller passes 0 (e.g. a customer-return delivery note), use
        # the current moving-average so the return lands at the same cost
        # basis the shipment went out at — symmetric with the outgoing branch.
        incoming_rate = flt(sle.get("incoming_rate")) or prev_val_rate
        incoming_value = flt(sle["actual_qty"]) * incoming_rate
        new_stock_value = prev_stock_value + incoming_value

        if new_qty > 0:
            new_val_rate = new_stock_value / new_qty
        else:
            new_val_rate = incoming_rate

        sle["incoming_rate"] = incoming_rate
        sle["stock_value_difference"] = incoming_value
    else:
        # Outgoing: use current valuation rate
        outgoing_rate = flt(sle.get("outgoing_rate")) or prev_val_rate
        outgoing_value = abs(flt(sle["actual_qty"])) * outgoing_rate
        new_stock_value = prev_stock_value - outgoing_value
        new_val_rate = prev_val_rate  # doesn't change on outgoing

        sle["outgoing_rate"] = outgoing_rate
        sle["stock_value_difference"] = -outgoing_value

    sle["qty_after_transaction"] = flt(new_qty)
    sle["valuation_rate"] = flt(new_val_rate, 2)
    sle["stock_value"] = flt(new_stock_value, 2)

def update_bin(sle):
    """Update the Bin (stock summary) table after an SLE.

    The Bin table maintains current stock levels per item+warehouse.
    It's the quick-lookup table for "how much do we have in stock?"
    """
    db = get_db()
    bin_name = f"{sle['item_code']}-{sle['warehouse']}"

    if db.exists("Bin", bin_name):
        db.set_value("Bin", bin_name, {
            "actual_qty": flt(sle["qty_after_transaction"]),
            "valuation_rate": flt(sle["valuation_rate"]),
            "stock_value": flt(sle["stock_value"]),
        })
    else:
        db.insert("Bin", _dict(
            name=bin_name,
            item_code=sle["item_code"],
            warehouse=sle["warehouse"],
            actual_qty=flt(sle["qty_after_transaction"]),
            valuation_rate=flt(sle["valuation_rate"]),
            stock_value=flt(sle["stock_value"]),
        ))

def get_stock_balance(item_code, warehouse):
    """Get current stock balance for an item in a warehouse."""
    db = get_db()
    bin_data = db.get_value(
        "Bin",
        {"item_code": item_code, "warehouse": warehouse},
        ["actual_qty", "valuation_rate", "stock_value"],
    )
    if bin_data:
        return _dict(bin_data)
    return _dict(actual_qty=0, valuation_rate=0, stock_value=0)

def get_stock_balance_all(item_code=None, warehouse=None):
    """Get stock balances, optionally filtered by item or warehouse."""
    db = get_db()
    filters = {}
    if item_code:
        filters["item_code"] = item_code
    if warehouse:
        filters["warehouse"] = warehouse

    return db.get_all(
        "Bin",
        filters=filters,
        fields=["item_code", "warehouse", "actual_qty", "valuation_rate", "stock_value"],
    )

# ---------------------------------------------------------------------------
# Voucher-level helpers
#
# Four documents all move stock and post matching GL: Delivery Note, Sales
# Invoice (update_stock), POS Invoice (update_stock), Purchase Invoice
# (update_stock). Before this section existed, each doc reimplemented the
# SLE + GL boilerplate, which is how POS drifted (sell-rate SLE, no GL) and
# needed a second fix round. These helpers are the single source of truth.
# ---------------------------------------------------------------------------

def build_sell_side_sles(doc, items):
    """SLEs for outgoing-stock docs (DN, direct-ship SI, POS update_stock).

    Negative actual_qty on a normal ship; positive on a return. Rates are
    passed as 0 so the stock ledger uses the moving-average cost for both
    directions — posting COGS at sell value was the original bug.
    """
    sl_entries = []
    for item in items:
        warehouse = item.get("warehouse")
        if not warehouse or not item.get("item_code"):
            continue
        actual_qty = -flt(item.get("qty"))
        sl_entries.append(_dict(
            item_code=item["item_code"],
            warehouse=warehouse,
            actual_qty=actual_qty,
            outgoing_rate=0,
            incoming_rate=0,
            voucher_type=doc.DOCTYPE,
            voucher_no=doc.name,
            voucher_detail_no=item.get("name"),
            posting_date=doc.posting_date,
            company=doc.company,
        ))
    return sl_entries

def build_buy_side_sles(doc, items):
    """SLEs for incoming-stock docs (direct-receive PI update_stock).

    Positive actual_qty on a normal receipt uses the supplier's invoice rate
    as the incoming cost — that's what the business actually paid, not a
    moving-average over prior stock. Negative actual_qty (return-to-supplier)
    falls back to moving-average via outgoing_rate=0.
    """
    sl_entries = []
    for item in items:
        warehouse = item.get("warehouse")
        if not warehouse or not item.get("item_code"):
            continue
        actual_qty = flt(item["qty"])
        rate = flt(item.get("net_rate") or item.get("rate", 0))
        sl_entries.append(_dict(
            item_code=item["item_code"],
            warehouse=warehouse,
            actual_qty=actual_qty,
            incoming_rate=rate if actual_qty > 0 else 0,
            outgoing_rate=0,
            voucher_type=doc.DOCTYPE,
            voucher_no=doc.name,
            voucher_detail_no=item.get("name"),
            posting_date=doc.posting_date,
            company=doc.company,
        ))
    return sl_entries

def build_cost_basis_gl(doc, *, remarks=None):
    """Dr COGS / Cr Stock In Hand at cost for a sell-side doc.

    Reads stock_value_difference from the SLEs already posted for this
    voucher, so call this AFTER make_sl_entries. Sign handles returns:
    stock_value_difference is negative for outgoing (Dr COGS/Cr SIH) and
    positive for returns (Cr COGS/Dr SIH).
    """
    db = get_db()

    default_expense = db.get_value("Company", doc.company, "default_expense_account")
    stock_account = db.get_value("Company", doc.company, "stock_in_hand_account")
    if not default_expense or not stock_account:
        return []

    cost_rows = db.sql(
        'SELECT COALESCE(SUM(stock_value_difference), 0) as diff '
        'FROM "Stock Ledger Entry" '
        'WHERE voucher_type = ? AND voucher_no = ? AND is_cancelled = 0',
        [doc.DOCTYPE, doc.name],
    )
    cost_diff = flt(cost_rows[0]["diff"]) if cost_rows else 0
    if not cost_diff:
        return []

    cogs_debit = -cost_diff
    sih_credit = -cost_diff
    cost_center = db.get_value("Company", doc.company, "default_cost_center")
    remark = remarks or f"{doc.DOCTYPE} {doc.name}"

    return [
        _dict(
            account=default_expense,
            debit=flt(cogs_debit, 2),
            debit_in_account_currency=flt(cogs_debit, 2),
            credit=0,
            credit_in_account_currency=0,
            cost_center=cost_center,
            voucher_type=doc.DOCTYPE,
            voucher_no=doc.name,
            posting_date=doc.posting_date,
            company=doc.company,
            remarks=remark,
        ),
        _dict(
            account=stock_account,
            debit=0,
            debit_in_account_currency=0,
            credit=flt(sih_credit, 2),
            credit_in_account_currency=flt(sih_credit, 2),
            voucher_type=doc.DOCTYPE,
            voucher_no=doc.name,
            posting_date=doc.posting_date,
            company=doc.company,
            remarks=remark,
        ),
    ]

def reverse_stock_sles(sl_entries):
    """Flip actual_qty and swap incoming/outgoing rates for cancel-time
    reversal. Returns new dicts so the caller's originals stay intact."""
    reversed_ = []
    for sle in sl_entries:
        flipped = _dict(dict(sle))
        flipped["actual_qty"] = -flt(sle["actual_qty"])
        incoming = sle.get("incoming_rate", 0)
        outgoing = sle.get("outgoing_rate", 0)
        flipped["incoming_rate"] = outgoing
        flipped["outgoing_rate"] = incoming
        reversed_.append(flipped)
    return reversed_
