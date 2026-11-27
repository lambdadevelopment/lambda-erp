"""Billing terms of a customer.

    customer_type        Company or Individual (a private person, e.g. a shop
                         customer). For statistics and for the deployment's
                         rules; it changes no calculation by itself.
    billing_cycle        Per Delivery (each delivery is invoiced on its own)
                         or Monthly (deliveries are collected and billed
                         together with the collective invoice). Empty means
                         Per Delivery.
    payment_terms_days   Days until an invoice is due, e.g. 30, 60 or 90.
                         Empty: the default of 30 days.
    credit_limit         A warning, never a block: an order or invoice that
                         takes the customer's open receivables over the limit
                         says so, and can still be submitted.

See docs/customer-terms.md.
"""
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import add_days, flt

CUSTOMER_TYPES = ("Company", "Individual")
BILLING_CYCLES = ("Per Delivery", "Monthly")
DEFAULT_PAYMENT_DAYS = 30
MAX_PAYMENT_DAYS = 365


def validate_customer_terms(customer):
    """Check the terms on a customer record being saved (a dict)."""
    kind = customer.get("customer_type")
    if kind not in (None, "") and kind not in CUSTOMER_TYPES:
        raise ValidationError(f"Customer: customer_type must be one of {', '.join(CUSTOMER_TYPES)}")
    cycle = customer.get("billing_cycle")
    if cycle not in (None, "") and cycle not in BILLING_CYCLES:
        raise ValidationError(f"Customer: billing_cycle must be one of {', '.join(BILLING_CYCLES)}")
    days = customer.get("payment_terms_days")
    if days not in (None, ""):
        try:
            number = float(str(days).strip())
        except ValueError:
            number = None
        if number is None or number != int(number):
            raise ValidationError("Customer: payment_terms_days must be a whole number of days")
        value = int(number)
        if not 0 <= value <= MAX_PAYMENT_DAYS:
            raise ValidationError(f"Customer: payment_terms_days must be between 0 and {MAX_PAYMENT_DAYS}")
        customer["payment_terms_days"] = value
    if flt(customer.get("credit_limit")) < 0:
        raise ValidationError("Customer: credit_limit cannot be negative")


def _terms(customer):
    if not customer:
        return {}
    db = get_db()
    columns = db._get_table_columns("Customer")
    fields = [f for f in ("customer_type", "billing_cycle", "payment_terms_days", "credit_limit") if f in columns]
    if not fields:
        return {}
    return db.get_value("Customer", customer, fields) or {}


def billing_cycle(customer):
    return _terms(customer).get("billing_cycle") or "Per Delivery"


def payment_terms_days(customer):
    days = _terms(customer).get("payment_terms_days")
    return DEFAULT_PAYMENT_DAYS if days in (None, "") else int(days)


def due_date(customer, posting_date):
    return add_days(posting_date, payment_terms_days(customer))


def open_receivables(customer, company=None):
    """What the customer owes on submitted sales invoices (credit notes net)."""
    where = ["customer = ?", "docstatus = 1"]
    params = [customer]
    if company:
        where.append("company = ?")
        params.append(company)
    rows = get_db().sql(
        'SELECT COALESCE(SUM(outstanding_amount), 0) AS open FROM "Sales Invoice" WHERE ' + " AND ".join(where), params)
    return flt(rows[0]["open"]) if rows else 0.0


def credit_check(doc):
    """{limit, open, this_document, total} when the document takes the customer
    over its credit limit, else None. Amounts in the company currency.

    A draft counts with its total; a submitted invoice is already part of the
    open receivables. Orders count with what is not yet invoiced of them."""
    customer = doc.get("customer")
    limit = flt(_terms(customer).get("credit_limit"))
    if not customer or limit <= 0 or flt(doc.get("is_return")):
        return None
    open_amount = open_receivables(customer, doc.get("company"))
    total = flt(doc.get("base_grand_total") or doc.get("grand_total"))
    if doc.DOCTYPE == "Sales Invoice":
        this = total if doc.docstatus == 0 else 0.0
    else:  # Sales Order: the part not invoiced yet
        this = total * max(0.0, 100 - flt(doc.get("per_billed"))) / 100 if doc.docstatus == 1 else total
    if open_amount + this <= limit + 0.005:
        return None
    return {"limit": limit, "open": flt(open_amount, 2), "this_document": flt(this, 2),
            "total": flt(open_amount + this, 2)}
