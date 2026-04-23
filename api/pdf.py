"""PDF generation for ERP documents."""

import os
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from lambda_erp.database import get_db
from api.services import load_document

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)

# Document type → (title, party_field, party_name_field, party_doctype, party_label)
DOC_CONFIG = {
    "Quotation":        ("Quotation",        "customer", "customer_name", "Customer", "Customer"),
    "Sales Order":      ("Sales Order",      "customer", "customer_name", "Customer", "Customer"),
    "Sales Invoice":    ("Sales Invoice",    "customer", "customer_name", "Customer", "Customer"),
    "Purchase Order":   ("Purchase Order",   "supplier", "supplier_name", "Supplier", "Supplier"),
    "Purchase Invoice": ("Purchase Invoice", "supplier", "supplier_name", "Supplier", "Supplier"),
    "Delivery Note":    ("Delivery Note",    "customer", "customer_name", "Customer", "Customer"),
    "Purchase Receipt": ("Purchase Receipt", "supplier", "supplier_name", "Supplier", "Supplier"),
}

SHOW_WAREHOUSE = {"Delivery Note", "Purchase Receipt"}


def _get_dict(row):
    """Convert a database row to a plain dict."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row) if row else {}


def generate_pdf(doctype_slug: str, name: str) -> bytes:
    """Generate a PDF for a document and return raw bytes."""
    doc = load_document(doctype_slug, name)
    db = get_db()

    # Resolve doctype display name
    doctype = doctype_slug.replace("-", " ").title()
    # Fix multi-word: "Sales Invoice" not "Sales-Invoice"
    for dt in DOC_CONFIG:
        if dt.lower().replace(" ", "-") == doctype_slug:
            doctype = dt
            break

    config = DOC_CONFIG.get(doctype)
    if not config:
        # Fallback for unknown types
        config = (doctype, None, None, None, "Party")

    title, party_field, party_name_field, party_doctype, party_label = config

    # Credit note / debit note titles
    if doc.get("is_return"):
        if doctype == "Sales Invoice":
            title = "Credit Note"
        elif doctype == "Purchase Invoice":
            title = "Debit Note"
        else:
            title = f"{title} (Return)"

    # Party info
    party_name = doc.get(party_name_field or "", "") or doc.get(party_field or "", "")
    party_info = {}
    if party_field and party_doctype and doc.get(party_field):
        row = db.get_value(party_doctype, doc[party_field],
                           ["email", "phone", "address", "city", "country", "tax_id"])
        if row:
            party_info = _get_dict(row)

    # Company info
    company_name = doc.get("company", "")
    company_info = {}
    if company_name:
        row = db.get_value("Company", company_name,
                           ["email", "phone", "address", "city", "country", "tax_id"])
        if row:
            company_info = _get_dict(row)

    # Currency
    currency = doc.get("currency", "USD") or "USD"

    # Meta fields (varies by doc type)
    meta_fields = []
    if doc.get("posting_date"):
        meta_fields.append({"label": "Date", "value": doc["posting_date"]})
    elif doc.get("transaction_date"):
        meta_fields.append({"label": "Date", "value": doc["transaction_date"]})
    if doc.get("due_date"):
        meta_fields.append({"label": "Due Date", "value": doc["due_date"]})
    if doc.get("valid_till"):
        meta_fields.append({"label": "Valid Till", "value": doc["valid_till"]})
    if doc.get("delivery_date"):
        meta_fields.append({"label": "Delivery Date", "value": doc["delivery_date"]})
    if doc.get("return_against"):
        meta_fields.append({"label": "Return Against", "value": doc["return_against"]})
    if doc.get("status"):
        meta_fields.append({"label": "Status", "value": doc["status"]})

    # Items
    items = doc.get("items", [])
    taxes = doc.get("taxes", [])

    # Page size setting
    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = "pdf_page_size"')
    page_size = page_size_row[0]["value"] if page_size_row else "A4"

    # Render
    template = _jinja_env.get_template("document.html")
    html_str = template.render(
        doc=doc,
        title=title,
        company_name=company_name,
        company_info=company_info,
        party_label=party_label,
        party_name=party_name,
        party_info=party_info,
        currency=currency,
        meta_fields=meta_fields,
        items=items,
        taxes=taxes,
        show_warehouse=doctype in SHOW_WAREHOUSE,
        page_size=page_size,
    )

    return HTML(string=html_str).write_pdf()
