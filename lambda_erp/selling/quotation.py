"""
Quotation (Sales Offer / Proposal).

A Quotation is the first step in the sales cycle:
  Quotation -> Sales Order -> Delivery Note -> Sales Invoice

It represents an offer to a customer with pricing, validity dates, and terms.
Key behaviors:
- Does NOT create GL entries (it's just a proposal)
- Can be converted to a Sales Order via make_sales_order()
- Tracks status: Draft -> Submitted/Open -> Ordered/Lost/Expired
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate, new_name
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.exceptions import ValidationError

class Quotation(Document):
    DOCTYPE = "Quotation"
    CHILD_TABLES = {
        "items": ("Quotation Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    PREFIX = "QTN"

    LINK_FIELDS = {
        "customer": "Customer",
        "company": "Company",
    }
    CHILD_LINK_FIELDS = {
        "items": {
            "item_code": "Item",
            "warehouse": "Warehouse",
        },
        "taxes": {
            "account_head": "Account",
            "cost_center": "Cost Center",
        },
    }

    def validate(self):
        """Validate the quotation before saving.

        Mirrors the reference implementation's Quotation.validate() which calls:
        - super().validate() (SellingController -> AccountsController -> TransactionBase)
        - validate_uom_is_integer
        - validate_valid_till
        - set_customer_name
        """
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.transaction_date:
            self.transaction_date = nowdate()

        self._validate_valid_till()
        self._set_customer_name()
        self._set_item_defaults()

        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        apply_pricing_rules(self)

        # Calculate taxes and totals (the core shared calculation)
        calculate_taxes_and_totals(self)

    def _validate_valid_till(self):
        if self.valid_till and getdate(self.valid_till) < getdate(self.transaction_date):
            raise ValidationError("Valid till date cannot be before transaction date")

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

    def _set_item_defaults(self):
        """Fill in item names and rates from master data."""
        db = get_db()
        for item in self.get("items"):
            if item.get("item_code") and not item.get("item_name"):
                item_data = db.get_value(
                    "Item", item["item_code"], ["item_name", "description", "stock_uom", "standard_rate"]
                )
                if item_data:
                    item["item_name"] = item_data.item_name
                    item["description"] = item.get("description") or item_data.description
                    item["uom"] = item.get("uom") or item_data.stock_uom
                    if not item.get("rate") and not item.get("price_list_rate"):
                        item["rate"] = flt(item_data.standard_rate)

    def on_submit(self):
        """On submit, set status to Open."""
        self._data["status"] = "Open"
        self._persist()

    def on_cancel(self):
        pass

    def is_expired(self):
        """Check if quotation validity has expired."""
        if self.valid_till:
            return getdate(self.valid_till) < getdate(nowdate())
        return False

def make_sales_order(quotation_name):
    """Convert a Quotation into a Sales Order.

    This is the standard document flow: Quotation -> Sales Order.

    the reference implementation uses get_mapped_doc() which copies fields from source to target
    based on a mapping configuration. We do the same thing directly.
    """
    from lambda_erp.selling.sales_order import SalesOrder

    db = get_db()
    quotation = Quotation.load(quotation_name)

    if quotation.docstatus != 1:
        raise ValidationError("Quotation must be submitted before creating Sales Order")

    if quotation.is_expired():
        raise ValidationError("Validity period of this quotation has ended")

    # Map Quotation fields to Sales Order
    so = SalesOrder(
        customer=quotation.customer,
        customer_name=quotation.customer_name,
        company=quotation.company,
        currency=quotation.currency,
        conversion_rate=quotation.conversion_rate,
        transaction_date=nowdate(),
    )

    # Map items
    for item in quotation.get("items"):
        so.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=item.get("qty"),
            uom=item.get("uom"),
            rate=item.get("rate"),
            price_list_rate=item.get("price_list_rate"),
            discount_percentage=item.get("discount_percentage"),
            warehouse=item.get("warehouse"),
            quotation_item=item.get("name"),
        ))

    # Map taxes
    for tax in quotation.get("taxes") or []:
        so.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,  # will be recalculated
            included_in_print_rate=tax.get("included_in_print_rate"),
        ))

    # Update quotation status
    db.set_value("Quotation", quotation_name, "status", "Ordered")

    return so

def make_sales_invoice_from_quotation(quotation_name):
    """Convert a Quotation directly into a Sales Invoice (skip Sales Order)."""
    from lambda_erp.accounting.sales_invoice import SalesInvoice

    db = get_db()
    quotation = Quotation.load(quotation_name)

    if quotation.docstatus != 1:
        raise ValidationError("Quotation must be submitted before creating Sales Invoice")

    if quotation.is_expired():
        raise ValidationError("Validity period of this quotation has ended")

    sinv = SalesInvoice(
        customer=quotation.customer,
        customer_name=quotation.customer_name,
        company=quotation.company,
        currency=quotation.currency,
        conversion_rate=quotation.conversion_rate,
        posting_date=nowdate(),
    )

    for item in quotation.get("items"):
        sinv.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=item.get("qty"),
            uom=item.get("uom"),
            rate=item.get("rate"),
            price_list_rate=item.get("price_list_rate"),
            discount_percentage=item.get("discount_percentage"),
            warehouse=item.get("warehouse"),
        ))

    for tax in quotation.get("taxes") or []:
        sinv.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
            included_in_print_rate=tax.get("included_in_print_rate"),
        ))

    db.set_value("Quotation", quotation_name, "status", "Ordered")

    return sinv

def make_delivery_note_from_quotation(quotation_name):
    """Convert a Quotation directly into a Delivery Note (skip Sales Order)."""
    from lambda_erp.stock.delivery_note import DeliveryNote

    db = get_db()
    quotation = Quotation.load(quotation_name)

    if quotation.docstatus != 1:
        raise ValidationError("Quotation must be submitted before creating Delivery Note")

    if quotation.is_expired():
        raise ValidationError("Validity period of this quotation has ended")

    dn = DeliveryNote(
        customer=quotation.customer,
        customer_name=quotation.customer_name,
        company=quotation.company,
        currency=quotation.currency,
        conversion_rate=quotation.conversion_rate,
        posting_date=nowdate(),
    )

    for item in quotation.get("items"):
        dn.append("items", _dict(
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=item.get("qty"),
            uom=item.get("uom"),
            rate=item.get("rate"),
            warehouse=item.get("warehouse"),
        ))

    for tax in quotation.get("taxes") or []:
        dn.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
        ))

    db.set_value("Quotation", quotation_name, "status", "Ordered")

    return dn
