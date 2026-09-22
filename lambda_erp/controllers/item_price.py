"""
Price List and Item Price — the base-price layer.

Item.standard_rate is a single number per item: one price, every customer,
every currency. That is the floor, not the model. A Price List names a set of
prices in one currency; Item Price rows give an item's rate within a list,
optionally narrowed to one party and a quantity band and a validity window.

Resolution order for a line with no supplied rate, most specific first:

  1. Item Price for (item, list, this party)
  2. Item Price for (item, list)
  3. Item.standard_rate

With no Price List configured anywhere, step 3 is reached immediately and
pricing behaves exactly as it did before this layer existed.

Currency is matched, never converted. A rate means nothing apart from the
currency it is quoted in, and converting at pricing time would make the number
depend on the exchange rate that happened to be live when someone saved the
draft — which `apply_pricing_rules` re-runs on every save, so it would not even
be stable within one document. A customer trading in two currencies gets one
Price List per currency.
"""

from lambda_erp.model import Document
from lambda_erp.utils import flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError


class PriceList(Document):
    DOCTYPE = "Price List"
    CHILD_TABLES = {}
    PREFIX = "PL"
    REQUIRED_FIELDS = ("price_list_name", "currency")
    CONDITIONAL_REQUIREMENTS = (
        'A Price List holds rates in exactly one currency. Resolution requires it to match the document currency; rates are never converted. Use one list per currency.',
        'At least one of Selling or Buying must be enabled.',
    )

    def validate(self):
        if not self.price_list_name:
            raise ValidationError("Price List Name is required")
        if not self.currency:
            raise ValidationError("Currency is required")
        if not flt(self._data.get("selling")) and not flt(self._data.get("buying")):
            raise ValidationError("At least one of Selling or Buying must be enabled")


class ItemPrice(Document):
    DOCTYPE = "Item Price"
    CHILD_TABLES = {}
    PREFIX = "IPRICE"
    REQUIRED_FIELDS = ("item_code", "price_list", "rate")
    LINK_FIELDS = {
        'item_code': 'Item',
        'price_list': 'Price List',
        'customer': 'Customer',
        'supplier': 'Supplier',
    }
    CONDITIONAL_REQUIREMENTS = (
        'A row without a customer/supplier is the list price for that item. A row naming a party overrides it for that party only.',
        'min_qty sets a volume break: the highest min_qty at or below the line quantity wins.',
        'A customer may only be set on a selling Price List, a supplier only on a buying one.',
    )

    def validate(self):
        if not self.item_code:
            raise ValidationError("Item Code is required")
        if not self.price_list:
            raise ValidationError("Price List is required")
        if flt(self.min_qty) < 0:
            raise ValidationError("Min Qty cannot be negative")
        if flt(self.rate) < 0:
            raise ValidationError("Rate cannot be negative")
        if self.customer and self.supplier:
            raise ValidationError("An Item Price names either a customer or a supplier, not both")

        db = get_db()
        pl = db.get_value("Price List", self.price_list, ["selling", "buying"])
        if pl:
            if self.customer and not pl.selling:
                raise ValidationError(
                    f"Price List {self.price_list} is not a selling list; it cannot name a customer")
            if self.supplier and not pl.buying:
                raise ValidationError(
                    f"Price List {self.price_list} is not a buying list; it cannot name a supplier")

        if self.valid_from and self.valid_upto:
            if getdate(self.valid_from) > getdate(self.valid_upto):
                raise ValidationError("Valid From cannot be after Valid Upto")


def resolve_price_list(doc, party_type=None, party_field=None):
    """The Price List that applies to this document, or None.

    Party default first, then the company default. A candidate is used only if
    it is enabled, quoted in the document's currency (see the module
    docstring), and marked for the side of the business this document trades
    on — otherwise a purchase document would happily price itself from the
    company's selling list.
    """
    db = get_db()
    currency = doc._data.get("currency")
    side = "buying" if party_type == "Supplier" else "selling"
    candidates = []

    if party_type and party_field:
        party = doc._data.get(party_field)
        if party:
            candidates.append(db.get_value(party_type, party, "default_price_list"))
    company = doc._data.get("company")
    if company:
        candidates.append(db.get_value("Company", company, "default_price_list"))

    for name in candidates:
        if not name:
            continue
        row = db.get_value("Price List", name, ["currency", "enabled", "selling", "buying", "discarded"])
        if not row or not row.enabled or row.discarded:
            continue
        if not row.get(side):
            continue
        if currency and row.currency and row.currency != currency:
            continue
        return name
    return None


def get_item_price(item_code, price_list, qty=0, party_field=None, party=None,
                   uom=None, date=None):
    """Rate for an item in a price list, or None if the list does not price it.

    `party_field` is "customer" or "supplier"; a row naming this party beats an
    unnamed list price. Within the same specificity the highest min_qty at or
    below `qty` wins, so volume breaks read naturally.
    """
    if not price_list or not item_code:
        return None
    db = get_db()
    on = date or nowdate()

    rows = db.sql(
        """
        SELECT * FROM "Item Price"
        WHERE item_code = ?
          AND price_list = ?
          AND COALESCE(enabled, 1) = 1
          AND COALESCE(discarded, 0) = 0
          AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
          AND (valid_upto IS NULL OR valid_upto = '' OR valid_upto >= ?)
          AND (COALESCE(min_qty, 0) = 0 OR COALESCE(min_qty, 0) <= ?)
        """,
        [item_code, price_list, on, on, flt(qty)],
    )
    if not rows:
        return None

    def usable(row):
        # A row naming a different party is not for this document.
        for field in ("customer", "supplier"):
            named = row.get(field)
            if named and not (party_field == field and named == party):
                return False
        # A row quoted per-UOM only applies to that UOM.
        if row.get("uom") and uom and row["uom"] != uom:
            return False
        return True

    rows = [r for r in rows if usable(r)]
    if not rows:
        return None

    def rank(row):
        party_match = 1 if (party_field and row.get(party_field)) else 0
        return (party_match, flt(row.get("min_qty")), str(row.get("name")))

    return flt(max(rows, key=rank)["rate"])


def set_item_defaults(doc, party_type=None, party_field=None, *, set_description=True):
    """Fill item names, units and unsupplied rates from master data.

    Replaces the near-identical copy each transaction document used to carry.
    Those copies resolved the rate only when `item_name` was absent, so
    supplying a display name silently suppressed pricing and the line fell
    through to 0. Name defaulting still respects that guard; price resolution
    no longer does — a line's rate should not depend on whether the caller
    happened to send a label.
    """
    db = get_db()
    price_list = resolve_price_list(doc, party_type, party_field)
    party = doc._data.get(party_field) if party_field else None
    date = doc._data.get("posting_date") or doc._data.get("transaction_date")

    for item in doc.get("items") or []:
        item_code = item.get("item_code")
        if not item_code:
            continue
        item_data = db.get_value(
            "Item", item_code,
            ["item_name", "description", "stock_uom", "standard_rate"],
        )
        if not item_data:
            continue

        if not item.get("item_name"):
            item["item_name"] = item_data.item_name
            if set_description:
                item["description"] = item.get("description") or item_data.description
            item["uom"] = item.get("uom") or item_data.stock_uom

        if item.get("rate") is None and item.get("price_list_rate") is None:
            rate = get_item_price(
                item_code, price_list,
                qty=flt(item.get("qty", 0)),
                party_field=party_field, party=party,
                uom=item.get("uom") or item_data.stock_uom,
                date=date,
            )
            if rate is None:
                item["rate"] = flt(item_data.standard_rate)
            else:
                item["price_list_rate"] = rate
                item["rate"] = rate
