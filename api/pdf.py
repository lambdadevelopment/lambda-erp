"""PDF generation for ERP documents."""

import os
from jinja2 import Environment, FileSystemLoader, ChoiceLoader
from weasyprint import HTML
from lambda_erp.database import get_db
from api.services import load_document

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Extension seam: deployment plugins can register their own template
# directories (searched BEFORE the built-in one) to override document.html with
# custom branding/styling. The render context in generate_pdf is unchanged, so
# a custom template just re-styles the same data.
_plugin_template_dirs: list[str] = []


def _build_loader():
    return ChoiceLoader(
        [FileSystemLoader(d) for d in _plugin_template_dirs] + [FileSystemLoader(TEMPLATE_DIR)]
    )


_jinja_env = Environment(loader=_build_loader(), autoescape=True)


def register_pdf_template_dir(path: str) -> None:
    """Register a directory of PDF templates searched before the built-in ones.

    Call from a plugin's register(). Drop a `document.html` (and/or other
    templates) in `path` to override the built-in PDF layout with your own
    styling — the template receives the same context generate_pdf() builds
    (doc, title, company_info, party_info, currency, meta_fields, items, taxes,
    page_size, ...). Later registrations win.
    """
    if path and path not in _plugin_template_dirs:
        _plugin_template_dirs.insert(0, path)
        _jinja_env.loader = _build_loader()

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
        fields = ["email", "phone", "address", "city", "zip_code", "country", "tax_id"]
        if party_name_field:
            fields = [party_name_field, *fields]
        row = db.get_value(party_doctype, doc[party_field], fields)
        if row:
            party_info = _get_dict(row)
            # Show the party's CURRENT name from the master, so a later
            # correction (e.g. fixing a typo) appears on the PDF — consistent
            # with the address fields, which are already looked up live. The
            # name stored on the document is only a fallback for when the
            # master record no longer exists.
            live_name = party_info.get(party_name_field) if party_name_field else None
            if live_name:
                party_name = live_name

    # Company info
    company_id = doc.get("company", "")
    company_name = company_id  # fallback to the id if the master is gone
    company_info = {}
    if company_id:
        row = db.get_value("Company", company_id,
                           ["company_name", "email", "phone", "address", "city", "zip_code", "country", "tax_id"])
        if row:
            company_info = _get_dict(row)
            # Show the company's CURRENT display name from the master.
            company_name = company_info.get("company_name") or company_id

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

    # Items — refresh each line's item_name from the Item master so a later
    # correction to an item's name appears on the PDF, consistent with the
    # party/company names. Quantities, rates, and amounts stay as transacted.
    # Falls back to the name stored on the line if the master is gone.
    items = doc.get("items", [])
    for item in items:
        code = item.get("item_code")
        if code:
            live_item_name = db.get_value("Item", code, "item_name")
            if live_item_name:
                item["item_name"] = live_item_name

    taxes = doc.get("taxes", [])

    # Page size setting
    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = \'pdf_page_size\'')
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
