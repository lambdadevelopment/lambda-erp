"""Currency exchange-rate lookup.

Rates live in the `Currency Exchange` table (date, from_currency, to_currency,
exchange_rate). A lookup carries forward the most recent rate on or before a
given date. The looked-up rate is snapshotted onto each document at posting
time (its `conversion_rate` column), so editing this table never changes a
book that has already been posted — the snapshot is what guarantees historical
reproducibility, not any immutability of the table itself.
"""

from lambda_erp.database import get_db
from lambda_erp.utils import flt, nowdate
from lambda_erp.exceptions import ValidationError


def get_exchange_rate(from_currency, to_currency, date=None):
    """Return units of to_currency per 1 unit of from_currency on/before date.

    - Same currency (or a missing currency) -> 1.0.
    - Otherwise the newest Currency Exchange rate with date <= the given date
      (carry-forward). If only the reverse pair exists, its inverse is used.
    - If no rate is found at all, raises rather than silently assuming 1.0 —
      booking a foreign-currency document at 1.0 would corrupt the base ledger.
    """
    if not from_currency or not to_currency or from_currency == to_currency:
        return 1.0

    db = get_db()
    on_date = date or nowdate()

    direct = db.sql(
        'SELECT exchange_rate FROM "Currency Exchange" '
        'WHERE from_currency = ? AND to_currency = ? AND date <= ? '
        'ORDER BY date DESC LIMIT 1',
        [from_currency, to_currency, on_date],
    )
    if direct and flt(direct[0]["exchange_rate"]) > 0:
        return flt(direct[0]["exchange_rate"])

    inverse = db.sql(
        'SELECT exchange_rate FROM "Currency Exchange" '
        'WHERE from_currency = ? AND to_currency = ? AND date <= ? '
        'ORDER BY date DESC LIMIT 1',
        [to_currency, from_currency, on_date],
    )
    if inverse and flt(inverse[0]["exchange_rate"]) > 0:
        return flt(1.0 / flt(inverse[0]["exchange_rate"]), 6)

    raise ValidationError(
        f"No exchange rate found for {from_currency} -> {to_currency} on or before "
        f"{on_date}. Add a Currency Exchange entry or pass conversion_rate explicitly."
    )
