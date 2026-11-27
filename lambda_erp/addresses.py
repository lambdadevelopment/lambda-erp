"""Several addresses per customer, and the two a sales document uses.

A customer keeps its main address on the customer record, as before. On top
of it, any number of Address records: accounting, central store, a ward.
Each says what it is for (invoices, deliveries or both) and one of each kind
can be the default.

A sales document (quotation, sales order, delivery note, sales invoice)
carries an invoice address and a delivery address. Each is a link to an
Address plus the text as it is printed:

    billing_address  / billing_address_display
    shipping_address / shipping_address_display

The text is a copy taken when the document is saved, so a document that was
issued keeps its address when the master changes. It can be edited for one
document, or typed in without a link when an order names an address that is
not on record: what the document says takes precedence.

Where a document has neither, it inherits from the document it was made
from (order from quotation, delivery from order, invoice from delivery or
order); else it takes the customer's default of that kind; else it stays
empty and the customer's main address is printed.

See docs/addresses.md.
"""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt, now

# document type -> where an inherited address comes from: (line field naming
# the source line or document, table to resolve a line to its document or
# None, source document type), first match wins.
SOURCES = {
    "Quotation": (),
    "Sales Order": (("quotation_item", "Quotation Item", "Quotation"),),
    "Delivery Note": (("against_sales_order", None, "Sales Order"),),
    "Sales Invoice": (("delivery_note", None, "Delivery Note"), ("sales_order", None, "Sales Order")),
}
KINDS = {"billing": ("is_billing", "is_default_billing", "invoices"),
         "shipping": ("is_shipping", "is_default_shipping", "deliveries")}


def register_address_document(doctype, sources=()):
    """Plugin seam: a further document type with a customer that carries
    billing_address / shipping_address (and their _display columns, which the
    plugin adds to its table). `sources` as in SOURCES."""
    SOURCES[doctype] = tuple(sources)


def format_address(address, customer_name=None):
    """An Address record as the lines that are printed."""
    lines = [address.get("recipient_name") or customer_name or "", address.get("contact_person") or "",
             *str(address.get("address") or "").splitlines(),
             " ".join(part for part in (address.get("zip_code"), address.get("city")) if part),
             address.get("country") or ""]
    return "\n".join(line.strip() for line in lines if line and line.strip())


def address_title(address, customer_name=None):
    """What an address is called when picking it: customer, label, town."""
    parts = [customer_name or address.get("customer") or "", address.get("label") or "", address.get("city") or ""]
    return ", ".join(part for part in parts if part)


def _columns(doctype):
    return get_db()._get_table_columns(doctype)


def _source_addresses(doc):
    """(billing link, billing text, shipping link, shipping text) of the
    document this one was made from, or None."""
    db = get_db()
    for field, line_table, source in SOURCES.get(doc.DOCTYPE, ()):
        if "billing_address" not in _columns(source):
            continue
        for row in doc.get("items") or ():
            reference = row.get(field)
            if not reference:
                continue
            parent = db.get_value(line_table, reference, "parent") if line_table else reference
            found = db.get_value(source, parent, ["customer", "billing_address", "billing_address_display",
                                                  "shipping_address", "shipping_address_display"]) if parent else None
            if found and found.get("customer") == doc.get("customer"):
                return found
    return None


def default_address(customer, kind):
    """The customer's default Address of a kind ('billing' / 'shipping'), or None."""
    usable, default, _ = KINDS[kind]
    rows = get_db().sql(
        f'SELECT name FROM "Address" WHERE customer = ? AND COALESCE(disabled, 0) = 0 '
        f'AND COALESCE({default}, 0) = 1 AND COALESCE({usable}, 0) = 1 ORDER BY name LIMIT 1', [customer])
    return rows[0]["name"] if rows else None


def resolve_document_addresses(doc):
    """Fill, check and snapshot a sales document's two addresses. Called
    before the document validates, on every save and on submit."""
    if doc.DOCTYPE not in SOURCES:
        return
    apply_customer_note(doc)
    db = get_db()
    if "billing_address" not in _columns(doc.DOCTYPE) or not _columns("Address"):
        return
    customer = doc.get("customer")
    if not customer:
        return
    stored = db.get_value(doc.DOCTYPE, doc.name, [
        "customer", "billing_address", "billing_address_display", "shipping_address",
        "shipping_address_display"]) if getattr(doc, "_persisted", False) and doc.name else None
    customer_name = db.get_value("Customer", customer, "customer_name") or customer
    inherited = None
    for kind, (usable, _, purpose) in KINDS.items():
        link_field, text_field = f"{kind}_address", f"{kind}_address_display"
        link = str(doc._data.get(link_field) or "").strip() or None
        text = str(doc._data.get(text_field) or "").strip() or None
        was_link = (stored or {}).get(link_field) or None
        was_text = str((stored or {}).get(text_field) or "").strip() or None
        if stored and stored.get("customer") != customer and link == was_link:
            # Another customer now: the old customer's address does not follow.
            link = text = was_link = was_text = None
        if not link and not text:
            if inherited is None:
                inherited = _source_addresses(doc) or {}
            link, text = inherited.get(link_field) or None, inherited.get(text_field) or None
            if not link and not text:
                link = default_address(customer, kind)
        if link:
            address = db.get_all("Address", filters={"name": link}, fields=["*"])
            address = address[0] if address else None
            if not address:
                raise ValidationError(f"{doc.DOCTYPE}: {link_field} {link} does not exist")
            if address.get("customer") != customer:
                raise ValidationError(
                    f"{doc.DOCTYPE}: address {link} belongs to customer {address.get('customer')}, not {customer}")
            if link != was_link:
                if flt(address.get("disabled")):
                    raise ValidationError(f"{doc.DOCTYPE}: address {link} is disabled")
                if not flt(address.get(usable)):
                    raise ValidationError(f"{doc.DOCTYPE}: address {link} is not marked for {purpose}")
            if not text or (link != was_link and text == was_text):
                # A newly picked address replaces the text of the previous one,
                # unless the text was changed in the same save.
                text = format_address(address, customer_name)
        doc._data[link_field], doc._data[text_field] = link, text


def finish_address(name):
    """After an Address was saved: its title, and one default per customer and kind."""
    db = get_db()
    rows = db.get_all("Address", filters={"name": name}, fields=["*"])
    if not rows:
        return
    address = rows[0]
    customer_name = db.get_value("Customer", address.get("customer"), "customer_name")
    values = {"address_title": address_title(address, customer_name)}
    for usable, default, _ in KINDS.values():
        if flt(address.get(default)):
            if not flt(address.get(usable)) or flt(address.get("disabled")):
                values[default] = 0
            else:
                db.sql(f'UPDATE "Address" SET {default} = 0, modified = ? WHERE customer = ? AND name <> ? '
                       f'AND COALESCE({default}, 0) = 1', [now(), address.get("customer"), name])
    db.set_value("Address", name, values)


def validate_address(merged):
    """Checks on an Address before it is saved."""
    if not str(merged.get("label") or "").strip():
        raise ValidationError("Address: label is required (what the address is: Accounting, Central store, ...)")
    if not any(str(merged.get(field) or "").strip() for field in ("address", "city", "zip_code")):
        raise ValidationError("Address: give at least the street, the zip code or the town")
    # Left out, an address is for both (the column defaults).
    usable = [flt(1 if merged.get(field) in (None, "") else merged.get(field)) for field in ("is_billing", "is_shipping")]
    if not any(usable):
        raise ValidationError("Address: mark it for invoices, for deliveries or both")


def document_address_lines(doc, kind):
    """The lines to print for a document's invoice ('billing') or delivery
    ('shipping') address, or [] when the document has none."""
    return [line for line in str(doc.get(f"{kind}_address_display") or "").splitlines() if line.strip()]


def delivery_instructions(doc):
    """Instructions of the delivery address a document links, for delivery notes."""
    link = doc.get("shipping_address")
    if not link or not _columns("Address"):
        return None
    return get_db().get_value("Address", link, "instructions") or None


# -- what a customer's documents have used --------------------------------------

USED_IN = ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice")
NOTE_FIELDS = {"Delivery Note": "delivery_note_text", "Sales Invoice": "invoice_note_text"}


def _same_text(a, b):
    return " ".join(str(a or "").split()).lower() == " ".join(str(b or "").split()).lower()


def customer_addresses(customer, limit=20):
    """The customer's saved addresses and the delivery and invoice addresses
    its documents have used that are not on record (typed for one order):
    newest first, with how often and when last, and the last document.

    Read-only: picking one means setting the link (saved) or the printed text
    (used before) on a draft."""
    db = get_db()
    if not customer:
        raise ValidationError("Name the customer")
    customer_name = db.get_value("Customer", customer, "customer_name")
    if customer_name is None:
        raise ValidationError(f"Customer {customer} not found")
    saved = []
    if _columns("Address"):
        for row in db.get_all("Address", filters={"customer": customer}, fields=["*"], order_by="label"):
            saved.append({"address": row["name"], "label": row.get("label"), "title": row.get("address_title"),
                          "text": format_address(row, customer_name),
                          "for_invoices": bool(flt(row.get("is_billing"))),
                          "for_deliveries": bool(flt(row.get("is_shipping"))),
                          "default_invoice": bool(flt(row.get("is_default_billing"))),
                          "default_delivery": bool(flt(row.get("is_default_shipping"))),
                          "disabled": bool(flt(row.get("disabled"))),
                          "instructions": row.get("instructions") or None})
    used = {"shipping": {}, "billing": {}}
    for doctype in USED_IN:
        columns = _columns(doctype)
        if "shipping_address_display" not in columns:
            continue
        date = "posting_date" if "posting_date" in columns else "transaction_date"
        for kind in ("shipping", "billing"):
            rows = db.sql(
                f'SELECT name, {kind}_address_display AS text, {date} AS date FROM "{doctype}" '
                "WHERE customer = ? AND docstatus < 2 AND COALESCE(discarded, 0) = 0 "
                f"AND COALESCE({kind}_address_display, '') <> ''", [customer])
            for row in rows:
                key = " ".join(str(row["text"]).split()).lower()
                if any(_same_text(row["text"], s["text"]) for s in saved):
                    continue
                entry = used[kind].setdefault(key, {"text": str(row["text"]).strip(), "times": 0,
                                                    "last_used": None, "last_document": None})
                entry["times"] += 1
                day = str(row["date"] or "")[:10]
                if entry["last_used"] is None or day > entry["last_used"]:
                    entry["last_used"], entry["last_document"] = day, {"doctype": doctype, "name": row["name"]}
    def newest(entries):
        return sorted(entries.values(), key=lambda e: (e["last_used"] or "", e["times"]), reverse=True)[:limit]
    return {"customer": customer, "customer_name": customer_name, "saved": saved,
            "used_delivery_addresses": newest(used["shipping"]),
            "used_invoice_addresses": newest(used["billing"])}


def apply_customer_note(doc):
    """A new delivery note or invoice starts with the customer's note for that
    document type (e.g. "Rechnung per E-Mail an …"). Copied once, like the
    address text, so it can be changed for one document and an issued document
    keeps it."""
    field = NOTE_FIELDS.get(doc.DOCTYPE)
    if not field or doc.docstatus != 0 or "customer_note" not in _columns(doc.DOCTYPE):
        return
    if doc._data.get("customer_note") is not None or not doc.get("customer"):
        return
    if field not in _columns("Customer"):
        return
    note = get_db().get_value("Customer", doc.customer, field)
    doc._data["customer_note"] = (note or "").strip() or None
