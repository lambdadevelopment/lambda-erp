"""Closing what is left open on an order line.

A customer cancels the rest of a line after a partial delivery ("we no
longer need it"), or a supplier will never send the rest. Closing records
that the open quantity will not be delivered (received) any more:

- the line keeps its ordered `qty`; `closed_qty` says how much of it was
  closed, with `close_reason`, `closed_by` and `closed_at`;
- open = qty − closed_qty − delivered_qty (received_qty on purchase lines);
- the order's progress, status, the backlog reports and stock planning
  count against qty − closed_qty, and no later delivery, receipt or invoice
  can take the closed quantity;
- deliveries, receipts and invoices posted before stay as they are.

Only what is neither delivered nor invoiced can be closed: an invoiced
quantity needs a credit note first. Reopening undoes a closing as long as
the order is still submitted.

See docs/order-progress-and-backlog.md.
"""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt, now

EPSILON = 1e-9

ORDER_TYPES = {
    "Sales Order": "delivered_qty",
    "Purchase Order": "received_qty",
}


def effective_qty(row):
    """What the line still stands for: ordered minus closed."""
    return max(0.0, flt(row.get("qty")) - flt(row.get("closed_qty")))


def closable_qty(row, progress):
    """What can still be closed: not delivered (received) and not invoiced."""
    done = max(flt(row.get(progress)), flt(row.get("billed_qty")))
    return max(0.0, effective_qty(row) - done)


def _load_order(doctype, order):
    if doctype not in ORDER_TYPES:
        raise ValidationError(f"Only sales and purchase order lines can be closed, not {doctype}")
    db = get_db()
    if db.dialect == "postgres":
        db.sql(f'SELECT name FROM "{doctype}" WHERE name = ? FOR UPDATE', [order])
    head = db.get_value(doctype, order, ["docstatus", "discarded"])
    if not head:
        raise ValidationError(f"{doctype} {order} not found")
    if head.get("docstatus") != 1 or head.get("discarded"):
        raise ValidationError(f"Only lines of a submitted {doctype.lower()} can be closed or reopened")
    return db.get_all(f"{doctype} Item", filters={"parent": order}, fields=["*"], order_by="idx")


def _pick(rows, lines, doctype, order):
    wanted = {str(value) for value in lines}
    chosen = [row for row in rows if row["name"] in wanted or str(row.get("idx")) in wanted]
    found = {row["name"] for row in chosen} | {str(row.get("idx")) for row in chosen}
    missing = sorted(wanted - found)
    if missing:
        raise ValidationError(f"{doctype} {order} has no line(s) {', '.join(missing)}")
    return chosen


def _refresh(doctype, order):
    from lambda_erp.workflow import refresh_order_progress
    if doctype == "Sales Order":
        from lambda_erp.selling.sales_order import SalesOrder as Order
    else:
        from lambda_erp.buying.purchase_order import PurchaseOrder as Order
    refresh_order_progress(Order.load(order))


def _release_closed_reservation(line):
    """A reservation holds no more than the line can still deliver."""
    db = get_db()
    if not db._get_table_columns("Stock Reservation"):
        return
    from lambda_erp.stock.reservation import OPEN_STATUSES, RELEASED, StockReservation
    rows = db.sql('SELECT name, status, reserved_qty FROM "Stock Reservation" WHERE sales_order_item = ? '
                  "AND COALESCE(discarded, 0) = 0", [line["name"]])
    keep = max(flt(line.get("delivered_qty")), effective_qty(line))
    for row in rows:
        if row["status"] not in OPEN_STATUSES or flt(row["reserved_qty"]) <= keep + EPSILON:
            continue
        doc = StockReservation.load(row["name"])
        if keep > EPSILON:
            doc.reserved_qty = keep
        else:
            doc.status = RELEASED
        doc.save()


def close_order_lines(doctype, order, *, reason, lines=None, qty=None, closed_by=None):
    """Close the open quantity of lines of a submitted order: all lines with
    something open, or those named in `lines` (row names or positions).
    `qty` closes only part of one line's open quantity."""
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return close_order_lines(doctype, order, reason=reason, lines=lines, qty=qty, closed_by=closed_by)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Closing an open quantity needs a reason (e.g. the customer cancelled the rest)")
    progress = ORDER_TYPES.get(doctype)
    rows = _load_order(doctype, order)
    if lines:
        chosen = _pick(rows, lines, doctype, order)
        for row in chosen:
            if closable_qty(row, progress) <= EPSILON:
                raise ValidationError(
                    f"{doctype} {order} line {row.get('idx')} ({row.get('item_code')}) has nothing open to close")
    else:
        chosen = [row for row in rows if closable_qty(row, progress) > EPSILON]
        if not chosen:
            raise ValidationError(f"{doctype} {order} has nothing open to close")
    if qty is not None:
        if len(chosen) != 1:
            raise ValidationError("qty applies to one line: name the line")
        if not flt(qty) > 0:
            raise ValidationError("qty must be positive")
        if flt(qty) > closable_qty(chosen[0], progress) + EPSILON:
            raise ValidationError(
                f"qty ({flt(qty):g}) exceeds what is open on the line ({closable_qty(chosen[0], progress):g}); "
                "delivered or invoiced quantities cannot be closed")
    stamp = now()
    result = []
    for row in chosen:
        closing = flt(qty) if qty is not None else closable_qty(row, progress)
        closed = flt(row.get("closed_qty")) + closing
        db.set_value(f"{doctype} Item", row["name"], {
            "closed_qty": closed, "close_reason": reason[:500], "closed_by": closed_by, "closed_at": stamp})
        row = {**row, "closed_qty": closed}
        if doctype == "Sales Order":
            _release_closed_reservation(row)
        result.append({"line": row["name"], "idx": row.get("idx"), "item_code": row.get("item_code"),
                       "ordered_qty": flt(row.get("qty")), progress: flt(row.get(progress)),
                       "billed_qty": flt(row.get("billed_qty")), "closed_now": closing, "closed_qty": closed,
                       "open_qty": max(0.0, effective_qty(row) - flt(row.get(progress)))})
    db.set_value(doctype, order, "modified", stamp)
    _refresh(doctype, order)
    head = db.get_value(doctype, order, ["status"])
    return {"doctype": doctype, "order": order, "reason": reason, "status": (head or {}).get("status"),
            "lines": result}


def reopen_order_lines(doctype, order, *, lines=None):
    """Undo closings: the closed quantity of the named lines (or of every
    closed line) is open again."""
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return reopen_order_lines(doctype, order, lines=lines)
    progress = ORDER_TYPES.get(doctype)
    rows = _load_order(doctype, order)
    chosen = _pick(rows, lines, doctype, order) if lines else rows
    chosen = [row for row in chosen if flt(row.get("closed_qty")) > EPSILON]
    if not chosen:
        raise ValidationError(f"{doctype} {order} has no closed quantity to reopen")
    stamp = now()
    for row in chosen:
        db.set_value(f"{doctype} Item", row["name"], {
            "closed_qty": 0, "close_reason": None, "closed_by": None, "closed_at": None})
    db.set_value(doctype, order, "modified", stamp)
    _refresh(doctype, order)
    head = db.get_value(doctype, order, ["status"])
    return {"doctype": doctype, "order": order, "status": (head or {}).get("status"),
            "lines": [{"line": row["name"], "idx": row.get("idx"), "item_code": row.get("item_code"),
                       "reopened_qty": flt(row.get("closed_qty")),
                       "open_qty": max(0.0, flt(row.get("qty")) - flt(row.get(progress)))} for row in chosen]}


def reset_draft_closings(doc):
    """A draft has nothing to close: drop closings a client sent or a copy
    carried over. Called from the orders' validate()."""
    if doc.docstatus != 0:
        return
    for row in doc.get("items") or []:
        for field in ("closed_qty", "close_reason", "closed_by", "closed_at"):
            if row.get(field):
                row[field] = 0 if field == "closed_qty" else None
