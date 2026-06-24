"""PDF generation for ERP documents."""

import io
import os
from jinja2 import Environment, FileSystemLoader, ChoiceLoader
from weasyprint import HTML
from lambda_erp.database import get_db
from api.services import load_document
from api.remarks_md import render_remarks
from lambda_erp.controllers.taxes_and_totals import split_by_frequency

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


_pdf_context_providers = []


def register_pdf_context(fn) -> None:
    """Register a provider that augments the PDF render context.

    `fn(doctype, name, context)` is called just before rendering with the
    assembled context (doc, company_info, party_info, currency, items, …) and
    may return a dict of EXTRA keys to merge in — e.g. a computed Swiss QR-bill
    image for invoices. Lets a deployment add per-document content the template
    can't compute itself. Exceptions are swallowed so a buggy provider can't
    break PDF generation.
    """
    _pdf_context_providers.append(fn)

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
    # The Proposal (Sammelofferte) has a wholly different shape — several
    # quotations rendered as lettered positions + an appended appendix — so it
    # gets its own render path rather than the single-document template below.
    if doctype_slug == "proposal":
        return generate_proposal_pdf(name)

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
                           ["company_name", "email", "phone", "address", "city", "zip_code", "country", "tax_id", "iban"])
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

    # Billing-frequency split: recurring line groups for the per-period blocks,
    # and whether to show the frequency column at all (any recurring line, or a
    # `>>` price line in the notes — which renders in that same column).
    _one_time, recurring_summary = split_by_frequency(doc)
    _remarks = doc.get("remarks") or ""
    show_frequency = bool(recurring_summary) or any(
        line.lstrip().startswith(">>") for line in _remarks.splitlines()
    )

    # Render
    template = _jinja_env.get_template("document.html")
    # base_url = the resolved template's own directory, so a template (e.g. a
    # plugin's branded override) can reference sibling assets — logo.png, fonts,
    # CSS — by relative path. Falls back to the built-in templates dir.
    base_url = template.filename or os.path.join(TEMPLATE_DIR, "document.html")
    context = dict(
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
        # Notes / Terms rendered from a small markup subset (headings, bold/
        # italic, right-aligned price lines) into safe HTML; templates style the
        # emitted classes. Falls back to plain `doc.remarks` if a template
        # doesn't use it. See api/remarks_md.py.
        remarks_html=render_remarks(doc.get("remarks")),
        # Billing-frequency split (offers with recurring lines). `recurring_summary`
        # is the per-period totals (one entry per Monatlich/Quartalsweise/… group);
        # the doc's own net_total/grand_total already reflect the one-time part only.
        # `show_frequency` tells a template to render the (untitled) frequency column,
        # true when any line is recurring OR the notes carry a `>>` price line.
        recurring_summary=recurring_summary,
        show_frequency=show_frequency,
    )

    # Let deployment plugins augment the context (e.g. a Swiss QR-bill image for
    # invoices). A provider that raises must not break PDF generation.
    for provider in _pdf_context_providers:
        try:
            extra = provider(doctype, name, context)
            if extra:
                context.update(extra)
        except Exception:
            pass

    html_str = template.render(**context)

    return HTML(string=html_str, base_url=base_url).write_pdf()


def _append_pdf(base_pdf: bytes, extra_pdf: bytes) -> bytes:
    """Concatenate `extra_pdf` after `base_pdf`. Used to staple the uploaded
    appendix onto the rendered offers. A corrupt/unreadable appendix must not
    sink the whole proposal, so on any failure we return the base unchanged."""
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        return base_pdf
    try:
        writer = PdfWriter()
        for src in (base_pdf, extra_pdf):
            reader = PdfReader(io.BytesIO(src))
            for page in reader.pages:
                writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return base_pdf


def generate_proposal_pdf(name: str) -> bytes:
    """Render a Proposal (Sammelofferte): a cover letter plus each referenced
    Quotation as a lettered position (A, B, C…), then append the uploaded
    appendix PDF if there is one. Never mutates the quotations."""
    proposal = load_document("proposal", name)
    db = get_db()

    # Customer (party) — looked up live so a corrected name/address shows.
    party_name = proposal.get("customer_name") or proposal.get("customer") or ""
    party_info = {}
    if proposal.get("customer"):
        row = db.get_value(
            "Customer", proposal["customer"],
            ["customer_name", "email", "phone", "address", "city", "zip_code",
             "country", "tax_id", "contact_person", "contact_email", "contact_phone"],
        )
        if row:
            party_info = _get_dict(row)
            party_name = party_info.get("customer_name") or party_name

    # Company / letterhead.
    company_id = proposal.get("company", "") or ""
    company_name = company_id
    company_info = {}
    if company_id:
        row = db.get_value(
            "Company", company_id,
            ["company_name", "email", "phone", "address", "city", "zip_code",
             "country", "tax_id", "iban"],
        )
        if row:
            company_info = _get_dict(row)
            company_name = company_info.get("company_name") or company_id

    # Build the lettered positions from the referenced quotations (idx order).
    positions = []
    rows = sorted(proposal.get("quotations", []) or [], key=lambda r: r.get("idx") or 0)
    for i, row in enumerate(rows):
        qname = row.get("quotation")
        if not qname:
            continue
        try:
            quote = load_document("quotation", qname)
        except Exception:
            continue
        items = quote.get("items", []) or []
        # Refresh each line's item_name from the master, consistent with the
        # single-document path.
        for item in items:
            code = item.get("item_code")
            if code:
                live = db.get_value("Item", code, "item_name")
                if live:
                    item["item_name"] = live
        default_title = items[0].get("item_name") if items else qname
        positions.append({
            "letter": chr(ord("A") + i),
            "quotation": qname,
            "title": row.get("position_title") or default_title or qname,
            "blurb": row.get("position_blurb") or quote.get("remarks") or "",
            "is_recommended": bool(row.get("is_recommended")),
            "currency": quote.get("currency", "USD") or "USD",
            "grand_total": quote.get("grand_total") or 0,
            "items": items,
        })

    # Page size setting (shared with the single-document path).
    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = \'pdf_page_size\'')
    page_size = page_size_row[0]["value"] if page_size_row else "A4"

    template = _jinja_env.get_template("proposal.html")
    base_url = template.filename or os.path.join(TEMPLATE_DIR, "proposal.html")
    context = dict(
        proposal=proposal,
        title=proposal.get("title") or "Offerte",
        company_name=company_name,
        company_info=company_info,
        party_name=party_name,
        party_info=party_info,
        positions=positions,
        page_size=page_size,
    )

    # Let deployment plugins augment the context, same seam as generate_pdf.
    for provider in _pdf_context_providers:
        try:
            extra = provider("Proposal", name, context)
            if extra:
                context.update(extra)
        except Exception:
            pass

    html_str = template.render(**context)
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()

    # Staple the uploaded appendix (stored as a blob) onto the end.
    appendix = db.sql('SELECT data FROM "Proposal Appendix" WHERE parent = ?', [name])
    if appendix and appendix[0].get("data"):
        data = appendix[0]["data"]
        if isinstance(data, memoryview):
            data = data.tobytes()
        if data:
            pdf_bytes = _append_pdf(pdf_bytes, bytes(data))

    return pdf_bytes
