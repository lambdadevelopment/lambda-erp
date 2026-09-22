"""Shared default-setting helpers for transactional documents."""

from lambda_erp.database import get_db
from lambda_erp.utils import flt
from lambda_erp.exceptions import ValidationError
from lambda_erp.controllers.currency import get_exchange_rate


def set_default_company(doc):
    """Set company to the first available company if not specified."""
    if doc._data.get("company"):
        return
    db = get_db()
    companies = db.get_all("Company", fields=["name"], limit=1)
    if companies:
        doc._data["company"] = companies[0]["name"]


def set_default_currency(doc, party_type=None, party_field=None):
    """Default a transaction's currency and conversion_rate for new entries.

    Currency precedence: a value already on the doc -> the party's
    default_currency -> the company's (base/functional) default_currency ->
    "USD".

    conversion_rate is forced to 1.0 whenever the document currency equals the
    company's base currency. For a foreign currency the caller-supplied rate is
    kept; if none was supplied it is looked up from the Currency Exchange table
    for the document's date. A foreign currency with no rate on file raises
    (via get_exchange_rate) rather than silently booking at 1.0.
    """
    db = get_db()
    company = doc._data.get("company")
    base_currency = db.get_value("Company", company, "default_currency") if company else None

    currency = doc._data.get("currency")
    if not currency:
        if party_type and party_field:
            party = doc._data.get(party_field)
            if party:
                currency = db.get_value(party_type, party, "default_currency")
        currency = currency or base_currency or "USD"
        doc._data["currency"] = currency

    rate = flt(doc._data.get("conversion_rate"))
    if base_currency and currency == base_currency:
        rate = 1.0
    elif rate <= 0:
        # No rate supplied — look one up (carry-forward) for the doc's date.
        doc_date = doc._data.get("posting_date") or doc._data.get("transaction_date")
        rate = get_exchange_rate(currency, base_currency or "USD", doc_date)
    doc._data["conversion_rate"] = rate


def apply_external_source_defaults(doc):
    """Safe defaults for a document owned by an upstream system.

    `external_source` means another system already decided this document's
    content: it computed the rates under its own contract, and if goods moved
    it recorded that movement in its own ledger. So pricing rules are off by
    default here — re-deriving a rate that was already agreed is how an
    integration silently bills a different number than the source system shows.

    An explicit `ignore_pricing_rule: 0` still wins, for the caller that wants
    upstream identity but local pricing.
    """
    for field in ("external_source", "external_reference"):
        value = doc.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
            raise ValidationError(f"{field} must be a nonblank string without surrounding whitespace")
    if doc.get("external_reference") and not doc.get("external_source"):
        raise ValidationError("external_reference requires external_source")
    if doc._persisted:
        stored = get_db().get_value(doc.DOCTYPE, doc.name, ["external_source", "external_reference"])
        for field in ("external_source", "external_reference"):
            if stored and stored.get(field) and stored[field] != doc.get(field):
                raise ValidationError(f"Cannot change {field} after it has been assigned")
    seen = set()
    for item in doc.get("items") or []:
        ref = item.get("external_line_reference")
        if ref is None:
            continue
        if not isinstance(ref, str) or not ref.strip() or ref != ref.strip():
            raise ValidationError("external_line_reference must be a nonblank string without surrounding whitespace")
        if ref in seen:
            raise ValidationError("Duplicate external_line_reference in this document")
        seen.add(ref)
    if not doc._data.get("external_source"):
        return
    if doc._data.get("ignore_pricing_rule") is None:
        doc._data["ignore_pricing_rule"] = 1
    if doc.DOCTYPE in {"Sales Invoice", "Purchase Invoice", "POS Invoice"} and doc.get("update_stock") is None:
        # POS's database default is 1. Persist the bill-only policy explicitly
        # so loading or returning this document cannot enable stock movement.
        doc._data["update_stock"] = 0


def derived_pricing_fields(source, *, is_return=False):
    """Carry price/stock policy, but never reuse an imported document identity.

    Partial invoices and credits are new documents. Their connector must assign
    a new external_reference; external_source alone retains the stock guard.
    Returns reverse historical prices, regardless of today's pricing rules.
    """
    return {
        "ignore_pricing_rule": 1 if is_return else source.get("ignore_pricing_rule"),
        "external_source": source.get("external_source"),
    }


def derived_line_pricing_fields(item):
    return {field: item.get(field) for field in (
        "ignore_pricing_rule", "external_line_reference", "pricing_rule",
    )}
