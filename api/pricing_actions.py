"""Price explanation over REST (/api/actions/explain_price), chat and MCP."""
from pydantic import Field

from api import mentions, services
from lambda_erp import permissions
from lambda_erp.controllers import price_resolution


class ExplainPrice(mentions.Input):
    doctype: str = Field(default="Sales Order", max_length=100)
    # A line of a saved document ...
    name: str | None = Field(default=None, max_length=200)
    line: str | int | None = None
    # ... or a what-if.
    item_code: str | None = Field(default=None, max_length=200)
    qty: float = Field(default=1, gt=0)
    customer: str | None = Field(default=None, max_length=200)
    supplier: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    currency: str | None = Field(default=None, max_length=10)
    date: str | None = Field(default=None, max_length=10)
    uom: str | None = Field(default=None, max_length=50)
    rate: float | None = Field(default=None, ge=0)


def explain(args):
    data = ExplainPrice.model_validate(args)
    doctype = services.SLUG_TO_DOCTYPE.get(data.doctype, data.doctype)
    if data.name:
        return price_resolution.explain_document_line(doctype, data.name, data.line or 1)
    permissions.require_in_scope("customer", data.customer, optional=True)
    if not data.item_code:
        raise ValueError("Give item_code (with customer or supplier) for a what-if, or name and line of a saved document")
    return price_resolution.explain_price(
        doctype, item_code=data.item_code, qty=data.qty, customer=data.customer, supplier=data.supplier,
        company=data.company, currency=data.currency, date=data.date, uom=data.uom, rate=data.rate)


def initialize_pricing_actions():
    services.register_action(
        "explain_price", explain,
        description=(
            "READ-ONLY. Why a price is what it is. Either a what-if: item_code, qty, customer (or supplier with a "
            "purchase doctype), optional date (the pricing date), currency, company: returns the rate a document "
            "would get, the base price source (Item Price / standard rate / none), the pricing rule applied, the "
            "other rules that matched and any tie. Or a saved line: doctype, name and line (position or row name): "
            "returns the decision stored on that line. Use it before quoting a price and to answer 'why this price'. "
            "Nothing is saved or changed."),
        parameters=ExplainPrice.model_json_schema(), minimum_role="viewer")
