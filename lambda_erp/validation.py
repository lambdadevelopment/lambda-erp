"""Shared document rules, used by every save/submit and exposed as metadata."""
import math
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt

TRANSACTION_TYPES = {
    'Quotation', 'Sales Order', 'Purchase Order', 'Sales Invoice',
    'Purchase Invoice', 'POS Invoice', 'Delivery Note', 'Purchase Receipt',
}


def missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def validate_document_requirements(doc):
    """No writes: reject incomplete business data before persistence or posting."""
    for field in doc.REQUIRED_FIELDS:
        if missing(doc.get(field)):
            raise ValidationError(f"{doc.DOCTYPE}: {field.replace('_', ' ').title()} is required")
    if doc.DOCTYPE not in TRANSACTION_TYPES:
        return
    if missing(doc.get('company')):
        raise ValidationError(f'{doc.DOCTYPE}: Company is required')
    moves_stock = doc.DOCTYPE in {'Delivery Note', 'Purchase Receipt'} or bool(flt(doc.get('update_stock')))
    db = get_db()
    for idx, row in enumerate(doc.get('items') or [], 1):
        prefix = f'{doc.DOCTYPE}: row {idx}'
        if not any(not missing(row.get(f)) for f in ('item_code', 'item_name', 'description')):
            raise ValidationError(f'{prefix}: Item Code or a description is required')
        try:
            qty = float(row.get('qty'))
        except (TypeError, ValueError):
            raise ValidationError(f'{prefix}: Qty is required and must be a number')
        if not math.isfinite(qty) or qty == 0 or (qty < 0 and not flt(doc.get('is_return'))):
            raise ValidationError(f'{prefix}: Qty must be positive (negative quantities are only allowed on returns)')
        if moves_stock and row.get('item_code'):
            item = db.get_value('Item', row['item_code'], ['is_stock_item'])
            if item and item.get('is_stock_item') and missing(row.get('warehouse')):
                raise ValidationError(f'{prefix}: Warehouse is required for stock item {row["item_code"]}')


def document_requirements(cls):
    """Machine-readable minimums plus conditional rules owned by the class."""
    required = list(getattr(cls, 'REQUIRED_FIELDS', ()))
    rules = list(getattr(cls, 'CONDITIONAL_REQUIREMENTS', ()))
    children = {}
    if cls.DOCTYPE in TRANSACTION_TYPES:
        required += ['company', 'items']
        party = 'supplier' if cls.DOCTYPE in {'Purchase Order', 'Purchase Invoice', 'Purchase Receipt'} else 'customer'
        required.append(party)
        children['items'] = {
            'required': ['qty'],
            'any_of': [['item_code', 'item_name', 'description']],
            'rules': [
                'Qty must be positive; negative quantities are only allowed on returns.',
                'Warehouse is required on stock items for Delivery Note, Purchase Receipt, or update_stock=1.',
            ],
        }
    return {'required': sorted(set(required)), 'conditional': rules, 'children': children}
