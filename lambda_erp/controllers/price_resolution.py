"""Price resolution: one path for every channel, and an explanation of it.

A line's price is resolved in two layers that already exist:

  1. the base price: an Item Price of the document's price list (a row for
     the party beats the list price), else Item.standard_rate
     (controllers/item_price.py);
  2. at most one Pricing Rule on top of it (controllers/pricing_rule.py).

Documents run exactly these two steps when they are saved, whatever created
them (form, REST, chat, MCP, import). This module adds what sits around them:

  - **dimensions**: match keys a deployment defines for pricing rules, such
    as a price family of items or a purchasing group of customers. The
    deployment says which values an item or party has; the rule engine does
    the matching, ranking and pricing as for its built-in keys.
  - **the decision**: where a line's price came from is kept on the line
    (`price_source`, `price_reference`, `pricing_rule`).
  - **explain**: the same resolution, run on a document that is never saved,
    with the candidates that were considered.

See docs/pricing-and-external-sources.md.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable

from lambda_erp.database import get_db
from lambda_erp.exceptions import PermissionDenied, ValidationError
from lambda_erp.utils import flt

PRICE_SOURCES = ("Item Price", "Standard Rate", "Entered", "Manual", "None")

BUILTIN_ITEM_KEYS = ("Item Code", "Item Group")
BUILTIN_PARTY_KEYS = ("Customer", "Customer Group", "Territory", "Supplier", "Supplier Group")


# -- dimensions ---------------------------------------------------------------

@dataclass(frozen=True)
class PriceDimension:
    name: str
    kind: str                    # "item" or "party"
    resolve: Callable            # context -> iterable of values this item / party has
    specificity: int
    party_type: str | None       # "Customer" / "Supplier" for a party dimension


_DIMENSIONS: dict[str, PriceDimension] = {}


def register_price_dimension(name, kind, resolve, *, specificity=None, party_type=None):
    """Plugin seam: a match key for pricing rules.

    `kind="item"` makes `name` selectable as a rule's Apply On (next to Item
    Code and Item Group); `kind="party"` as its Applicable For (next to
    Customer, Customer Group, ...). A rule then names one value of the
    dimension in `apply_on_value` / `applicable_for_value`.

    `resolve(context)` returns the values the line's item or the document's
    party has in this dimension (a list, possibly empty). `context` carries
    item_code, item_group, customer, customer_group, territory, supplier,
    supplier_group, company, currency, date and side ("selling"/"buying"),
    so buying and selling can be assigned differently.

    `specificity` ranks a match against the built-in keys when two rules have
    the same priority. Items: Item Code is 2, Item Group is 1 (default 1).
    Parties: Customer / Supplier is 3, the group keys are 2, nobody is 0
    (default 2). A party dimension says which `party_type` it is about.

    The dimension only says who belongs where. Prices stay in pricing rules.
    """
    if kind not in ("item", "party"):
        raise ValueError('kind must be "item" or "party"')
    name = str(name or "").strip()
    if not name or name in BUILTIN_ITEM_KEYS or name in BUILTIN_PARTY_KEYS:
        raise ValueError(f"{name!r} is not a usable dimension name")
    if kind == "party" and party_type not in ("Customer", "Supplier"):
        raise ValueError('a party dimension needs party_type "Customer" or "Supplier"')
    if specificity is None:
        specificity = 1 if kind == "item" else 2
    _DIMENSIONS[name] = PriceDimension(name, kind, resolve, int(specificity),
                                       party_type if kind == "party" else None)


def unregister_price_dimension(name):
    _DIMENSIONS.pop(name, None)


def dimensions(kind=None):
    return {name: d for name, d in _DIMENSIONS.items() if kind is None or d.kind == kind}


def strict_conflicts():
    """Setting `pricing_strict_conflicts`: refuse a line when two rules of the
    same priority and specificity apply to it, instead of choosing by the
    remaining tie-breakers. Off by default."""
    db = get_db()
    if not db._get_table_columns("Settings"):
        return False
    rows = db.sql('SELECT value FROM "Settings" WHERE key = ?', ["pricing_strict_conflicts"])
    return bool(rows) and str(rows[0]["value"]).strip().lower() in ("1", "true", "yes", "on")


# -- trace (only while explaining) --------------------------------------------

_TRACE = ContextVar("price_resolution_trace", default=None)


def _line_trace(item):
    trace = _TRACE.get()
    if trace is None:
        return None
    return trace.setdefault(id(item), {})


def trace_base(item, source, reference, price_list, row):
    entry = _line_trace(item)
    if entry is None:
        return
    entry["base"] = {"source": source, "reference": reference, "price_list": price_list,
                     "rate": flt(item.get("rate")),
                     "valid_from": (row or {}).get("valid_from") or None,
                     "valid_upto": (row or {}).get("valid_upto") or None,
                     "min_qty": flt((row or {}).get("min_qty")) if row else None,
                     "party_specific": bool(row and (row.get("customer") or row.get("supplier")))}


def trace_rules(item, rules, applied, tied):
    entry = _line_trace(item)
    if entry is None:
        return

    def describe(rule):
        key = rule.get("apply_on") or "Item Code"
        who = rule.get("applicable_for") or None
        return {"name": rule.get("name"), "title": rule.get("title"), "priority": int(rule.get("priority") or 0),
                "apply_on": key, "applicable_for": who, "type": rule.get("rate_or_discount"),
                "rate": flt(rule.get("rate")), "discount_percentage": flt(rule.get("discount_percentage")),
                "discount_amount": flt(rule.get("discount_amount")),
                "valid_from": rule.get("valid_from") or None, "valid_upto": rule.get("valid_upto") or None,
                "min_qty": flt(rule.get("min_qty"))}

    entry["rules"] = [describe(rule) for rule in rules]
    entry["applied_rule"] = describe(applied) if applied else None
    entry["tied_with"] = [rule.get("name") for rule in tied]


# -- explain ------------------------------------------------------------------

_SLUGS = {"Quotation": "quotation", "Sales Order": "sales-order", "Sales Invoice": "sales-invoice",
          "POS Invoice": "pos-invoice", "Purchase Order": "purchase-order", "Purchase Invoice": "purchase-invoice"}
_CLASSES = {
    "Quotation": ("lambda_erp.selling.quotation", "Quotation"),
    "Sales Order": ("lambda_erp.selling.sales_order", "SalesOrder"),
    "Sales Invoice": ("lambda_erp.accounting.sales_invoice", "SalesInvoice"),
    "POS Invoice": ("lambda_erp.accounting.pos_invoice", "POSInvoice"),
    "Purchase Order": ("lambda_erp.buying.purchase_order", "PurchaseOrder"),
    "Purchase Invoice": ("lambda_erp.accounting.purchase_invoice", "PurchaseInvoice"),
}
_BUYING = ("Purchase Order", "Purchase Invoice", "Purchase Receipt")


def _document_class(doctype):
    if doctype not in _CLASSES:
        raise ValidationError(f"Prices are resolved for: {', '.join(_CLASSES)}")
    module, name = _CLASSES[doctype]
    import importlib
    return getattr(importlib.import_module(module), name)


def _require_explainable(doctype):
    """An explanation names prices, lists and rules: buying-side ones are
    purchase prices."""
    from lambda_erp import permissions
    permissions.require(doctype, "read")
    if doctype in _BUYING and "purchase_price" in permissions.hidden_classes():
        raise PermissionDenied("Your access profile does not allow you to see purchase prices or how they are found")


def _summary(base, rule, line, manual=False, carried=False):
    source = (base or {}).get("source")
    if manual:
        text = "The rate was entered by hand on the saved line; pricing rules no longer change it."
    elif carried:
        text = "The price was carried over from the source document; pricing rules were not run again."
    elif source == "Item Price":
        text = f"Base price from Item Price {base.get('reference')}" + (
            f" of price list {base['price_list']}" if base.get("price_list") else "") + "."
    elif source == "Standard Rate":
        text = "Base price is the item's standard rate (no Item Price applies)."
    elif source == "Entered":
        text = "The rate was given with the line."
    elif source == "None":
        text = "No price source: no Item Price applies and the item has no standard rate."
    else:
        text = "The origin of this price was not recorded (the line predates price decisions)."
    if rule:
        text += f" Pricing rule {rule['name']} ({rule.get('title') or rule.get('type')}) applies."
    return text


def explain_price(doctype="Sales Order", *, item_code, qty=1, customer=None, supplier=None, company=None,
                  currency=None, date=None, uom=None, rate=None):
    """What a line would cost and why, without saving anything.

    Builds a document of `doctype` with this one line and runs the pricing
    steps a real save runs, so the answer is what the document would get.
    `date` is the pricing date. A `rate` is treated as entered by the caller
    (rules then apply on top of it, as they do on a document).
    """
    _require_explainable(doctype)
    cls = _document_class(doctype)
    db = get_db()
    buying = doctype in _BUYING
    if not company:
        rows = db.get_all("Company", fields=["name"], limit=2)
        if len(rows) != 1:
            raise ValidationError("company is required")
        company = rows[0]["name"]
    if not db.exists("Item", item_code):
        raise ValidationError(f"Item {item_code} not found")
    party_field = "supplier" if buying else "customer"
    party = supplier if buying else customer
    if not party:
        raise ValidationError(f"{party_field} is required")
    date_field = "posting_date" if doctype.endswith("Invoice") else "transaction_date"
    line = {"item_code": item_code, "qty": flt(qty) or 1}
    if uom:
        line["uom"] = uom
    if rate is not None:
        line["rate"] = flt(rate)
    doc = cls({"company": company, party_field: party, date_field: date, "pricing_date": date,
               **({"currency": currency} if currency else {}), "items": [line]})
    from lambda_erp.controllers.defaults import set_default_currency
    from lambda_erp.controllers.item_price import pricing_date, resolve_price_list, set_item_defaults
    from lambda_erp.controllers.pricing_rule import apply_pricing_rules

    party_type = "Supplier" if buying else "Customer"
    set_default_currency(doc, party_type, party_field)
    item = doc.get("items")[0]
    token = _TRACE.set({})
    try:
        set_item_defaults(doc, party_type, party_field)
        if rate is not None:
            trace_base(item, "Entered", None, None, None)
            item["price_source"] = "Entered"
        apply_pricing_rules(doc)
        trace = _TRACE.get().get(id(item), {})
    finally:
        _TRACE.reset(token)
    base, rule = trace.get("base"), trace.get("applied_rule")
    return {
        "doctype": doctype, "item_code": item_code, "qty": flt(item.get("qty")), "uom": item.get("uom"),
        party_field: party, "company": company, "currency": doc._data.get("currency"),
        "pricing_date": pricing_date(doc),
        "price_list": resolve_price_list(doc, party_type, party_field),
        "rate": flt(item.get("rate")),
        "price_list_rate": item.get("price_list_rate"),
        "discount_percentage": flt(item.get("discount_percentage")),
        "discount_amount": flt(item.get("discount_amount")),
        "price_source": (base or {}).get("source"),
        "price_reference": (base or {}).get("reference"),
        "base": base,
        "pricing_rule": rule,
        "tied_with": trace.get("tied_with") or [],
        "rules_considered": trace.get("rules") or [],
        "explanation": _summary(base, rule, item),
    }


def explain_document_line(doctype, name, line=1):
    """The stored price decision of one line of a saved document (`line` is
    its row name or its 1-based position), with the validity of the Item
    Price and rule it names as they are now."""
    _require_explainable(doctype)
    cls = _document_class(doctype)
    from lambda_erp import permissions
    permissions.require_record_visible(doctype, name)
    doc = cls.load(name)
    rows = doc.get("items") or []
    if isinstance(line, int) or str(line).isdigit():
        index = int(line) - 1
        row = rows[index] if 0 <= index < len(rows) else None
    else:
        row = next((r for r in rows if r.get("name") == line), None)
    if row is None:
        raise ValidationError(f"{doctype} {name} has no line {line}")
    db = get_db()
    source, reference = row.get("price_source"), row.get("price_reference")
    base = {"source": source, "reference": reference} if source else None
    if source == "Item Price" and reference:
        price = db.get_value("Item Price", reference, ["price_list", "rate", "valid_from", "valid_upto", "customer", "supplier"])
        if price:
            base.update({"price_list": price.get("price_list"), "current_rate": flt(price.get("rate")),
                         "valid_from": price.get("valid_from") or None, "valid_upto": price.get("valid_upto") or None,
                         "party_specific": bool(price.get("customer") or price.get("supplier"))})
    rule = None
    if row.get("pricing_rule"):
        stored = db.get_value("Pricing Rule", row["pricing_rule"], ["name", "title", "priority", "rate_or_discount",
                                                                     "valid_from", "valid_upto", "enabled"])
        rule = {"name": row["pricing_rule"], **({
            "title": stored.get("title"), "priority": int(stored.get("priority") or 0),
            "type": stored.get("rate_or_discount"), "valid_from": stored.get("valid_from") or None,
            "valid_upto": stored.get("valid_upto") or None, "enabled": bool(stored.get("enabled"))} if stored else {})}
    manual = source == "Manual"
    carried = bool(flt(row.get("ignore_pricing_rule"))) and not manual
    from lambda_erp.controllers.item_price import pricing_date
    return {
        "doctype": doctype, "name": name, "line": row.get("name"), "item_code": row.get("item_code"),
        "qty": flt(row.get("qty")), "rate": flt(row.get("rate")), "price_list_rate": row.get("price_list_rate"),
        "discount_percentage": flt(row.get("discount_percentage")), "discount_amount": flt(row.get("discount_amount")),
        "pricing_date": pricing_date(doc), "docstatus": doc.docstatus,
        "price_source": source, "price_reference": reference, "base": base, "pricing_rule": rule,
        "manual": manual, "rules_skipped": bool(flt(row.get("ignore_pricing_rule")) or flt(doc.get("ignore_pricing_rule"))),
        "explanation": _summary(base, rule, row, manual=manual, carried=carried),
    }
