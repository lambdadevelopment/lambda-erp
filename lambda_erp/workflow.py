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
    from lambda_erp.accounting.settlement import INVOICE_TYPES
    if doc.DOCTYPE in {'Payment Entry', 'Journal Entry'}:
        field = 'references' if doc.DOCTYPE == 'Payment Entry' else 'accounts'
        refs.update((row.get('reference_doctype') or row.get('reference_type'), row['reference_name']) for row in doc.get(field)
                    if (row.get('reference_doctype') or row.get('reference_type')) in INVOICE_TYPES and row.get('reference_name'))
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


def apply_amount_only(doc):
    """A return invoice that corrects money only (a price correction, a
    goodwill credit). No goods come back: it never moves stock and leaves the
    billed and returnable quantities of its original alone. Its lines still
    name the original rows with a negative qty (the units the credit is for)
    so tax and totals work like on any other return."""
    if not flt(doc.get('amount_only')):
        return False
    if not flt(doc.get('is_return')):
        raise ValidationError('Amount only is for returns (credit / debit notes): set is_return and return_against')
    doc._data['update_stock'] = 0
    return True


def _goods_returns(doctype, alias='p'):
    """SQL filter: leave amount-only returns out of quantity sums."""
    if 'amount_only' not in get_db()._get_table_columns(doctype):
        return ''
    return f' AND COALESCE({alias}.amount_only, 0) = 0'


def _row_batch(row):
    return str(row.get('batch_no') or '').strip()


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
    amount_only = bool(flt(doc.get('amount_only')))
    if not amount_only and doc.DOCTYPE.endswith('Invoice') and bool(flt(doc.get('update_stock'))) != bool(flt(original.update_stock)):
        raise ValidationError('Return update_stock must match the original invoice; for a price correction without goods set amount_only')
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    table = doc.CHILD_TABLES['items'][0]
    original_rows = db.get_all(table, filters={'parent': doc.return_against}, fields=['*'])
    quantities = defaultdict(float)
    for row in original_rows:
        quantities[_return_key(row, rule)] += flt(row.get('qty'))
    previous = [] if amount_only else db.sql(
        f'SELECT i.* FROM "{table}" i JOIN "{doc.DOCTYPE}" p ON p.name = i.parent '
        'WHERE p.return_against = ? AND p.is_return = 1 AND p.docstatus = 1 AND p.name != ?'
        + _goods_returns(doc.DOCTYPE),
        [doc.return_against, doc.name])
    returned = defaultdict(float)
    for row in previous:
        returned[_return_key(row, rule)] += abs(flt(row.get('qty')))
    # Batches the original moved, per line identity. A return can only bring
    # back (or send back) a batch that was on the original, and no more of it.
    batch_quantities = defaultdict(float)
    batch_returned = defaultdict(float)
    batch_requested = defaultdict(float)
    with_batches = set()
    for row in original_rows:
        if _row_batch(row):
            with_batches.add(_return_key(row, rule))
            batch_quantities[(*_return_key(row, rule), _row_batch(row))] += flt(row.get('qty'))
    for row in previous:
        if _row_batch(row):
            batch_returned[(*_return_key(row, rule), _row_batch(row))] += abs(flt(row.get('qty')))
    requested = defaultdict(float)
    for row in doc.get('items'):
        if flt(row.get('qty')) >= 0:
            raise ValidationError('Return Qty must be negative on every row')
        key = _return_key(row, rule)
        if key not in quantities:
            raise ValidationError('Return item and order line must match an original document row; use the return converter')
        requested[key] += abs(flt(row.get('qty')))
        batch = _row_batch(row)
        if batch and key in with_batches and not amount_only:
            if (*key, batch) not in batch_quantities:
                raise ValidationError(
                    f'Batch {batch} of {key[0]} is not on {doc.DOCTYPE} {doc.return_against}; '
                    f'a return must name a batch the original moved '
                    f'({", ".join(sorted(b for *k, b in batch_quantities if tuple(k) == key))})')
            batch_requested[(*key, batch)] += abs(flt(row.get('qty')))
    for key, qty in batch_requested.items():
        remaining = max(0, batch_quantities[key] - batch_returned[key])
        if qty > remaining + EPSILON:
            raise ValidationError(
                f'Return qty ({qty}) of batch {key[-1]} of {key[0]} exceeds what is left to return of that batch '
                f'({remaining}; already returned {batch_returned[key]})')
    for key, qty in requested.items():
        if amount_only:
            # Money only: bounded by what was invoiced (and by the value cap
            # on the invoice), not by what is still returnable.
            if qty > quantities[key] + EPSILON:
                raise ValidationError(f'Amount-only qty ({qty}) for {key[0]} exceeds the invoiced qty ({quantities[key]})')
            continue
        remaining = max(0, quantities[key] - returned[key])
        if qty > remaining + EPSILON:
            raise ValidationError(f'Return qty ({qty}) for {key[0]} exceeds remaining returnable qty ({remaining}; already returned {returned[key]})')


def _sum_line(doctype, line_field, line, *, exclude=None, stock_only=False):
    db = get_db()
    return flt(db.sql(
        f'SELECT COALESCE(SUM(i.qty), 0) AS qty FROM "{doctype} Item" i '
        f'JOIN "{doctype}" p ON p.name = i.parent WHERE i."{line_field}" = ? '
        'AND p.docstatus = 1 AND p.name != ?' + (' AND p.update_stock = 1' if stock_only else '')
        + _goods_returns(doctype),
        [line, exclude or ''])[0]['qty'])


def returnable_rows(original):
    """Converters propose only remaining quantities; submit still rechecks."""
    db = get_db()
    rule = ORDER_REFERENCES.get(original.DOCTYPE)
    table = original.CHILD_TABLES['items'][0]
    flag = ', p.amount_only AS amount_only' if 'amount_only' in db._get_table_columns(original.DOCTYPE) else ''
    previous = db.sql(f'SELECT i.*{flag} FROM "{table}" i JOIN "{original.DOCTYPE}" p ON p.name = i.parent '
                      'WHERE p.return_against = ? AND p.is_return = 1 AND p.docstatus = 1', [original.name])
    used = defaultdict(float)
    used_batch = defaultdict(float)  # returned per line identity and batch
    credited = defaultdict(float)  # value already credited per line identity
    corrected = set()
    for row in previous:
        key = _return_key(row, rule)
        credited[key] += abs(flt(row.qty)) * flt(row.get('rate'))
        if flt(row.get('amount_only')):
            corrected.add(key)
        elif _row_batch(row):
            used_batch[(*key, _row_batch(row))] += abs(flt(row.qty))
        else:
            used[key] += abs(flt(row.qty))
    invoiced = defaultdict(float)
    for row in original.get('items'):
        invoiced[_return_key(row, rule)] += flt(row.get('qty')) * flt(row.get('rate'))
    result = []
    for row in original.get('items'):
        key = _return_key(row, rule)
        remaining = flt(row.get('qty'))
        if _row_batch(row):
            # A returned batch comes off the original rows of that batch.
            batch_key = (*key, _row_batch(row))
            consumed = min(remaining, used_batch[batch_key])
            used_batch[batch_key] -= consumed
            remaining -= consumed
        consumed = min(remaining, used[key])
        used[key] -= consumed
        remaining -= consumed
        if remaining > EPSILON:
            result.append({**row, 'qty':remaining})
    if not result:
        raise ValidationError('No remaining returnable quantity on this document')
    # After a price correction the goods come back at what was left to pay
    # for them, not at the original price (which would exceed the value cap).
    for key in corrected:
        rows = [row for row in result if _return_key(row, rule) == key]
        at_original = sum(flt(row['qty']) * flt(row.get('rate')) for row in rows)
        left = max(0, invoiced[key] - credited[key])
        if at_original > left + EPSILON:
            for row in rows:
                row['rate'] = flt(row.get('rate')) * left / at_original
    return result


def validate_order_quantities(doc, *, cancelling=False):
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    if not rule:
        return
    if flt(doc.get('amount_only')):
        return  # money only: order quantities are untouched
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
    from lambda_erp.accounting.settlement import INVOICE_TYPES
    if doc.DOCTYPE in INVOICE_TYPES:
        for kind, child in [('Journal Entry', 'Journal Entry Account'), ('Payment Entry', 'Payment Entry Reference')]:
            discriminator = "COALESCE(NULLIF(r.reference_doctype, ''), r.reference_type)" if kind == 'Journal Entry' else 'r.reference_doctype'
            rows = db.sql(f'SELECT p.name FROM "{kind}" p JOIN "{child}" r ON r.parent = p.name '
                          f'WHERE p.docstatus = 1 AND {discriminator} = ? AND r.reference_name = ? LIMIT 1', [doc.DOCTYPE, doc.name])
            if rows:
                raise ValidationError(f'Cancel linked {kind} {rows[0].name} before cancelling {doc.DOCTYPE} {doc.name}')
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
            # Reservations follow the order: created on request at submit,
            # consumed by deliveries, cancelled with the order.
            from lambda_erp.stock.reservation import sync_sales_order
            sync_sales_order(name)
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
