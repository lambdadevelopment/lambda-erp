"""Order actions beyond a single document save: closing what is left open on
order lines and one invoice for several deliveries. REST
(/api/actions/...), chat and MCP."""
from typing import Literal

from pydantic import Field

from api import mentions, services
from lambda_erp import permissions


class CloseOrderLines(mentions.Input):
    doctype: Literal["Sales Order", "Purchase Order"] = "Sales Order"
    order: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)
    lines: list[str] | None = Field(default=None, max_length=500)
    qty: float | None = Field(default=None, gt=0)


class ReopenOrderLines(mentions.Input):
    doctype: Literal["Sales Order", "Purchase Order"] = "Sales Order"
    order: str = Field(min_length=1, max_length=200)
    lines: list[str] | None = Field(default=None, max_length=500)


class CustomerAddresses(mentions.Input):
    customer: str = Field(min_length=1, max_length=200)


def list_customer_addresses(args):
    data = CustomerAddresses.model_validate(args)
    permissions.require_readable("Customer", data.customer)
    permissions.require("Address", "read")
    from lambda_erp.addresses import customer_addresses
    return customer_addresses(data.customer)


class CollectiveInvoice(mentions.Input):
    customer: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    from_date: str | None = Field(default=None, max_length=10)
    to_date: str | None = Field(default=None, max_length=10)
    delivery_notes: list[str] | None = Field(default=None, max_length=500)
    lines: list[str] | None = Field(default=None, max_length=2000)


def _user_id(user):
    return (user or {}).get("user_id") or (user or {}).get("name")


def close_lines(args, user=None):
    data = CloseOrderLines.model_validate(args)
    permissions.require_readable(data.doctype, data.order)
    permissions.require(data.doctype, "update")
    from lambda_erp.order_closing import close_order_lines
    return close_order_lines(data.doctype, data.order, reason=data.reason, lines=data.lines, qty=data.qty,
                             closed_by=_user_id(user))


def reopen_lines(args):
    data = ReopenOrderLines.model_validate(args)
    permissions.require_readable(data.doctype, data.order)
    permissions.require(data.doctype, "update")
    from lambda_erp.order_closing import reopen_order_lines
    return reopen_order_lines(data.doctype, data.order, lines=data.lines)


def prepare_collective_invoice(args):
    data = CollectiveInvoice.model_validate(args)
    permissions.require_writable("Sales Invoice", "create", keeps_hidden_fields=True)
    permissions.require("Delivery Note", "read")
    permissions.require_in_scope("customer", data.customer, optional=True)
    for name in data.delivery_notes or []:
        permissions.require_readable("Delivery Note", name)
    from lambda_erp.stock.delivery_note import make_collective_sales_invoice
    invoice = make_collective_sales_invoice(
        data.customer, company=data.company, from_date=data.from_date, to_date=data.to_date,
        delivery_notes=data.delivery_notes, lines=data.lines)
    permissions.require_in_scope("customer", invoice.customer)
    invoice.save()
    return services.load_document("sales-invoice", invoice.name)


def _credit_limit_warning(doc):
    """A warning, never a block: the order or invoice takes the customer over
    its credit limit (lambda_erp/customer_terms.py)."""
    if doc.DOCTYPE not in ("Sales Order", "Sales Invoice") or doc.docstatus == 2:
        return []
    from lambda_erp.customer_terms import credit_check
    check = credit_check(doc)
    if not check:
        return []
    return [{"code": "credit_limit_exceeded", **check, "message": (
        f"Credit limit of {check['limit']:,.2f} exceeded: open receivables {check['open']:,.2f}"
        + (f" plus this document {check['this_document']:,.2f}" if check["this_document"] else "")
        + f" = {check['total']:,.2f}. This is a warning; the document can still be submitted.")}]


def initialize_order_actions():
    if _credit_limit_warning not in services.DOCUMENT_WARNING_PROVIDERS:
        services.DOCUMENT_WARNING_PROVIDERS.append(_credit_limit_warning)
    services.register_action(
        "close_order_lines", close_lines,
        description=(
            "Close what is left OPEN on lines of a SUBMITTED sales order (doctype 'Sales Order', the default) or "
            "purchase order ('Purchase Order'): the customer cancelled the rest, or the supplier will never send it. "
            "All lines with something open, or `lines` (row names or positions); `qty` closes only part of one "
            "line. A reason is required. The closed quantity is no longer to deliver, receive or bill; deliveries "
            "and invoices already posted stay as they are; a reservation for it is released. Delivered or invoiced "
            "quantities cannot be closed. Confirm with the user which lines before closing."),
        parameters=CloseOrderLines.model_json_schema(), minimum_role="manager", pass_user=True)
    services.register_action(
        "reopen_order_lines", reopen_lines,
        description=(
            "Undo close_order_lines on a submitted sales or purchase order: the closed quantity of `lines` (or of "
            "every closed line) is open again. A released reservation is not restored; reserve again if needed."),
        parameters=ReopenOrderLines.model_json_schema(), minimum_role="manager")
    services.register_action(
        "list_customer_addresses", list_customer_addresses,
        description=(
            "READ-ONLY: a customer's saved addresses (with label, for invoices / deliveries, defaults, delivery "
            "instructions) and the delivery and invoice addresses its documents used that are NOT on record "
            "(typed for one order), newest first with times used, last used and the last document. Use it when "
            "the user asks which addresses a customer has or has used, or when an order names an address that "
            "differs from the default. To use a saved one, set shipping_address / billing_address (its id) on the "
            "draft; to use an earlier or a new one-off address, set shipping_address_display / "
            "billing_address_display to the text and leave the link empty. Never invent an address."),
        parameters=CustomerAddresses.model_json_schema(), minimum_role="viewer")
    services.register_action(
        "prepare_collective_invoice", prepare_collective_invoice,
        description=(
            "Create ONE DRAFT Sales Invoice for what several deliveries delivered and has not been billed yet "
            "(monthly / collective invoice): every unbilled line of the customer's submitted delivery notes, "
            "optionally from_date / to_date (posting dates, YYYY-MM-DD), or the named delivery_notes, or only the "
            "delivery note lines in `lines`. Each invoice line keeps its delivery note and order line; already "
            "billed lines are never taken again. Check what is open first with the unbilled-deliveries report. "
            "Nothing is posted until the draft is submitted."),
        parameters=CollectiveInvoice.model_json_schema(), minimum_role="manager")
