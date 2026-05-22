"""Shared default-setting helpers for transactional documents."""

from lambda_erp.database import get_db
from lambda_erp.utils import flt
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
