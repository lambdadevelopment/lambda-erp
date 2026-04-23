"""Bridge between FastAPI request data and lambda_erp Document classes."""

from lambda_erp.utils import _dict
from lambda_erp.database import get_db

from lambda_erp.selling.quotation import (
    Quotation, make_sales_order,
    make_sales_invoice_from_quotation, make_delivery_note_from_quotation,
)
from lambda_erp.selling.sales_order import SalesOrder, make_sales_invoice
from lambda_erp.buying.purchase_order import PurchaseOrder, make_purchase_invoice
from lambda_erp.accounting.sales_invoice import SalesInvoice, make_sales_return
from lambda_erp.accounting.purchase_invoice import PurchaseInvoice, make_purchase_return
from lambda_erp.accounting.payment_entry import PaymentEntry
from lambda_erp.accounting.journal_entry import JournalEntry
from lambda_erp.stock.stock_entry import StockEntry
from lambda_erp.stock.delivery_note import DeliveryNote, make_delivery_note, make_delivery_return
from lambda_erp.stock.purchase_receipt import PurchaseReceipt, make_purchase_receipt, make_purchase_receipt_return
from lambda_erp.accounting.pos_invoice import POSInvoice
from lambda_erp.controllers.pricing_rule import PricingRule
from lambda_erp.accounting.budget import Budget
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.accounting.bank_transaction import BankTransaction


# --- Doctype registries ---

DOCUMENT_CLASSES = {
    "Quotation": Quotation,
    "Sales Order": SalesOrder,
    "Sales Invoice": SalesInvoice,
    "Purchase Order": PurchaseOrder,
    "Purchase Invoice": PurchaseInvoice,
    "Payment Entry": PaymentEntry,
    "Journal Entry": JournalEntry,
    "Stock Entry": StockEntry,
    "Delivery Note": DeliveryNote,
    "Purchase Receipt": PurchaseReceipt,
    "POS Invoice": POSInvoice,
    "Pricing Rule": PricingRule,
    "Budget": Budget,
    "Subscription": Subscription,
    "Bank Transaction": BankTransaction,
}

CONVERTERS = {
    ("Quotation", "Sales Order"): make_sales_order,
    ("Quotation", "Sales Invoice"): make_sales_invoice_from_quotation,
    ("Quotation", "Delivery Note"): make_delivery_note_from_quotation,
    ("Sales Order", "Sales Invoice"): make_sales_invoice,
    ("Sales Order", "Delivery Note"): make_delivery_note,
    ("Purchase Order", "Purchase Invoice"): make_purchase_invoice,
    ("Purchase Order", "Purchase Receipt"): make_purchase_receipt,
    # Returns (same-to-same conversion creates a return document)
    ("Sales Invoice", "Sales Invoice"): make_sales_return,
    ("Purchase Invoice", "Purchase Invoice"): make_purchase_return,
    ("Delivery Note", "Delivery Note"): make_delivery_return,
    ("Purchase Receipt", "Purchase Receipt"): make_purchase_receipt_return,
}

MASTER_TABLES = {
    "customer": ("Customer", "customer_name"),
    "supplier": ("Supplier", "supplier_name"),
    "item": ("Item", "item_name"),
    "warehouse": ("Warehouse", "warehouse_name"),
    "account": ("Account", "account_name"),
    "company": ("Company", "company_name"),
    "cost-center": ("Cost Center", "cost_center_name"),
}

# Slug <-> doctype name mapping
SLUG_TO_DOCTYPE = {}
DOCTYPE_TO_SLUG = {}
for dt in DOCUMENT_CLASSES:
    slug = dt.lower().replace(" ", "-")
    SLUG_TO_DOCTYPE[slug] = dt
    DOCTYPE_TO_SLUG[dt] = slug


def get_document_class(doctype_slug: str):
    """Get document class from URL slug."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        return None, None
    return doctype, DOCUMENT_CLASSES[doctype]


def create_document(doctype_slug: str, data: dict) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls(data)
    doc.save()
    return doc.as_dict()


def load_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    return doc.as_dict()


def update_document(doctype_slug: str, name: str, data: dict) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    # Update parent fields
    for key, value in data.items():
        if key not in ("name", "docstatus", "creation") and key not in doc.CHILD_TABLES:
            doc._data[key] = value
    # Update child tables if provided
    for table_name in doc.CHILD_TABLES:
        if table_name in data:
            doc._children[table_name] = []
            for row in data[table_name]:
                doc.append(table_name, _dict(row))
    doc.save()
    return doc.as_dict()


def submit_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    doc.submit()
    return doc.as_dict()


def cancel_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    doc.cancel()
    return doc.as_dict()


def convert_document(doctype_slug: str, name: str, target_doctype: str) -> dict:
    source_doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not source_doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    converter = CONVERTERS.get((source_doctype, target_doctype))
    if not converter:
        raise ValueError(f"Cannot convert {source_doctype} to {target_doctype}")

    new_doc = converter(name)
    new_doc.save()
    return new_doc.as_dict()


DATE_FIELDS = {
    "Quotation": "transaction_date",
    "Sales Order": "transaction_date",
    "Purchase Order": "transaction_date",
    "Sales Invoice": "posting_date",
    "Purchase Invoice": "posting_date",
    "Payment Entry": "posting_date",
    "Journal Entry": "posting_date",
    "Stock Entry": "posting_date",
    "Delivery Note": "posting_date",
    "Purchase Receipt": "posting_date",
    "POS Invoice": "posting_date",
    "Bank Transaction": "posting_date",
}


def list_documents(doctype_slug: str, filters: dict = None, limit: int = 50, offset: int = 0) -> list:
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    db = get_db()
    db_filters = {}
    from_date = None
    to_date = None
    if filters:
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key == "from_date":
                from_date = value
            elif key == "to_date":
                to_date = value
            else:
                db_filters[key] = value

    # Date range filtering via the doctype's primary date field
    date_field = DATE_FIELDS.get(doctype)
    if date_field:
        if from_date:
            db_filters[date_field] = (">=", from_date)
        if to_date:
            # If we already set from_date, we need a second condition on the same field
            if from_date:
                # Use raw SQL fallback below
                pass
            else:
                db_filters[date_field] = ("<=", to_date)

    # If both from and to are set, the dict can only hold one constraint per key
    # Fall through to a raw SQL query for that case
    if date_field and from_date and to_date:
        return _list_with_date_range(
            db, doctype, doctype_slug, db_filters, date_field, from_date, to_date, limit, offset
        )

    # get_all doesn't support offset, so use raw SQL when needed
    if offset:
        return _list_with_offset(db, doctype, doctype_slug, db_filters, limit, offset)

    rows = db.get_all(
        doctype,
        filters=db_filters if db_filters else None,
        fields=["*"],
        order_by="creation DESC",
        limit=limit,
    )

    return _attach_children(db, doctype_slug, rows)


def count_documents(doctype_slug: str, filters: dict = None) -> int:
    """Count documents matching the filters (ignores limit/offset)."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    db = get_db()
    db_filters = {}
    from_date = None
    to_date = None
    if filters:
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key == "from_date":
                from_date = value
            elif key == "to_date":
                to_date = value
            else:
                db_filters[key] = value

    date_field = DATE_FIELDS.get(doctype)
    where_parts = []
    params = []
    if date_field and from_date:
        where_parts.append(f'"{date_field}" >= ?')
        params.append(from_date)
    if date_field and to_date:
        where_parts.append(f'"{date_field}" <= ?')
        params.append(to_date)
    for k, v in db_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)

    query = f'SELECT COUNT(*) as c FROM "{doctype}"'
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    rows = db.sql(query, params)
    return int(rows[0]["c"]) if rows else 0


def _list_with_offset(db, doctype, doctype_slug, db_filters, limit, offset):
    where_parts = []
    params = []
    for k, v in db_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)
    query = f'SELECT * FROM "{doctype}"'
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    query += " ORDER BY creation DESC"
    if limit:
        query += f" LIMIT {int(limit)}"
    if offset:
        query += f" OFFSET {int(offset)}"
    rows = db.sql(query, params)
    return _attach_children(db, doctype_slug, rows)


def _attach_children(db, doctype_slug: str, rows: list) -> list:
    _, cls = get_document_class(doctype_slug)
    result = []
    for row in rows:
        doc_dict = dict(row)
        if cls and cls.CHILD_TABLES:
            for table_name, (child_doctype, _) in cls.CHILD_TABLES.items():
                children = db.get_all(
                    child_doctype,
                    filters={"parent": row["name"]},
                    fields=["*"],
                    order_by="idx",
                )
                doc_dict[table_name] = [dict(c) for c in children]
        result.append(doc_dict)
    return result


def _list_with_date_range(db, doctype, doctype_slug, extra_filters, date_field, from_date, to_date, limit, offset=0):
    """List documents when both from_date and to_date are set."""
    where_parts = [f'"{date_field}" >= ?', f'"{date_field}" <= ?']
    params = [from_date, to_date]
    for k, v in extra_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)
    query = (
        f'SELECT * FROM "{doctype}" WHERE ' + " AND ".join(where_parts)
        + " ORDER BY creation DESC"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    if offset:
        query += f" OFFSET {int(offset)}"
    rows = db.sql(query, params)
    return _attach_children(db, doctype_slug, rows)
