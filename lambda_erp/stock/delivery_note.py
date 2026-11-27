"""
Delivery Note.

Delivery Note records goods shipped to a customer from a Sales Order:
  Sales Order -> Delivery Note -> Sales Invoice

Key behaviors:
- Creates Stock Ledger Entries (stock OUT from warehouse)
- Creates GL entries (Dr: COGS, Cr: Stock In Hand)
- Updates delivered_qty on the linked Sales Order
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.database import get_db
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.controllers.defaults import set_default_currency, derived_line_pricing_fields as agreed_line_price
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.stock_ledger import (
    make_cancel_sl_entries,
    make_sl_entries,
    build_sell_side_sles,
    build_cost_basis_gl,
    reverse_stock_sles,
)
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries

class DeliveryNote(Document):
    DOCTYPE = "Delivery Note"
    SUBMITTABLE = True
    CHILD_TABLES = {
        "items": ("Delivery Note Item", None),
        "taxes": ("Sales Taxes and Charges", None),
    }
    # Pinned at submit to the PDF layout it was issued with (api.pdf_templates).
    SERVER_MANAGED_FIELDS = ("pdf_template_version",)
    PREFIX = "DN"

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
        if not self.customer:
            raise ValidationError("Customer is required")
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        if not self.posting_date:
            self.posting_date = nowdate()

        self._set_customer_name()
        # The price list is chosen by currency, so the currency comes first.
        set_default_currency(self, "Customer", "customer")
        self._set_item_defaults()

        if self.is_return:
            self._validate_return()

        calculate_taxes_and_totals(self)
        self._set_status()

    def _set_customer_name(self):
        if not self.customer_name and self.customer:
            db = get_db()
            self.customer_name = db.get_value("Customer", self.customer, "customer_name")

    def _set_item_defaults(self):
        # The same price resolution as orders and invoices: price list,
        # customer-specific prices, then pricing rules. Lines that come from
        # an order carry its price and are left alone.
        from lambda_erp.controllers.item_price import set_item_defaults
        from lambda_erp.controllers.pricing_rule import apply_pricing_rules
        set_item_defaults(self, "Customer", "customer")
        apply_pricing_rules(self)

    def _validate_return(self):
        from lambda_erp.workflow import validate_return
        validate_return(self)

    def _set_status(self):
        if self.docstatus == 0:
            self._data["status"] = "Draft"
        elif self.docstatus == 2:
            self._data["status"] = "Cancelled"
        elif self.docstatus == 1:
            if flt(self.per_billed) >= 100:
                self._data["status"] = "Completed"
            else:
                self._data["status"] = "To Bill"

    def on_submit(self):
        from lambda_erp.stock.reservation import validate_sellable
        validate_sellable(self)
        from lambda_erp.stock.consignment import validate_consignment_sale
        validate_consignment_sale(self)
        sl_entries = self._get_sl_entries()
        make_sl_entries(sl_entries)

        gl_entries = self._get_gl_entries()
        if gl_entries:
            make_gl_entries(gl_entries)

        # Shared lifecycle recalculates order progress after ledger hooks.

    def on_cancel(self):
        reversed_sles = reverse_stock_sles(self._get_sl_entries())
        if reversed_sles:
            make_cancel_sl_entries(reversed_sles)

        make_reverse_gl_entries(
            voucher_type=self.DOCTYPE,
            voucher_no=self.name,
        )

        # Shared lifecycle recalculates order progress after ledger hooks.

    def _get_sl_entries(self):
        return build_sell_side_sles(self, self.get("items"))

    def _get_gl_entries(self):
        return build_cost_basis_gl(self, remarks=f"Delivery Note {self.name}")

    def _update_sales_order_delivered(self, cancel=False):
        from lambda_erp.workflow import refresh_order_progress
        refresh_order_progress(self)

def _proposed_picks(so, item, qty):
    """The lines a delivery of `qty` of an order line should become: one per
    batch and storage location, earliest expiry first. Stock that cannot be
    picked now is left off (the order line stays open for it); when nothing
    can be picked, or the item needs neither batch nor location, the line is
    proposed as it is."""
    db = get_db()
    plain = [{"qty": qty}]
    if not item.get("item_code") or not item.get("warehouse"):
        return plain
    tracked = flt(db.get_value("Item", item["item_code"], "has_batch_no"))
    located = flt(db.get_value("Warehouse", item["warehouse"], "use_locations"))
    from lambda_erp.stock.serial import is_serial_item
    if not tracked and not located and not is_serial_item(item["item_code"]):
        return plain
    from lambda_erp.stock.reservation import suggest_picks
    picks = suggest_picks(item["item_code"], item["warehouse"], qty, customer=so.customer,
                          sales_order_item=item.get("name"))["picks"]
    return picks or plain


def make_delivery_note(sales_order_name):
    """Convert a Sales Order into a Delivery Note."""
    from lambda_erp.selling.sales_order import SalesOrder

    so = SalesOrder.load(sales_order_name)
    if so.docstatus != 1:
        raise ValidationError("Sales Order must be submitted before creating Delivery Note")

    dn = DeliveryNote(
        customer=so.customer,
        customer_name=so.customer_name,
        company=so.company,
        currency=so.currency,
        conversion_rate=so.conversion_rate,
        posting_date=nowdate(),
    )

    for item in so.get("items"):
        undelivered = flt(item.get("qty")) - flt(item.get("closed_qty")) - flt(item.get("delivered_qty"))
        if undelivered <= 0:
            continue
        # A batch-tracked item, or a warehouse with storage locations, needs a
        # batch / location per line: propose them first-expired-first-out. It
        # is a proposal on a draft; nothing is reserved or moved by it.
        for pick in _proposed_picks(so, item, undelivered):
            dn.append("items", _dict(
                # The price was agreed in the source document: pricing rules do not run again.
                **agreed_line_price(item),
                item_code=item.get("item_code"),
                item_name=item.get("item_name"),
                description=item.get("description"),
                qty=pick["qty"],
                uom=item.get("uom"), order_uom=item.get("order_uom"),
                conversion_factor=item.get("conversion_factor"),
                rate=item.get("rate"),
                warehouse=item.get("warehouse"),
                batch_no=pick.get("batch_no"),
                location=pick.get("location"),
                serial_no=pick.get("serial_no"),
                against_sales_order=so.name,
                so_detail=item.get("name"),
            ))

    for tax in so.get("taxes") or []:
        dn.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
        ))

    return dn

def make_delivery_return(dn_name):
    """Create a return Delivery Note (stock back in) from an existing Delivery Note."""
    original = DeliveryNote.load(dn_name)

    if original.docstatus != 1:
        raise ValidationError("Delivery Note must be submitted before creating a return")
    if original.is_return:
        raise ValidationError("Cannot create a return against a return")

    return_dn = DeliveryNote(
        customer=original.customer,
        company=original.company,
        currency=original.get("currency") or "USD",
        conversion_rate=original.get("conversion_rate") or 1.0,
        posting_date=nowdate(),
        is_return=1,
        return_against=original.name,
    )

    from lambda_erp.workflow import returnable_rows
    for item in returnable_rows(original):
        return_dn.append("items", _dict(
            # The price was agreed in the source document: pricing rules do not run again.
            **agreed_line_price(item),
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=-flt(item.get("qty")),
            uom=item.get("uom"), order_uom=item.get("order_uom"),
                conversion_factor=item.get("conversion_factor"),
            rate=flt(item.get("rate")),
            warehouse=item.get("warehouse"),
            batch_no=item.get("batch_no"),
            location=item.get("location"),
            serial_no=item.get("serial_no"),
            against_sales_order=item.get("against_sales_order"),
            so_detail=item.get("so_detail"),
        ))

    return return_dn


def _append_delivery_lines(invoice, note, only=None):
    """Invoice lines for what a delivery note delivered and has not been
    billed yet (only the note lines named in `only`, if given)."""
    from lambda_erp.controllers.defaults import derived_line_pricing_fields
    for item in note.get("items"):
        if only is not None and item.get("name") not in only:
            continue
        unbilled = flt(item.get("qty")) - flt(item.get("billed_qty"))
        if unbilled <= 0:
            continue
        invoice.append("items", _dict(
            **derived_line_pricing_fields(item),
            item_code=item.get("item_code"),
            item_name=item.get("item_name"),
            description=item.get("description"),
            qty=unbilled,
            uom=item.get("uom"), order_uom=item.get("order_uom"),
                conversion_factor=item.get("conversion_factor"),
            rate=item.get("rate"),
            price_list_rate=item.get("price_list_rate"),
            discount_percentage=item.get("discount_percentage"),
            discount_amount=item.get("discount_amount"),
            warehouse=item.get("warehouse"),
            sales_order=item.get("against_sales_order"),
            sales_order_item=item.get("so_detail"),
            delivery_note=note.name,
            dn_detail=item.get("name"),
        ))


def _tax_key(note):
    return tuple(sorted((str(tax.get("charge_type") or ""), str(tax.get("account_head") or ""),
                         flt(tax.get("rate"))) for tax in note.get("taxes") or []))


def _append_note_taxes(invoice, note):
    for tax in note.get("taxes") or []:
        invoice.append("taxes", _dict(
            charge_type=tax.get("charge_type"),
            account_head=tax.get("account_head"),
            description=tax.get("description"),
            rate=tax.get("rate"),
            tax_amount=0,
        ))


def make_sales_invoice_from_delivery_note(dn_name):
    """Bill what a delivery note delivered and has not been billed yet.

    Deliver first, invoice afterwards: the invoice lines point back to the
    delivery note lines (and to the order lines behind them), carry the
    delivered price, and do not move stock again."""
    from lambda_erp.accounting.sales_invoice import SalesInvoice

    note = DeliveryNote.load(dn_name)
    if note.docstatus != 1:
        raise ValidationError("Delivery Note must be submitted before creating a Sales Invoice")
    if note.is_return:
        raise ValidationError("A return delivery is credited from the original invoice, not invoiced")

    invoice = SalesInvoice(
        customer=note.customer,
        customer_name=note.customer_name,
        company=note.company,
        currency=note.currency,
        conversion_rate=note.conversion_rate,
        posting_date=nowdate(),
        update_stock=0,
    )
    _append_delivery_lines(invoice, note)
    if not invoice.get("items"):
        raise ValidationError(f"Delivery Note {note.name} is already billed in full")
    _append_note_taxes(invoice, note)
    return invoice


def unbilled_delivery_lines(customer=None, *, company=None, from_date=None, to_date=None, delivery_notes=None,
                            billing_cycle=None):
    """Delivered and not yet invoiced: one row per line of a submitted
    delivery note (not a return) with `unbilled_qty` = qty − billed_qty,
    oldest delivery first. Read-only; see make_collective_sales_invoice."""
    db = get_db()
    where = ["n.docstatus = 1", "COALESCE(n.is_return, 0) = 0", "COALESCE(n.discarded, 0) = 0",
             "i.qty - COALESCE(i.billed_qty, 0) > 1e-9"]
    params = []
    for column, value in (("n.customer", customer), ("n.company", company)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if from_date:
        where.append("n.posting_date >= ?")
        params.append(str(from_date)[:10])
    if to_date:
        where.append("n.posting_date <= ?")
        params.append(str(to_date)[:10])
    has_cycle = "billing_cycle" in db._get_table_columns("Customer")
    if billing_cycle and has_cycle:
        # Empty means per delivery (customer_terms.py).
        where.append("COALESCE(NULLIF(c.billing_cycle, ''), 'Per Delivery') = ?")
        params.append(billing_cycle)
    if delivery_notes:
        names = sorted({str(name) for name in delivery_notes})
        where.append(f"n.name IN ({', '.join('?' for _ in names)})")
        params += names
    rows = db.sql(
        'SELECT n.name AS delivery_note, n.posting_date, n.customer, n.customer_name, n.company, n.currency, '
        'i.name AS dn_detail, i.idx, i.item_code, i.item_name, i.uom, i.qty, COALESCE(i.billed_qty, 0) AS billed_qty, '
        'i.rate, i.against_sales_order AS sales_order, i.so_detail AS sales_order_item, '
        + ("COALESCE(NULLIF(c.billing_cycle, ''), 'Per Delivery') AS billing_cycle " if has_cycle
           else "'Per Delivery' AS billing_cycle ")
        + 'FROM "Delivery Note Item" i JOIN "Delivery Note" n ON n.name = i.parent '
        'LEFT JOIN "Customer" c ON c.name = n.customer '
        f'WHERE {" AND ".join(where)} ORDER BY n.posting_date, n.name, i.idx', params)
    result = []
    for row in rows:
        unbilled = flt(row["qty"]) - flt(row["billed_qty"])
        result.append({**dict(row), "qty": flt(row["qty"]), "billed_qty": flt(row["billed_qty"]),
                       "unbilled_qty": unbilled, "rate": flt(row["rate"]),
                       "unbilled_amount": flt(unbilled * flt(row["rate"]), 2)})
    return result


def make_collective_sales_invoice(customer=None, *, company=None, from_date=None, to_date=None,
                                  delivery_notes=None, lines=None):
    """One invoice for what several deliveries to a customer delivered and
    has not been billed yet: a monthly (collective) invoice.

    Takes every unbilled line of the customer's submitted delivery notes in
    the period, or of the named `delivery_notes`, or only the delivery note
    lines named in `lines`. Each invoice line keeps its delivery note and
    order line, so billing progress follows on both, and a line already
    billed is not offered again. A draft: submit rechecks every line against
    what is left to bill, under a lock."""
    from lambda_erp.accounting.sales_invoice import SalesInvoice

    if not customer and not delivery_notes and not lines:
        raise ValidationError("Name the customer (with a period) or the delivery notes to invoice")
    rows = unbilled_delivery_lines(customer, company=company, from_date=from_date, to_date=to_date,
                                   delivery_notes=delivery_notes)
    if lines:
        wanted = {str(line) for line in lines}
        rows = [row for row in rows if row["dn_detail"] in wanted]
        missing = wanted - {row["dn_detail"] for row in rows}
        if missing:
            raise ValidationError(
                f"Delivery note line(s) {', '.join(sorted(missing))} are not open to bill "
                "(not found, not submitted, a return, outside the filters or already billed)")
    if not rows:
        raise ValidationError("Nothing delivered is left to bill for this selection")
    for field, label in (("customer", "customers"), ("company", "companies"), ("currency", "currencies")):
        values = sorted({str(row[field] or "") for row in rows})
        if len(values) > 1:
            raise ValidationError(
                f"One invoice bills one customer: the selection spans several {label} ({', '.join(values)}); "
                f"narrow it down")
    chosen = {}
    for row in rows:
        chosen.setdefault(row["delivery_note"], set()).add(row["dn_detail"])
    notes = [DeliveryNote.load(name) for name in chosen]
    if len({_tax_key(note) for note in notes}) > 1:
        raise ValidationError(
            "The delivery notes carry different taxes (" + ", ".join(note.name for note in notes)
            + "); invoice them separately")
    rates = {flt(note.get("conversion_rate") or 1) for note in notes}
    if len(rates) > 1:
        raise ValidationError(
            "The delivery notes were made at different exchange rates; invoice them separately")
    first = notes[0]
    invoice = SalesInvoice(
        customer=first.customer,
        customer_name=first.customer_name,
        company=first.company,
        currency=first.currency,
        conversion_rate=first.conversion_rate,
        posting_date=nowdate(),
        update_stock=0,
    )
    for note in notes:
        _append_delivery_lines(invoice, note, only=chosen[note.name])
    _append_note_taxes(invoice, first)
    return invoice
