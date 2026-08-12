"""Asset — one row per physical unit of an asset-tracked Item.

The Item stays the *type* ("Kubota U 17 E", with its rate and pricing rules);
the Asset is the *unit* (the three machines you actually own, each with its own
plate number, home yard, and hour meter). Pricing, quoting and invoicing keep
keying off the Item, so nothing about the selling flow changes — only the
question "which one, and is it free?" becomes answerable.

Opt-in: an Item is only asset-tracked when `is_asset_tracked = 1`. The flag
defaults to 0, so every existing deployment behaves exactly as before after the
version bump — no receipt suddenly demands unit identities. Creating an Asset
against an Item that hasn't opted in is refused with a message that says how.

Nothing here posts to the GL or the Stock Ledger. An Asset is a record of a
thing you own, not a valuation entry; see docs/adr-0002-asset-and-reservation.md.
"""

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document

# Current state of the unit. This is the *now* status, deliberately NOT a
# window: "is it free on the 14th" is answered by the Reservation calendar, not
# by this field (see reservation.py). Retired units drop out of the pool.
ASSET_AVAILABLE = "Available"
ASSET_ON_HIRE = "On Hire"
ASSET_MAINTENANCE = "Maintenance"
ASSET_RETIRED = "Retired"

ASSET_STATUSES = [ASSET_AVAILABLE, ASSET_ON_HIRE, ASSET_MAINTENANCE, ASSET_RETIRED]


# The "Asset" table itself is declared with every other core table in
# lambda_erp/database.py — one source of truth for the schema.


def is_asset_tracked(db, item_code: str) -> bool:
    """True when the Item has opted in to unit-level tracking.

    Reads through get_value (never a hand-written SELECT of a maybe-missing
    column) so it stays correct on both backends — on SQLite an unknown column
    silently returns its own name as a string rather than raising (see
    docs/agents/gotchas.md)."""
    row = db.get_value("Item", item_code, ["is_asset_tracked"])
    if not row:
        return False
    return bool(row.get("is_asset_tracked"))


class Asset(Document):
    DOCTYPE = "Asset"
    CHILD_TABLES = {}
    PREFIX = "ASSET"
    LINK_FIELDS = {
        "item_code": "Item",
        "warehouse": "Warehouse",
        "company": "Company",
    }

    def validate(self):
        if not self.item_code:
            raise ValidationError("Item Code is required")

        db = get_db()
        # _validate_links proves the Item exists (it runs after validate), but
        # the opt-in check needs the row now and must give the better message.
        if not db.exists("Item", self.item_code):
            raise ValidationError(
                f"Asset: Item Code '{self.item_code}' does not exist in Item"
            )
        if not is_asset_tracked(db, self.item_code):
            raise ValidationError(
                f"Item '{self.item_code}' is not asset-tracked. Set "
                f"is_asset_tracked = 1 on the Item before creating Assets for it."
            )

        status = self._data.get("status") or ASSET_AVAILABLE
        if status not in ASSET_STATUSES:
            raise ValidationError(
                f"Invalid asset status '{status}' — must be one of "
                f"{', '.join(ASSET_STATUSES)}"
            )
        self._data["status"] = status

        # An asset tag is the plate / manufacturer serial. Optional, but when
        # given it identifies exactly one unit — a duplicate means someone
        # entered the same machine twice, which would double the pool capacity.
        tag = (self._data.get("asset_tag") or "").strip()
        if tag:
            self._data["asset_tag"] = tag
            clash = db.sql(
                'SELECT name FROM "Asset" WHERE asset_tag = ? AND name != ? '
                "AND COALESCE(discarded, 0) = 0 LIMIT 1",
                [tag, self.name],
            )
            if clash:
                raise ValidationError(
                    f"Asset tag '{tag}' is already used by {clash[0]['name']}"
                )

        # Default the display name so lists and link fields read sensibly
        # without forcing the caller to invent one.
        if not self._data.get("asset_name"):
            self._data["asset_name"] = f"{self.item_code} {tag}".strip()

        # Fall back to the item's default warehouse so a single-yard deployment
        # never has to think about locations.
        if not self._data.get("warehouse"):
            item = db.get_value("Item", self.item_code, ["default_warehouse"])
            if item and item.get("default_warehouse"):
                self._data["warehouse"] = item["default_warehouse"]


def usable_assets(db, item_code: str, warehouse: str | None = None) -> list:
    """Assets of an item that can still be hired out — i.e. the pool.

    Retired, disabled and discarded units are excluded. `Maintenance` is NOT
    excluded: it describes the unit *today*, and this function answers a
    question about a future window. Blocking a machine for a service slot is a
    Reservation with no party, so that one calendar governs every window.
    """
    # COALESCE rather than a `{"disabled": 0}` filter: `disabled = 0` does not
    # match NULL, and a row written by a plugin or a raw INSERT may leave the
    # flag unset. Portable across SQLite and Postgres.
    sql = (
        'SELECT name, asset_name, asset_tag, warehouse, status FROM "Asset" '
        "WHERE item_code = ? AND COALESCE(disabled, 0) = 0 "
        "AND COALESCE(discarded, 0) = 0 AND COALESCE(status, '') != ?"
    )
    params = [item_code, ASSET_RETIRED]
    if warehouse:
        sql += " AND warehouse = ?"
        params.append(warehouse)
    return db.sql(sql + " ORDER BY name", params)
