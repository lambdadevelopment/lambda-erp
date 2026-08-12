"""Reservation — a date-ranged commitment against an Asset or an item pool.

This is the primitive the rest of the system was missing. `Bin.reserved_qty`
already tracks *how many* units a Sales Order has spoken for, but it carries no
dates, so it can never answer "is a machine free from the 14th to the 19th".
Reservation is that answer, and it deliberately sits beside Bin rather than
replacing it: Bin stays the Sales Order's stock semantic, this is the calendar.

Two levels, because that's how hire actually works:

- **pooled** — `item_code` + `warehouse` + `qty`: "*a* 1.7 t excavator out of
  St. Gallen, 14.–19.". What you commit at quote and order time, when nobody
  cares which unit.
- **unit** — `asset` set: "*that* machine". Assigned at dispatch, or earlier
  when the customer insists on a specific one.

A unit reservation also consumes one slot of its pool, so the two levels stay
consistent: booking three machines pooled and pinning a fourth is four of your
capacity, not one plus three.

Windows are **half-open**: `[from, to)`. A hire ending 09:00 and the next
starting 09:00 do not collide, which is the behaviour a dispatcher expects.

Nothing here posts to the GL or the Stock Ledger. A commitment is not a
transaction — the invoice that eventually bills the hire is, and that flows
through the ordinary selling cycle untouched.
"""

import datetime

from lambda_erp.assets.asset import ASSET_RETIRED, is_asset_tracked, usable_assets
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document
from lambda_erp.utils import flt

RESERVED = "Reserved"
OUT = "Out"
RETURNED = "Returned"
CANCELLED = "Cancelled"

RESERVATION_STATUSES = [RESERVED, OUT, RETURNED, CANCELLED]

# Only these hold the calendar. Returned/Cancelled rows stay for the audit
# trail but stop blocking, so a released window is immediately re-bookable.
BLOCKING_STATUSES = (RESERVED, OUT)

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"


# The "Reservation" table is declared with every other core table in
# lambda_erp/database.py — one source of truth for the schema.


def normalize_datetime(value, field="datetime") -> str:
    """Normalise to a fixed-width `YYYY-MM-DD HH:MM:SS` string.

    Every stored bound goes through this, which is what lets the overlap test
    be a plain string comparison — fixed-width ISO sorts lexicographically, and
    TEXT comparison behaves identically on SQLite and Postgres. A bare date
    means midnight, so `2026-08-14` and `2026-08-14 00:00:00` are one instant
    rather than two incomparable strings.

    Any timezone suffix is dropped: a hire window is wall-clock time at the
    yard, not an instant on a global timeline.
    """
    if value is None or value == "":
        raise ValidationError(f"{field} is required")
    if isinstance(value, datetime.datetime):
        return value.strftime(_DT_FORMAT)
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day).strftime(_DT_FORMAT)

    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1].strip()
    # Strip a trailing +02:00 / -05:00 offset without touching the date's own
    # hyphens (offsets only ever appear after the time part).
    for sep in ("+", "-"):
        idx = text.find(sep, 11)
        if idx > 0:
            text = text[:idx].strip()
            break
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValidationError(
            f"Invalid {field} '{value}' — expected YYYY-MM-DD or "
            f"YYYY-MM-DD HH:MM:SS"
        )
    return parsed.strftime(_DT_FORMAT)


class Reservation(Document):
    DOCTYPE = "Reservation"
    CHILD_TABLES = {}
    PREFIX = "RES"
    LINK_FIELDS = {
        "item_code": "Item",
        "warehouse": "Warehouse",
        "asset": "Asset",
        "company": "Company",
    }
    DYNAMIC_LINK_FIELDS = {
        "party": ("party_type", {"Customer": "Customer", "Supplier": "Supplier"}),
    }

    def validate(self):
        db = get_db()

        status = self._data.get("status") or RESERVED
        if status not in RESERVATION_STATUSES:
            raise ValidationError(
                f"Invalid reservation status '{status}' — must be one of "
                f"{', '.join(RESERVATION_STATUSES)}"
            )
        self._data["status"] = status

        from_dt = normalize_datetime(self._data.get("from_datetime"), "From Datetime")
        to_dt = normalize_datetime(self._data.get("to_datetime"), "To Datetime")
        if from_dt >= to_dt:
            raise ValidationError(
                f"To Datetime ({to_dt}) must be after From Datetime ({from_dt})"
            )
        self._data["from_datetime"] = from_dt
        self._data["to_datetime"] = to_dt

        asset = self._data.get("asset")
        if asset:
            self._resolve_from_asset(db, asset)
        elif not self._data.get("item_code"):
            raise ValidationError("Item Code is required (or set an Asset)")

        item_code = self._data["item_code"]
        if not db.exists("Item", item_code):
            raise ValidationError(
                f"Reservation: Item Code '{item_code}' does not exist in Item"
            )
        # Capacity is counted from Asset rows, so an item with no units has no
        # pool to reserve against. Pooled reservation of ordinary (untracked)
        # stock is a different feature — see the ADR's scope note.
        if not is_asset_tracked(db, item_code):
            raise ValidationError(
                f"Item '{item_code}' is not asset-tracked. Set is_asset_tracked = 1 "
                f"on the Item before reserving it."
            )

        if not self._data.get("warehouse"):
            item = db.get_value("Item", item_code, ["default_warehouse"])
            if item and item.get("default_warehouse"):
                self._data["warehouse"] = item["default_warehouse"]
        if not self._data.get("warehouse"):
            raise ValidationError(
                "Warehouse is required — set it on the Reservation, on the Asset, "
                "or as the Item's default warehouse"
            )

        qty = flt(self._data.get("qty") or (1 if asset else 0))
        if asset:
            # A pinned unit is exactly one machine; anything else would
            # double-count it against the pool.
            qty = 1
        elif qty <= 0:
            raise ValidationError("Qty must be greater than zero")
        self._data["qty"] = qty

        if status in BLOCKING_STATUSES:
            self._check_availability(db, from_dt, to_dt)

    def _resolve_from_asset(self, db, asset: str) -> None:
        """Pin the reservation to one unit, inheriting its item and location."""
        row = db.get_value(
            "Asset", asset, ["item_code", "warehouse", "status", "disabled"]
        )
        if not row:
            raise ValidationError(
                f"Reservation: Asset '{asset}' does not exist in Asset"
            )
        if row.get("status") == ASSET_RETIRED or row.get("disabled"):
            raise ValidationError(
                f"Asset {asset} is retired or disabled and cannot be reserved"
            )
        declared = self._data.get("item_code")
        if declared and declared != row.get("item_code"):
            raise ValidationError(
                f"Asset {asset} is a '{row.get('item_code')}', not a '{declared}'"
            )
        self._data["item_code"] = row.get("item_code")
        if not self._data.get("warehouse") and row.get("warehouse"):
            self._data["warehouse"] = row["warehouse"]

    def _check_availability(self, db, from_dt: str, to_dt: str) -> None:
        """Refuse a double-booking, at whichever level this reservation binds."""
        asset = self._data.get("asset")
        item_code = self._data["item_code"]
        warehouse = self._data["warehouse"]

        if asset:
            clash = overlapping_reservations(
                db, asset=asset, from_dt=from_dt, to_dt=to_dt, exclude=self.name
            )
            if clash:
                first = clash[0]
                raise ValidationError(
                    f"Asset {asset} is already committed from "
                    f"{first['from_datetime']} to {first['to_datetime']} "
                    f"by {first['name']}"
                )

        capacity = pool_capacity(db, item_code, warehouse)
        if capacity <= 0:
            raise ValidationError(
                f"No assets available for '{item_code}' at {warehouse} — "
                f"create Asset records for its units first"
            )
        committed = committed_qty(
            db, item_code, warehouse, from_dt, to_dt, exclude=self.name
        )
        wanted = flt(self._data.get("qty") or 1)
        if committed + wanted > capacity:
            raise ValidationError(
                f"Only {max(0.0, capacity - committed):g} of {capacity:g} "
                f"'{item_code}' free at {warehouse} between {from_dt} and "
                f"{to_dt} — {wanted:g} requested"
            )


# --- Availability engine -------------------------------------------------
# The whole overlap rule, in one place: two half-open windows collide when
# `existing.from < new.to AND existing.to > new.from`. Touching ends don't.


def overlapping_reservations(db, *, from_dt, to_dt, item_code=None,
                             warehouse=None, asset=None, exclude=None,
                             statuses=BLOCKING_STATUSES) -> list:
    """Blocking reservations that overlap the window, narrowed by whichever of
    asset / item+warehouse was given. `exclude` skips a row by name so editing
    a reservation never collides with itself."""
    from_dt = normalize_datetime(from_dt, "From Datetime")
    to_dt = normalize_datetime(to_dt, "To Datetime")

    placeholders = ", ".join(["?"] * len(statuses))
    sql = (
        'SELECT name, item_code, warehouse, asset, qty, from_datetime, '
        'to_datetime, status, voucher_type, voucher_no FROM "Reservation" '
        f"WHERE status IN ({placeholders}) AND COALESCE(discarded, 0) = 0 "
        "AND from_datetime < ? AND to_datetime > ?"
    )
    params = list(statuses) + [to_dt, from_dt]

    if asset:
        sql += " AND asset = ?"
        params.append(asset)
    if item_code:
        sql += " AND item_code = ?"
        params.append(item_code)
    if warehouse:
        sql += " AND warehouse = ?"
        params.append(warehouse)
    if exclude:
        sql += " AND name != ?"
        params.append(exclude)

    return db.sql(sql + " ORDER BY from_datetime", params)


def pool_capacity(db, item_code: str, warehouse: str | None = None) -> float:
    """How many units of an item could be hired out of a location at all."""
    return float(len(usable_assets(db, item_code, warehouse)))


def committed_qty(db, item_code: str, warehouse: str | None, from_dt, to_dt,
                  exclude=None) -> float:
    """Units already spoken for in the window — pooled and pinned together."""
    rows = overlapping_reservations(
        db, item_code=item_code, warehouse=warehouse,
        from_dt=from_dt, to_dt=to_dt, exclude=exclude,
    )
    return sum(flt(r.get("qty") or 1) for r in rows)


def available_qty(db, item_code: str, warehouse: str | None, from_dt, to_dt,
                  exclude=None) -> float:
    """Free units of an item at a location across the whole window."""
    capacity = pool_capacity(db, item_code, warehouse)
    committed = committed_qty(db, item_code, warehouse, from_dt, to_dt, exclude)
    return max(0.0, capacity - committed)


def available_assets(db, item_code: str, warehouse: str | None, from_dt, to_dt,
                     exclude=None) -> list:
    """The specific units free for the whole window — what dispatch picks from.

    Pooled reservations reduce the *count* available without naming a unit, so
    this deliberately reports units with no overlapping commitment of their
    own; callers assigning a machine should also respect `available_qty`.
    """
    pinned = {
        r["asset"]
        for r in overlapping_reservations(
            db, item_code=item_code, warehouse=warehouse,
            from_dt=from_dt, to_dt=to_dt, exclude=exclude,
        )
        if r.get("asset")
    }
    return [a for a in usable_assets(db, item_code, warehouse) if a["name"] not in pinned]


def release_reservations(db, voucher_type: str, voucher_no: str,
                         status: str = CANCELLED) -> int:
    """Release every blocking reservation held by a document, freeing the window.

    The hook a cancelled order or a completed return calls. Writes the status
    directly rather than round-tripping save(): re-validating would re-run the
    availability check against the row's own window, and releasing a booking
    should never be refusable.
    """
    if status not in RESERVATION_STATUSES:
        raise ValidationError(f"Invalid reservation status '{status}'")
    placeholders = ", ".join(["?"] * len(BLOCKING_STATUSES))
    rows = db.sql(
        f'SELECT name FROM "Reservation" WHERE voucher_type = ? AND voucher_no = ? '
        f"AND status IN ({placeholders})",
        [voucher_type, voucher_no] + list(BLOCKING_STATUSES),
    )
    for row in rows:
        db.set_value("Reservation", row["name"], {"status": status})
    return len(rows)
