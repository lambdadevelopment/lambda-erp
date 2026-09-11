"""Cross-document quantities and planning, shared by REST, chat and model writes."""
from collections import defaultdict

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt
from lambda_erp.validation import ORDER_REFERENCES

RETURN_TYPES = {'Delivery Note', 'Purchase Receipt', 'Sales Invoice', 'Purchase Invoice', 'POS Invoice'}
EPSILON = 1e-9


def lock_workflow_references(doc):
    """Serialize checks against the same original/order, including cancel."""
    db = get_db()
    refs = set()
    if doc.DOCTYPE in RETURN_TYPES and doc.get('return_against'):
        refs.add((doc.DOCTYPE, doc.return_against))
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    if rule:
        table, parent, _, _ = rule
        refs.update((table, row[parent]) for row in doc.get('items') if row.get(parent))
    for table, name in sorted(refs):
        if db.dialect == 'postgres':
            db.sql(f'SELECT name FROM "{table}" WHERE name = ? FOR UPDATE', [name])
    # A shared item lock also covers first-ever Bin creation and competing
    # orders for the same stock. SQLite's lifecycle lock covers its connection.
    if db.dialect == 'postgres':
        items = {row.get('item_code') for row in (doc.get('items') or []) if row.get('item_code')}
        for item in sorted(items):
            db.sql('SELECT name FROM "Item" WHERE name = ? FOR UPDATE', [item])


def _return_key(row, rule):
    identity = row.get('item_code') or row.get('item_name') or row.get('description')
    return (identity, row.get(rule[2]) if rule else None)


def validate_return(doc):
    if not doc.get('return_against'):
        raise ValidationError(f'Return Against is required for a return {doc.DOCTYPE}')
    db = get_db()
    original = db.get_value(doc.DOCTYPE, doc.return_against, ['name', 'docstatus', 'is_return', 'company', 'customer', 'supplier', 'currency', 'update_stock'])
    if not original:
        raise ValidationError(f'Original {doc.DOCTYPE} {doc.return_against} not found')
    if original.docstatus != 1 or original.is_return:
        raise ValidationError(f'Original {doc.DOCTYPE} must be submitted and cannot itself be a return')
    party = 'supplier' if doc.DOCTYPE in {'Purchase Receipt', 'Purchase Invoice'} else 'customer'
    for field in ('company', party):
        if doc.get(field) != original.get(field):
            raise ValidationError(f'Return {field} must match the original {doc.DOCTYPE}')
    if doc.get('currency') and doc.currency != original.currency:
        raise ValidationError('Return currency must match the original document')
    if not doc.get('currency'):
        doc.currency = original.currency
    if doc.DOCTYPE.endswith('Invoice') and bool(flt(doc.get('update_stock'))) != bool(flt(original.update_stock)):
        raise ValidationError('Return update_stock must match the original invoice')
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    table = doc.CHILD_TABLES['items'][0]
    original_rows = db.get_all(table, filters={'parent': doc.return_against}, fields=['*'])
    quantities = defaultdict(float)
    for row in original_rows:
        quantities[_return_key(row, rule)] += flt(row.get('qty'))
    previous = db.sql(
        f'SELECT i.* FROM "{table}" i JOIN "{doc.DOCTYPE}" p ON p.name = i.parent '
        'WHERE p.return_against = ? AND p.is_return = 1 AND p.docstatus = 1 AND p.name != ?',
        [doc.return_against, doc.name])
    returned = defaultdict(float)
    for row in previous:
        returned[_return_key(row, rule)] += abs(flt(row.get('qty')))
    requested = defaultdict(float)
    for row in doc.get('items'):
        if flt(row.get('qty')) >= 0:
            raise ValidationError('Return Qty must be negative on every row')
        key = _return_key(row, rule)
        if key not in quantities:
            raise ValidationError('Return item and order line must match an original document row; use the return converter')
        requested[key] += abs(flt(row.get('qty')))
    for key, qty in requested.items():
        remaining = max(0, quantities[key] - returned[key])
        if qty > remaining + EPSILON:
            raise ValidationError(f'Return qty ({qty}) for {key[0]} exceeds remaining returnable qty ({remaining}; already returned {returned[key]})')


def _sum_line(doctype, line_field, line, *, exclude=None, stock_only=False):
    db = get_db()
    return flt(db.sql(
        f'SELECT COALESCE(SUM(i.qty), 0) AS qty FROM "{doctype} Item" i '
        f'JOIN "{doctype}" p ON p.name = i.parent WHERE i."{line_field}" = ? '
        'AND p.docstatus = 1 AND p.name != ?' + (' AND p.update_stock = 1' if stock_only else ''),
        [line, exclude or ''])[0]['qty'])


def returnable_rows(original):
    """Converters propose only remaining quantities; submit still rechecks."""
    db = get_db()
    rule = ORDER_REFERENCES.get(original.DOCTYPE)
    table = original.CHILD_TABLES['items'][0]
    previous = db.sql(f'SELECT i.* FROM "{table}" i JOIN "{original.DOCTYPE}" p ON p.name = i.parent '
                      'WHERE p.return_against = ? AND p.is_return = 1 AND p.docstatus = 1', [original.name])
    used = defaultdict(float)
    for row in previous:
        used[_return_key(row, rule)] += abs(flt(row.qty))
    result = []
    for row in original.get('items'):
        key = _return_key(row, rule)
        consumed = min(flt(row.get('qty')), used[key])
        used[key] -= consumed
        remaining = flt(row.get('qty')) - consumed
        if remaining > EPSILON:
            result.append({**row, 'qty':remaining})
    if not result:
        raise ValidationError('No remaining returnable quantity on this document')
    return result


def validate_order_quantities(doc, *, cancelling=False):
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    if not rule:
        return
    order_type, _, line_field, _ = rule
    quantities = defaultdict(float)
    for row in doc.get('items'):
        if row.get(line_field):
            quantities[row[line_field]] += 0 if cancelling else flt(row.get('qty'))
    for line, qty in quantities.items():
        ordered = flt(get_db().get_value(order_type + ' Item', line, 'qty'))
        posted = _sum_line(doc.DOCTYPE, line_field, line, exclude=doc.name)
        if posted + qty > ordered + EPSILON:
            raise ValidationError(f'{doc.DOCTYPE}: Qty exceeds remaining order quantity ({max(0, ordered - posted)}) for line {line}; already booked {posted} of {ordered}')
        # Direct-stock invoices and delivery/receipt notes consume the same
        # physical order capacity even though billing is tracked separately.
        if doc.DOCTYPE in {'Delivery Note', 'Purchase Receipt'} or flt(doc.get('update_stock')):
            sales = order_type == 'Sales Order'
            movement, movement_line = ('Delivery Note', 'so_detail') if sales else ('Purchase Receipt', 'po_detail')
            invoice, invoice_line = ('Sales Invoice', 'sales_order_item') if sales else ('Purchase Invoice', 'purchase_order_item')
            moved = _sum_line(movement, movement_line, line, exclude=doc.name if doc.DOCTYPE == movement else None)
            moved += _sum_line(invoice, invoice_line, line, exclude=doc.name if doc.DOCTYPE == invoice else None, stock_only=True)
            if moved + qty > ordered + EPSILON:
                raise ValidationError(f'{doc.DOCTYPE}: Qty exceeds remaining order stock quantity ({max(0, ordered - moved)}) for line {line}, including direct-stock invoices')


def validate_cancellation(doc):
    db = get_db()
    if doc.DOCTYPE in RETURN_TYPES and db.get_all(doc.DOCTYPE, filters={'return_against': doc.name, 'docstatus': 1}, limit=1):
        raise ValidationError('Cancel the submitted returns before cancelling their original document')
    if doc.DOCTYPE in {'Sales Order', 'Purchase Order'}:
        for kind, (order_type, parent, _, _) in ORDER_REFERENCES.items():
            if order_type == doc.DOCTYPE and db.sql(
                f'SELECT p.name FROM "{kind}" p JOIN "{kind} Item" i ON i.parent = p.name '
                f'WHERE i."{parent}" = ? AND p.docstatus = 1 LIMIT 1', [doc.name]):
                raise ValidationError(f'Cancel linked submitted {kind} documents before cancelling this order')
    validate_order_quantities(doc, cancelling=True)


def refresh_order_progress(doc):
    """Recalculate from submitted vouchers; never add/subtract cached counters."""
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return refresh_order_progress(doc)
    if doc.DOCTYPE in {'Sales Order', 'Purchase Order'}:
        orders = {(doc.DOCTYPE, doc.name)}
    elif doc.DOCTYPE in ORDER_REFERENCES:
        table, parent, _, _ = ORDER_REFERENCES[doc.DOCTYPE]
        orders = {(table, row[parent]) for row in doc.get('items') if row.get(parent)}
    else:
        return
    for kind, name in sorted(orders):
        if db.dialect == 'postgres':
            db.sql(f'SELECT name FROM "{kind}" WHERE name = ? FOR UPDATE', [name])
        sales = kind == 'Sales Order'
        movement, move_line = ('Delivery Note', 'so_detail') if sales else ('Purchase Receipt', 'po_detail')
        invoice, invoice_line = ('Sales Invoice', 'sales_order_item') if sales else ('Purchase Invoice', 'purchase_order_item')
        progress = 'delivered_qty' if sales else 'received_qty'
        lines = db.get_all(kind + ' Item', filters={'parent': name}, fields=['*'])
        total = moved = billed = 0
        for row in lines:
            delivered = _sum_line(movement, move_line, row.name) + _sum_line(invoice, invoice_line, row.name, stock_only=True)
            invoiced = _sum_line(invoice, invoice_line, row.name)
            db.set_value(kind + ' Item', row.name, {progress: delivered, 'billed_qty': invoiced})
            total += flt(row.qty)
            moved += delivered
            billed += invoiced
        values = {'per_delivered' if sales else 'per_received': flt(moved / total * 100, 2) if total else 0}
        if 'per_billed' in db._get_table_columns(kind):
            values['per_billed'] = flt(billed / total * 100, 2) if total else 0
        db.set_value(kind, name, values)
        if sales:
            from lambda_erp.selling.sales_order import SalesOrder
            order = SalesOrder.load(name)
            order._set_status()
            db.set_value(kind, name, 'status', order.status)
        refresh_planning(kind, lines)


def refresh_planning(kind, lines):
    db = get_db()
    progress, column = ('delivered_qty', 'reserved_qty') if kind == 'Sales Order' else ('received_qty', 'ordered_qty')
    pairs = {(row.get('item_code'), row.get('warehouse')) for row in lines if row.get('item_code') and row.get('warehouse')}
    for item, warehouse in sorted(pairs):
        if not db.get_value('Item', item, 'is_stock_item'):
            continue
        name = f'{item}-{warehouse}'
        db.sql('INSERT INTO "Bin" (name, item_code, warehouse) VALUES (?, ?, ?) ON CONFLICT DO NOTHING', [name, item, warehouse])
        name = db.get_value('Bin', {'item_code':item, 'warehouse':warehouse}, 'name')
        if db.dialect == 'postgres':
            db.sql('SELECT name FROM "Bin" WHERE name = ? FOR UPDATE', [name])
        rows = db.sql(f'SELECT i.qty, i."{progress}" AS done FROM "{kind} Item" i '
                      f'JOIN "{kind}" p ON p.name = i.parent WHERE p.docstatus = 1 AND i.item_code = ? AND i.warehouse = ?', [item, warehouse])
        db.set_value('Bin', name, column, sum(max(0, flt(row.qty) - flt(row.done)) for row in rows))
