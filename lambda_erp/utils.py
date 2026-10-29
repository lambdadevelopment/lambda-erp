"""
Utility functions ported from the framework/the reference implementation.

These replace framework.utils.flt, cint, getdate, etc. that are used
throughout the the reference implementation business logic.
"""

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

def flt(value, precision=None):
    """Convert to float, with optional rounding precision. Safe numeric
    conversion used everywhere amounts are handled."""
    if value is None or value == "":
        return 0.0
    try:
        value = float(value)
    except (ValueError, TypeError):
        return 0.0
    if precision is not None:
        value = round(value, precision)
    return value

def cint(value):
    """Convert to integer safely."""
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0

def cstr(value):
    """Convert to string safely."""
    if value is None:
        return ""
    return str(value)

def getdate(value=None):
    """Convert string or datetime to date."""
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    return date.today()

def nowdate():
    """Return today's date as string."""
    return date.today().isoformat()

def now():
    """Return the current instant as a timezone-aware UTC ISO string
    (e.g. "2026-07-29T09:57:00.123456+00:00").

    The offset is deliberate: a naive isoformat() (no zone) is ambiguous, and a
    client parsing it (JS `new Date(...)`) reads it as *browser-local*, so a
    timestamp stamped on a UTC server renders shifted by the viewer's UTC
    offset. Stamping the zone makes every stored timestamp (creation, modified,
    occurred_at, …) an unambiguous instant that round-trips correctly."""
    return datetime.now(timezone.utc).isoformat()

def add_days(dt, days):
    """Add days to a date."""
    return getdate(dt) + timedelta(days=days)

def add_months(dt, months):
    """Add months to a date."""
    dt = getdate(dt)
    month = dt.month + months
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return date(year, month, day)

def _days_in_month(year, month):
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days

def fmt_money(amount, precision=2, currency=None):
    """Format amount as money string."""
    if amount is None:
        amount = 0
    formatted = f"{flt(amount):,.{precision}f}"
    if currency:
        return f"{currency} {formatted}"
    return formatted

def rounded(value, precision=0):
    """Round using banker's rounding (round half up)."""
    if precision < 0:
        return value
    d = Decimal(str(value))
    factor = Decimal(10) ** -precision
    return float(d.quantize(factor, rounding=ROUND_HALF_UP))

def get_fiscal_year(dt=None, company=None):
    """Get fiscal year for a date. Default: calendar year."""
    dt = getdate(dt)
    # Simple calendar year assumption - can be customized
    return (date(dt.year, 1, 1), date(dt.year, 12, 31), str(dt.year))

class _dict(dict):
    """A dict subclass that allows attribute-style access.

    Lightweight data container — GL entries, SL entries, item details, etc.
    are all passed around as _dict instances.
    """

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError:
            pass

    def copy(self):
        return _dict(super().copy())

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        return self

def new_name(prefix):
    """Generate a sequential name like 'ACC-SINV-0001'.

    Simple replacement for the framework's naming system. The number comes
    from the database's shared naming series (Database.next_in_series), so
    every process on the same database draws from one sequence.
    """
    from lambda_erp.database import get_db

    return f"{prefix}-{get_db().next_in_series(prefix):04d}"
