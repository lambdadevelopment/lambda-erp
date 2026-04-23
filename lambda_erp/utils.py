"""
Utility functions ported from the framework/the reference implementation.

These replace framework.utils.flt, cint, getdate, etc. that are used
throughout the the reference implementation business logic.
"""

import math
from datetime import date, datetime, timedelta
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
    """Return current datetime as string."""
    return datetime.now().isoformat()

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

def new_name(prefix, sequence_store={}):
    """Generate a sequential name like 'ACC-SINV-0001'.

    Simple replacement for the framework's naming system.
    On first use of a prefix, checks the database for the highest existing
    sequence number so we don't collide after a server restart.
    """
    if prefix not in sequence_store:
        # Bootstrap from database
        from lambda_erp.database import get_db
        try:
            db = get_db()
            # Search all tables for names matching this prefix pattern
            tables = db.sql("SELECT name FROM sqlite_master WHERE type='table'")
            max_num = 0
            for t in tables:
                table_name = t["name"] if isinstance(t, dict) else t[0]
                try:
                    rows = db.sql(
                        f'SELECT name FROM "{table_name}" WHERE name LIKE ?',
                        [f"{prefix}-%"],
                    )
                    for row in rows:
                        n = row["name"] if isinstance(row, dict) else row[0]
                        try:
                            num = int(n.rsplit("-", 1)[-1])
                            max_num = max(max_num, num)
                        except (ValueError, IndexError):
                            pass
                except Exception:
                    pass
            sequence_store[prefix] = max_num
        except Exception:
            sequence_store[prefix] = 0

    count = sequence_store[prefix] + 1
    sequence_store[prefix] = count
    return f"{prefix}-{count:04d}"
