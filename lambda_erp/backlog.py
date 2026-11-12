"""Open order quantities: what is still to deliver, what can be delivered
now, and what is on its way from suppliers.

Everything here is read from the orders and the stock as they are; nothing
is stored, reserved, delivered or invoiced by asking.

Per sales order line:

    ordered     the line's qty
    delivered   delivered and not returned (delivery notes and direct-stock
                invoices, net of returns)
    returned    how much of what was delivered came back
    billed      invoiced, net of credit notes for returned goods
    reserved    still held by the line's stock reservation
    open        ordered - delivered

See docs/order-progress-and-backlog.md.
"""
from collections import defaultdict

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt, getdate, now, nowdate

EPSILON = 1e-9


def _open_purchase_lines(item_codes=None, supplier=None, item_code=None, warehouse=None):
    db = get_db()
    where = ["p.docstatus = 1", "COALESCE(p.discarded, 0) = 0", "i.qty > COALESCE(i.received_qty, 0)"]
    params = []
    if item_codes is not None:
        if not item_codes:
            return []
        where.append(f"i.item_code IN ({', '.join('?' for _ in item_codes)})")
        params += list(item_codes)
    for column, value in (("p.supplier", supplier), ("i.item_code", item_code), ("i.warehouse", warehouse)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    return db.sql(
        'SELECT p.name AS purchase_order, p.supplier, p.supplier_name, p.transaction_date, p.schedule_date, '
        'i.name AS purchase_order_item, i.idx, i.item_code, i.item_name, i.warehouse, i.qty, '
        'COALESCE(i.received_qty, 0) AS received_qty, i.confirmed_date, i.confirmed_qty, '
        'i.confirmation_reference '
        'FROM "Purchase Order Item" i JOIN "Purchase Order" p ON p.name = i.parent '
        f'WHERE {" AND ".join(where)} ORDER BY p.transaction_date, p.name, i.idx', params)


def _expected_date(row):
    return row.get("confirmed_date") or row.get("schedule_date") or None


def sales_backlog(customer=None, item_code=None, warehouse=None, sales_order=None, item_prefix=None,
                  only_deliverable=False, with_purchase_orders=True):
    """Open sales order lines with what could be delivered now.

    Stock is shared out in the order the lines are due (delivery date, then
    order date, order and line): a line first gets what its own reservation
    holds, then free stock (in stock, not blocked, not reserved for anyone)
    as far as it lasts. So `deliverable_qty` over all lines never exceeds
    what is there.
    """
    from lambda_erp.stock.reservation import availability_rows

    db = get_db()
    where = ["o.docstatus = 1", "COALESCE(o.discarded, 0) = 0", "i.qty > COALESCE(i.delivered_qty, 0)"]
    params = []
    for column, value in (("o.customer", customer), ("i.item_code", item_code), ("i.warehouse", warehouse),
                          ("o.name", sales_order)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if item_prefix:
        where.append("i.item_code LIKE ?")
        params.append(str(item_prefix).replace("%", "") + "%")
    held = "CASE WHEN r.reserved_qty > COALESCE(i.delivered_qty, 0) THEN r.reserved_qty - COALESCE(i.delivered_qty, 0) ELSE 0 END"
    lines = db.sql(
        'SELECT o.name AS sales_order, o.customer, o.customer_name, o.transaction_date, o.delivery_date, '
        'i.name AS sales_order_item, i.idx, i.item_code, i.item_name, i.warehouse, i.qty AS ordered_qty, '
        'COALESCE(i.delivered_qty, 0) AS delivered_qty, COALESCE(i.returned_qty, 0) AS returned_qty, '
        'COALESCE(i.billed_qty, 0) AS billed_qty, '
        f'COALESCE((SELECT SUM({held}) FROM "Stock Reservation" r WHERE r.sales_order_item = i.name '
        "AND r.status IN ('Reserved', 'Partially Delivered') AND COALESCE(r.discarded, 0) = 0), 0) AS reserved_qty "
        'FROM "Sales Order Item" i JOIN "Sales Order" o ON o.name = i.parent '
        f'WHERE {" AND ".join(where)} '
        'ORDER BY COALESCE(o.delivery_date, o.transaction_date), o.transaction_date, o.name, i.idx', params)

    free = {}
    incoming = defaultdict(list)
    if with_purchase_orders:
        for row in _open_purchase_lines(sorted({line["item_code"] for line in lines if line.get("item_code")})):
            incoming[(row["item_code"], row["warehouse"])].append(row)

    result = []
    for line in lines:
        key = (line["item_code"], line["warehouse"])
        if key not in free:
            rows = availability_rows(line["item_code"], line["warehouse"]) if line["warehouse"] else []
            free[key] = rows[0].available_qty if rows else 0.0
        open_qty = flt(line["ordered_qty"]) - flt(line["delivered_qty"])
        reserved = min(flt(line["reserved_qty"]), open_qty)
        from_free = min(open_qty - reserved, free[key])
        free[key] -= from_free
        deliverable = reserved + from_free
        if only_deliverable and deliverable <= EPSILON:
            continue
        entry = {**dict(line), "ordered_qty": flt(line["ordered_qty"]), "delivered_qty": flt(line["delivered_qty"]),
                 "returned_qty": flt(line["returned_qty"]), "billed_qty": flt(line["billed_qty"]),
                 "open_qty": open_qty, "reserved_qty": reserved, "deliverable_qty": deliverable,
                 "short_qty": max(0.0, open_qty - deliverable)}
        if with_purchase_orders:
            orders = incoming.get(key, [])
            dated = sorted((str(_expected_date(row))[:10] for row in orders if _expected_date(row)))
            entry["on_purchase_order_qty"] = sum(flt(row["qty"]) - flt(row["received_qty"]) for row in orders)
            entry["next_expected_date"] = dated[0] if dated else None
            entry["purchase_orders"] = [
                {"purchase_order": row["purchase_order"], "open_qty": flt(row["qty"]) - flt(row["received_qty"]),
                 "confirmed_date": row.get("confirmed_date") or None,
                 "schedule_date": row.get("schedule_date") or None,
                 "confirmation_reference": row.get("confirmation_reference") or None}
                for row in orders]
        result.append(entry)
    return result


def purchase_backlog(supplier=None, item_code=None, warehouse=None, overdue_only=False, on_date=None):
    """Open purchase order lines with the supplier's confirmed date. A line
    is overdue when its expected date (confirmed, else the order's schedule
    date) has passed and it is not fully received."""
    today = str(on_date or nowdate())[:10]
    result = []
    for row in _open_purchase_lines(supplier=supplier, item_code=item_code, warehouse=warehouse):
        expected = _expected_date(row)
        overdue = bool(expected and str(expected)[:10] < today)
        if overdue_only and not overdue:
            continue
        result.append({**dict(row), "qty": flt(row["qty"]), "received_qty": flt(row["received_qty"]),
                       "open_qty": flt(row["qty"]) - flt(row["received_qty"]),
                       "confirmed_qty": flt(row["confirmed_qty"]) if row.get("confirmed_qty") is not None else None,
                       "expected_date": str(expected)[:10] if expected else None,
                       "confirmed": bool(row.get("confirmed_date")), "overdue": overdue,
                       "days_overdue": (getdate(today) - getdate(str(expected)[:10])).days if overdue else 0})
    return result


def confirm_purchase_order_lines(purchase_order, confirmed_date, *, lines=None, confirmed_qty=None,
                                 confirmation_reference=None, confirmed_by=None):
    """Record the supplier's confirmed delivery date on lines of a submitted
    purchase order (all open lines, or those named by row name or position).

    The confirmation belongs to exactly these order lines: it is stored on
    them with the supplier's reference, who recorded it and when. Recording
    it again replaces the earlier date. Nothing else on the order changes.
    """
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return confirm_purchase_order_lines(
                purchase_order, confirmed_date, lines=lines, confirmed_qty=confirmed_qty,
                confirmation_reference=confirmation_reference, confirmed_by=confirmed_by)
    order = db.get_value("Purchase Order", purchase_order, ["docstatus", "discarded"])
    if not order or order.get("docstatus") != 1 or order.get("discarded"):
        raise ValidationError("A delivery date can only be confirmed on a submitted purchase order")
    try:
        date = getdate(str(confirmed_date)[:10]).isoformat()
    except (TypeError, ValueError):
        raise ValidationError("confirmed_date must be a date (YYYY-MM-DD)")
    if db.dialect == "postgres":
        db.sql('SELECT name FROM "Purchase Order" WHERE name = ? FOR UPDATE', [purchase_order])
    rows = db.get_all("Purchase Order Item", filters={"parent": purchase_order}, fields=["*"], order_by="idx")
    if lines:
        wanted = {str(value) for value in lines}
        chosen = [row for row in rows if row["name"] in wanted or str(row.get("idx")) in wanted]
        if len(chosen) != len(wanted):
            raise ValidationError(f"Purchase Order {purchase_order} has no line(s) {', '.join(sorted(wanted))}")
    else:
        chosen = [row for row in rows if flt(row.get("qty")) > flt(row.get("received_qty")) + EPSILON]
        if not chosen:
            raise ValidationError(f"Purchase Order {purchase_order} has no open lines to confirm")
    if confirmed_qty is not None:
        if len(chosen) != 1:
            raise ValidationError("confirmed_qty applies to one line: name the line")
        if not flt(confirmed_qty) > 0 or flt(confirmed_qty) > flt(chosen[0].get("qty")) + EPSILON:
            raise ValidationError("confirmed_qty must be positive and at most the ordered qty")
    stamp = now()
    for row in chosen:
        db.set_value("Purchase Order Item", row["name"], {
            "confirmed_date": date,
            "confirmed_qty": flt(confirmed_qty) if confirmed_qty is not None else None,
            "confirmation_reference": (str(confirmation_reference).strip() or None) if confirmation_reference else None,
            "confirmed_at": stamp, "confirmed_by": confirmed_by})
    db.set_value("Purchase Order", purchase_order, "modified", stamp)
    return {"purchase_order": purchase_order, "confirmed_date": date,
            "lines": [{"purchase_order_item": row["name"], "idx": row.get("idx"), "item_code": row.get("item_code"),
                       "open_qty": flt(row.get("qty")) - flt(row.get("received_qty"))} for row in chosen]}
