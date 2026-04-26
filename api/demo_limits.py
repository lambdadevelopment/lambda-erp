"""Token-spend rate limiting for the public demo.

Two sliding 1-hour windows guard the demo budget:
  * global — caps total USD spend across all visitors per hour
  * per-IP — caps a single client IP per hour

Default thresholds: $10/hr global ($240/day). The per-IP cap defaults
to ~$0.52/hr (the previous absolute cap, i.e. 25% of the old $50/day
budget) and is also clamped to at most 25% of the global cap so one
actor cannot monopolize it.

Only `public_manager` traffic counts against the global cap (that's
demo traffic); admin/manager sessions are still logged but exempt.

Every LLM call is persisted to the `Demo Spend Log` table so admins can
see per-window breakdowns (1h/2h/4h/12h/24h/7d) via
/api/admin/demo-spend. Single-replica deploy is the hard prerequisite
for this design — the in-process SQLite file is the single source of
truth. If the container ever scales horizontally, swap the store.

Pricing lives in `api.providers`; this module only owns persistence and
limit checks.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from lambda_erp.database import get_db


_WINDOW_SECONDS = 3600  # 1 hour

# Upper bound on how long a reservation may hold budget before it's
# considered stale and ignored. Must exceed the longest provider call
# timeout (OpenAI/Anthropic clients use 120s) plus a fudge for the
# event-loop latency between the SDK call returning and settle() firing.
# Defence-in-depth against cancellation leaks that slip past try/finally.
_RESERVATION_TTL_SECONDS = 180


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS "Demo Spend Log" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ip TEXT,
    role TEXT,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL NOT NULL,
    session_id TEXT
);
"""
_INDEX_TS_SQL = 'CREATE INDEX IF NOT EXISTS "idx_demo_spend_ts" ON "Demo Spend Log" (ts)'
_INDEX_IP_TS_SQL = 'CREATE INDEX IF NOT EXISTS "idx_demo_spend_ip_ts" ON "Demo Spend Log" (ip, ts)'

_schema_lock = threading.Lock()
_schema_ready = False


@dataclass
class _Reservation:
    ip: str
    role: str
    estimated_usd: float
    created_at: float


def init_schema() -> None:
    """Create the spend-log table. Safe to call repeatedly; guarded by a
    module-level flag so we don't re-issue DDL on every request."""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        db = get_db()
        db.conn.execute(_SCHEMA_SQL)
        db.conn.execute(_INDEX_TS_SQL)
        db.conn.execute(_INDEX_IP_TS_SQL)
        db.conn.commit()
        _schema_ready = True


class DemoSpendLimiter:
    """SQLite-backed spend tracker with sliding-window caps.

    `check()` reads SUM(cost_usd) over the last hour for both the global
    demo bucket (role=public_manager) and the caller's IP; `record()`
    appends one row per LLM call for both limiting and analytics.
    """

    def __init__(self, global_hourly_usd: float, per_ip_hourly_usd: float):
        self.global_hourly_usd = global_hourly_usd
        self.per_ip_hourly_usd = per_ip_hourly_usd
        self._reservation_lock = threading.Lock()
        self._next_reservation_id = 1
        self._reservations: dict[int, _Reservation] = {}

    def _reserved_totals_unlocked(self, ip: str) -> tuple[float, float]:
        # Opportunistically evict stale reservations. This is the only
        # path that totals reservations, so piggy-backing the sweep keeps
        # the lock count down without needing a separate background task.
        now = time.time()
        cutoff = now - _RESERVATION_TTL_SECONDS
        stale = [rid for rid, row in self._reservations.items() if row.created_at < cutoff]
        for rid in stale:
            self._reservations.pop(rid, None)

        global_reserved = 0.0
        ip_reserved = 0.0
        for row in self._reservations.values():
            if row.role != "public_manager":
                continue
            global_reserved += row.estimated_usd
            if row.ip == ip:
                ip_reserved += row.estimated_usd
        return global_reserved, ip_reserved

    # ---- checks --------------------------------------------------------

    def check(self, ip: str) -> Optional[str]:
        """Return a human-readable reason if `ip` (or the global demo
        bucket) has crossed its hourly cap, else None. Admin/manager
        traffic is never blocked here — that decision is at the call
        site via `is_demo_role(role)`."""
        init_schema()
        db = get_db()
        cutoff = time.time() - _WINDOW_SECONDS

        global_spend = db.conn.execute(
            'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM "Demo Spend Log" '
            'WHERE ts > ? AND role = ?',
            (cutoff, "public_manager"),
        ).fetchone()["s"]
        if global_spend >= self.global_hourly_usd:
            return (
                f"Demo budget exhausted for this hour "
                f"(~${global_spend:.2f} / ${self.global_hourly_usd:.2f}). "
                "Please try again later."
            )

        ip_spend = db.conn.execute(
            'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM "Demo Spend Log" '
            'WHERE ts > ? AND ip = ? AND role = ?',
            (cutoff, ip, "public_manager"),
        ).fetchone()["s"]
        if ip_spend >= self.per_ip_hourly_usd:
            return (
                f"You've reached the hourly demo limit for your IP "
                f"(~${ip_spend:.2f} / ${self.per_ip_hourly_usd:.2f}). "
                "Please try again later."
            )
        return None

    def reserve(
        self,
        ip: str,
        *,
        estimated_usd: float,
        role: str | None = None,
    ) -> tuple[Optional[str], Optional[int]]:
        """Atomically reserve budget before an outbound LLM call."""
        amount = max(float(estimated_usd or 0.0), 0.0)
        if amount <= 0:
            return None, None
        if role != "public_manager":
            return None, None

        init_schema()
        db = get_db()
        cutoff = time.time() - _WINDOW_SECONDS
        with self._reservation_lock:
            global_spend = db.conn.execute(
                'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM "Demo Spend Log" '
                'WHERE ts > ? AND role = ?',
                (cutoff, "public_manager"),
            ).fetchone()["s"]
            ip_spend = db.conn.execute(
                'SELECT COALESCE(SUM(cost_usd), 0) AS s FROM "Demo Spend Log" '
                'WHERE ts > ? AND ip = ? AND role = ?',
                (cutoff, ip, "public_manager"),
            ).fetchone()["s"]
            reserved_global, reserved_ip = self._reserved_totals_unlocked(ip)

            projected_global = float(global_spend) + reserved_global + amount
            if projected_global > self.global_hourly_usd:
                return (
                    f"Demo budget exhausted for this hour "
                    f"(~${global_spend + reserved_global:.2f} / ${self.global_hourly_usd:.2f}). "
                    "Please try again later.",
                    None,
                )

            projected_ip = float(ip_spend) + reserved_ip + amount
            if projected_ip > self.per_ip_hourly_usd:
                return (
                    f"You've reached the hourly demo limit for your IP "
                    f"(~${ip_spend + reserved_ip:.2f} / ${self.per_ip_hourly_usd:.2f}). "
                    "Please try again later.",
                    None,
                )

            reservation_id = self._next_reservation_id
            self._next_reservation_id += 1
            self._reservations[reservation_id] = _Reservation(
                ip=ip,
                role=role or "",
                estimated_usd=amount,
                created_at=time.time(),
            )
            return None, reservation_id

    def release(self, reservation_id: int | None) -> None:
        """Drop a reservation without recording spend (failed call path)."""
        if reservation_id is None:
            return
        with self._reservation_lock:
            self._reservations.pop(reservation_id, None)

    def settle(
        self,
        reservation_id: int | None,
        *,
        actual_cost_usd: float,
        ip: str,
        role: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        session_id: str | None = None,
    ) -> None:
        """Finalize a reservation and persist the actual spend row."""
        if reservation_id is not None:
            with self._reservation_lock:
                self._reservations.pop(reservation_id, None)
        self.record(
            ip,
            actual_cost_usd,
            role=role,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            session_id=session_id,
        )

    # ---- recording -----------------------------------------------------

    def record(
        self,
        ip: str,
        cost_usd: float,
        *,
        role: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        session_id: str | None = None,
    ) -> None:
        if cost_usd <= 0:
            return
        init_schema()
        db = get_db()
        db.conn.execute(
            'INSERT INTO "Demo Spend Log" '
            '(ts, ip, role, provider, model, prompt_tokens, completion_tokens, cost_usd, session_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                time.time(),
                ip,
                role,
                provider,
                model,
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                float(cost_usd),
                session_id,
            ),
        )
        db.conn.commit()

    # ---- reporting -----------------------------------------------------

    def snapshot(self) -> dict:
        """Lightweight health snapshot — for logs / live dashboards."""
        init_schema()
        db = get_db()
        cutoff = time.time() - _WINDOW_SECONDS
        row = db.conn.execute(
            'SELECT COALESCE(SUM(cost_usd), 0) AS s, '
            'COUNT(DISTINCT ip) AS ips '
            'FROM "Demo Spend Log" WHERE ts > ? AND role = ?',
            (cutoff, "public_manager"),
        ).fetchone()
        return {
            "global_hourly_usd": round(row["s"], 4),
            "global_hourly_cap": self.global_hourly_usd,
            "per_ip_hourly_cap": self.per_ip_hourly_usd,
            "active_ips": int(row["ips"] or 0),
        }


# ---------------------------------------------------------------------------
# Process-wide singleton. Thresholds are read once at import time from env.
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# $10/hr → $240/day cap.
_GLOBAL_HOURLY_USD = _env_float("LAMBDA_ERP_DEMO_GLOBAL_HOURLY_USD", 10.0)
# Pinned to the previous absolute default (25% of the old $50/day
# global ≈ $0.521/hr) so raising the global cap does not also raise
# what a single IP can spend.
_PER_IP_HOURLY_USD = _env_float(
    "LAMBDA_ERP_DEMO_PER_IP_HOURLY_USD",
    (50.0 / 24.0) * 0.25,
)
_PER_IP_HOURLY_USD = min(_PER_IP_HOURLY_USD, _GLOBAL_HOURLY_USD * 0.25)

limiter = DemoSpendLimiter(
    global_hourly_usd=_GLOBAL_HOURLY_USD,
    per_ip_hourly_usd=_PER_IP_HOURLY_USD,
)


def is_demo_role(role: Optional[str]) -> bool:
    """Limits only apply to the shared demo account, not admins/managers."""
    return role == "public_manager"


def demo_max_completion_tokens(default: int = 4096) -> int:
    """Env-overridable cap for `max_completion_tokens` on demo calls.

    Defaults to 1024 — tight enough to bound worst-case turn cost but
    generous enough for typical replies. Callers apply this only for
    `public_manager`; logged-in managers/admins keep the original cap."""
    raw = os.environ.get("LAMBDA_ERP_DEMO_MAX_COMPLETION_TOKENS")
    if raw is None or raw == "":
        return 1024
    try:
        return max(256, int(raw))
    except ValueError:
        return 1024


def demo_call_reserve_usd() -> float:
    """Conservative pre-call reservation for demo LLM requests."""
    raw = os.environ.get("LAMBDA_ERP_DEMO_CALL_RESERVE_USD")
    if raw is None or raw == "":
        return 0.35
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 0.35


def demo_max_message_chars() -> int:
    """Env-overridable cap on the length of a single chat message from a
    public_manager session. Without this, a visitor could paste ~100k
    characters and burn the global hourly budget in a single call even
    though the per-call max_completion_tokens is tight — input tokens
    aren't capped server-side otherwise."""
    raw = os.environ.get("LAMBDA_ERP_DEMO_MAX_MESSAGE_CHARS")
    if raw is None or raw == "":
        return 300
    try:
        return max(50, int(raw))
    except ValueError:
        return 300


def demo_max_attachment_bytes() -> int:
    """Env-overridable cap on the size of a single chat attachment uploaded
    from a public_manager session. Default 100 KiB. Images/PDFs are sent
    to the LLM as base64 multimodal parts, so each uploaded byte becomes
    ~1.33 bytes of prompt — a 10 MB file would otherwise blow the hourly
    budget in one call."""
    raw = os.environ.get("LAMBDA_ERP_DEMO_MAX_ATTACHMENT_BYTES")
    if raw is None or raw == "":
        return 100 * 1024
    try:
        return max(1024, int(raw))
    except ValueError:
        return 100 * 1024
