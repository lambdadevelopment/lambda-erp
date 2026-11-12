"""Serial numbers: one number, one unit.

A serial number is unique **per item** (two items may use the same number)
and identifies exactly one unit. Tracking is switched on per item
(`Item.has_serial_no`). Every stock movement of a tracked item lists the
serial numbers it moves, as many as its quantity, and the unit is in at most
one place at a time:

    Serial No         where each unit is now (warehouse, storage location,
                      batch) or that it is gone, and to whom
    Serial Movement   every movement of every unit, with its document

Both are written only by the stock ledger, next to the entry they belong to.
Quantities and values stay where they are (Bin, Stock Balance Detail); a
serial number adds identity, not a second stock count.

See docs/serial-numbers.md.
"""
import re
import uuid

from lambda_erp.database import get_db
from lambda_erp.exceptions import NegativeStockError, ValidationError
from lambda_erp.utils import _dict, flt, now

IN_STOCK = "In Stock"
_SPLIT = re.compile(r"[\n,;]+")

# What becomes of a unit that leaves stock, by the document that moved it.
_GONE = {
    "Delivery Note": "Delivered", "Sales Invoice": "Delivered", "POS Invoice": "Delivered",
    "Purchase Receipt": "Returned to Supplier", "Purchase Invoice": "Returned to Supplier",
    "Stock Count": "Written Off",
}
_CUSTOMER_VOUCHERS = ("Delivery Note", "Sales Invoice", "POS Invoice")


def parse_serials(value):
    """The serial numbers in a line's `serial_no`: separated by line breaks,
    commas or semicolons, in the order given, without blanks."""
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        parts = [str(part) for part in value]
    else:
        parts = _SPLIT.split(str(value))
    return [part.strip() for part in parts if part and part.strip()]


def format_serials(serials):
    return "\n".join(serials) if serials else None


def get_serial(item_code, serial_no):
    """The Serial No row of one unit, or None."""
    rows = get_db().sql('SELECT * FROM "Serial No" WHERE item_code = ? AND serial_no = ?', [item_code, serial_no])
    return rows[0] if rows else None


def serials_in_stock(item_code, warehouse=None, batch_no=None, location=None):
    """Units in stock, ordered by serial number."""
    where, params = ["item_code = ?", "warehouse IS NOT NULL", "warehouse <> ''"], [item_code]
    for column, value in (("warehouse", warehouse), ("batch_no", batch_no), ("location", location)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    return get_db().sql(f'SELECT * FROM "Serial No" WHERE {" AND ".join(where)} ORDER BY serial_no', params)


def is_serial_item(item_code):
    db = get_db()
    if "has_serial_no" not in db._get_table_columns("Item"):
        return False
    return bool(flt(db.get_value("Item", item_code, "has_serial_no")))


def resolve_entry_serials(sle):
    """Check one stock ledger entry's serial numbers against its item and
    quantity and return them (an empty list when the entry carries none).

    - an item that is not serial-tracked takes none;
    - a tracked item needs exactly as many as the quantity, each once (the
      exceptions are the reversal of a voucher from before tracking and a
      Serial Assignment's source side);
    - a quantity of a tracked item is a whole number: a unit is not divisible.
    """
    item_code = sle["item_code"]
    serials = parse_serials(sle.get("serial_no"))
    if not is_serial_item(item_code):
        if serials:
            raise ValidationError(
                f"Item {item_code} is not serial-tracked: remove the serial numbers from the line, "
                f"or switch the item to serial tracking first")
        return []
    quantity = abs(flt(sle["actual_qty"]))
    if abs(quantity - round(quantity)) > 1e-9:
        raise ValidationError(f"Item {item_code} is serial-tracked: its quantity must be a whole number, not {quantity:g}")
    if not serials:
        if sle.get("allow_unassigned_serial"):
            return []
        raise ValidationError(
            f"Serial numbers are required for item {item_code} (serial-tracked): {int(round(quantity))} for a "
            f"quantity of {int(round(quantity))} in warehouse {sle['warehouse']}")
    twice = sorted({serial for serial in serials if serials.count(serial) > 1})
    if twice:
        raise ValidationError(f"Serial number(s) listed twice for item {item_code}: {', '.join(twice)}")
    if len(serials) != int(round(quantity)):
        raise ValidationError(
            f"Item {item_code}: {len(serials)} serial number(s) for a quantity of {int(round(quantity))}. "
            f"Each unit needs its own serial number")
    return serials


def apply_entry_serials(sle, serials, batch_no="", location=""):
    """Move the units of a posted stock ledger entry: a unit can only come in
    if it is not in stock, and only go out of the warehouse (batch, storage
    location) it is in. Raising here rolls the whole document back."""
    db = get_db()
    incoming = flt(sle["actual_qty"]) > 0
    if not serials:
        if not incoming and is_serial_item(sle["item_code"]):
            _check_unassigned(sle)
        return
    lock = ' FOR UPDATE' if db.dialect == 'postgres' else ''
    voucher = sle.get("voucher_type")
    customer = None
    if not incoming and voucher in _CUSTOMER_VOUCHERS and sle.get("voucher_no"):
        customer = db.get_value(voucher, sle["voucher_no"], "customer")
    for serial in serials:
        rows = db.sql('SELECT * FROM "Serial No" WHERE item_code = ? AND serial_no = ?' + lock,
                      [sle["item_code"], serial])
        unit = rows[0] if rows else None
        label = f"Serial number {serial} of item {sle['item_code']}"
        if incoming:
            if unit and unit.get("warehouse"):
                raise ValidationError(f"{label} is already in stock in warehouse {unit['warehouse']}")
            values = {"warehouse": sle["warehouse"], "location": location or None,
                      "batch_no": batch_no or (unit or {}).get("batch_no") or None,
                      "status": IN_STOCK, "customer": None, "modified": now()}
            if unit:
                db.set_value("Serial No", unit["name"], values)
                name = unit["name"]
            else:
                while True:
                    name = f"SERIAL-{db.next_in_series('SERIAL'):06d}"
                    if not db.exists("Serial No", name):
                        break
                db.insert("Serial No", _dict(name=name, item_code=sle["item_code"], serial_no=serial,
                                             creation=now(), disabled=0, **values))
        else:
            _check_leaving(sle, serial, unit, batch_no, location)
            db.set_value("Serial No", unit["name"], {
                "warehouse": None, "location": None, "status": _GONE.get(voucher, "Issued"),
                "customer": customer, "modified": now()})
            name = unit["name"]
        db.insert("Serial Movement", _dict(
            name=uuid.uuid4().hex, serial=name, item_code=sle["item_code"], serial_no=serial,
            stock_ledger_entry=sle.get("name"), voucher_type=voucher, voucher_no=sle.get("voucher_no"),
            voucher_detail_no=sle.get("voucher_detail_no"), warehouse=sle["warehouse"],
            location=location or None, batch_no=batch_no or None, actual_qty=1 if incoming else -1,
            posting_date=sle.get("posting_date"), creation=now()))


def _check_leaving(sle, serial, unit, batch_no, location):
    label = f"Serial number {serial} of item {sle['item_code']}"
    if not unit:
        raise NegativeStockError(f"Unknown serial number {serial} for item {sle['item_code']}")
    if unit.get("warehouse") != sle["warehouse"]:
        where = (f"it is in warehouse {unit['warehouse']}" if unit.get("warehouse")
                 else f"it is not in stock ({unit.get('status') or 'gone'})")
        raise NegativeStockError(f"{label} is not in warehouse {sle['warehouse']}: {where}")
    if batch_no and unit.get("batch_no") and unit["batch_no"] != batch_no:
        raise ValidationError(f"{label} belongs to batch {unit['batch_no']}, not {batch_no}")
    if location and unit.get("location") and unit["location"] != location:
        raise ValidationError(f"{label} lies in storage location {unit['location']}, not {location}")


def check_leaving_serials(sle, serials, batch_no="", location=""):
    """Before an outgoing entry touches quantities: every unit it names is in
    the warehouse (batch, storage location) it leaves from. Naming the unit
    says more than the quantity error that would follow."""
    if not serials or flt(sle["actual_qty"]) > 0:
        return
    db = get_db()
    for serial in serials:
        rows = db.sql('SELECT * FROM "Serial No" WHERE item_code = ? AND serial_no = ?', [sle["item_code"], serial])
        _check_leaving(sle, serial, rows[0] if rows else None, batch_no, location)


def _check_unassigned(sle):
    """Stock of a tracked item that has no serial number yet (from before
    tracking) can leave only as far as such stock exists."""
    db = get_db()
    in_bin = flt(db.get_value("Bin", {"item_code": sle["item_code"], "warehouse": sle["warehouse"]}, "actual_qty"))
    numbered = flt(db.sql('SELECT COUNT(*) AS n FROM "Serial No" WHERE item_code = ? AND warehouse = ?',
                          [sle["item_code"], sle["warehouse"]])[0]["n"])
    if in_bin - numbered < -1e-9:
        raise NegativeStockError(
            f"Not enough stock without a serial number of item {sle['item_code']} in warehouse {sle['warehouse']}. "
            f"Available: {max(0.0, in_bin - numbered + abs(flt(sle['actual_qty']))):g}, "
            f"requested: {abs(flt(sle['actual_qty'])):g}")


def serial_agreement():
    """Rows where the serial records disagree with themselves or with the
    stock; empty when healthy.

    - a unit's movements add up to 1 if it is in stock, else 0;
    - in a warehouse there are never more numbered units than units."""
    db = get_db()
    problems = []
    if not db._get_table_columns("Serial No"):
        return problems
    sums = {row["serial"]: flt(row["qty"]) for row in db.sql(
        'SELECT serial, SUM(actual_qty) AS qty FROM "Serial Movement" GROUP BY serial')}
    for unit in db.sql('SELECT name, item_code, serial_no, warehouse FROM "Serial No"'):
        expected = 1.0 if unit.get("warehouse") else 0.0
        if abs(sums.get(unit["name"], 0.0) - expected) > 1e-9:
            problems.append({"item_code": unit["item_code"], "serial_no": unit["serial_no"],
                             "movements": sums.get(unit["name"], 0.0), "in_stock": bool(unit.get("warehouse"))})
    for row in db.sql('SELECT s.item_code, s.warehouse, COUNT(*) AS n FROM "Serial No" s '
                      "WHERE s.warehouse IS NOT NULL AND s.warehouse <> '' GROUP BY s.item_code, s.warehouse"):
        in_bin = flt(db.get_value("Bin", {"item_code": row["item_code"], "warehouse": row["warehouse"]}, "actual_qty"))
        if flt(row["n"]) > in_bin + 1e-9:
            problems.append({"item_code": row["item_code"], "warehouse": row["warehouse"],
                             "numbered_units": flt(row["n"]), "bin_qty": in_bin})
    return problems


def serial_trace(serial_no, item_code=None):
    """Where a unit is and everything that happened to it. A serial number
    alone can belong to several items: all of them are returned."""
    db = get_db()
    where, params = ["serial_no = ?"], [serial_no]
    if item_code:
        where.append("item_code = ?")
        params.append(item_code)
    result = []
    for unit in db.sql(f'SELECT * FROM "Serial No" WHERE {" AND ".join(where)} ORDER BY item_code', params):
        movements = db.sql(
            'SELECT posting_date, voucher_type, voucher_no, voucher_detail_no, warehouse, location, batch_no, '
            'actual_qty FROM "Serial Movement" WHERE serial = ? ORDER BY creation, name', [unit["name"]])
        parties = {}
        history = []
        for move in movements:
            entry = {**dict(move), "actual_qty": flt(move["actual_qty"]), "party_type": None, "party": None}
            field = {"Delivery Note": "customer", "Sales Invoice": "customer", "POS Invoice": "customer",
                     "Purchase Receipt": "supplier", "Purchase Invoice": "supplier"}.get(move["voucher_type"])
            if field and move.get("voucher_no"):
                key = (move["voucher_type"], move["voucher_no"])
                if key not in parties:
                    parties[key] = db.get_value(move["voucher_type"], move["voucher_no"], field)
                entry["party_type"], entry["party"] = field.capitalize(), parties[key]
            history.append(entry)
        result.append({
            "item_code": unit["item_code"], "serial_no": unit["serial_no"], "status": unit.get("status"),
            "warehouse": unit.get("warehouse") or None, "location": unit.get("location") or None,
            "batch_no": unit.get("batch_no") or None, "customer": unit.get("customer") or None,
            "movements": history})
    return result


def propose_serials(item_code, warehouse, qty, batch_no=None, location=None):
    """The serial numbers to take for `qty` units from a place, by serial
    number: a proposal for a draft, or None when the item is not tracked or
    not enough numbered units lie there."""
    if not is_serial_item(item_code):
        return None
    wanted = int(round(flt(qty)))
    units = [row for row in serials_in_stock(item_code, warehouse)
             if (row.get("batch_no") or "") == (batch_no or "") and (row.get("location") or "") == (location or "")]
    if wanted <= 0 or len(units) < wanted:
        return None
    return format_serials([row["serial_no"] for row in units[:wanted]])
