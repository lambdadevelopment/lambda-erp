"""Normalize missing prices without losing an explicit zero."""
import math
from lambda_erp.exceptions import ValidationError


def normalize_item_prices(doc):
    from lambda_erp.validation import TRANSACTION_TYPES
    if doc.DOCTYPE not in TRANSACTION_TYPES:
        return
    for index, row in enumerate(doc.get('items') or [], 1):
        for field in ('rate', 'price_list_rate'):
            value = row.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                row[field] = None
                continue
            try:
                number = float(value)
            except (ValueError, TypeError):
                raise ValidationError(f'{doc.DOCTYPE} row {index}: {field} must be a non-negative finite number')
            if not math.isfinite(number) or number < 0:
                raise ValidationError(f'{doc.DOCTYPE} row {index}: {field} must be a non-negative finite number')
            row[field] = number
        _normalize_deduction(doc, index, row)


def _normalize_deduction(doc, index, row):
    """A labelled deduction on a line: the price stays the full price
    (price_list_rate) and `discount_amount` per unit comes off it, so the line
    never goes negative and keeps its own tax. Given only a rate, that rate is
    the full price."""
    value = row.get('discount_amount')
    if value is None or (isinstance(value, str) and not value.strip()):
        return
    try:
        deduction = float(value)
    except (ValueError, TypeError):
        deduction = math.nan
    if not math.isfinite(deduction) or deduction < 0:
        raise ValidationError(f'{doc.DOCTYPE} row {index}: discount_amount must be a non-negative finite number')
    row['discount_amount'] = deduction
    if isinstance(row.get('discount_label'), str):
        row['discount_label'] = row['discount_label'].strip() or None
    if not deduction or float(row.get('discount_percentage') or 0):
        return
    if row.get('discount_label') and not row.get('price_list_rate') and row.get('rate'):
        row['price_list_rate'] = row['rate']
    full = row.get('price_list_rate')
    if full and deduction > full + 1e-9:
        raise ValidationError(f'{doc.DOCTYPE} row {index}: the deduction ({deduction:.2f} per unit) '
                              f'is larger than the price ({full:.2f})')
