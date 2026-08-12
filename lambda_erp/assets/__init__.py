"""Unit-level asset identity and date-ranged reservations.

Deliberately NOT part of `lambda_erp/stock/`. Stock models quantities that are
consumed — you buy 10, sell 3, and the ledger moves the value out as COGS. An
Asset is an owned unit that goes out and comes back: it is never consumed, so
nothing here touches the Stock Ledger or the General Ledger.

Two pieces:

- `asset.py` — the Asset master: one row per physical unit, pointing at the
  Item that describes its *type* (and carries its pricing).
- `reservation.py` — the Reservation primitive: a half-open time window that
  commits either a specific Asset or `qty` units from an item+warehouse pool,
  plus the availability engine that answers "what's free between X and Y".

See docs/adr-0002-asset-and-reservation.md for why it's shaped this way.
"""

from lambda_erp.assets.asset import (
    Asset, ASSET_STATUSES, ASSET_RETIRED, usable_assets,
)
from lambda_erp.assets.reservation import (
    Reservation, RESERVATION_STATUSES, BLOCKING_STATUSES,
    available_assets, available_qty, committed_qty, overlapping_reservations,
    pool_capacity, release_reservations,
)

__all__ = [
    "Asset", "ASSET_STATUSES", "ASSET_RETIRED", "usable_assets",
    "Reservation", "RESERVATION_STATUSES", "BLOCKING_STATUSES",
    "available_assets", "available_qty", "committed_qty",
    "overlapping_reservations", "pool_capacity", "release_reservations",
]
