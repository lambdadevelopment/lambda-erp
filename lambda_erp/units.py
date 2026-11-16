"""Units of measure: ordering and pricing in a pack, stocking in pieces.

An item is stocked in one unit, its `stock_uom` ("Stk"). An **Item Unit**
record gives the item a further unit and says how many stock units it holds:

    Item Unit   GLOVE-M   uom "Karton"   conversion_factor 100

A document line may then be entered in that unit: 2 Karton at 290.00. When
the document is saved the line is converted to the stock unit and keeps what
was entered:

    entered   qty 2      uom Karton   rate 290.00
    stored    qty 200    uom Stk      rate 2.90     amount 580.00
              order_uom Karton   conversion_factor 100   order_qty 2

Quantity and rate are therefore per stock unit on every stored line, and
everything that reads them (stock ledger, valuation, reservations, picking by
expiry, deliveries, invoices, backlog, returns, consignment, stock counts)
needs no knowledge of units. `order_uom`, `conversion_factor` and
`order_qty` say what the line was ordered as; the factor is a snapshot, so
changing the Item Unit later does not change a stored document. They follow
the line into the documents made from it, and `order_qty` is always
`qty / conversion_factor`: delivering 100 of the 200 shows 1 Karton.

A line is converted when its `uom` names one of the item's units. Sending the
stored line back unchanged (uom = stock unit) converts nothing. A unit the
item does not have is refused as soon as the item has any Item Unit; items
without one keep accepting any text in `uom`, as before 1.4.22.

See docs/units.md.
"""
import math

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document
from lambda_erp.utils import flt

# Decimal places a converted quantity is kept to.
QTY_PLACES = 6
# What is quoted per unit on a line and is divided by the factor with it.
RATE_FIELDS = ("rate", "price_list_rate", "discount_amount")


class ItemUnit(Document):
    DOCTYPE = "Item Unit"
    CHILD_TABLES = {}
    PREFIX = "IUNIT"
    REQUIRED_FIELDS = ("item_code", "uom", "conversion_factor")
    LINK_FIELDS = {"item_code": "Item"}
    CONDITIONAL_REQUIREMENTS = (
        "conversion_factor is how many stock units (the item's stock_uom) one of this unit holds: "
        "100 for a carton of 100 pieces. It must be a positive number.",
        "uom is the unit's name as it is written on orders (Karton, Pack, VE). It cannot be the item's own "
        "stock_uom, and an item has each unit once.",
        "A document line entered with this uom is converted to the stock unit when saved; the line keeps "
        "order_uom, conversion_factor and order_qty. Changing or disabling a unit does not change stored documents.",
        "must_be_whole_number = 1: a line entered in this unit needs a whole quantity (2 Karton, not 1.5). "
        "Quantities in the stock unit are not affected.",
    )

    def validate(self):
        db = get_db()
        self._data["uom"] = str(self._data.get("uom") or "").strip()
        if not self.item_code:
            raise ValidationError("Item Unit: Item Code is required")
        item = db.get_value("Item", self.item_code, ["name", "stock_uom"])
        if not item:
            raise ValidationError(f"Item Unit: item {self.item_code} does not exist")
        if not self.uom:
            raise ValidationError("Item Unit: UOM is required")
        if self.uom == (item.stock_uom or ""):
            raise ValidationError(
                f"Item Unit: {self.uom} is the stock unit of {self.item_code} itself (factor 1); "
                f"an Item Unit names a further unit")
        try:
            factor = float(self._data.get("conversion_factor"))
        except (TypeError, ValueError):
            raise ValidationError("Item Unit: Conversion Factor must be a number")
        if not math.isfinite(factor) or factor <= 0:
            raise ValidationError("Item Unit: Conversion Factor must be positive")
        self._data["conversion_factor"] = factor
        enabled = self._data.get("enabled")
        self._data["enabled"] = 1 if enabled in (None, "") else int(flt(enabled) != 0)
        self._data["must_be_whole_number"] = int(flt(self._data.get("must_be_whole_number")) != 0)
        twin = db.sql(
            'SELECT name FROM "Item Unit" WHERE item_code = ? AND uom = ? AND name <> ? '
            'AND COALESCE(discarded, 0) = 0 LIMIT 1',
            [self.item_code, self.uom, self.name or ""])
        if twin:
            raise ValidationError(
                f"Item Unit: {self.item_code} already has the unit {self.uom} ({twin[0]['name']})")


def _table_ready(db):
    return bool(db._get_table_columns("Item Unit"))


def unit_records(item_code):
    """The enabled further units of an item as {uom: {"factor", "whole"}}."""
    db = get_db()
    if not item_code or not _table_ready(db):
        return {}
    whole = "must_be_whole_number" in db._get_table_columns("Item Unit")
    rows = db.sql(
        'SELECT * FROM "Item Unit" WHERE item_code = ? '
        'AND COALESCE(enabled, 1) = 1 AND COALESCE(discarded, 0) = 0 ORDER BY uom',
        [item_code])
    return {row["uom"]: {"factor": flt(row["conversion_factor"]),
                         "whole": bool(whole and flt(row.get("must_be_whole_number")))} for row in rows}


def units_of(item_code):
    """The enabled further units of an item as {uom: conversion_factor}."""
    return {uom: unit["factor"] for uom, unit in unit_records(item_code).items()}


def conversion_factor(item_code, uom):
    """Stock units per `uom` for an item, or None if the item has no such unit."""
    return units_of(item_code).get(uom)


def pending_factor(item, stock_uom, label="Row", qty_field="qty"):
    """The factor a line still has to be converted by, or None.

    A line is converted when its `uom` is one of the item's units. None for
    a line already in the stock unit, and for an item without any Item Unit
    (its `uom` stays free text). Raises for a unit the item does not have,
    and for a broken quantity of a unit that is only handled whole.
    """
    uom = str(item.get("uom") or "").strip()
    if not uom or uom == (stock_uom or ""):
        return None
    units = unit_records(item.get("item_code"))
    if not units:
        return None
    unit = units.get(uom)
    if not unit or not unit["factor"]:
        known = ", ".join(f"{name} (= {flt(value['factor']):g} {stock_uom})" for name, value in units.items())
        raise ValidationError(
            f"{label}: {uom} is not a unit of item {item.get('item_code')}. It is stocked in {stock_uom}; "
            f"its further units are {known}. Use one of these, or add an Item Unit for {uom} first")
    entered = flt(item.get(qty_field))
    if unit["whole"] and abs(entered - round(entered)) > 1e-9:
        raise ValidationError(
            f"{label}: {item.get('item_code')} is handled in whole {uom} only, not {entered:g}. "
            f"Enter a whole number of {uom}, or the quantity in {stock_uom} "
            f"(1 {uom} = {flt(unit['factor']):g} {stock_uom})")
    return unit["factor"]


def convert_line(item, stock_uom, factor, rate_fields=RATE_FIELDS, qty_field="qty"):
    """Convert one line from the unit in `uom` to the stock unit, in place.
    Returns the quantity as it was entered."""
    entered = flt(item.get(qty_field))
    item["order_uom"] = str(item.get("uom")).strip()
    item["conversion_factor"] = factor
    item[qty_field] = flt(entered * factor, QTY_PLACES)
    for field in rate_fields:
        if item.get(field) not in (None, ""):
            # Not rounded: 10.00 for a pack of 3 is 3.3333... per piece, and
            # the line amount is rounded once, from quantity x rate.
            item[field] = flt(item[field]) / factor
    item["uom"] = stock_uom
    return entered


def stamp_order_qty(item, qty_field="qty"):
    """Derive `order_qty` (and tidy the snapshot) of a line in the stock unit."""
    factor = flt(item.get("conversion_factor"))
    if item.get("order_uom") and math.isfinite(factor) and factor > 0 and item.get(qty_field) not in (None, ""):
        item["order_qty"] = flt(flt(item.get(qty_field)) / factor, QTY_PLACES)
    else:
        item["order_uom"] = None
        item["conversion_factor"] = None
        item["order_qty"] = None


def has_unit_columns(doc, child="items"):
    tables = getattr(doc, "CHILD_TABLES", {})
    return child in tables and "order_uom" in get_db()._get_table_columns(tables[child][0])


def apply_units(doc, rate_fields=RATE_FIELDS, child="items"):
    """Convert the lines of a document that were entered in a further unit.
    For documents that do not price their lines (Stock Entry); documents that
    do go through `item_price.set_item_defaults`, which converts and prices
    in one pass."""
    if not has_unit_columns(doc, child):
        return
    db = get_db()
    for idx, item in enumerate(doc.get(child) or [], 1):
        code = item.get("item_code")
        stock_uom = db.get_value("Item", code, "stock_uom") if code else None
        if stock_uom is not None:
            factor = pending_factor(item, stock_uom, f"{doc.DOCTYPE} row {idx}")
            if factor:
                convert_line(item, stock_uom, factor, rate_fields)
        stamp_order_qty(item)


def in_stock_units(row):
    """(quantity, rate) of a row as it will be stored: per stock unit. For
    code that compares a row as it was sent with one that was saved (the
    content hash of a consignment report)."""
    qty = flt(row.get("qty"))
    rate = None if row.get("rate") in (None, "") else flt(row.get("rate"))
    code = row.get("item_code")
    stock_uom = get_db().get_value("Item", code, "stock_uom") if code else None
    factor = pending_factor(row, stock_uom, "Row") if stock_uom is not None else None
    if factor:
        qty = flt(qty * factor, QTY_PLACES)
        rate = None if rate is None else rate / factor
    return qty, rate


def describe(item, stock_uom=None):
    """"2 Karton (= 100 Stk each)" for a line ordered in a further unit, else ''."""
    if not item.get("order_uom") or not flt(item.get("conversion_factor")):
        return ""
    unit = stock_uom or item.get("uom") or ""
    return (f"{flt(item.get('order_qty')):g} {item['order_uom']} "
            f"(= {flt(item['conversion_factor']):g} {unit} each)").replace("  ", " ")
