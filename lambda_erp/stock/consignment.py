"""Consignment: own stock that lies at a customer until it is used.

A **consignment warehouse** is a warehouse of ours with a customer on it
(`Warehouse.consignment_customer`). Goods in it are still ours: they are in
our stock and in our stock value.

    Stocking     own warehouse -> consignment warehouse      Material Transfer, no sale
    Withdrawal   the customer used goods from it             billed, and taken out of stock, once
    Recall       consignment warehouse -> own warehouse       Material Transfer, no sale
    Replacement  a stocking that follows a withdrawal         its own document and status

Stocking and recall are plain Stock Entries. A **Consignment Withdrawal** is
the record of what the customer reported as used. Submitting it creates one
Sales Invoice that also takes the goods out of the consignment warehouse
(`update_stock`), so quantity, batch, cost of goods and revenue are booked
exactly once, by one document. The withdrawal itself posts nothing.

See docs/consignment.md.
"""
import hashlib
import json

from lambda_erp.database import get_db
from lambda_erp.exceptions import ConflictError, ValidationError
from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, now, nowdate

NOT_REQUESTED, PENDING, DONE, DROPPED = "Not Requested", "Pending", "Done", "Cancelled"


def consignment_customer(warehouse):
    """The customer a consignment warehouse belongs to, or None for one of
    our ordinary warehouses."""
    if not warehouse:
        return None
    db = get_db()
    if "consignment_customer" not in db._get_table_columns("Warehouse"):
        return None
    return db.get_value("Warehouse", warehouse, "consignment_customer") or None


def validate_consignment_sale(doc):
    """Goods in a consignment warehouse are set out for one customer: a sale
    out of it goes to that customer. For anyone else they are recalled first."""
    if flt(doc.get("is_return")):
        return
    for idx, row in enumerate(doc.get("items") or [], 1):
        owner = consignment_customer(row.get("warehouse"))
        if owner and owner != doc.get("customer"):
            raise ValidationError(
                f"{doc.DOCTYPE} row {idx}: warehouse {row['warehouse']} is the consignment warehouse of customer "
                f"{owner}. Recall the goods to one of your own warehouses before selling them to someone else")


def _serials(value):
    from lambda_erp.stock.serial import parse_serials
    return parse_serials(value)


def content_hash(warehouse, items, replenish=False):
    """What a withdrawal report says, reduced to what matters: the same
    report gives the same hash whatever the order of its lines."""
    lines = sorted(
        (str(row.get("item_code") or ""), str(row.get("batch_no") or ""), str(row.get("location") or ""),
         round(flt(row.get("qty")), 6), None if row.get("rate") in (None, "") else round(flt(row.get("rate")), 6),
         sorted(_serials(row.get("serial_no"))))
        for row in items or [])
    payload = json.dumps({"warehouse": warehouse, "lines": lines, "replenish": bool(flt(replenish))},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class ConsignmentWithdrawal(Document):
    DOCTYPE = "Consignment Withdrawal"
    SUBMITTABLE = True
    CHILD_TABLES = {"items": ("Consignment Withdrawal Item", None)}
    PREFIX = "CWD"
    REQUIRED_FIELDS = ("warehouse", "items")
    CHILD_REQUIREMENTS = {"items": {"required": ["item_code", "qty"]}}
    SERVER_MANAGED_FIELDS = ("sales_invoice", "replacement_stock_entry", "replenishment_status", "content_hash")
    CONDITIONAL_REQUIREMENTS = (
        "Records what a customer reported as used from their consignment warehouse. warehouse must be a consignment "
        "warehouse (Warehouse.consignment_customer); customer is taken from it. Rows: item_code, positive qty, and "
        "batch_no / location where the item or warehouse needs them; rate is optional (else the customer's price).",
        "Submitting creates and submits ONE Sales Invoice that bills the customer and takes the goods out of the "
        "consignment warehouse. Do not create a delivery note or invoice for the same goods as well.",
        "replenish=1 additionally drafts a Material Transfer that restocks the withdrawn quantities from "
        "replenish_from_warehouse (default: the warehouse's replenishment_warehouse). It is a separate document "
        "with its own status; the withdrawal is billed whether or not the replacement can be shipped.",
        "external_source + external_reference identify the customer's report. The same report twice is refused "
        "here; use the record_consignment_withdrawal action, which answers a repeated report with the first result.",
        "Cancelling cancels its invoice. If the invoice is paid, cancel the payment first or credit it with a "
        "return invoice instead.",
    )
    LINK_FIELDS = {
        "warehouse": "Warehouse",
        "customer": "Customer",
        "company": "Company",
        "replenish_from_warehouse": "Warehouse",
    }
    CHILD_LINK_FIELDS = {"items": {"item_code": "Item"}}

    def validate(self):
        db = get_db()
        owner = consignment_customer(self.warehouse)
        if not owner:
            raise ValidationError(
                f"Warehouse {self.warehouse} is not a consignment warehouse (it has no consignment customer)")
        if self._data.get("customer") and self.customer != owner:
            raise ValidationError(f"Warehouse {self.warehouse} is the consignment warehouse of {owner}, not {self.customer}")
        self._data["customer"] = owner
        self._data["company"] = db.get_value("Warehouse", self.warehouse, "company")
        if not self.posting_date:
            self.posting_date = nowdate()
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        for idx, row in enumerate(self.get("items"), 1):
            if not row.get("item_code"):
                raise ValidationError(f"Consignment Withdrawal row {idx}: Item Code is required")
            if not flt(row.get("qty")) > 0:
                raise ValidationError(f"Consignment Withdrawal row {idx}: Qty must be positive")
            row["qty"] = flt(row["qty"])
            if row.get("rate") in ("",):
                row["rate"] = None
            if row.get("rate") is not None and flt(row["rate"]) < 0:
                raise ValidationError(f"Consignment Withdrawal row {idx}: Rate cannot be negative")
            if not row.get("item_name"):
                row["item_name"] = db.get_value("Item", row["item_code"], "item_name")

        source, reference = self._data.get("external_source"), self._data.get("external_reference")
        for field, value in (("external_source", source), ("external_reference", reference)):
            if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
                raise ValidationError(f"{field} must be a nonblank string without surrounding whitespace, or absent")
        if reference and not source:
            raise ValidationError("external_reference requires external_source")
        self._data["content_hash"] = content_hash(self.warehouse, self.get("items"), self._data.get("replenish"))
        if reference:
            twin = find_withdrawal(source, reference, exclude=self.name)
            if twin:
                raise ConflictError(
                    f"Report {source} / {reference} is already recorded as Consignment Withdrawal {twin['name']}")

        if flt(self._data.get("replenish")):
            origin = self._data.get("replenish_from_warehouse") or db.get_value(
                "Warehouse", self.warehouse, "replenishment_warehouse")
            if not origin:
                raise ValidationError(
                    "Replenishment needs a source: set replenish_from_warehouse, or the consignment "
                    "warehouse's replenishment_warehouse")
            if origin == self.warehouse or consignment_customer(origin):
                raise ValidationError("Replenishment comes from one of your own warehouses")
            self._data["replenish_from_warehouse"] = origin
        if not self._data.get("replenishment_status") or self.docstatus == 0:
            self._data["replenishment_status"] = NOT_REQUESTED

    def _validate_server_managed_fields(self):
        # content_hash is recomputed on every save of a draft.
        stored = self._data.get("content_hash")
        if not self._persisted:
            for field in self.SERVER_MANAGED_FIELDS:
                self._data.pop(field, None)
            return
        current = get_db().get_value(self.DOCTYPE, self.name, list(self.SERVER_MANAGED_FIELDS)) or {}
        for field in self.SERVER_MANAGED_FIELDS:
            if field == "content_hash":
                continue
            if (self._data.get(field) or None) != (current.get(field) or None):
                raise ValidationError(f"{self.DOCTYPE}: {field} is server-managed and cannot be supplied or changed")
        self._data["content_hash"] = stored

    def on_submit(self):
        """Bill and withdraw once, through one Sales Invoice."""
        from lambda_erp.accounting.sales_invoice import SalesInvoice

        invoice = SalesInvoice(
            customer=self.customer, company=self.company, posting_date=self.posting_date, update_stock=1,
            remarks=f"Consignment withdrawal {self.name}"
                    + (f" ({self.external_reference})" if self._data.get("external_reference") else ""))
        for row in self.get("items"):
            line = _dict(item_code=row["item_code"], qty=flt(row["qty"]), warehouse=self.warehouse,
                         batch_no=row.get("batch_no") or None, location=row.get("location") or None,
                         serial_no=row.get("serial_no") or None)
            if row.get("rate") is not None:
                line["rate"] = flt(row["rate"])
            invoice.append("items", line)
        # The sale is taxed like any other invoice of the company.
        from lambda_erp.controllers.tax_defaults import apply_default_taxes
        apply_default_taxes(invoice)
        invoice.save()
        invoice.submit()
        self._data["sales_invoice"] = invoice.name
        if flt(self._data.get("replenish")):
            entry = make_consignment_stocking(
                self.warehouse, [{"item_code": row["item_code"], "qty": row["qty"], "location": row.get("location")}
                                 for row in self.get("items")],
                from_warehouse=self.replenish_from_warehouse)
            entry.remarks = f"Replacement for consignment withdrawal {self.name}"
            entry.save()
            self._data["replacement_stock_entry"] = entry.name
            self._data["replenishment_status"] = PENDING
        self._data["status"] = "Billed"
        self._persist(commit=False)

    def on_cancel(self):
        from lambda_erp.accounting.sales_invoice import SalesInvoice
        from lambda_erp.stock.stock_entry import StockEntry

        db = get_db()
        if self.sales_invoice and db.get_value("Sales Invoice", self.sales_invoice, "docstatus") == 1:
            invoice = SalesInvoice.load(self.sales_invoice)
            invoice._cancelled_by_withdrawal = True
            try:
                invoice.cancel()
            except ValidationError as exc:
                raise ValidationError(
                    f"Cannot cancel {self.name}: its invoice {self.sales_invoice} cannot be cancelled ({exc}). "
                    f"Credit it with a return invoice instead, which also puts the goods back") from exc
        if self.replacement_stock_entry:
            state = db.get_value("Stock Entry", self.replacement_stock_entry, ["docstatus", "discarded"])
            if state and state.get("docstatus") == 0 and not state.get("discarded"):
                StockEntry.load(self.replacement_stock_entry).discard()
        db.set_value(self.DOCTYPE, self.name, {"status": "Cancelled"})
        sync_replenishment(self.replacement_stock_entry)


def find_withdrawal(external_source, external_reference, exclude=None):
    """The active (not cancelled, not discarded) withdrawal recorded for a
    customer's report, or None."""
    if not external_source or not external_reference:
        return None
    rows = get_db().sql(
        'SELECT * FROM "Consignment Withdrawal" WHERE external_source = ? AND external_reference = ? '
        'AND docstatus < 2 AND COALESCE(discarded, 0) = 0 AND name <> ?',
        [external_source, external_reference, exclude or ""])
    return rows[0] if rows else None


def record_withdrawal(warehouse, items, *, external_source=None, external_reference=None, posting_date=None,
                      replenish=False, replenish_from_warehouse=None, remarks=None):
    """Record and bill a customer's withdrawal report, once.

    With `external_source` and `external_reference` (the customer's report
    number) the call is idempotent:

    - the same report with the same content returns what the first call
      created (`replayed: True`); nothing is posted again;
    - the same report with different content is refused (ConflictError, HTTP
      409): cancel the recorded withdrawal first, then send the correction.

    Without a reference every call is a new withdrawal.
    """
    db = get_db()
    if not db._in_transaction:
        with db.atomic():
            return record_withdrawal(
                warehouse, items, external_source=external_source, external_reference=external_reference,
                posting_date=posting_date, replenish=replenish,
                replenish_from_warehouse=replenish_from_warehouse, remarks=remarks)
    if external_reference:
        existing = find_withdrawal(external_source, external_reference)
        if existing:
            if existing["content_hash"] == content_hash(warehouse, items, replenish):
                return {**_result(existing["name"]), "replayed": True}
            raise ConflictError(
                f"Report {external_source} / {external_reference} was already recorded as Consignment Withdrawal "
                f"{existing['name']} with different content. Cancel that withdrawal before recording the correction")
    doc = ConsignmentWithdrawal(
        warehouse=warehouse, posting_date=posting_date, external_source=external_source or None,
        external_reference=external_reference or None, replenish=1 if replenish else 0,
        replenish_from_warehouse=replenish_from_warehouse, remarks=remarks)
    for row in items or []:
        doc.append("items", _dict({key: row.get(key) for key in ("item_code", "qty", "batch_no", "location", "rate", "uom", "serial_no")
                                   if row.get(key) not in (None, "")}))
    doc.save()
    doc.submit()
    return {**_result(doc.name), "replayed": False}


def _result(name):
    row = get_db().get_value("Consignment Withdrawal", name, [
        "name", "customer", "warehouse", "status", "sales_invoice", "replacement_stock_entry",
        "replenishment_status", "external_source", "external_reference"])
    return dict(row)


def sync_replenishment(stock_entry):
    """Keep a withdrawal's replenishment_status in step with its replacement
    Stock Entry (called when a stock entry is submitted, cancelled or discarded)."""
    if not stock_entry:
        return
    db = get_db()
    if not db._get_table_columns("Consignment Withdrawal"):
        return
    rows = db.sql('SELECT name, replenishment_status FROM "Consignment Withdrawal" '
                  'WHERE replacement_stock_entry = ?', [stock_entry])
    if not rows:
        return
    state = db.get_value("Stock Entry", stock_entry, ["docstatus", "discarded"]) or {}
    status = (DONE if state.get("docstatus") == 1
              else DROPPED if state.get("docstatus") == 2 or state.get("discarded") else PENDING)
    for row in rows:
        if row["replenishment_status"] != status:
            db.set_value("Consignment Withdrawal", row["name"], {"replenishment_status": status, "modified": now()})


# -- stocking and recall ------------------------------------------------------

def make_consignment_stocking(warehouse, items, *, from_warehouse=None):
    """A draft Material Transfer that stocks a consignment warehouse with
    `items` ([{item_code, qty, location?}]) from one of our warehouses.

    Batches and source locations are proposed first-expired-first-out where
    the source can supply the whole quantity; otherwise the row is left for
    the picker (the draft cannot be submitted until it is complete). Nothing
    moves, and nothing is invoiced, by a stocking."""
    from lambda_erp.stock.reservation import suggest_picks
    from lambda_erp.stock.stock_entry import StockEntry

    db = get_db()
    owner = consignment_customer(warehouse)
    if not owner:
        raise ValidationError(f"Warehouse {warehouse} is not a consignment warehouse")
    origin = from_warehouse or db.get_value("Warehouse", warehouse, "replenishment_warehouse")
    if not origin:
        raise ValidationError("from_warehouse is required (the consignment warehouse has no replenishment_warehouse)")
    if origin == warehouse or consignment_customer(origin):
        raise ValidationError("A consignment warehouse is stocked from one of your own warehouses")
    entry = StockEntry(company=db.get_value("Warehouse", warehouse, "company"),
                       stock_entry_type="Material Transfer", posting_date=nowdate(),
                       from_warehouse=origin, to_warehouse=warehouse)
    for row in items or []:
        qty = flt(row.get("qty"))
        if not row.get("item_code") or not qty > 0:
            raise ValidationError("Every stocking row needs an item_code and a positive qty")
        target = {"t_location": row.get("location")} if row.get("location") else {}
        got = suggest_picks(row["item_code"], origin, qty, customer=owner)
        picks = got["picks"] if got["shortfall_qty"] <= 1e-9 else [{"qty": qty}]
        for pick in picks:
            entry.append("items", _dict(
                item_code=row["item_code"], qty=flt(pick["qty"]), s_warehouse=origin, t_warehouse=warehouse,
                batch_no=pick.get("batch_no"), s_location=pick.get("location"),
                serial_no=pick.get("serial_no"), **target))
    if not entry.get("items"):
        raise ValidationError("Nothing to stock")
    return entry


def make_consignment_recall(warehouse, to_warehouse, *, items=None, to_location=None):
    """A draft Material Transfer that brings goods back from a consignment
    warehouse: everything in it, or `items` ([{item_code, qty?, batch_no?}]).
    No sale, no credit note: the goods were ours all along."""
    from lambda_erp.stock.stock_entry import StockEntry
    from lambda_erp.stock.stock_ledger import get_stock_details

    db = get_db()
    if not consignment_customer(warehouse):
        raise ValidationError(f"Warehouse {warehouse} is not a consignment warehouse")
    if not db.exists("Warehouse", to_warehouse) or to_warehouse == warehouse or consignment_customer(to_warehouse):
        raise ValidationError("Goods are recalled to one of your own warehouses")
    stock = [row for row in get_stock_details(warehouse=warehouse) if flt(row["actual_qty"]) > 0]
    entry = StockEntry(company=db.get_value("Warehouse", warehouse, "company"),
                       stock_entry_type="Material Transfer", posting_date=nowdate(),
                       from_warehouse=warehouse, to_warehouse=to_warehouse)

    def add(row, qty):
        from lambda_erp.stock.serial import propose_serials
        entry.append("items", _dict(
            item_code=row["item_code"], qty=qty, s_warehouse=warehouse, t_warehouse=to_warehouse,
            batch_no=row["batch_no"] or None, s_location=row["location"] or None, t_location=to_location or None,
            serial_no=propose_serials(row["item_code"], warehouse, qty, row["batch_no"], row["location"])))

    if not items:
        for row in sorted(stock, key=lambda r: (r["item_code"], r["batch_no"], r["location"])):
            add(row, flt(row["actual_qty"]))
    else:
        for wanted in items:
            matching = [row for row in stock if row["item_code"] == wanted.get("item_code")
                        and (not wanted.get("batch_no") or row["batch_no"] == wanted["batch_no"])]
            remaining = flt(wanted.get("qty")) or sum(flt(row["actual_qty"]) for row in matching)
            if not matching or remaining > sum(flt(row["actual_qty"]) for row in matching) + 1e-9:
                raise ValidationError(
                    f"Warehouse {warehouse} does not hold {remaining:g} of {wanted.get('item_code')}"
                    + (f" batch {wanted['batch_no']}" if wanted.get("batch_no") else ""))
            for row in sorted(matching, key=lambda r: (r["batch_no"], r["location"])):
                take = min(remaining, flt(row["actual_qty"]))
                if take > 1e-9:
                    add(row, take)
                    remaining -= take
    if not entry.get("items"):
        raise ValidationError(f"Consignment warehouse {warehouse} is empty")
    return entry
