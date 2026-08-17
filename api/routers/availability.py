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


@router.get("/calendar")
def calendar_feed(
    from_datetime: str = Query(..., alias="from", description="Window start (YYYY-MM-DD or with time)."),
    to_datetime: str = Query(..., alias="to", description="Window end; half-open [from, to)."),
    warehouse: str | None = Query(None, description="Optional yard/warehouse to scope both lanes and bars."),
    item_code: str | None = Query(None, description="Optional item (machine type) to scope to."),
):
    """Feed for the fleet availability timeline: the asset lanes (rows) plus the
    reservations that occupy them within the window (bars).

    - `assets` are the active, non-retired units (optionally scoped by yard /
      item) — the timeline's Y axis.
    - `reservations` are the blocking hires overlapping [from, to) — the bars —
      via the single canonical overlap rule (overlapping_reservations), enriched
      with the party for a bar label. Pooled bookings (no `asset`) carry a null
      `asset`; the caller renders them on an item lane or a "pool" row.
    """
    db = get_db()

    asset_filters: dict = {"disabled": 0}
    if warehouse:
        asset_filters["warehouse"] = warehouse
    if item_code:
        asset_filters["item_code"] = item_code
    assets = [
        {
            "name": a.get("name"),
            "asset_tag": a.get("asset_tag"),
            "item_code": a.get("item_code"),
            "warehouse": a.get("warehouse"),
            "status": a.get("status"),
            "meter_reading": a.get("meter_reading"),
        }
        for a in db.get_all("Asset", filters=asset_filters, fields=["*"], order_by="item_code, asset_tag")
        if a.get("status") != "Retired"
    ]

    try:
        rows = overlapping_reservations(
            db, from_dt=from_datetime, to_dt=to_datetime,
            item_code=item_code, warehouse=warehouse,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    reservations = []
    for r in rows:
        extra = db.get_value("Reservation", r["name"], ["party_type", "party", "purpose"]) or {}
        reservations.append({
            "name": r.get("name"),
            "item_code": r.get("item_code"),
            "asset": r.get("asset"),
            "warehouse": r.get("warehouse"),
            "qty": r.get("qty"),
            "from_datetime": r.get("from_datetime"),
            "to_datetime": r.get("to_datetime"),
            "status": r.get("status"),
            "party_type": extra.get("party_type"),
            "party": extra.get("party"),
            "purpose": extra.get("purpose"),
        })

    return {
        "from": from_datetime,
        "to": to_datetime,
        "warehouse": warehouse,
        "item_code": item_code,
        "assets": assets,
        "reservations": reservations,
    }
