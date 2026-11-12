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
from lambda_erp.exceptions import NegativeStockError, ValidationError

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

        batch_no = resolve_entry_batch(sle)
        location = resolve_entry_location(sle)
        from lambda_erp.stock import serial as serial_numbers
        serials = serial_numbers.resolve_entry_serials(sle)
        serial_numbers.check_leaving_serials(sle, serials, batch_no, location)

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
            batch_no=batch_no or None,
            location=location or None,
            serial_no=serial_numbers.format_serials(serials),
            company=sle.get("company"),
            is_cancelled=0,
            creation=now(),
            modified=now(),
        )

        # Calculate running balances
        sle_doc['incoming_rate_is_explicit'] = sle.get('incoming_rate_is_explicit', False)
        sle_doc['outgoing_rate_is_explicit'] = sle.get('outgoing_rate_is_explicit', False)
        update_stock_values(sle_doc, allow_negative_stock)
        sle_doc.pop('incoming_rate_is_explicit', None)
        sle_doc.pop('outgoing_rate_is_explicit', None)

        # Persist
        db.insert("Stock Ledger Entry", sle_doc)

        # Update Bin (summary table) and the per-batch quantity under it
        update_bin(sle_doc)
        update_stock_balance_detail(sle_doc, batch_no, allow_negative_stock, location)
        # Each serial-numbered unit moves with the entry (stock/serial.py).
        serial_numbers.apply_entry_serials(sle_doc, serials, batch_no, location)

    if not db._in_transaction:
        db.commit()

def make_cancel_sl_entries(sl_entries):
    """Post the reversal of a voucher's stock entries on cancel.

    Unlike before 1.4.2 (every cancel passed allow_negative_stock=True), a
    reversal may not take stock below zero: cancelling a receipt whose goods
    have since been delivered or issued is refused, the caller's transaction
    rolls back, and nothing is half-reversed. Reversals that put stock back
    (cancelling a delivery or an issue) are never affected.
    """
    # A voucher posted before its item became batch-tracked has no batch on
    # its lines; its reversal goes back to (or comes out of) stock without a
    # batch, never to an invented one.
    # The same holds for storage locations, and a reversal may put stock back
    # into a location that has been disabled since.
    sl_entries = [_dict({**sle, "is_reversal": True,
                         "allow_unassigned_batch": not sle.get("batch_no"),
                         "allow_unassigned_location": not sle.get("location"),
                         "allow_unassigned_serial": not sle.get("serial_no")})
                  for sle in sl_entries]
    try:
        make_sl_entries(sl_entries, allow_negative_stock=False)
    except NegativeStockError as exc:
        first = sl_entries[0] if sl_entries else {}
        voucher = f"{first.get('voucher_type') or 'this document'} {first.get('voucher_no') or ''}".strip()
        raise NegativeStockError(
            f"Cannot cancel {voucher}: its goods have already left the warehouse "
            f"(they were delivered, issued or moved since). Cancel or return those "
            f"documents first. {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Batches
#
# A batch is identified by (item, batch label): the same label can exist on
# several items. Tracking is switched on per item (Item.has_batch_no). Every
# stock ledger entry of a tracked item names its batch; quantities are kept
# per item x warehouse x batch in "Stock Balance Detail", whose rows always add
# up to the Bin. Valuation stays per item x warehouse (moving average on Bin).
# ---------------------------------------------------------------------------

def get_batch(item_code, batch_no):
    """The Batch master row for (item, label), or None."""
    return get_db().get_value(
        "Batch", {"item_code": item_code, "batch_no": batch_no},
        ["name", "batch_no", "item_code", "expiry_date", "disabled"])


AUDITED_BATCH_FIELDS = ("expiry_date", "blocked", "blocked_reason", "disabled")


def log_batch_changes(batch_name, before, after, source):
    """Keep who changed a batch's expiry date or block, when, and from what.
    `before` / `after` are the batch's values; only audited fields that
    differ are recorded."""
    import uuid

    db = get_db()
    if not db._get_table_columns("Batch Change"):
        return
    from lambda_erp.permissions import current_principal
    principal = current_principal() or {}
    who = principal.get("user_id") or principal.get("name")

    def text(field, value):
        if value in (None, ""):
            return None
        if field in ("blocked", "disabled"):
            return "1" if flt(value) else "0"
        return str(value)[:10] if field == "expiry_date" else str(value)

    for field in AUDITED_BATCH_FIELDS:
        if field not in after:
            continue
        old, new = text(field, (before or {}).get(field)), text(field, after.get(field))
        if field in ("blocked", "disabled"):
            old, new = old or "0", new or "0"
        if old == new:
            continue
        db.insert("Batch Change", _dict(
            name=uuid.uuid4().hex, batch=batch_name, item_code=(before or {}).get("item_code") or after.get("item_code"),
            batch_no=(before or {}).get("batch_no") or after.get("batch_no"), field=field,
            old_value=old, new_value=new, changed_by=who, changed_at=now(), source=source))


def _date_text(value):
    return str(value)[:10] if value not in (None, "") else None


def resolve_entry_batch(sle):
    """Check one stock ledger entry's batch against its item and return the
    batch label ('' when the entry carries none).

    - an item that is not batch-tracked takes no batch;
    - a tracked item needs one on every movement (the explicit exceptions are
      the reversal of a voucher from before tracking and Batch Assignment);
    - incoming stock creates the batch if it is new (with the expiry date from
      the receiving line when the item has expiry dates);
    - outgoing stock needs a batch that exists.
    """
    db = get_db()
    item_code = sle["item_code"]
    batch_no = str(sle.get("batch_no") or "").strip()
    item = db.get_value("Item", item_code, ["has_batch_no", "has_expiry_date"])
    tracked = bool(item and flt(item.get("has_batch_no")))
    if not tracked:
        if batch_no:
            raise ValidationError(
                f"Item {item_code} is not batch-tracked: remove batch {batch_no} from the line, "
                f"or switch the item to batch tracking first")
        return ""
    if not batch_no:
        if sle.get("allow_unassigned_batch"):
            return ""
        raise ValidationError(
            f"Batch is required for item {item_code} (batch-tracked) in warehouse {sle['warehouse']}")

    batch = get_batch(item_code, batch_no)
    expiry = _date_text(sle.get("expiry_date"))
    if flt(sle["actual_qty"]) < 0:
        if not batch:
            raise ValidationError(f"Unknown batch {batch_no} for item {item_code}")
        return batch_no
    if batch:
        known = _date_text(batch.get("expiry_date"))
        if expiry and known and expiry != known:
            raise ValidationError(
                f"Batch {batch_no} of item {item_code} already has expiry date {known}, not {expiry}. "
                f"Correct the line or the batch")
        if expiry and not known:
            db.set_value("Batch", batch["name"], {"expiry_date": expiry, "modified": now()})
            log_batch_changes(batch["name"], batch, {"expiry_date": expiry},
                              f"{sle.get('voucher_type') or 'Stock'} {sle.get('voucher_no') or ''}".strip())
        return batch_no
    if flt(item.get("has_expiry_date")) and not expiry:
        raise ValidationError(
            f"Batch {batch_no} of item {item_code} is new: its expiry date is required on the receiving line")
    while True:
        name = f"BATCH-{db.next_in_series('BATCH'):05d}"
        if not db.exists("Batch", name):
            break
    db.insert("Batch", _dict(name=name, batch_no=batch_no, item_code=item_code, expiry_date=expiry,
                             disabled=0, creation=now(), modified=now()))
    return batch_no


def get_storage_location(warehouse, location):
    """The Storage Location master row for (warehouse, label), or None."""
    return get_db().get_value(
        "Storage Location", {"warehouse": warehouse, "location": location},
        ["name", "location", "warehouse", "disabled"])


def resolve_entry_location(sle):
    """Check one stock ledger entry's storage location against its warehouse
    and return the label ('' when the entry carries none).

    - a warehouse that does not use locations takes none;
    - a warehouse that uses them needs one on every movement (the explicit
      exceptions are the reversal of a voucher from before, and the source
      side of a move within the warehouse: that is how stock without a
      location is put away);
    - the location must exist in that warehouse, and a disabled location
      takes no new stock.
    """
    db = get_db()
    warehouse = sle["warehouse"]
    location = str(sle.get("location") or "").strip()
    uses = flt(db.get_value("Warehouse", warehouse, "use_locations"))
    if not uses:
        if location:
            raise ValidationError(
                f"Warehouse {warehouse} does not use storage locations: remove location {location} "
                f"from the line, or switch the warehouse to storage locations first")
        return ""
    if not location:
        if sle.get("allow_unassigned_location"):
            return ""
        raise ValidationError(
            f"Storage location is required in warehouse {warehouse} (item {sle['item_code']})")
    row = get_storage_location(warehouse, location)
    if not row:
        raise ValidationError(f"Unknown storage location {location} in warehouse {warehouse}")
    if flt(sle["actual_qty"]) > 0 and row.get("disabled") and not sle.get("is_reversal"):
        raise ValidationError(f"Storage location {location} in warehouse {warehouse} is disabled: no stock can be put there")
    return location


def update_stock_balance_detail(sle, batch_no, allow_negative_stock=False, location=""):
    """Apply an entry to the quantity of its item x warehouse x batch x location.

    This is where a batch or a location cannot be over-drawn: 2 of batch A and
    8 of batch B is 10 in the Bin, but an issue of 3 from A is refused. For
    stock without a batch and location the row mirrors the Bin."""
    import uuid

    db = get_db()
    key = [sle["item_code"], sle["warehouse"], batch_no, location]
    db.sql('INSERT INTO "Stock Balance Detail" (name, item_code, warehouse, batch_no, location, actual_qty) '
           'VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT DO NOTHING', [uuid.uuid4().hex, *key])
    lock = ' FOR UPDATE' if db.dialect == 'postgres' else ''
    row = db.sql('SELECT name, actual_qty FROM "Stock Balance Detail" '
                 'WHERE item_code = ? AND warehouse = ? AND batch_no = ? AND location = ?' + lock, key)[0]
    available = flt(row["actual_qty"])
    new_qty = available + flt(sle["actual_qty"])
    if new_qty < -1e-9 and not allow_negative_stock:
        what = f"batch {batch_no} of item" if batch_no else "stock without a batch of item"
        where = f"warehouse {sle['warehouse']}"
        if location:
            where = f"storage location {location} of {where}"
        elif flt(db.get_value("Warehouse", sle["warehouse"], "use_locations")):
            where = f"{where} without a storage location"
        raise NegativeStockError(
            f"Not enough of {what} {sle['item_code']} in {where}. "
            f"Available: {available}, requested: {abs(flt(sle['actual_qty']))}")
    db.set_value("Stock Balance Detail", row["name"], "actual_qty", flt(new_qty))


def get_stock_details(item_code=None, warehouse=None, batch_no=None, location=None):
    """Quantities per item x warehouse x batch x location ('' = without)."""
    filters = {key: value for key, value in
               (("item_code", item_code), ("warehouse", warehouse), ("batch_no", batch_no), ("location", location))
               if value is not None}
    return get_db().get_all("Stock Balance Detail", filters=filters or None,
                            fields=["item_code", "warehouse", "batch_no", "location", "actual_qty"])


def get_batch_balances(item_code=None, warehouse=None, batch_no=None):
    """Quantities per item x warehouse x batch (batch_no '' = without batch),
    added up over the storage locations of the warehouse."""
    totals = {}
    for row in get_stock_details(item_code, warehouse, batch_no):
        key = (row["item_code"], row["warehouse"], row["batch_no"])
        totals[key] = totals.get(key, 0) + flt(row["actual_qty"])
    return [_dict(item_code=key[0], warehouse=key[1], batch_no=key[2], actual_qty=qty)
            for key, qty in sorted(totals.items())]


def stock_ledger_agreement():
    """Rows where the three views of a quantity disagree; empty when healthy.

    For every item x warehouse: sum of ledger entries = Bin = sum of the
    per-batch balances; and for every item x warehouse x batch: sum of ledger
    entries = its balance."""
    db = get_db()
    problems = []
    ledger, detail = {}, {}
    for row in db.sql('SELECT item_code, warehouse, COALESCE(batch_no, \'\') AS batch_no, '
                      'COALESCE(location, \'\') AS location, SUM(actual_qty) AS qty '
                      'FROM "Stock Ledger Entry" '
                      'GROUP BY item_code, warehouse, COALESCE(batch_no, \'\'), COALESCE(location, \'\')'):
        ledger[(row["item_code"], row["warehouse"], row["batch_no"], row["location"])] = flt(row["qty"])
    for row in db.sql('SELECT item_code, warehouse, batch_no, location, actual_qty FROM "Stock Balance Detail"'):
        detail[(row["item_code"], row["warehouse"], row["batch_no"], row["location"])] = flt(row["actual_qty"])
    for key in sorted(set(ledger) | set(detail)):
        if abs(ledger.get(key, 0) - detail.get(key, 0)) > 1e-6:
            problems.append({"item_code": key[0], "warehouse": key[1], "batch_no": key[2], "location": key[3],
                             "ledger_qty": ledger.get(key, 0), "detail_qty": detail.get(key, 0)})
    totals = {}
    for (item, warehouse, _, _), qty in detail.items():
        totals[(item, warehouse)] = totals.get((item, warehouse), 0) + qty
    bins = {(row["item_code"], row["warehouse"]): flt(row["actual_qty"])
            for row in db.sql('SELECT item_code, warehouse, actual_qty FROM "Bin"')}
    for key in sorted(set(totals) | set(bins)):
        if abs(totals.get(key, 0) - bins.get(key, 0)) > 1e-6:
            problems.append({"item_code": key[0], "warehouse": key[1], "batch_no": None,
                             "bin_qty": bins.get(key, 0), "detail_qty": totals.get(key, 0)})
    return problems


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
        incoming_rate = flt(sle.get("incoming_rate")) if sle.get("incoming_rate_is_explicit") else (flt(sle.get("incoming_rate")) or prev_val_rate)
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
        outgoing_rate = flt(sle.get("outgoing_rate")) if sle.get("outgoing_rate_is_explicit") else (flt(sle.get("outgoing_rate")) or prev_val_rate)
        outgoing_value = abs(flt(sle["actual_qty"])) * outgoing_rate
        new_stock_value = prev_stock_value - outgoing_value
        new_val_rate = new_stock_value / new_qty if sle.get('outgoing_rate_is_explicit') and new_qty > 0 else prev_val_rate

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
            batch_no=item.get("batch_no"),
            location=item.get("location"),
            serial_no=item.get("serial_no"),
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
    # Inventory is valued in the company's base currency; line rates are in
    # document currency, so scale by the doc's conversion_rate (1.0 = no-op).
    conversion_rate = flt(doc.get("conversion_rate")) or 1.0
    sl_entries = []
    for item in items:
        warehouse = item.get("warehouse")
        if not warehouse or not item.get("item_code"):
            continue
        actual_qty = flt(item["qty"])
        rate = flt(item.get("net_rate") if item.get("net_rate") is not None else item.get("rate")) * conversion_rate
        sl_entries.append(_dict(
            item_code=item["item_code"],
            warehouse=warehouse,
            actual_qty=actual_qty,
            incoming_rate=rate if actual_qty > 0 else 0,
            incoming_rate_is_explicit=actual_qty > 0,
            outgoing_rate=0,
            voucher_type=doc.DOCTYPE,
            voucher_no=doc.name,
            voucher_detail_no=item.get("name"),
            batch_no=item.get("batch_no"),
            expiry_date=item.get("expiry_date"),
            location=item.get("location"),
            serial_no=item.get("serial_no"),
            posting_date=doc.posting_date,
            company=doc.company,
        ))
    return sl_entries

def stock_account_for(company, warehouse=None):
    """The Stock account a warehouse's stock value is booked on.

    The warehouse's own `account` if set, else the company's
    `stock_in_hand_account`, else the company's first leaf account of type
    Stock. Every stock-moving document resolves it here: before 1.4.2 Stock
    Entry used the warehouse account (falling back to *any* Stock account)
    while receipts, invoices and deliveries always used the company's
    stock_in_hand_account, so the GL drifted from the warehouses' stock value
    as soon as a warehouse had its own account (or a chart had several Stock
    accounts).
    """
    from lambda_erp.exceptions import ValidationError

    db = get_db()
    account = db.get_value("Warehouse", warehouse, "account") if warehouse else None
    if account:
        info = db.get_value("Account", account, ["company", "is_group", "account_type"])
        if not info or info.company != company or info.is_group or info.account_type != "Stock":
            raise ValidationError(
                f"Warehouse {warehouse}: its account {account} must be a Stock account belonging to {company}")
        return account
    return db.get_value("Company", company, "stock_in_hand_account") or db.get_value(
        "Account", {"company": company, "account_type": "Stock", "is_group": 0}, "name")


def build_cost_basis_gl(doc, *, remarks=None):
    """Dr COGS / Cr Stock In Hand at cost for a sell-side doc.

    Reads stock_value_difference from the SLEs already posted for this
    voucher, so call this AFTER make_sl_entries. Sign handles returns:
    stock_value_difference is negative for outgoing (Dr COGS/Cr SIH) and
    positive for returns (Cr COGS/Dr SIH). The stock side is booked per
    warehouse on its stock account (stock_account_for).
    """
    db = get_db()

    default_expense = db.get_value("Company", doc.company, "default_expense_account")
    if not default_expense or not stock_account_for(doc.company):
        return []

    rows = db.sql(
        'SELECT warehouse, COALESCE(SUM(stock_value_difference), 0) as diff '
        'FROM "Stock Ledger Entry" '
        'WHERE voucher_type = ? AND voucher_no = ? AND is_cancelled = 0 GROUP BY warehouse',
        [doc.DOCTYPE, doc.name],
    )
    by_account = {}
    for row in rows:
        diff = flt(row["diff"])
        if diff:
            account = stock_account_for(doc.company, row["warehouse"])
            by_account[account] = by_account.get(account, 0) + diff
    cost_diff = sum(by_account.values())
    if not cost_diff:
        return []

    cost_center = db.get_value("Company", doc.company, "default_cost_center")
    remark = remarks or f"{doc.DOCTYPE} {doc.name}"
    common = dict(voucher_type=doc.DOCTYPE, voucher_no=doc.name, posting_date=doc.posting_date,
                  company=doc.company, remarks=remark)

    gl = [_dict(account=default_expense, debit=flt(-cost_diff, 2), debit_in_account_currency=flt(-cost_diff, 2),
                credit=0, credit_in_account_currency=0, cost_center=cost_center, **common)]
    for account, diff in by_account.items():
        gl.append(_dict(account=account, debit=0, debit_in_account_currency=0,
                        credit=flt(-diff, 2), credit_in_account_currency=flt(-diff, 2), **common))
    return gl

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
        flipped['incoming_rate_is_explicit'] = sle.get('outgoing_rate_is_explicit', False)
        flipped['outgoing_rate_is_explicit'] = sle.get('incoming_rate_is_explicit', False)
        reversed_.append(flipped)
    return reversed_
