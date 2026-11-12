"""Sales reservations, availability and FEFO picking.

Four quantities per item and warehouse, each unit of stock in exactly one of
the last three:

    physical   what the stock ledger says is there
    blocked    physical stock that may not be sold: an expired batch or a
               batch that was blocked
    reserved   stock set aside by open Stock Reservations (for a sales order
               line, or free-standing with a reason)
    available  physical - blocked - reserved

A reservation is hard: no stock movement may take what is reserved for
someone else. What is still open on an order-linked reservation is derived
from the order line's delivered quantity, never counted separately.

Picking follows FEFO (first expired, first out). That is a physical picking
strategy; it has nothing to do with valuation, which stays moving average.

See docs/reservations-and-fefo.md.
"""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, now, nowdate

RESERVED, PARTLY, DELIVERED, RELEASED, CANCELLED = (
    "Reserved", "Partially Delivered", "Delivered", "Released", "Cancelled")
STATUSES = (RESERVED, PARTLY, DELIVERED, RELEASED, CANCELLED)
OPEN_STATUSES = (RESERVED, PARTLY)
EPSILON = 1e-9

# What is still set aside by a reservation row `r` (order line joined as `soi`).
_OPEN_QTY = (
    "CASE WHEN r.sales_order_item IS NULL OR r.sales_order_item = '' THEN r.reserved_qty "
    "WHEN r.reserved_qty > COALESCE(soi.delivered_qty, 0) THEN r.reserved_qty - COALESCE(soi.delivered_qty, 0) "
    "ELSE 0 END")
_OPEN_FROM = ('FROM "Stock Reservation" r LEFT JOIN "Sales Order Item" soi ON soi.name = r.sales_order_item '
              "WHERE r.status IN ('Reserved', 'Partially Delivered') AND COALESCE(r.discarded, 0) = 0 ")


def _today(on_date=None):
    return str(on_date or nowdate())[:10]


def _lock_item(item_code):
    """Serialise everything that decides on this item's availability."""
    db = get_db()
    if db.dialect == "postgres":
        db.sql('SELECT name FROM "Item" WHERE name = ? FOR UPDATE', [item_code])


# -- availability -------------------------------------------------------------

def batch_state(batch, on_date=None):
    """None when a batch may be sold, else why not: 'blocked' or 'expired'."""
    if not batch:
        return None
    if flt(batch.get("blocked")):
        return "blocked"
    expiry = batch.get("expiry_date")
    if expiry and str(expiry)[:10] < _today(on_date):
        return "expired"
    return None


def availability_rows(item_code=None, warehouse=None, item_prefix=None, on_date=None):
    """physical / blocked / reserved / available per item x warehouse."""
    db = get_db()
    where, params = ["1 = 1"], []
    for column, value in (("d.item_code", item_code), ("d.warehouse", warehouse)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if item_prefix:
        where.append("d.item_code LIKE ?")
        params.append(str(item_prefix).replace("%", "") + "%")
    today = _today(on_date)
    rows = db.sql(
        'SELECT d.item_code, d.warehouse, SUM(d.actual_qty) AS physical, '
        'SUM(CASE WHEN COALESCE(b.blocked, 0) = 1 OR (b.expiry_date IS NOT NULL AND b.expiry_date < ?) '
        'THEN d.actual_qty ELSE 0 END) AS blocked '
        'FROM "Stock Balance Detail" d '
        'LEFT JOIN "Batch" b ON b.item_code = d.item_code AND b.batch_no = d.batch_no '
        f'WHERE {" AND ".join(where)} GROUP BY d.item_code, d.warehouse ORDER BY d.item_code, d.warehouse',
        [today, *params])
    reserved = {}
    r_where, r_params = "", []
    for column, value in (("r.item_code", item_code), ("r.warehouse", warehouse)):
        if value:
            r_where += f" AND {column} = ?"
            r_params.append(value)
    for row in db.sql(f'SELECT r.item_code, r.warehouse, SUM({_OPEN_QTY}) AS qty {_OPEN_FROM}{r_where} '
                      'GROUP BY r.item_code, r.warehouse', r_params):
        reserved[(row["item_code"], row["warehouse"])] = flt(row["qty"])
    result = []
    for row in rows:
        physical, blocked = flt(row["physical"]), flt(row["blocked"])
        held = reserved.get((row["item_code"], row["warehouse"]), 0)
        result.append(_dict(item_code=row["item_code"], warehouse=row["warehouse"], physical_qty=physical,
                            blocked_qty=blocked, reserved_qty=held,
                            available_qty=max(0.0, physical - blocked - held)))
    return result


def stock_buckets(item_code, warehouse, on_date=None):
    rows = availability_rows(item_code, warehouse, on_date=on_date)
    if rows:
        return rows[0]
    return _dict(item_code=item_code, warehouse=warehouse, physical_qty=0.0, blocked_qty=0.0,
                 reserved_qty=0.0, available_qty=0.0)


def open_reserved(item_code, warehouse, *, exclude=None, exclude_order_item=None):
    """Quantity set aside by open reservations, optionally without one
    reservation (by name) or without the one of an order line."""
    sql = f"SELECT COALESCE(SUM({_OPEN_QTY}), 0) AS qty {_OPEN_FROM} AND r.item_code = ? AND r.warehouse = ?"
    params = [item_code, warehouse]
    if exclude:
        sql += " AND r.name <> ?"
        params.append(exclude)
    if exclude_order_item:
        sql += " AND (r.sales_order_item IS NULL OR r.sales_order_item <> ?)"
        params.append(exclude_order_item)
    return flt(get_db().sql(sql, params)[0]["qty"])


def reservations_for(item_code, warehouse):
    """Open reservations of an item in a warehouse, for messages."""
    return get_db().sql(
        f"SELECT r.name, r.sales_order, r.customer, {_OPEN_QTY} AS open_qty {_OPEN_FROM} "
        "AND r.item_code = ? AND r.warehouse = ? ORDER BY r.creation, r.name", [item_code, warehouse])


# -- the document -------------------------------------------------------------

class StockReservation(Document):
    DOCTYPE = "Stock Reservation"
    CHILD_TABLES = {}
    PREFIX = "SRES"
    REQUIRED_FIELDS = ("item_code", "warehouse", "reserved_qty")
    SERVER_MANAGED_FIELDS = ("reserved_by",)
    CONDITIONAL_REQUIREMENTS = (
        "Sets reserved_qty of a stock item aside in one warehouse; nobody else can deliver, issue or transfer it away. "
        "Refused when more than the available quantity (physical - blocked - already reserved) is asked for.",
        "For a sales order line give sales_order and sales_order_item (one reservation per line; reserved_qty is the "
        "total reserved for the line, at most its ordered qty). What is still open follows the line's delivered "
        "quantity automatically. Use the reserve_sales_order action to reserve a whole order.",
        "Without a sales order, remarks must say what the stock is set aside for.",
        "Submit/cancel are unsupported. To give stock back, update status to Released. "
        "Partially Delivered, Delivered and Cancelled are set by the system.",
    )
    LINK_FIELDS = {
        "item_code": "Item",
        "warehouse": "Warehouse",
        "sales_order": "Sales Order",
        "customer": "Customer",
        "company": "Company",
    }

    def validate(self):
        db = get_db()
        stored = db.get_value(self.DOCTYPE, self.name, ["status", "reserved_qty", "item_code", "warehouse",
                                                        "sales_order_item"]) if self._persisted else None
        try:
            qty = float(self._data.get("reserved_qty"))
        except (TypeError, ValueError):
            raise ValidationError("Stock Reservation: Reserved Qty is required and must be a number")
        if not qty > 0 or qty != qty or qty in (float("inf"),):
            raise ValidationError("Stock Reservation: Reserved Qty must be positive")
        self._data["reserved_qty"] = qty

        item = db.get_value("Item", self.item_code, ["is_stock_item"])
        if not item or not flt(item.get("is_stock_item")):
            raise ValidationError(f"Stock Reservation: {self.item_code} is not a stock item")
        warehouse = db.get_value("Warehouse", self.warehouse, ["company", "is_group"])
        if not warehouse or flt(warehouse.get("is_group")):
            raise ValidationError("Stock Reservation: Warehouse must be a warehouse that holds stock")
        if not self._data.get("company"):
            self._data["company"] = warehouse.get("company")

        requested = self._data.get("status") or RESERVED
        previous = (stored or {}).get("status")
        if requested not in STATUSES:
            raise ValidationError(f"Stock Reservation: status must be one of {', '.join(STATUSES)}")
        if previous == CANCELLED and requested != CANCELLED:
            raise ValidationError("A cancelled reservation cannot be reopened; reserve again")
        if requested == CANCELLED and previous != CANCELLED:
            raise ValidationError("Cancelled is set when the sales order is cancelled; use Released to give stock back")
        released = requested in (RELEASED, CANCELLED)

        delivered = 0.0
        if self._data.get("sales_order_item") or self._data.get("sales_order"):
            delivered = self._validate_order_line(db)
        elif not str(self._data.get("remarks") or "").strip():
            raise ValidationError(
                "Stock Reservation: without a sales order, remarks must say what the stock is set aside for")

        if not released:
            self._data["status"] = (DELIVERED if self._data.get("sales_order_item") and delivered >= qty - EPSILON
                                    else PARTLY if delivered > EPSILON else RESERVED)
        else:
            self._data["status"] = requested

        if self._data["status"] in OPEN_STATUSES:
            mine = max(0.0, qty - delivered) if self._data.get("sales_order_item") else qty
            before = 0.0
            if stored and stored.get("status") in OPEN_STATUSES and stored.get("item_code") == self.item_code \
                    and stored.get("warehouse") == self.warehouse:
                before = max(0.0, flt(stored.get("reserved_qty")) - delivered) \
                    if stored.get("sales_order_item") else flt(stored.get("reserved_qty"))
            if mine > before + EPSILON:
                # Only what is new has to be available: a reservation that
                # already holds its stock can always be saved again.
                _lock_item(self.item_code)
                buckets = stock_buckets(self.item_code, self.warehouse)
                free = buckets.physical_qty - buckets.blocked_qty - open_reserved(
                    self.item_code, self.warehouse, exclude=self.name if self._persisted else None)
                if mine > free + EPSILON:
                    raise ValidationError(
                        f"Cannot reserve {mine:g} of {self.item_code} in warehouse {self.warehouse}: "
                        f"only {max(0.0, free):g} available (in stock {buckets.physical_qty:g}, "
                        f"blocked {buckets.blocked_qty:g}, reserved for others "
                        f"{buckets.physical_qty - buckets.blocked_qty - free:g})")

        if not self._data.get("reserved_by"):
            from lambda_erp.permissions import current_principal
            principal = current_principal() or {}
            self._data["reserved_by"] = principal.get("user_id") or principal.get("name")

    def _validate_order_line(self, db):
        line_name = self._data.get("sales_order_item")
        if not line_name or not self._data.get("sales_order"):
            raise ValidationError("Stock Reservation: sales_order and sales_order_item go together")
        line = db.get_value("Sales Order Item", line_name, ["parent", "item_code", "qty", "delivered_qty"])
        order = db.get_value("Sales Order", self.sales_order, ["docstatus", "customer", "company", "discarded"])
        if not line or line.get("parent") != self.sales_order or not order:
            raise ValidationError("Stock Reservation: the order line must belong to the sales order")
        if line.get("item_code") != self.item_code:
            raise ValidationError("Stock Reservation: Item must match the order line")
        if order.get("docstatus") != 1 and self._data.get("status") not in (RELEASED, CANCELLED):
            raise ValidationError("Stock can only be reserved for a submitted sales order")
        if flt(self.reserved_qty) > flt(line.get("qty")) + EPSILON:
            raise ValidationError(
                f"Stock Reservation: Reserved Qty ({flt(self.reserved_qty):g}) exceeds the ordered qty ({flt(line.get('qty')):g})")
        twin = db.sql('SELECT name FROM "Stock Reservation" WHERE sales_order_item = ? AND name <> ? '
                      "AND status <> 'Cancelled' AND COALESCE(discarded, 0) = 0 LIMIT 1", [line_name, self.name or ""])
        if twin:
            raise ValidationError(
                f"Order line already has reservation {twin[0]['name']}; change its Reserved Qty instead")
        self._data["customer"] = order.get("customer")
        return flt(line.get("delivered_qty"))


# -- reserving for sales orders -----------------------------------------------

def reserve_sales_order(sales_order, lines=None):
    """Reserve what is available for the undelivered part of a submitted
    sales order (all stock lines, or the order lines named in `lines`).

    A line that cannot be covered completely is reserved as far as stock
    goes; the rest stays open and is reported as `short_qty`."""
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return reserve_sales_order(sales_order, lines)
    order = db.get_value("Sales Order", sales_order, ["docstatus", "customer", "company"])
    if not order or order.get("docstatus") != 1:
        raise ValidationError("Stock can only be reserved for a submitted sales order")
    wanted = set(lines or [])
    result = []
    rows = db.get_all("Sales Order Item", filters={"parent": sales_order}, fields=["*"], order_by="idx")
    for line in sorted(rows, key=lambda row: (row.get("item_code") or "", row.get("idx") or 0)):
        if wanted and line["name"] not in wanted:
            continue
        if not line.get("item_code") or not line.get("warehouse"):
            continue
        if not flt(db.get_value("Item", line["item_code"], "is_stock_item")):
            continue
        _lock_item(line["item_code"])
        delivered = flt(line.get("delivered_qty"))
        undelivered = max(0.0, flt(line.get("qty")) - delivered)
        existing = db.sql('SELECT name, reserved_qty, status FROM "Stock Reservation" WHERE sales_order_item = ? '
                          "AND status <> 'Cancelled' AND COALESCE(discarded, 0) = 0", [line["name"]])
        current = existing[0] if existing else None
        held = (max(0.0, flt(current["reserved_qty"]) - delivered)
                if current and current["status"] in OPEN_STATUSES else 0.0)
        need = undelivered - held
        take = 0.0
        if need > EPSILON:
            buckets = stock_buckets(line["item_code"], line["warehouse"])
            take = min(need, buckets.available_qty)
            if take > EPSILON:
                if current:
                    doc = StockReservation.load(current["name"])
                    doc.reserved_qty = delivered + held + take
                    doc.status = RESERVED
                    doc.warehouse = line["warehouse"]
                else:
                    doc = StockReservation(item_code=line["item_code"], warehouse=line["warehouse"],
                                           reserved_qty=delivered + take, sales_order=sales_order,
                                           sales_order_item=line["name"], company=order.get("company"))
                doc.save()
                current = {"name": doc.name}
        result.append({"sales_order_item": line["name"], "item_code": line["item_code"],
                       "warehouse": line["warehouse"], "ordered_qty": flt(line.get("qty")),
                       "delivered_qty": delivered, "reserved_qty": held + max(0.0, take),
                       "newly_reserved_qty": max(0.0, take),
                       "short_qty": max(0.0, need - max(0.0, take)) if need > EPSILON else 0.0,
                       "reservation": current["name"] if current else None})
    return {"sales_order": sales_order, "lines": result,
            "fully_reserved": all(row["short_qty"] <= EPSILON for row in result)}


def sync_sales_order(sales_order):
    """Keep an order's reservations in step with it. Called whenever order
    progress is recalculated (submit, delivery, return, cancel)."""
    db = get_db()
    if not db._get_table_columns("Stock Reservation"):
        return
    order = db.get_value("Sales Order", sales_order, ["docstatus", "reserve_stock"])
    if not order:
        return
    rows = db.sql('SELECT r.name, r.status, r.reserved_qty, COALESCE(soi.delivered_qty, 0) AS delivered '
                  'FROM "Stock Reservation" r LEFT JOIN "Sales Order Item" soi ON soi.name = r.sales_order_item '
                  'WHERE r.sales_order = ?', [sales_order])
    if order.get("docstatus") == 2:
        for row in rows:
            if row["status"] in OPEN_STATUSES:
                db.set_value("Stock Reservation", row["name"], {"status": CANCELLED, "modified": now()})
        return
    if order.get("docstatus") != 1:
        return
    if flt(order.get("reserve_stock")) and not rows:
        reserve_sales_order(sales_order)
        return
    for row in rows:
        if row["status"] not in (*OPEN_STATUSES, DELIVERED):
            continue
        delivered, qty = flt(row["delivered"]), flt(row["reserved_qty"])
        status = DELIVERED if delivered >= qty - EPSILON else PARTLY if delivered > EPSILON else RESERVED
        if status != row["status"]:
            db.set_value("Stock Reservation", row["name"], {"status": status, "modified": now()})


# -- nobody takes what is reserved --------------------------------------------

def _touched_pairs(doc):
    pairs = set()
    for row in doc.get("items") or []:
        item = row.get("item_code")
        if not item:
            continue
        for field in ("warehouse", "s_warehouse", "t_warehouse"):
            if row.get(field):
                pairs.add((item, row[field]))
    return pairs


def _coverage(item_code, warehouse):
    buckets = stock_buckets(item_code, warehouse)
    return buckets.physical_qty - buckets.blocked_qty - buckets.reserved_qty


def begin_guard(doc):
    """Before a document posts: how well are reservations covered for the
    stock it touches? None when no reservation is involved (the usual case)."""
    db = get_db()
    if not db._get_table_columns("Stock Reservation"):
        return None
    pairs = _touched_pairs(doc)
    if not pairs:
        return None
    items = sorted({item for item, _ in pairs})
    held = db.sql(
        'SELECT DISTINCT item_code, warehouse FROM "Stock Reservation" '
        "WHERE status IN ('Reserved', 'Partially Delivered') AND COALESCE(discarded, 0) = 0 "
        f'AND item_code IN ({", ".join("?" for _ in items)})', items)
    relevant = pairs & {(row["item_code"], row["warehouse"]) for row in held}
    if not relevant:
        return None
    return {pair: _coverage(*pair) for pair in sorted(relevant)}


def finish_guard(doc, guard, *, cancelling=False):
    """After it posted (and order progress was recalculated): it must not
    have taken stock that is reserved for someone else. Raising here rolls
    the whole document back."""
    if not guard:
        return
    for (item_code, warehouse), before in guard.items():
        after = _coverage(item_code, warehouse)
        if after < -EPSILON and after < before - EPSILON:
            holders = ", ".join(
                f"{row['sales_order'] or row['name']} ({flt(row['open_qty']):g})"
                for row in reservations_for(item_code, warehouse) if flt(row["open_qty"]) > EPSILON)
            action = "cancel" if cancelling else "post"
            raise ValidationError(
                f"Cannot {action} {doc.DOCTYPE} {doc.name}: it would take {item_code} in warehouse {warehouse} "
                f"that is reserved ({-after:g} short). Reserved for: {holders}. "
                f"Deliver against those orders, or release a reservation first")


# -- shelf life ---------------------------------------------------------------

_AVAILABILITY_RULES = []


def register_availability_rule(rule):
    """Plugin seam: adjust the minimum remaining shelf life a sale needs.

    `rule(item_code=..., customer=..., days=...)` returns the number of days
    that must remain on a batch at delivery, or None to leave `days` as it
    is. Rules run in registration order, each seeing the previous result;
    the first `days` is Item.min_shelf_life_days. Example: a customer who
    accepts short-dated goods returns 0."""
    _AVAILABILITY_RULES.append(rule)


def required_shelf_life(item_code, customer=None):
    days = int(flt(get_db().get_value("Item", item_code, "min_shelf_life_days")))
    for rule in _AVAILABILITY_RULES:
        changed = rule(item_code=item_code, customer=customer, days=days)
        if changed is not None:
            days = max(0, int(changed))
    return days


def _days_left(expiry, on_date):
    if not expiry:
        return None
    return (getdate(str(expiry)[:10]) - getdate(on_date)).days


def validate_sellable(doc):
    """A sale may not ship a blocked or expired batch, and needs an explicit
    exception (the line's shelf_life_exception) for a batch with less than
    the required shelf life left. Returns and stock entries are not sales:
    expired goods can still be moved, returned or disposed of."""
    if flt(doc.get("is_return")):
        return
    from lambda_erp.stock.stock_ledger import get_batch

    db = get_db()
    on_date = max(_today(doc.get("posting_date")), _today())
    for idx, row in enumerate(doc.get("items") or [], 1):
        batch_no = str(row.get("batch_no") or "").strip()
        if not batch_no or not row.get("item_code"):
            continue
        batch = db.get_value("Batch", {"item_code": row["item_code"], "batch_no": batch_no},
                             ["name", "expiry_date", "blocked", "blocked_reason"])
        if not batch:
            continue  # the stock ledger reports an unknown batch
        state = batch_state(batch, on_date)
        prefix = f"{doc.DOCTYPE} row {idx}: batch {batch_no} of {row['item_code']}"
        if state == "blocked":
            reason = f" ({batch['blocked_reason']})" if batch.get("blocked_reason") else ""
            raise ValidationError(f"{prefix} is blocked{reason} and cannot be sold")
        if state == "expired":
            raise ValidationError(f"{prefix} expired on {str(batch['expiry_date'])[:10]} and cannot be sold")
        required = required_shelf_life(row["item_code"], doc.get("customer"))
        if required <= 0:
            continue
        left = _days_left(batch.get("expiry_date"), on_date)
        if left is not None and left >= required:
            continue
        if str(row.get("shelf_life_exception") or "").strip():
            from lambda_erp.permissions import current_principal
            principal = current_principal() or {}
            if row.get("name") and "shelf_life_exception_by" in db._get_table_columns(doc.CHILD_TABLES["items"][0]):
                db.set_value(doc.CHILD_TABLES["items"][0], row["name"], "shelf_life_exception_by",
                             principal.get("user_id") or principal.get("name"))
            continue
        remaining = "has no expiry date" if left is None else f"has {left} day(s) left"
        raise ValidationError(
            f"{prefix} {remaining}, less than the {required} day(s) required. Pick a batch with a later "
            f"expiry date, or record the approved exception in the line's shelf_life_exception")


# -- FEFO ---------------------------------------------------------------------

def suggest_picks(item_code, warehouse, qty, *, customer=None, on_date=None, sales_order_item=None,
                  include_short_dated=False):
    """Which batches and storage locations to take `qty` from, earliest expiry
    first. Read-only: nothing is reserved, moved or invoiced.

    Order: expiry date ascending, batches without a date last, then batch
    label, then storage location; the same inputs give the same picks.
    Blocked and expired batches are never proposed. Batches with too little
    shelf life left are left out unless `include_short_dated` (they then
    carry needs_exception). Stock reserved for other orders is not offered;
    the reservation of `sales_order_item` itself is.
    """
    db = get_db()
    qty = flt(qty)
    if not qty > 0:
        raise ValidationError("qty must be positive")
    reference = _today(on_date)
    required = required_shelf_life(item_code, customer)
    rows = db.sql(
        'SELECT d.batch_no, d.location, d.actual_qty, b.expiry_date, b.blocked, b.blocked_reason '
        'FROM "Stock Balance Detail" d '
        'LEFT JOIN "Batch" b ON b.item_code = d.item_code AND b.batch_no = d.batch_no '
        'WHERE d.item_code = ? AND d.warehouse = ? AND d.actual_qty > 0', [item_code, warehouse])
    tracked = flt(db.get_value("Item", item_code, "has_batch_no"))
    candidates, excluded = [], []
    for row in rows:
        entry = {"batch_no": row["batch_no"] or None, "location": row["location"] or None,
                 "expiry_date": str(row["expiry_date"])[:10] if row["expiry_date"] else None,
                 "available_qty": flt(row["actual_qty"])}
        state = batch_state(row, reference)
        left = _days_left(row["expiry_date"], reference)
        entry["days_left"] = left
        if state:
            excluded.append({**entry, "reason": state})
            continue
        if tracked and not row["batch_no"]:
            excluded.append({**entry, "reason": "no batch assigned"})
            continue
        short = required > 0 and (left is None or left < required)
        if short and not include_short_dated:
            excluded.append({**entry, "reason": f"less than {required} day(s) shelf life left"})
            continue
        candidates.append({**entry, "needs_exception": bool(short)})
    candidates.sort(key=lambda c: (c["expiry_date"] is None, c["expiry_date"] or "", c["batch_no"] or "",
                                   c["location"] or ""))
    buckets = stock_buckets(item_code, warehouse, on_date=reference)
    others = open_reserved(item_code, warehouse, exclude_order_item=sales_order_item) if sales_order_item \
        else buckets.reserved_qty
    limit = max(0.0, buckets.physical_qty - buckets.blocked_qty - others)
    remaining = min(qty, limit)
    picks = []
    for candidate in candidates:
        if remaining <= EPSILON:
            break
        take = min(remaining, candidate["available_qty"])
        pick = {"batch_no": candidate["batch_no"], "location": candidate["location"],
                "expiry_date": candidate["expiry_date"], "days_left": candidate["days_left"],
                "qty": take, "needs_exception": candidate["needs_exception"]}
        from lambda_erp.stock.serial import propose_serials
        units = propose_serials(item_code, warehouse, take, candidate["batch_no"], candidate["location"])
        if units:
            pick["serial_no"] = units
        picks.append(pick)
        remaining -= take
    picked = sum(pick["qty"] for pick in picks)
    return {"item_code": item_code, "warehouse": warehouse, "strategy": "FEFO", "requested_qty": qty,
            "picked_qty": picked, "shortfall_qty": max(0.0, qty - picked),
            "reserved_for_others_qty": others, "required_shelf_life_days": required,
            "picks": picks, "excluded": excluded}
