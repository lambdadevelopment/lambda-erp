"""Reservation dependency locks and guards shared by all document entry points."""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

VOUCHER_TYPES = {'Quotation', 'Sales Order', 'Sales Invoice', 'Purchase Order'}


def lock_rental_references(doc):
    """Lock vouchers before item pools; unit and pooled bookings share a lock.

    Called inside save/discard's transaction, before reading availability. Asset
    capacity changes take the same item locks. SQLite uses BEGIN IMMEDIATE.
    Include old references so moving a booking cannot race its former voucher.
    """
    if doc.DOCTYPE not in {'Asset', 'Reservation'}:
        return
    db = get_db()
    old = db.get_value(doc.DOCTYPE, doc.name, ['item_code', 'asset', 'voucher_type', 'voucher_no']) if doc._persisted else None
    versions = [doc.as_dict()] + ([old] if old else [])
    vouchers, items, assets = set(), set(), set()
    for row in versions:
        if doc.DOCTYPE == 'Reservation':
            if row.get('voucher_type') in VOUCHER_TYPES and row.get('voucher_no'):
                vouchers.add((row['voucher_type'], row['voucher_no']))
            if row.get('asset'):
                assets.add(row['asset'])
        if row.get('item_code'):
            items.add(row['item_code'])
    if db.dialect == 'postgres':
        for table, name in sorted(vouchers):
            db.sql(f'SELECT name FROM "{table}" WHERE name = ? FOR UPDATE', [name])
    for name in sorted(assets):
        suffix = ' FOR UPDATE' if db.dialect == 'postgres' else ''
        rows = db.sql('SELECT item_code FROM "Asset" WHERE name = ?' + suffix, [name])
        if rows and rows[0]['item_code']:
            items.add(rows[0]['item_code'])
    if db.dialect == 'postgres':
        for name in sorted(items):
            db.sql('SELECT name FROM "Item" WHERE name = ? FOR UPDATE', [name])


def validate_reservation_voucher(doc):
    from lambda_erp.assets.reservation import BLOCKING_STATUSES
    if doc.status not in BLOCKING_STATUSES or not doc.get('voucher_no'):
        return
    kind = doc.get('voucher_type')
    if kind not in VOUCHER_TYPES:
        raise ValidationError('Unsupported reservation Voucher Type')
    row = get_db().get_value(kind, doc.voucher_no, ['docstatus', 'discarded'])
    if not row or row.get('docstatus') == 2 or row.get('discarded'):
        raise ValidationError('An active reservation requires an existing, non-cancelled, non-discarded voucher')


def validate_voucher_release(doc):
    if doc.DOCTYPE not in VOUCHER_TYPES:
        return
    rows = get_db().sql(
        'SELECT name FROM "Reservation" WHERE voucher_type = ? AND voucher_no = ? '
        "AND status IN ('Reserved', 'Out') AND COALESCE(discarded, 0) = 0 LIMIT 1",
        [doc.DOCTYPE, doc.name])
    if rows:
        raise ValidationError(f'Resolve active reservation {rows[0]["name"]} before cancelling or discarding {doc.DOCTYPE} {doc.name}; return an Out asset before releasing its booking')


def validate_asset_change(doc, *, discarding=False):
    if not doc._persisted:
        return
    db = get_db()
    old = db.get_value('Asset', doc.name, ['item_code', 'warehouse', 'company', 'status', 'disabled', 'discarded'])
    changed = any(doc.get(k) != old.get(k) for k in ('item_code', 'warehouse', 'company'))
    removes_capacity = discarding or doc.get('status') == 'Retired' or bool(doc.get('disabled'))
    if not changed and not removes_capacity:
        return
    rows = db.sql(
        'SELECT name FROM "Reservation" WHERE COALESCE(discarded, 0) = 0 '
        "AND status IN ('Reserved', 'Out') AND (asset = ? OR "
        "((asset IS NULL OR asset = '') AND item_code = ? AND warehouse = ?)) LIMIT 1",
        [doc.name, old['item_code'], old['warehouse']])
    if rows:
        raise ValidationError(f'Resolve active reservation {rows[0]["name"]} before moving, changing, disabling, retiring or discarding Asset {doc.name}; pooled bookings must be assigned or released first')
