"""Shared document rules, used by every save/submit and exposed as metadata."""
import math
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt

TRANSACTION_TYPES = {
    'Quotation', 'Sales Order', 'Purchase Order', 'Sales Invoice',
    'Purchase Invoice', 'POS Invoice', 'Delivery Note', 'Purchase Receipt',
}

ORDER_REFERENCES = {
    'Delivery Note': ('Sales Order', 'against_sales_order', 'so_detail', 'customer'),
    'Sales Invoice': ('Sales Order', 'sales_order', 'sales_order_item', 'customer'),
    'Purchase Receipt': ('Purchase Order', 'against_purchase_order', 'po_detail', 'supplier'),
    'Purchase Invoice': ('Purchase Order', 'purchase_order', 'purchase_order_item', 'supplier'),
}


def validate_order_references(doc):
    rule = ORDER_REFERENCES.get(doc.DOCTYPE)
    if not rule:
        return
    table, parent_field, line_field, party = rule
    db = get_db()
    for idx, row in enumerate(doc.get('items') or [], 1):
        order, line = row.get(parent_field), row.get(line_field)
        if missing(order) and missing(line):
            continue
        if missing(order) or missing(line):
            raise ValidationError(f'{doc.DOCTYPE} row {idx}: {parent_field} and {line_field} must be supplied together; use the order converter or select its exact line')
        parent = db.get_value(table, order, ['company', party, 'docstatus', 'discarded'])
        detail = db.get_value(table + ' Item', line, ['parent', 'item_code'])
        if not parent or parent.get('docstatus') != 1 or parent.get('discarded'):
            raise ValidationError(f'{doc.DOCTYPE} row {idx}: referenced {table} must exist and be submitted')
        if not detail or detail.get('parent') != order:
            raise ValidationError(f'{doc.DOCTYPE} row {idx}: order line must belong to the referenced {table}')
        if parent.get('company') != doc.get('company') or parent.get(party) != doc.get(party):
            raise ValidationError(f'{doc.DOCTYPE} row {idx}: order Company and {party} must match the document')
        if detail.get('item_code') != row.get('item_code'):
            raise ValidationError(f'{doc.DOCTYPE} row {idx}: Item Code must match the order line')


def missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def validate_document_requirements(doc):
    """No writes: reject incomplete business data before persistence or posting."""
    for field in doc.REQUIRED_FIELDS:
        if missing(doc.get(field)):
            raise ValidationError(f"{doc.DOCTYPE}: {field.replace('_', ' ').title()} is required")
    validate_order_references(doc)
    from lambda_erp.workflow import validate_order_quantities
    validate_order_quantities(doc)
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
    rules.append('Create requires a new document name (omit name for automatic naming). Existing documents must be loaded and updated as drafts; submitted/cancelled/discarded or stale records cannot be overwritten. Reload after a conflict.')
    children = dict(getattr(cls, 'CHILD_REQUIREMENTS', {}))
    if cls.DOCTYPE in ORDER_REFERENCES:
        table, parent, line, party = ORDER_REFERENCES[cls.DOCTYPE]
        rules.append('Cumulative submitted quantities cannot exceed the referenced order line quantity; check remaining quantities before creating another delivery or invoice.')
        rules.append(f'{parent} and {line} must be supplied together, referencing a submitted {table} and its exact item line with matching company, {party} and item_code. Use the converter when possible.')
    from lambda_erp.workflow import RETURN_TYPES
    if cls.DOCTYPE in RETURN_TYPES:
        rules.append('Returns require a submitted non-return original with matching company and party, negative quantities, original order-line references and quantities within the remaining returnable amount across all rows and previous returns. Use the return converter.')
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
