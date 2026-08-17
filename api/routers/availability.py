"""Rental availability endpoint.

Exposes the Reservation availability engine (lambda_erp/assets/reservation.py)
over REST so the booking form can check "is this free?" before saving and the
fleet calendar can render free vs booked. Read-only — posts nothing.

See docs/RENTAL_UI_PLAN.md (Phase 1). The overlap rule itself lives in one
place (reservation.overlapping_reservations); this router only surfaces it.
"""

from fastapi import APIRouter, Depends, Query, HTTPException

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.assets.reservation import (
    pool_capacity,
    committed_qty,
    available_qty,
    available_assets,
    overlapping_reservations,
)
from api.auth import require_role

router = APIRouter(
    prefix="/availability",
    tags=["availability"],
    dependencies=[Depends(require_role("viewer"))],
)


@router.get("")
def check_availability(
    item_code: str = Query(..., description="Item (machine type) to check."),
    from_datetime: str = Query(..., alias="from", description="Window start (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)."),
    to_datetime: str = Query(..., alias="to", description="Window end; half-open [from, to)."),
    warehouse: str | None = Query(None, description="Optional yard/warehouse to scope the pool."),
    exclude: str | None = Query(None, description="Reservation name to ignore (editing an existing hire)."),
):
    """Availability of an item over a window: total pool, how many are committed,
    how many are free, and which specific units are pickable.

    - `available_qty` counts free capacity (pooled + pinned commitments both
      reduce it).
    - `available_assets` are the units with no overlapping commitment of their
      own — what dispatch assigns from. A unit can be absent from this list yet
      `available_qty` still be > 0 (a pooled booking reduced the count without
      naming a unit); the form should respect both.
    """
    db = get_db()
    try:
        capacity = pool_capacity(db, item_code, warehouse)
        committed = committed_qty(db, item_code, warehouse, from_datetime, to_datetime, exclude)
        avail_qty = available_qty(db, item_code, warehouse, from_datetime, to_datetime, exclude)
        units = available_assets(db, item_code, warehouse, from_datetime, to_datetime, exclude)
        overlap = overlapping_reservations(
            db, from_dt=from_datetime, to_dt=to_datetime,
            item_code=item_code, warehouse=warehouse, exclude=exclude,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "from": from_datetime,
        "to": to_datetime,
        "capacity": capacity,
        "committed": committed,
        "available_qty": avail_qty,
        "available": avail_qty > 0,
        "available_assets": [
            {
                "name": a.get("name"),
                "asset_tag": a.get("asset_tag"),
                "warehouse": a.get("warehouse"),
                "status": a.get("status"),
                "meter_reading": a.get("meter_reading"),
            }
            for a in units
        ],
        "overlapping": overlap,
    }
