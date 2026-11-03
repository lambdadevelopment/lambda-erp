"""PDF generation for ERP documents."""

import io
import re
import os
from dataclasses import dataclass
from jinja2 import Environment, FileSystemLoader, ChoiceLoader
from weasyprint import HTML
from lambda_erp.database import get_db
from api.services import load_document, get_document_class
from api.pdf_contract import prepare, validate_rendered_pdf, PDFError, register_pdf_state_labels, line_deduction  # noqa: F401
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
    can't compute itself. Return None when inapplicable. Exceptions fail the
    export so required content cannot silently disappear.
    """
    _pdf_context_providers.append(fn)

# Document type → (title, party_field, party_name_field, party_doctype, party_label)
DOC_CONFIG = {
    "Quotation":        ("Quotation",        "customer", "customer_name", "Customer", "Customer"),
    "Sales Order":      ("Sales Order",      "customer", "customer_name", "Customer", "Customer"),
    "POS Invoice":      ("POS Invoice",      "customer", "customer_name", "Customer", "Customer"),
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


@dataclass(frozen=True)
class RenderedPDF:
    data: bytes
    modified: str | None
    status: str
    warnings: list


# Placeholders the printed texts (intro and notes/terms) may use, e.g. a default
# closing text "Gültig bis {{ valid_till }}". Plain substitution, not Jinja:
# unknown names stay as written.
TEXT_PLACEHOLDERS = ("name", "customer_name", "party_name", "company", "transaction_date", "posting_date",
                     "valid_till", "due_date", "delivery_date", "schedule_date", "currency")
_PLACEHOLDER = re.compile(r"\{\{\s*(" + "|".join(TEXT_PLACEHOLDERS) + r")\s*\}\}")


def add_line_deductions(items):
    """Lines with a labelled deduction print the full price and, below it,
    the deduction: item.gross_rate, item.gross_amount, item.discount_total
    (the deduction for the whole line, positive) and item.discount_label."""
    for item in items:
        deduction = line_deduction(item)
        if deduction:
            item["gross_rate"], item["gross_amount"] = deduction["gross_rate"], deduction["gross_amount"]
            item["discount_total"], item["discount_label"] = deduction["amount"], deduction["label"]
        else:
            item["discount_total"] = 0


def fill_text_placeholders(text, doc, party_name=None, company_name=None) -> str:
    if not text:
        return ""
    party = party_name or doc.get("customer_name") or doc.get("supplier_name")
    values = {field: doc.get(field) for field in TEXT_PLACEHOLDERS}
    # customer_name is the document's party (a supplier on purchase orders).
    values.update(customer_name=party, party_name=party, company=company_name or doc.get("company"))
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1)) or ""), text)


def generate_pdf(doctype_slug: str, name: str) -> bytes:
    return render_document_pdf(doctype_slug, name).data


def render_document_pdf(doctype_slug: str, name: str) -> RenderedPDF:
    """Single validated renderer used by UI, REST, MCP and generated files.

    A submitted document with an archived PDF returns the file as issued
    (api/pdf_templates.py); otherwise it renders with its PDF template.
    """
    from api import pdf_templates
    from api.pdf_contract import state_for
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise PDFError(f'Unknown PDF document type: {doctype_slug}', code='pdf_unsupported')
    doc = load_document(doctype_slug, name)
    archived = pdf_templates.archived_pdf(doctype, doc)
    if archived is not None:
        return RenderedPDF(archived, doc.get('modified'), state_for(doctype, doc), [])
    return render_pdf_for(doctype, name, doc, pdf_templates.layout_for(doctype, doc))


def render_pdf_for(doctype: str, name: str, doc: dict, layout=None, *, blocked=None) -> RenderedPDF:
    """Render and validate `doc` (a loaded document dict), optionally with a
    PDF template layout. URLs a user template tried to load are appended to
    `blocked`."""
    contract = prepare(doctype, doc)
    kind = contract['profile'].kind
    tokens = contract['tokens']
    try:
        if kind == 'proposal':
            result = _render_proposal_pdf(name, doc, contract, layout, blocked)
        elif kind == 'commercial':
            result = _render_commercial_pdf(doctype, name, doc, contract, layout, blocked)
        elif doctype == 'Delivery Note':
            result = _render_commercial_pdf(doctype, name, doc, contract, layout, blocked, file='delivery_note.html')
            tokens = contract['core_tokens']
        elif doctype == 'Reservation':
            result = _render_reservation_pdf(name, doc, contract, layout, blocked)
            tokens = contract['core_tokens']
        else:
            result = _render_record_pdf(doctype, name, doc, contract)
    except PDFError:
        raise
    except Exception as exc:
        raise PDFError(f'PDF rendering failed for {doctype} {name}: {exc}', code='pdf_render_failed') from exc
    validate_rendered_pdf(result, tokens)
    return RenderedPDF(result, doc.get('modified'), contract['status'], contract['warnings'])


def _finish(doctype, name, context, file, layout=None, blocked=None):
    """Let plugins add to the context, then render it with `layout` (a PDF
    template) or the built-in `file`."""
    from api import pdf_templates
    for provider in _pdf_context_providers:
        try:
            extra = provider(doctype, name, context)
            if extra:
                context.update(extra)
                pdf_templates.note_provided_variables(doctype, [key for key, value in extra.items() if value])
        except Exception as exc:
            raise PDFError('PDF context provider failed', code='pdf_context_failed') from exc

    if layout is not None:
        data, urls = pdf_templates.render_html(layout, context)
        if blocked is not None:
            blocked.extend(urls)
        return data

    template = _jinja_env.get_template(file)
    # base_url = the resolved template's own directory, so a template (e.g. a
    # plugin's branded override) can reference sibling assets — logo.png, fonts,
    # CSS — by relative path. Falls back to the built-in templates dir.
    base_url = template.filename or os.path.join(TEMPLATE_DIR, file)
    return HTML(string=template.render(**context), base_url=base_url).write_pdf()


def _render_commercial_pdf(doctype, name, doc, contract, layout=None, blocked=None, file="document.html"):
    db = get_db()
    config = DOC_CONFIG.get(doctype)
    if not config:
        raise PDFError(f'No commercial PDF mapping for {doctype}', code='pdf_unsupported')

    title, party_field, party_name_field, party_doctype, party_label = config

    # Credit note / debit note titles
    if doc.get("is_return"):
        if doctype in {"Sales Invoice", "POS Invoice"}:
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
    currency = doc["currency"]

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

    # Items — print the name written on each line. A line may carry its own
    # wording under a generic article (e.g. one project service with a
    # "Phase 1: …" line per step), so the Item master's name is only a
    # fallback for a line that has none.
    items = doc.get("items", [])
    for item in items:
        code = item.get("item_code")
        if code and not item.get("item_name"):
            master_item_name = db.get_value("Item", code, "item_name")
            if master_item_name:
                item["item_name"] = master_item_name
    add_line_deductions(items)
    if doctype in SHOW_WAREHOUSE:
        for item in items:
            if item.get("warehouse"):
                item["warehouse_name"] = db.get_value("Warehouse", item["warehouse"], "warehouse_name") or item["warehouse"]

    taxes = doc.get("taxes", [])

    # Page size setting
    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = \'pdf_page_size\'')
    page_size = page_size_row[0]["value"] if page_size_row else "A4"

    # Billing-frequency split: recurring line groups for the per-period blocks,
    # and whether to show the frequency column at all (any recurring line, or a
    # `>>` price line in the notes — which renders in that same column).
    _one_time, recurring_summary = split_by_frequency(doc)
    _remarks = fill_text_placeholders(doc.get("remarks"), doc, party_name, company_name)
    show_frequency = bool(recurring_summary) or any(
        line.lstrip().startswith(">>") for line in _remarks.splitlines()
    )

    context = dict(
        doc=doc,
        pdf_status=contract["status"],
        pdf_state_notice=contract["notice"],
        pdf_warnings=contract["warnings"],
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
        remarks_html=render_remarks(_remarks),
        # The offer's intro text (above the lines), same markup. Templates
        # decide where either text prints.
        intro_html=render_remarks(fill_text_placeholders(doc.get("intro_text"), doc, party_name, company_name)),
        # Billing-frequency split (offers with recurring lines). `recurring_summary`
        # is the per-period totals (one entry per Monatlich/Quartalsweise/… group);
        # the doc's own net_total/grand_total already reflect the one-time part only.
        # `show_frequency` tells a template to render the (untitled) frequency column,
        # true when any line is recurring OR the notes carry a `>>` price line.
        recurring_summary=recurring_summary,
        show_frequency=show_frequency,
    )

    # Deployment plugins may augment the context (e.g. a Swiss QR-bill image for
    # invoices). Provider errors must fail the export.
    return _finish(doctype, name, context, file, layout, blocked)


def _append_pdf(base_pdf: bytes, extra_pdf: bytes) -> bytes:
    """A supplied appendix is required output; corruption must be reported."""
    from pypdf import PdfReader, PdfWriter
    try:
        writer = PdfWriter()
        for src in (base_pdf, extra_pdf):
            reader = PdfReader(io.BytesIO(src))
            if not reader.pages:
                raise ValueError('Empty appendix')
            for page in reader.pages:
                writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        raise PDFError('Proposal appendix is not a readable PDF', code='pdf_invalid_appendix') from exc


def generate_proposal_pdf(name: str) -> bytes:
    # Keep the public extension import; all paths use the shared contract.
    return generate_pdf('proposal', name)


def _render_proposal_pdf(name, proposal, contract, layout=None, blocked=None):
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
            raise PDFError('Proposal PDF requires a Quotation in every row')
        quote = load_document("quotation", qname)
        if quote.get('company') != proposal.get('company') or quote.get('customer') != proposal.get('customer'):
            raise PDFError('Proposal PDF: quotation company and customer must match')
        quote_contract = prepare('Quotation', quote)
        # Each included offer must be rendered completely. A non-final state
        # (draft, cancelled, discarded) is printed beside its number.
        contract['tokens'].extend(quote_contract['tokens'])
        contract['warnings'].extend(quote_contract['warnings'])
        items = quote.get("items", []) or []
        # The line's own name wins; the master's only fills an empty one,
        # consistent with the single-document path.
        for item in items:
            code = item.get("item_code")
            if code and not item.get("item_name"):
                live = db.get_value("Item", code, "item_name")
                if live:
                    item["item_name"] = live
        add_line_deductions(items)
        default_title = items[0].get("item_name") if items else qname
        positions.append({
            "letter": chr(ord("A") + i),
            "quotation": qname,
            "pdf_status": quote_contract["status"],
            "pdf_state_notice": quote_contract["notice"],
            "title": row.get("position_title") or default_title or qname,
            "blurb": row.get("position_blurb") or quote.get("remarks") or "",
            "is_recommended": bool(row.get("is_recommended")),
            "currency": quote["currency"],
            "net_total": quote["net_total"],
            "grand_total": quote["grand_total"],
            "total_taxes_and_charges": quote["total_taxes_and_charges"],
            "taxes": quote.get("taxes") or [],
            "recurring_summary": split_by_frequency(quote)[1],
            "items": items,
        })

    # Page size setting (shared with the single-document path).
    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = \'pdf_page_size\'')
    page_size = page_size_row[0]["value"] if page_size_row else "A4"

    context = dict(
        proposal=proposal,
        doc=proposal,
        pdf_status=contract["status"],
        pdf_state_notice=contract["notice"],
        pdf_warnings=contract["warnings"],
        title=proposal.get("title") or "Offerte",
        company_name=company_name,
        company_info=company_info,
        party_name=party_name,
        party_info=party_info,
        positions=positions,
        page_size=page_size,
    )
    pdf_bytes = _finish("Proposal", name, context, "proposal.html", layout, blocked)

    # Staple the uploaded appendix (stored as a blob) onto the end.
    appendix = db.sql('SELECT data FROM "Proposal Appendix" WHERE parent = ?', [name])
    if appendix and appendix[0].get("data"):
        data = appendix[0]["data"]
        if isinstance(data, memoryview):
            data = data.tobytes()
        if data:
            pdf_bytes = _append_pdf(pdf_bytes, bytes(data))

    return pdf_bytes


def _render_reservation_pdf(name, doc, contract, layout=None, blocked=None):
    """A reservation confirmation for the customer (or supplier) it is for."""
    db = get_db()
    company_id = doc.get("company") or ""
    company_info = _get_dict(db.get_value(
        "Company", company_id, ["company_name", "email", "phone", "address", "city", "zip_code", "country",
                                "tax_id", "iban"])) if company_id else {}
    party_type, party = doc.get("party_type"), doc.get("party")
    party_info = {}
    if party_type in ("Customer", "Supplier") and party:
        name_field = "customer_name" if party_type == "Customer" else "supplier_name"
        party_info = _get_dict(db.get_value(party_type, party, [name_field, "email", "phone", "address", "city",
                                                                  "zip_code", "country", "tax_id"]))
    asset = _get_dict(db.get_value("Asset", doc["asset"], ["asset_name", "asset_tag"])) if doc.get("asset") else {}
    item_name = db.get_value("Item", doc["item_code"], "item_name") if doc.get("item_code") else None
    warehouse_name = db.get_value("Warehouse", doc["warehouse"], "warehouse_name") if doc.get("warehouse") else None

    def stamp(value):
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(value)).strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError):
            return str(value or "")

    page_size_row = db.sql('SELECT value FROM "Settings" WHERE key = ?', ['pdf_page_size'])
    context = dict(
        doc=doc,
        title=contract["profile"].title,
        pdf_status=contract["status"],
        pdf_state_notice=contract["notice"],
        pdf_warnings=contract["warnings"],
        company_name=company_info.get("company_name") or company_id,
        company_info=company_info,
        party_label=party_type or "",
        party_name=contract["party_name"] or "",
        party_info=party_info,
        item_name=item_name or doc.get("item_code") or "",
        asset_name=asset.get("asset_name") or doc.get("asset") or "",
        asset_tag=asset.get("asset_tag") or "",
        warehouse_name=warehouse_name or doc.get("warehouse") or "",
        period_from=stamp(doc.get("from_datetime")),
        period_to=stamp(doc.get("to_datetime")),
        qty=doc.get("qty"),
        page_size=page_size_row[0]["value"] if page_size_row else "A4",
    )
    return _finish("Reservation", name, context, "reservation.html", layout, blocked)


# Shared record layouts use strict contexts; a misspelled field must fail rather
# than quietly disappear. Deployment overrides can extend these same blocks.
def _render_record_pdf(doctype, name, doc, contract):
    from jinja2 import StrictUndefined
    template = _jinja_env.overlay(undefined=StrictUndefined).get_template('record.html')
    company = contract['company']
    page_size_row = get_db().sql('SELECT value FROM "Settings" WHERE key = ?', ['pdf_page_size'])
    context = dict(
        title=contract['profile'].title, name=name, pdf_status=contract['status'],
        pdf_state_notice=contract['notice'],
        pdf_warnings=contract['warnings'], print_fields=contract['fields'],
        print_tables=contract['tables'], print_labels={}, print_title=contract['profile'].title,
        company_info=company, company_name=company.get('company_name') or doc.get('company') or '',
        company_currency=contract['company_currency'] if doctype in {'Journal Entry', 'Budget', 'Subscription', 'Pricing Rule'} else '',
        party_name=contract['party_name'],
        page_size=page_size_row[0]['value'] if page_size_row else 'A4',
    )
    for provider in _pdf_context_providers:
        # A registered provider may be optional, but required providers must
        # propagate failures (for example a mandatory payment part).
        try:
            extra = provider(doctype, name, context)
            if extra:
                context.update(extra)
        except Exception as exc:
            raise PDFError('PDF context provider failed', code='pdf_context_failed') from exc
    return HTML(string=template.render(**context), base_url=template.filename).write_pdf()
