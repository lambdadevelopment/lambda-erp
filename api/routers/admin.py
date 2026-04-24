"""Admin-only observability endpoints.

Currently exposes the demo spend overview. Add future admin-only
reports here to keep `require_admin`-gated routes together.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from api.auth import require_admin
from api.demo_limits import init_schema, limiter
from lambda_erp.database import get_db


router = APIRouter(prefix="/admin", tags=["admin"])


# Fixed windows the UI offers. Ordered for display.
_WINDOWS = [
    ("1h", 3600),
    ("2h", 2 * 3600),
    ("4h", 4 * 3600),
    ("12h", 12 * 3600),
    ("24h", 24 * 3600),
    ("7d", 7 * 24 * 3600),
]


def _window_totals(now: float, seconds: int) -> dict:
    db = get_db()
    cutoff = now - seconds

    totals = db.conn.execute(
        'SELECT '
        '  COALESCE(SUM(cost_usd), 0) AS total_usd, '
        '  COALESCE(SUM(CASE WHEN role = "public_manager" THEN cost_usd ELSE 0 END), 0) AS demo_usd, '
        '  COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, '
        '  COALESCE(SUM(completion_tokens), 0) AS completion_tokens, '
        '  COUNT(*) AS call_count, '
        '  COUNT(DISTINCT ip) AS unique_ips '
        'FROM "Demo Spend Log" WHERE ts > ?',
        (cutoff,),
    ).fetchone()

    by_provider_rows = db.conn.execute(
        'SELECT provider, '
        '  COALESCE(SUM(cost_usd), 0) AS cost_usd, '
        '  COUNT(*) AS call_count, '
        '  COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, '
        '  COALESCE(SUM(completion_tokens), 0) AS completion_tokens '
        'FROM "Demo Spend Log" WHERE ts > ? GROUP BY provider',
        (cutoff,),
    ).fetchall()

    by_provider = {
        (row["provider"] or "unknown"): {
            "cost_usd": round(row["cost_usd"], 6),
            "call_count": int(row["call_count"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
        }
        for row in by_provider_rows
    }

    return {
        "total_usd": round(totals["total_usd"], 6),
        "demo_usd": round(totals["demo_usd"], 6),
        "prompt_tokens": int(totals["prompt_tokens"]),
        "completion_tokens": int(totals["completion_tokens"]),
        "call_count": int(totals["call_count"]),
        "unique_ips": int(totals["unique_ips"] or 0),
        "by_provider": by_provider,
    }


@router.get("/demo-spend")
def demo_spend_overview(_user: dict = Depends(require_admin)):
    """Per-window spend breakdown, caps, and top-10 IPs over 24h.

    Only `public_manager` spend counts against the demo cap — `demo_usd`
    in each window is the number the global cap compares against. Non-
    demo calls (your own admin/manager sessions) are included in
    `total_usd` so you can see the full OpenAI + Anthropic bill from this
    deployment.
    """
    init_schema()
    now = time.time()

    windows = {name: _window_totals(now, seconds) for name, seconds in _WINDOWS}

    db = get_db()
    top_ip_rows = db.conn.execute(
        'SELECT ip, role, '
        '  COALESCE(SUM(cost_usd), 0) AS cost_usd, '
        '  COUNT(*) AS call_count '
        'FROM "Demo Spend Log" WHERE ts > ? '
        'GROUP BY ip, role '
        'ORDER BY cost_usd DESC '
        'LIMIT 10',
        (now - 24 * 3600,),
    ).fetchall()
    top_ips_24h = [
        {
            "ip": row["ip"] or "unknown",
            "role": row["role"],
            "cost_usd": round(row["cost_usd"], 6),
            "call_count": int(row["call_count"]),
        }
        for row in top_ip_rows
    ]

    return {
        "caps": {
            "global_hourly_usd": limiter.global_hourly_usd,
            "per_ip_hourly_usd": limiter.per_ip_hourly_usd,
        },
        "windows": windows,
        "top_ips_24h": top_ips_24h,
    }
