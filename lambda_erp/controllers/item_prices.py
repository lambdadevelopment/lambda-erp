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
