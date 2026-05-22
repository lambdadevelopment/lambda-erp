"""Shared default-setting helpers for transactional documents."""

from lambda_erp.database import get_db
from lambda_erp.utils import flt


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
    kept (defaulting to 1.0 until an exchange-rate source exists), so the GL's
    base amounts equal document amounts only when the rate is genuinely 1.0.
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
        rate = 1.0
    doc._data["conversion_rate"] = rate
