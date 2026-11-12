"""Item bundles: one item code that stands for several items.

A bundle is an item that is not stocked, sold or priced itself. Its Item
Bundle record lists the items it stands for, with a quantity each. Putting
the bundle on a line of a sales document replaces that line by one line per
component when the document is saved:

    line     SET-KNEE, qty 2
    becomes  IMPLANT-A, qty 2   (1 per set)
             SCREW-4,   qty 8   (4 per set)

From there the components are ordinary lines: each is priced, reserved,
picked by batch, delivered and invoiced on its own, and can be changed or
removed on the draft. What differs per customer (the tool for the left or
the right hand) is simply not part of the bundle and is added as a line.

Each line that came from a bundle keeps `bundle_item` and `bundle_qty` (the
bundle and how many of it were entered), so documents can say what was
ordered as a set.

See docs/bundles.md.
"""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt

# Document types whose item lines expand bundles.
EXPANDING = {"Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice"}
MAX_DEPTH = 5

# What a component line takes over from the bundle line; everything about the
# item itself (name, unit, price, batch) is its own.
CARRIED = ("warehouse", "delivery_date", "cost_center", "income_account", "location")
# What a bundle line may not carry: it has no price and no stock of its own.
REFUSED = ("rate", "price_list_rate", "discount_percentage", "discount_amount", "batch_no", "serial_no")


def register_bundle_document(doctype):
    """Plugin seam: a further document type whose `items` lines expand
    bundles (its line table needs bundle_item TEXT and bundle_qty REAL)."""
    EXPANDING.add(doctype)


def bundle_of(item_code):
    """The enabled Item Bundle record of an item code, or None."""
    db = get_db()
    if not item_code or not db._get_table_columns("Item Bundle"):
        return None
    rows = db.sql('SELECT name FROM "Item Bundle" WHERE item_code = ? AND COALESCE(disabled, 0) = 0 '
                  'AND COALESCE(discarded, 0) = 0 LIMIT 1', [item_code])
    return rows[0]["name"] if rows else None


def components(item_code, qty=1.0, _path=()):
    """The items a bundle stands for, as [(item_code, qty)], bundles inside
    bundles resolved. `qty` is how many of the bundle."""
    name = bundle_of(item_code)
    if not name:
        return [(item_code, flt(qty))]
    if item_code in _path or len(_path) >= MAX_DEPTH:
        raise ValidationError(f"Item Bundle {item_code} contains itself: {' > '.join((*_path, item_code))}")
    result = []
    for row in get_db().get_all("Item Bundle Item", filters={"parent": name}, fields=["item_code", "qty"],
                                order_by="idx"):
        result.extend(components(row["item_code"], flt(qty) * flt(row["qty"]), (*_path, item_code)))
    return result


def expand_bundle_lines(doc):
    """Replace every bundle line of a sales document by its component lines.
    Called before the document validates, on every save and on submit; a
    document without bundle lines is left exactly as it is."""
    if doc.DOCTYPE not in EXPANDING:
        return
    lines = doc.get("items") or []
    if not any(bundle_of(row.get("item_code")) for row in lines):
        return
    table = doc.CHILD_TABLES["items"][0]
    stamps = "bundle_item" in get_db()._get_table_columns(table)
    expanded = []
    for position, row in enumerate(lines, 1):
        code = row.get("item_code")
        if not bundle_of(code):
            expanded.append(row)
            continue
        qty = flt(row.get("qty"))
        if not qty:
            raise ValidationError(f"{doc.DOCTYPE} row {position}: bundle {code} needs a quantity")
        priced = [field for field in REFUSED if row.get(field) not in (None, "", 0, 0.0)]
        if priced:
            raise ValidationError(
                f"{doc.DOCTYPE} row {position}: {code} is a bundle and has no {', '.join(priced)} of its own. "
                f"Leave it out; the items of the bundle are priced and picked as their own lines")
        for item_code, item_qty in components(code, qty):
            line = _dict({field: row.get(field) for field in CARRIED if row.get(field) not in (None, "")})
            line.update(item_code=item_code, qty=item_qty)
            if stamps:
                line.update(bundle_item=code, bundle_qty=qty)
            expanded.append(line)
    doc._children["items"] = []
    for line in expanded:
        doc.append("items", line)


class ItemBundle(Document):
    DOCTYPE = "Item Bundle"
    CHILD_TABLES = {"items": ("Item Bundle Item", None)}
    PREFIX = "BNDL"
    REQUIRED_FIELDS = ("item_code", "items")
    CHILD_REQUIREMENTS = {"items": {"required": ["item_code", "qty"]}}
    CONDITIONAL_REQUIREMENTS = (
        "item_code is the bundle: an item that is not a stock item and has no batch or serial tracking. "
        "Create that item first; one bundle per item.",
        "items lists what the bundle stands for, with the quantity per one bundle. A component can itself be a "
        "bundle. The bundle cannot contain itself.",
        "On quotations, sales orders, delivery notes and sales / POS invoices, a line with the bundle's item_code "
        "is replaced by one line per component when the document is saved. A bundle has no price: the components "
        "are priced as usual.",
        "Changing a bundle changes what new lines expand to; lines on existing documents stay as they are.",
    )
    LINK_FIELDS = {"item_code": "Item"}
    CHILD_LINK_FIELDS = {"items": {"item_code": "Item"}}

    def validate(self):
        db = get_db()
        item = db.get_value("Item", self.item_code, ["item_name", "is_stock_item", "has_batch_no", "has_serial_no"])
        if not item:
            raise ValidationError(f"Item Bundle: item {self.item_code} does not exist")
        if flt(item.get("is_stock_item")) or flt(item.get("has_batch_no")) or flt(item.get("has_serial_no")):
            raise ValidationError(
                f"Item Bundle: {self.item_code} is a stock item. A bundle is an item that is not stocked itself "
                f"(is_stock_item = 0, no batch or serial tracking)")
        if db.sql('SELECT 1 FROM "Stock Ledger Entry" WHERE item_code = ? LIMIT 1', [self.item_code]):
            raise ValidationError(f"Item Bundle: {self.item_code} has stock movements and cannot become a bundle")
        twin = db.sql('SELECT name FROM "Item Bundle" WHERE item_code = ? AND name <> ? '
                      'AND COALESCE(discarded, 0) = 0 LIMIT 1', [self.item_code, self.name or ""])
        if twin:
            raise ValidationError(f"Item {self.item_code} already has bundle {twin[0]['name']}; change that one")
        if not self._data.get("bundle_name"):
            self._data["bundle_name"] = item.get("item_name") or self.item_code
        rows = self.get("items") or []
        if not rows:
            raise ValidationError("Item Bundle: at least one item is required")
        seen = set()
        for idx, row in enumerate(rows, 1):
            code = row.get("item_code")
            if not code or not db.exists("Item", code):
                raise ValidationError(f"Item Bundle row {idx}: item {code or ''} does not exist")
            if code == self.item_code:
                raise ValidationError(f"Item Bundle row {idx}: a bundle cannot contain itself")
            if code in seen:
                raise ValidationError(f"Item Bundle row {idx}: {code} is listed twice; give it one line with the total quantity")
            seen.add(code)
            try:
                qty = float(row.get("qty"))
            except (TypeError, ValueError):
                raise ValidationError(f"Item Bundle row {idx}: Qty must be a number")
            if not qty > 0 or qty != qty or qty == float("inf"):
                raise ValidationError(f"Item Bundle row {idx}: Qty must be positive")
            row["qty"] = qty
            row["item_name"] = db.get_value("Item", code, "item_name")
            # A component that is a bundle must not lead back here.
            if bundle_of(code):
                inner = [inner_code for inner_code, _ in components(code, 1, (self.item_code,))]
                if self.item_code in inner:
                    raise ValidationError(f"Item Bundle row {idx}: {code} contains {self.item_code}")
