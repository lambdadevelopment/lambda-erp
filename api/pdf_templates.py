"""User-editable PDF templates for customer- and supplier-facing documents.

A template is HTML + CSS rendered with Jinja, the same context the built-in
layout receives. People (managers, admins) and the chat edit a template's
working copy (`draft_html`) as often as they like; publishing makes it the
layout new documents print with (`live_html`). Nothing is versioned while
editing.

A version is written only when a document is submitted: the layout it was
issued with is frozen as one row per distinct content (matched by sha256), so
fifty offers with the same layout share one version. The submitted document
points at that version and the PDF as issued is archived; a reprint returns
the archived file. A version exists only because a document used it, so it
can never be deleted, and a template can be deleted only until its first use.

User templates are untrusted code. They render in Jinja's immutable sandbox,
and WeasyPrint may load nothing but data: URLs, the template's own uploaded
assets and files beside the deployment's built-in templates (its logo). Any
other URL (http, file, internal hosts) is blocked and reported.

The built-in layout ("standard") is not stored: it is whatever file the
deployment ships for that document type (STANDARD_FILES; a plugin's branded
override wins). It is pinned and archived like any other template, but rendered
as trusted code. A template belongs to one document type and gets the same
context as that type's built-in layout.

Proposals and reservations are never submitted: they print with the layout
they have selected (or the default) at the time, and nothing is archived.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from jinja2 import FunctionLoader, TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import Field

from api import services
from api.pdf_contract import PDFError
from lambda_erp.database import get_db
from lambda_erp.utils import new_name, now

logger = logging.getLogger("pdf_templates")

STANDARD = "standard"
STANDARD_TITLE = "Standard"
# Document types whose PDF layout can be chosen (each has a pdf_template
# column; submittable ones also pdf_template_version), and the built-in layout
# file each prints with. The file's context is what a template for the type gets.
DOCTYPES = ("Quotation", "Sales Order", "Sales Invoice", "Purchase Order", "Delivery Note",
            "Proposal", "Reservation")
STANDARD_FILES = {"Delivery Note": "delivery_note.html", "Proposal": "proposal.html",
                  "Reservation": "reservation.html"}
DEFAULT_KEY = "pdf_template:{doctype}"


def standard_file(doctype: str) -> str:
    return STANDARD_FILES.get(doctype, "document.html")


def _submittable(doctype: str) -> bool:
    return bool(getattr(services.DOCUMENT_CLASSES.get(doctype), "SUBMITTABLE", False))

MAX_HTML = 400_000
MAX_RENDERED_HTML = 5_000_000
MAX_ASSET_BYTES = 2_000_000
MAX_ASSETS = 20
ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
}
# Relative URLs in a user template resolve against this base, so the fetcher
# can tell "the template's own file" from anything else.
ASSET_BASE = "https://template.invalid/"


def _builtin_source(name):
    """Lets a template {% include %} / {% extends %} the deployment's built-in
    layout files (e.g. proposal.html's totals partial), nothing else."""
    from api import pdf
    return pdf._jinja_env.loader.get_source(pdf._jinja_env, name)


_sandbox = ImmutableSandboxedEnvironment(autoescape=True, loader=FunctionLoader(_builtin_source))


# ---------------------------------------------------------------------------
# Layouts and rendering
# ---------------------------------------------------------------------------

@dataclass
class Layout:
    """The HTML a document prints with, and where it came from."""
    template: str
    title: str
    html: str
    assets: dict = field(default_factory=dict)  # filename -> sha256
    version: str | None = None
    trusted: bool = False  # the deployment's own file template
    file: str = "document.html"  # for a trusted layout: the built-in file (its directory is the base URL)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps({"html": self.html, "assets": self.assets}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class _Resources:
    """What a user template may load: data: URLs, its assets, built-in files."""

    def __init__(self, assets: dict):
        self.assets = assets
        self.blocked: list[str] = []

    def load(self, url: str):
        if url.startswith(ASSET_BASE):
            name = unquote(urlsplit(url).path.lstrip("/"))
            sha = self.assets.get(name)
            if sha:
                rows = get_db().sql('SELECT content_type, data FROM "PDF Template Asset" WHERE sha256 = ?', [sha])
                if rows:
                    return bytes(rows[0]["data"]), rows[0]["content_type"]
            for directory in _template_dirs():
                root = os.path.realpath(directory)
                path = os.path.realpath(os.path.join(root, name))
                if path.startswith(root + os.sep) and os.path.isfile(path) and not path.endswith(".html"):
                    with open(path, "rb") as fh:
                        return fh.read(), mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.blocked.append(url[:200])
        raise ValueError(f"PDF templates can't load {url[:200]}: upload it as a template asset instead")


def _fetcher(resources: _Resources):
    from weasyprint import urls

    if hasattr(urls, "URLFetcherResponse"):  # WeasyPrint >= 68
        class Fetcher(urls.URLFetcher):
            def fetch(self, url, headers=None):
                if url.startswith("data:"):
                    return super().fetch(url, headers)
                body, mime = resources.load(url)
                return urls.URLFetcherResponse(url, body, {"Content-Type": mime})
        return Fetcher(allowed_protocols={"data", "https"})

    def fetch(url, timeout=10, ssl_context=None):
        if url.startswith("data:"):
            return urls.default_url_fetcher(url)
        body, mime = resources.load(url)
        return {"string": body, "mime_type": mime, "redirected_url": url}
    return fetch


def _template_dirs():
    from api import pdf
    return [*pdf._plugin_template_dirs, pdf.TEMPLATE_DIR]


def render_html(layout: Layout, context: dict) -> tuple[bytes, list[str]]:
    """Render a layout's HTML to PDF; returns (pdf, blocked URLs)."""
    from weasyprint import HTML
    if layout.trusted:
        from api import pdf
        template = pdf._jinja_env.from_string(layout.html)
        base_url = pdf._jinja_env.get_template(layout.file).filename
        return HTML(string=template.render(**context), base_url=base_url).write_pdf(), []
    try:
        html = _sandbox.from_string(layout.html).render(**context)
    except TemplateError as exc:
        raise PDFError(f"PDF template {layout.title!r} failed: {exc}", code="pdf_template_error") from exc
    if len(html) > MAX_RENDERED_HTML:
        raise PDFError(f"PDF template {layout.title!r} produced more than 5 MB of HTML",
                       code="pdf_template_error")
    resources = _Resources(layout.assets)
    data = HTML(string=html, base_url=ASSET_BASE, url_fetcher=_fetcher(resources)).write_pdf()
    return data, resources.blocked


def standard_html(doctype: str = "Quotation") -> str:
    from api import pdf
    source, _filename, _uptodate = pdf._jinja_env.loader.get_source(pdf._jinja_env, standard_file(doctype))
    return source


def _standard_layout(doctype: str) -> Layout:
    return Layout(STANDARD, STANDARD_TITLE, standard_html(doctype), trusted=True, file=standard_file(doctype))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _row(template_id: str) -> dict | None:
    rows = get_db().sql('SELECT * FROM "PDF Template" WHERE name = ?', [template_id])
    return dict(rows[0]) if rows else None


def _require(template_id: str) -> dict:
    row = _row(template_id)
    if not row:
        raise ValueError(f"Unknown PDF template {template_id!r}. Call list_pdf_templates for the available ones.")
    return row


def _assets(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def default_template(doctype: str) -> str:
    rows = get_db().sql('SELECT value FROM "Settings" WHERE key = ?', [DEFAULT_KEY.format(doctype=doctype)])
    value = rows[0]["value"] if rows else None
    if value and value != STANDARD:
        row = _row(value)
        if row and not row["archived"]:
            return value
    return STANDARD


def _live_layout(template_id: str, doctype: str) -> Layout:
    if template_id == STANDARD:
        return _standard_layout(doctype)
    row = _require(template_id)
    html = row["live_html"]
    if html is None:
        raise PDFError(f"PDF template {row['title']!r} has not been published yet", code="pdf_template_error")
    return Layout(template_id, row["title"], html, _assets(row["live_assets"]))


def _version_layout(version: str, doctype: str) -> Layout | None:
    rows = get_db().sql('SELECT * FROM "PDF Template Version" WHERE name = ?', [version])
    if not rows:
        return None
    row = rows[0]
    title = STANDARD_TITLE if row["template"] == STANDARD else ((_row(row["template"]) or {}).get("title") or row["template"])
    return Layout(row["template"], title, row["html"], _assets(row["assets"]), version=version,
                  trusted=row["template"] == STANDARD, file=standard_file(doctype))


def layout_for(doctype: str, doc: dict) -> Layout | None:
    """The layout a document prints with now, or None for non-template types."""
    if doctype not in DOCTYPES:
        return None
    if doc.get("docstatus") in (1, 2) and doc.get("pdf_template_version"):
        layout = _version_layout(doc["pdf_template_version"], doctype)
        if layout:
            return layout
    if doc.get("docstatus") in (1, 2):
        return None  # issued before templates existed: the built-in path, as before
    return _live_layout(doc.get("pdf_template") or default_template(doctype), doctype)


def archived_pdf(doctype: str, doc: dict) -> bytes | None:
    """The PDF as issued, for a submitted (not cancelled) document."""
    if doctype not in DOCTYPES or doc.get("docstatus") != 1:
        return None
    rows = get_db().sql('SELECT data FROM "PDF Archive" WHERE doctype = ? AND document_name = ?',
                        [doctype, doc["name"]])
    return bytes(rows[0]["data"]) if rows else None


def _pin(layout: Layout) -> str:
    """The version row for this content, created on first use."""
    db = get_db()
    digest = layout.fingerprint
    name = f"{layout.template}@{digest[:16]}"
    db.sql('INSERT INTO "PDF Template Version" (name, template, sha256, html, assets, created_at) '
           'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
           [name, layout.template, digest, layout.html, json.dumps(layout.assets, sort_keys=True), now()])
    return name


_LAYOUT_FAILURES = {"pdf_template_error", "pdf_render_incomplete", "pdf_render_failed"}
# Status a document has right after submit, which the archived PDF shows. Other
# types' status keeps changing after issue (paid, delivered), so the archived
# copy doesn't print one.
_SUBMITTED_STATUS = {"Quotation": "Open"}


_ARCHIVE_ON_SUBMIT = ContextVar("pdf_archive_on_submit", default=True)


@contextmanager
def pin_only():
    """Pin layouts at submit but don't render or archive the PDF.

    For bulk seeding (the demo's historical simulation submits thousands of
    offers at boot): those offers print on demand from their pinned version.
    """
    token = _ARCHIVE_ON_SUBMIT.set(False)
    try:
        yield
    finally:
        _ARCHIVE_ON_SUBMIT.reset(token)


def _before_submit(doc):
    """Pin the layout and archive the PDF, inside the submit transaction.

    A custom layout that can't print this document blocks the submit, so every
    issued offer has a printable record. The built-in layout keeps today's
    behaviour: if it can't render, the document is submitted without an
    archive and printed on demand.
    """
    doctype = doc.DOCTYPE
    template = doc.get("pdf_template") or default_template(doctype)
    layout = _live_layout(template, doctype)
    version = _pin(layout)
    doc._data["pdf_template"] = template
    doc._data["pdf_template_version"] = version
    layout.version = version
    if not _ARCHIVE_ON_SUBMIT.get():
        return
    data = doc.as_dict()
    data["status"] = _SUBMITTED_STATUS.get(doctype)
    from api.pdf import render_pdf_for
    try:
        rendered = render_pdf_for(doctype, doc.name, data, layout)
    except PDFError as exc:
        # The document itself not being printable (prepare's checks) never
        # blocked a submit; a custom layout failing to print it does.
        if not layout.trusted and exc.code in _LAYOUT_FAILURES:
            raise PDFError(f"Can't submit {doc.name}: the PDF template {layout.title!r} "
                           f"doesn't print it: {exc}", code=exc.code) from exc
        logger.warning("[pdf_archive] %s %s not archived: %s", doctype, doc.name, exc)
        return
    _store_archive(doctype, doc.name, layout, rendered.data)


def _store_archive(doctype, name, layout, data):
    get_db().sql(
        'INSERT INTO "PDF Archive" (doctype, document_name, template, template_version, sha256, data, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
        [doctype, name, layout.template, layout.version, hashlib.sha256(data).hexdigest(), data, now()])


# ---------------------------------------------------------------------------
# Printed texts: the intro and the closing notes/terms
# ---------------------------------------------------------------------------

# (document field, template variable, what a user calls it)
_TEXTS = (("intro_text", "intro_html", "intro text"), ("remarks", "remarks_html", "closing text / terms"))
TEXT_DEFAULT_KEY = "pdf_text_default:{doctype}:{field}"


def texts_for(doctype: str) -> tuple:
    """The printed texts a document type has (offers, orders, invoices, purchase
    orders, delivery notes); proposals and reservations have their own fields."""
    columns = get_db()._get_table_columns(doctype)
    return tuple(text for text in _TEXTS if text[0] in columns) if doctype in DOCTYPES else ()


def text_defaults(doctype: str) -> dict:
    texts = texts_for(doctype)
    if not texts:
        return {}
    keys = {TEXT_DEFAULT_KEY.format(doctype=doctype, field=field): field for field, _var, _label in texts}
    rows = get_db().sql('SELECT key, value FROM "Settings" WHERE key IN (' + ",".join("?" for _ in keys) + ")",
                        list(keys))
    found = {keys[row["key"]]: row["value"] for row in rows}
    return {field: found.get(field) or "" for field, _var, _label in texts}


def _apply_text_defaults(doc):
    """A new offer starts with the default texts, unless the caller sent the field
    (an empty string means "no text"). Copied, so later default edits don't
    change existing offers."""
    if doc._persisted:
        return
    for field, value in text_defaults(doc.DOCTYPE).items():
        if value and doc._data.get(field) is None:
            doc._data[field] = value


def _prints(html: str | None, field: str, var: str) -> bool:
    return bool(html) and (var in html or f"doc.{field}" in html)


# A line's deduction text (item.discount_label) is printed per line; a layout
# without it prints those lines with their net amount only.
LINE_DEDUCTION = ("line_deduction", "discount_label", "line deductions (item.discount_label / item.discount_total)")


# Content a deployment's context providers add (e.g. qr_bill_img, the Swiss QR
# bill on invoices) that a layout must print: registered explicitly, and learned
# from what the providers return (api.pdf records it after each render).
_REQUIRED_VARIABLES: dict[str, dict[str, str]] = {}


def register_pdf_template_requirement(doctype: str, variable: str, label: str) -> None:
    """A context variable every layout of `doctype` should print (publish and the
    document warn when one doesn't), e.g. ("Sales Invoice", "qr_bill_img", "QR bill")."""
    _REQUIRED_VARIABLES.setdefault(doctype, {})[variable] = label


def note_provided_variables(doctype: str, keys) -> None:
    for key in keys:
        _REQUIRED_VARIABLES.setdefault(doctype, {}).setdefault(key, key)


def _required(doctype: str) -> list[tuple[str, str, str]]:
    """(field, variable, label) of everything a layout of `doctype` should print."""
    columns = get_db()._get_table_columns(f"{doctype} Item") if doctype in DOCTYPES else set()
    deduction = (LINE_DEDUCTION,) if "discount_label" in columns else ()
    variables = tuple((var, var, label) for var, label in _REQUIRED_VARIABLES.get(doctype, {}).items())
    return [*texts_for(doctype), *deduction, *variables]


def _has_line_deduction(doc) -> bool:
    return any(row.get("discount_label") and float(row.get("discount_amount") or 0) > 0
               for row in doc.get("items") or [])


def _text_warnings(doc) -> list[dict]:
    """Warn when an offer has a text its PDF layout never prints."""
    if doc.DOCTYPE not in DOCTYPES:
        return []
    texts = [(f, v, label) for f, v, label in texts_for(doc.DOCTYPE) if (doc.get(f) or "").strip()]
    if _has_line_deduction(doc):
        texts.append(LINE_DEDUCTION)
    texts.extend((var, var, label) for var, label in _REQUIRED_VARIABLES.get(doc.DOCTYPE, {}).items())
    if not texts:
        return []
    try:
        layout = layout_for(doc.DOCTYPE, doc.as_dict())
    except (PDFError, ValueError):
        return []
    if layout is None:
        return []
    known = {text[0] for text in (*_TEXTS, LINE_DEDUCTION)}
    return [{"code": f"pdf_{field}_not_printed" if field in known else "pdf_variable_not_printed",
             "field": field, "variable": var, "label": label, "template": layout.title,
             "message": f"The PDF layout {layout.title!r} doesn't print the {label}; the PDF shows it without."}
            for field, var, label in texts if not layout.trusted and not _prints(layout.html, field, var)]


# ---------------------------------------------------------------------------
# Checks and previews
# ---------------------------------------------------------------------------

def _sample_document(doctype: str, name: str | None):
    from api.services import load_document, DOCTYPE_TO_SLUG
    slug = DOCTYPE_TO_SLUG.get(doctype, doctype.lower().replace(" ", "-"))
    if name:
        return load_document(slug, name)
    db = get_db()
    columns = db._get_table_columns(doctype)
    where = ["COALESCE(discarded, 0) = 0" if "discarded" in columns else "1 = 1"]
    if "docstatus" in columns:
        where.append("COALESCE(docstatus, 0) < 2")
    lines = f"{doctype} Item"
    if db._get_table_columns(lines):
        where.append(f'name IN (SELECT parent FROM "{lines}")')
    rows = db.sql(f'SELECT name FROM "{doctype}" WHERE {" AND ".join(where)} ORDER BY modified DESC, name DESC LIMIT 1')
    return load_document(slug, rows[0]["name"]) if rows else None


def _compile(html: str):
    if len(html) > MAX_HTML:
        raise ValueError(f"Template HTML is limited to {MAX_HTML // 1000} kB")
    try:
        _sandbox.parse(html)
    except TemplateError as exc:
        line = getattr(exc, "lineno", None)
        raise ValueError(f"Template syntax error{f' on line {line}' if line else ''}: {exc}") from None


def render_preview(template_id: str, *, source: str = "draft", document: str | None = None,
                   doctype: str | None = None):
    """Render a template's draft or live HTML against a document.

    Returns (rendered PDF or None when there is no document to print, the
    sample's name, blocked URLs).
    """
    if template_id == STANDARD:
        doctype = _doctype(doctype or DOCTYPES[0])
        layout = _standard_layout(doctype)
    else:
        row = _require(template_id)
        doctype = row["doctype"]
        html = row["draft_html"] if source == "draft" else row["live_html"]
        if html is None:
            raise ValueError("This template has not been published yet; preview the draft instead")
        assets = row["assets"] if source == "draft" else row["live_assets"]
        layout = Layout(template_id, row["title"], html, _assets(assets))
    if not layout.trusted:
        _compile(layout.html)
    doc = _sample_document(doctype, document)
    if doc is None:
        return None, None, []
    from api.pdf import render_pdf_for
    blocked: list[str] = []
    rendered = render_pdf_for(doctype, doc["name"], doc, layout, blocked=blocked)
    return rendered, doc["name"], blocked


# ---------------------------------------------------------------------------
# Actions (REST, chat, MCP)
# ---------------------------------------------------------------------------

from api import mentions  # noqa: E402  (Input base; imported late to keep the module header readable)

ID = Field(min_length=1, max_length=100)


class ListTemplates(mentions.Input):
    doctype: str | None = Field(default=None, max_length=100)
    include_archived: bool = False


DOCTYPE_FIELD = Field(default="Quotation", max_length=100,
                      description="Document type: " + ", ".join(DOCTYPES) + ".")


class GetTemplate(mentions.Input):
    template: str = ID
    include_html: bool = True
    doctype: str | None = Field(default=None, max_length=100,
                                description="For 'standard': which document type's built-in layout (default Quotation).")


class CreateTemplate(mentions.Input):
    title: str = Field(min_length=1, max_length=120)
    doctype: str = DOCTYPE_FIELD
    based_on: str = Field(default=STANDARD, max_length=100,
                          description="'standard' (the type's built-in layout) or any template, also of another "
                                      "type, e.g. the offer layout to start an invoice layout in the same style.")
    html: str | None = Field(default=None, max_length=MAX_HTML)
    description: str = Field(default="", max_length=2000)


class UpdateTemplate(mentions.Input):
    template: str = ID
    html: str | None = Field(default=None, max_length=MAX_HTML)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    expected_draft_modified: str | None = Field(default=None, max_length=50)
    publish: bool = False


class TemplateRef(mentions.Input):
    template: str = ID


class PublishTemplate(mentions.Input):
    template: str = ID
    document: str | None = Field(default=None, max_length=140)


class ArchiveTemplate(mentions.Input):
    template: str = ID
    archived: bool = True


class SetDefault(mentions.Input):
    doctype: str = DOCTYPE_FIELD
    template: str = ID


class TextDefaults(mentions.Input):
    doctype: str = DOCTYPE_FIELD


class SetTextDefaults(mentions.Input):
    doctype: str = DOCTYPE_FIELD
    intro_text: str | None = Field(default=None, max_length=10_000)
    remarks: str | None = Field(default=None, max_length=10_000)


class Preview(mentions.Input):
    template: str = ID
    source: str = Field(default="draft", pattern="^(draft|live)$")
    document: str | None = Field(default=None, max_length=140)
    doctype: str | None = Field(default=None, max_length=100,
                                description="For 'standard': which document type (default Quotation).")


class DeleteAsset(mentions.Input):
    template: str = ID
    filename: str = Field(min_length=1, max_length=100)


def _actor(user) -> str | None:
    return (user or {}).get("user_id") or (user or {}).get("name")


def _doctype(value: str) -> str:
    wanted = value.strip().lower().replace("-", " ").replace("_", " ")
    for doctype in DOCTYPES:
        if wanted == doctype.lower():
            return doctype
    raise ValueError(f"PDF templates are available for: {', '.join(DOCTYPES)}")


def _issued_by_version(template_id: str, doctypes=DOCTYPES) -> dict[str, tuple[int, str | None]]:
    """version -> (issued documents pinned to it, one example), over the given types."""
    db, out = get_db(), {}
    for doctype in doctypes:
        if not _submittable(doctype):
            continue
        rows = db.sql(f'SELECT d.pdf_template_version AS version, COUNT(*) AS n, MIN(d.name) AS example '
                      f'FROM "{doctype}" d JOIN "PDF Template Version" v ON v.name = d.pdf_template_version '
                      f'WHERE v.template = ? GROUP BY d.pdf_template_version', [template_id])
        for row in rows:
            count, example = out.get(row["version"], (0, None))
            out[row["version"]] = (count + int(row["n"]), example or row["example"])
    return out


def _used_by(template_id: str, doctypes=DOCTYPES) -> int:
    return sum(count for count, _example in _issued_by_version(template_id, doctypes).values())


def _versions(template_id: str, doctypes=DOCTYPES) -> list[dict]:
    rows = get_db().sql('SELECT name, created_at FROM "PDF Template Version" WHERE template = ? '
                        'ORDER BY created_at, name', [template_id])
    issued = _issued_by_version(template_id, doctypes)
    return [{"version": row["name"], "label": f"v{i}", "first_used": row["created_at"],
             "documents": issued.get(row["name"], (0, None))[0],
             "example_document": issued.get(row["name"], (0, None))[1]}
            for i, row in enumerate(rows, 1) if row["name"] in issued]


def _summary(row: dict, defaults: dict) -> dict:
    assets = _assets(row["assets"])
    return {
        "template": row["name"], "title": row["title"], "doctype": row["doctype"],
        "description": row["description"] or "", "standard": False,
        "default": defaults.get(row["doctype"]) == row["name"], "archived": bool(row["archived"]),
        "published": row["live_html"] is not None,
        "unpublished_changes": row["live_html"] is None or row["draft_html"] != row["live_html"]
        or assets != _assets(row["live_assets"]),
        "draft_modified": row["draft_modified"], "published_at": row["published_at"],
        "used_by_documents": _used_by(row["name"], (row["doctype"],)),
        "issued": _submittable(row["doctype"]),
    }


def _standard_summary(defaults: dict, doctype: str | None = None) -> dict:
    """The built-in layouts, one per document type ("doctype" names one when
    asked for, else all of them)."""
    return {"template": STANDARD, "title": STANDARD_TITLE, "doctype": doctype or ", ".join(DOCTYPES),
            "doctypes": list(DOCTYPES), "file": standard_file(doctype) if doctype else None,
            "description": "The built-in layout this deployment ships for each document type. Read-only: "
                           "create a template based on it to change the look.",
            "standard": True,
            "default": defaults.get(doctype) == STANDARD if doctype else any(v == STANDARD for v in defaults.values()),
            "archived": False, "published": True, "unpublished_changes": False, "issued": True,
            "used_by_documents": _used_by(STANDARD, (doctype,) if doctype else DOCTYPES)}


def list_pdf_templates(args, user=None):
    data = ListTemplates.model_validate(args)
    doctype = _doctype(data.doctype) if data.doctype else None
    defaults = {dt: default_template(dt) for dt in DOCTYPES}
    rows = get_db().sql('SELECT * FROM "PDF Template" ORDER BY title, name')
    templates = [_standard_summary(defaults, doctype)] + [
        _summary(dict(row), defaults) for row in rows
        if (data.include_archived or not row["archived"]) and (not doctype or row["doctype"] == doctype)]
    return {"templates": templates, "defaults": defaults}


_REFERENCE_HEAD = """Templates are HTML + CSS with Jinja ({{ }} / {% %}), rendered to PDF by WeasyPrint (supports @page, page counters, flexbox; no JavaScript). This is the reference for <<DOCTYPE>> layouts; its built-in layout is <<FILE>> (get_pdf_template template='standard', doctype='<<DOCTYPE>>'), which may {% include %} or {% extends %} the other built-in files.
Variables:
"""

_PARTY_VARIABLES = """- company_name, company_info (email, phone, address, city, zip_code, country, tax_id, iban)
- party_label ("Customer"/"Supplier"), party_name, party_info (email, phone, address, city, zip_code, country, tax_id)
- title: the document title; pdf_state_notice: "Draft"/"Cancelled" text or empty; pdf_status; page_size ("A4"/"Letter")
"""

_TEXT_VARIABLES = """- intro_html: the intro text as safe HTML ({{ intro_html|safe }}); print it near the top, usually under the title
- remarks_html: the closing text / notes / terms as safe HTML ({{ remarks_html|safe }}); usually at the end
  Both are optional per document; print each wherever the layout wants, but print both (a published layout that omits one gets a warning).
  The texts may contain the placeholders {{ name }}, {{ customer_name }} (the document's customer or supplier), {{ company }}, {{ transaction_date }}, {{ posting_date }}, {{ valid_till }}, {{ due_date }}, {{ delivery_date }}, {{ currency }}; they are filled in before the template sees them.
"""

_REFERENCES = {
    "commercial": """- doc: the document (name, transaction_date or posting_date, valid_till / due_date / delivery_date, customer or supplier and their _name, currency, net_total, total_taxes_and_charges, grand_total, outstanding_amount (invoices), is_return (credit note), remarks, status, docstatus, ...)
<<PARTY>>- currency, meta_fields (list of {label, value})
- items: list of {idx, item_code, item_name, description, qty, uom, rate, amount, discount_percentage, frequency}
  A line can carry a deduction (e.g. "Abzüglich bereits verrechnet"): then item.discount_total > 0 (the deduction for the whole line), item.discount_label is its text, and item.gross_rate / item.gross_amount are the full price. Print the full price on the line and a sub-line with the label and −discount_total; item.amount is the net (gross_amount − discount_total). A layout that ignores it prints only the net amount (and gets a warning).
- taxes: list of {description, rate, tax_amount, total}
<<TEXTS>>- recurring_summary: per billing period [{frequency, label, net_total, grand_total, ...}]; show_frequency: whether any line recurs
- plus whatever deployment plugins add (e.g. qr_bill_img, the Swiss QR bill, on invoices: <img src="{{ qr_bill_img }}">; print it, usually on its own page at the end)
Rules: the PDF must show the document number, the customer or supplier, the currency, net and grand total, and every line's name and amount (the export rejects a layout that omits them), plus pdf_state_notice when set.""",
    "Delivery Note": """- doc: the delivery note (name, posting_date, customer, customer_name, remarks, status, docstatus, is_return, ...)
<<PARTY>>- meta_fields (list of {label, value}); show_warehouse (true)
- items: list of {idx, item_code, item_name, description, qty, uom, warehouse, warehouse_name} (rate/amount exist but a delivery note usually doesn't print prices)
<<TEXTS>>Rules: the PDF must show the document number, the customer and every line's name (the export rejects a layout that omits them), plus pdf_state_notice when set.""",
    "Proposal": """- proposal (also doc): the proposal (name, title, proposal_date, customer, customer_name, cover_letter, partner_name, partner_email, status)
<<PARTY>>- positions: one per included offer, in order: {letter ("A", "B", ...), quotation, title, blurb, is_recommended, currency, net_total, total_taxes_and_charges, grand_total, taxes, recurring_summary, items, pdf_state_notice}
  Each position's items are offer lines (same fields as an offer's items, including the deduction fields).
- An uploaded appendix PDF is attached after the layout's pages automatically.
Rules: the PDF must show the proposal number, the customer and, for every position, its offer number, every line's name and amount, and the position's net and grand total (the export rejects a layout that omits them).""",
    "Reservation": """- doc: the reservation (name, status, allocation_mode "Unit"/"Pool", item_code, asset, warehouse, qty, from_datetime, to_datetime, party_type, party, purpose, notes, voucher_type, voucher_no)
<<PARTY>>- item_name, asset_name, asset_tag, warehouse_name: display names
- period_from, period_to: the period formatted as "16.09.2026 08:00"; qty
- pdf_warnings: e.g. {code: "asset_unassigned"} for a pool booking without a machine yet
Rules: the PDF must show the reservation number and the customer or supplier, plus pdf_state_notice when set.""",
}

_REFERENCE_TAIL = """
Numbers: format with "%.2f"|format(value) or "{:,.2f}".format(value).
Images and fonts: upload them as template assets and reference them by file name (<img src="logo.png">, url("font.woff2")); external URLs are blocked.
Renderer notes (WeasyPrint):
<<FONTS>>
- A background on @page always fills the whole sheet; background-size/position there are ignored. For a band or rule at the paper edge, use an element with position: fixed and negative offsets equal to the page margins (e.g. top: -22mm; left: -20mm; width: 210mm; height: 2mm). Fixed elements repeat on every page.
- Margin boxes (@top-left, @bottom-right, ...) sit inside the page margin and can't reach the paper edge; use them for text such as page numbers.
- Repeating headers: <thead> rows repeat on every page; position: running(name) with content: element(name) in a margin box puts an element in every page's margin.
- Flexbox and grid work and break across pages.
Look at the result with view_pdf_pages after each change."""


def context_reference(doctype: str = "Quotation") -> str:
    from api.pdf_fonts import fonts_line
    body = _REFERENCES.get(doctype) or _REFERENCES["commercial"]
    body = body.replace("<<PARTY>>", _PARTY_VARIABLES).replace("<<TEXTS>>", _TEXT_VARIABLES if texts_for(doctype) else "")
    head = _REFERENCE_HEAD.replace("<<DOCTYPE>>", doctype).replace("<<FILE>>", standard_file(doctype))
    return head + body + _REFERENCE_TAIL.replace("<<FONTS>>", fonts_line())


def get_pdf_template(args, user=None):
    data = GetTemplate.model_validate(args)
    defaults = {dt: default_template(dt) for dt in DOCTYPES}
    if data.template == STANDARD:
        doctype = _doctype(data.doctype or DOCTYPES[0])
        result = _standard_summary(defaults, doctype)
        if data.include_html:
            result["html"] = standard_html(doctype)
        result["versions"] = _versions(STANDARD, (doctype,))
        result["assets"] = []
    else:
        row = _require(data.template)
        doctype = row["doctype"]
        result = _summary(row, defaults)
        if data.include_html:
            result["draft_html"] = row["draft_html"]
            result["live_html"] = row["live_html"]
        result["versions"] = _versions(row["name"], (doctype,))
        result["assets"] = _asset_list(_assets(row["assets"]))
        result["based_on"] = row["based_on"]
        result["created_at"], result["created_by"] = row["created_at"], row["created_by"]
        result["draft_modified_by"], result["published_by"] = row["draft_modified_by"], row["published_by"]
    result["context_reference"] = context_reference(doctype)
    result["required"] = [{"field": field, "variable": var, "label": label} for field, var, label in _required(doctype)]
    return result


def _asset_list(assets: dict) -> list[dict]:
    if not assets:
        return []
    rows = get_db().sql('SELECT sha256, content_type, LENGTH(data) AS size FROM "PDF Template Asset" WHERE sha256 IN ('
                        + ",".join("?" for _ in assets) + ")", list(assets.values()))
    meta = {row["sha256"]: row for row in rows}
    return [{"filename": name, "content_type": (meta.get(sha) or {}).get("content_type"),
             "size_bytes": int((meta.get(sha) or {}).get("size") or 0)} for name, sha in sorted(assets.items())]


def create_pdf_template(args, user=None):
    data = CreateTemplate.model_validate(args)
    doctype = _doctype(data.doctype)
    if data.html is not None:
        html, assets = data.html, {}
    elif data.based_on == STANDARD:
        html, assets = standard_html(doctype), {}
    else:
        source = _require(data.based_on)
        html, assets = source["draft_html"], _assets(source["assets"])
    _compile(html)
    db, stamp, actor = get_db(), now(), _actor(user)
    name = new_name("PDFT")
    with db.atomic():
        db.sql('INSERT INTO "PDF Template" (name, title, doctype, description, draft_html, assets, based_on, '
               'created_at, created_by, draft_modified, draft_modified_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               [name, data.title.strip(), doctype, data.description, html, json.dumps(assets, sort_keys=True),
                data.based_on if data.html is None else None, stamp, actor, stamp, actor])
    return get_pdf_template({"template": name, "include_html": False})


def update_pdf_template(args, user=None):
    """Edit the working copy in place; no version is created."""
    data = UpdateTemplate.model_validate(args)
    row = _require(data.template)
    if data.expected_draft_modified and data.expected_draft_modified != row["draft_modified"]:
        raise ValueError("The template was changed by someone else since you opened it "
                         f"(saved {row['draft_modified']} by {row['draft_modified_by']}). Reload it first.")
    changes = {}
    if data.html is not None:
        _compile(data.html)
        changes["draft_html"] = data.html
    if data.title is not None:
        changes["title"] = data.title.strip()
    if data.description is not None:
        changes["description"] = data.description
    db = get_db()
    if changes:
        changes.update(draft_modified=now(), draft_modified_by=_actor(user))
        with db.atomic():
            db.sql('UPDATE "PDF Template" SET ' + ", ".join(f"{key} = ?" for key in changes) + " WHERE name = ?",
                   [*changes.values(), row["name"]])
    if data.publish:
        return publish_pdf_template({"template": row["name"]}, user)
    return get_pdf_template({"template": row["name"], "include_html": False})


def publish_pdf_template(args, user=None):
    """Make the working copy the live layout, after a test render."""
    data = PublishTemplate.model_validate(args)
    row = _require(data.template)
    if row["archived"]:
        raise ValueError("Restore the template before publishing it")
    try:
        rendered, sample, blocked = render_preview(row["name"], source="draft", document=data.document)
    except PDFError as exc:
        raise ValueError(f"Not published: {exc}") from None
    if blocked:
        raise ValueError("Not published: the template loads resources that aren't allowed: "
                         + ", ".join(blocked[:5]) + ". Upload images and fonts as template assets.")
    db, stamp = get_db(), now()
    with db.atomic():
        db.sql('UPDATE "PDF Template" SET live_html = draft_html, live_assets = assets, published_at = ?, '
               'published_by = ? WHERE name = ?', [stamp, _actor(user), row["name"]])
    result = get_pdf_template({"template": row["name"], "include_html": False})
    result["checked_against"] = sample
    missing = [(field, var, label) for field, var, label in _required(row["doctype"])
               if not _prints(row["draft_html"], field, var)]
    known = {text[0] for text in (*_TEXTS, LINE_DEDUCTION)}
    result["missing_texts"] = [field for field, _var, _label in missing if field in known]
    result["missing_variables"] = [{"variable": var, "label": label} for field, var, label in missing
                                   if field not in known]
    result["warnings"] = [f"The layout never prints the {label} ({var}); documents that have one will print "
                          "without it." for _field, var, label in missing]
    if sample is None:
        result["note"] = "Published without a test render: there is no document to print yet."
    return result


def revert_pdf_template_draft(args, user=None):
    """Throw away unpublished edits: the working copy becomes the live layout again."""
    data = TemplateRef.model_validate(args)
    row = _require(data.template)
    if row["live_html"] is None:
        raise ValueError("This template has never been published; there is nothing to revert to")
    db = get_db()
    with db.atomic():
        db.sql('UPDATE "PDF Template" SET draft_html = live_html, assets = live_assets, draft_modified = ?, '
               'draft_modified_by = ? WHERE name = ?', [now(), _actor(user), row["name"]])
    return get_pdf_template({"template": row["name"], "include_html": False})


def archive_pdf_template(args, user=None):
    data = ArchiveTemplate.model_validate(args)
    row = _require(data.template)
    if data.archived and default_template(row["doctype"]) == row["name"]:
        raise ValueError("This template is the default; choose another default first")
    db = get_db()
    with db.atomic():
        db.sql('UPDATE "PDF Template" SET archived = ? WHERE name = ?', [int(data.archived), row["name"]])
    return get_pdf_template({"template": row["name"], "include_html": False})


def delete_pdf_template(args, user=None):
    data = TemplateRef.model_validate(args)
    row = _require(data.template)
    db = get_db()
    with db.atomic():
        if db.sql('SELECT 1 FROM "PDF Template Version" WHERE template = ? LIMIT 1', [row["name"]]):
            raise ValueError(f"{row['title']!r} has been used for issued documents and is kept as their "
                             "record. Archive it to hide it instead.")
        doctype = row["doctype"]
        columns = db._get_table_columns(doctype)
        where = ["pdf_template = ?"] + (["COALESCE(docstatus, 0) = 0"] if "docstatus" in columns else []) \
            + (["COALESCE(discarded, 0) = 0"] if "discarded" in columns else [])
        drafts = db.sql(f'SELECT name FROM "{doctype}" WHERE {" AND ".join(where)}', [row["name"]])
        if drafts:
            raise ValueError(f"{row['title']!r} is selected on {doctype} drafts ({', '.join(r['name'] for r in drafts[:5])}). "
                             "Choose another template there first, or archive it instead.")
        if default_template(row["doctype"]) == row["name"]:
            db.sql('DELETE FROM "Settings" WHERE key = ?', [DEFAULT_KEY.format(doctype=row["doctype"])])
        db.sql('DELETE FROM "PDF Template" WHERE name = ?', [row["name"]])
    return {"deleted": row["name"], "title": row["title"]}


def set_default_pdf_template(args, user=None):
    data = SetDefault.model_validate(args)
    doctype = _doctype(data.doctype)
    if data.template != STANDARD:
        row = _require(data.template)
        if row["doctype"] != doctype or row["archived"] or row["live_html"] is None:
            raise ValueError("The default must be a published, active template for " + doctype)
    db, key = get_db(), DEFAULT_KEY.format(doctype=doctype)
    with db.atomic():
        db.sql('DELETE FROM "Settings" WHERE key = ?', [key])
        db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', [key, data.template])
    return list_pdf_templates({"doctype": doctype})


def _with_texts(value: str) -> str:
    doctype = _doctype(value)
    if not texts_for(doctype):
        raise ValueError(f"{doctype} has no intro or closing text; types with texts: "
                         + ", ".join(dt for dt in DOCTYPES if texts_for(dt)))
    return doctype


def get_pdf_text_defaults(args, user=None):
    data = TextDefaults.model_validate(args)
    doctype = _with_texts(data.doctype)
    from api.pdf import TEXT_PLACEHOLDERS
    return {"doctype": doctype, **text_defaults(doctype),
            "placeholders": ["{{ %s }}" % name for name in TEXT_PLACEHOLDERS]}


def set_pdf_text_defaults(args, user=None):
    data = SetTextDefaults.model_validate(args)
    doctype = _with_texts(data.doctype)
    db = get_db()
    with db.atomic():
        for field in ("intro_text", "remarks"):
            value = getattr(data, field)
            if value is None:
                continue  # not sent: unchanged
            key = TEXT_DEFAULT_KEY.format(doctype=doctype, field=field)
            db.sql('DELETE FROM "Settings" WHERE key = ?', [key])
            if value.strip():
                db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', [key, value])
    return get_pdf_text_defaults({"doctype": doctype})


def preview_pdf_template(args, user=None):
    """Render the draft (or live) layout against a document, as a download."""
    data = Preview.model_validate(args)
    try:
        rendered, sample, blocked = render_preview(data.template, source=data.source, document=data.document,
                                                   doctype=data.doctype)
    except PDFError as exc:
        raise ValueError(str(exc)) from None
    doctype = _doctype(data.doctype or DOCTYPES[0]) if data.template == STANDARD else _require(data.template)["doctype"]
    if rendered is None:
        return {"rendered": False, "note": f"There is no {doctype} to preview with yet; create one first."}
    from api.pdf_exports import create_pdf_export
    from api.services import DOCTYPE_TO_SLUG
    export = create_pdf_export(DOCTYPE_TO_SLUG[doctype], sample, user, rendered=rendered)
    export.update(template=data.template, source=data.source, rendered=True,
                  blocked_resources=blocked, warnings=[f"Blocked: {url}" for url in blocked])
    return export


def delete_pdf_template_asset(args, user=None):
    data = DeleteAsset.model_validate(args)
    row = _require(data.template)
    assets = _assets(row["assets"])
    if data.filename not in assets:
        raise ValueError(f"{row['title']!r} has no asset {data.filename!r}")
    assets.pop(data.filename)
    _save_assets(row["name"], assets, user)
    return get_pdf_template({"template": row["name"], "include_html": False})


def add_pdf_template_asset(template_id: str, filename: str, payload: bytes, user=None) -> dict:
    """Store an image or font for a template's working copy (REST upload)."""
    row = _require(template_id)
    if not ASSET_NAME.fullmatch(filename or ""):
        raise ValueError("Asset names may use letters, digits, '.', '_' and '-' (e.g. logo.png)")
    content_type = ASSET_TYPES.get(os.path.splitext(filename)[1].lower())
    if not content_type:
        raise ValueError("Assets may be images (png, jpg, gif, webp, svg) or fonts (woff, woff2, ttf, otf)")
    if not payload or len(payload) > MAX_ASSET_BYTES:
        raise ValueError(f"Assets must be between 1 byte and {MAX_ASSET_BYTES // 1_000_000} MB")
    font = None
    if content_type.startswith("font/"):
        from api.pdf_fonts import font_file_info
        font = font_file_info(payload)
    assets = _assets(row["assets"])
    if filename not in assets and len(assets) >= MAX_ASSETS:
        raise ValueError(f"A template holds at most {MAX_ASSETS} assets")
    digest = hashlib.sha256(payload).hexdigest()
    db = get_db()
    with db.atomic():
        db.sql('INSERT INTO "PDF Template Asset" (sha256, content_type, data, created_at) VALUES (?, ?, ?, ?) '
               'ON CONFLICT DO NOTHING', [digest, content_type, payload, now()])
        assets[filename] = digest
        _save_assets(row["name"], assets, user)
    result = get_pdf_template({"template": row["name"], "include_html": False})
    if font:
        weight = "bold" if "bold" in (font["style"] or "").lower() else "normal"
        style = "italic" if "italic" in (font["style"] or "").lower() else "normal"
        result["font"] = {**font, "css": (
            f'@font-face {{ font-family: "{font["family"]}"; src: url("{filename}"); '
            f'font-weight: {weight}; font-style: {style}; }}')}
    return result


def _save_assets(template_id, assets, user):
    db = get_db()
    with db.atomic():
        db.sql('UPDATE "PDF Template" SET assets = ?, draft_modified = ?, draft_modified_by = ? WHERE name = ?',
               [json.dumps(assets, sort_keys=True), now(), _actor(user), template_id])


def _validate_selection(doc):
    """A draft may pick any active, published template of its type."""
    template = doc.get("pdf_template")
    if not template or template == STANDARD or doc.docstatus != 0:
        return
    row = _row(template)
    if not row or row["doctype"] != doc.DOCTYPE:
        raise PDFError(f"{doc.DOCTYPE}: unknown PDF template {template!r}; call list_pdf_templates",
                       code="pdf_template_error")
    if row["live_html"] is None:
        raise PDFError(f"{doc.DOCTYPE}: PDF template {row['title']!r} is not published yet",
                       code="pdf_template_error")
    stored = get_db().get_value(doc.DOCTYPE, doc.name, "pdf_template") if doc._persisted else None
    if row["archived"] and stored != template:
        raise PDFError(f"{doc.DOCTYPE}: PDF template {row['title']!r} is archived", code="pdf_template_error")


_ACTIONS = {
    "list_pdf_templates": (list_pdf_templates, ListTemplates, "viewer",
        "List the PDF layouts (templates) documents can print with (offers, sales orders, sales invoices "
        "incl. credit notes, purchase orders, delivery notes, proposals, reservations), including the built-in "
        "'standard' one, which is the default per document type and how many issued documents used each. "
        "Filter with doctype."),
    "get_pdf_template": (get_pdf_template, GetTemplate, "viewer",
        "Show one PDF template: its working copy (draft_html), the published layout (live_html), assets, the "
        "versions issued documents were printed with, the reference of variables a template of its document "
        "type can use, and what it must print (required). For 'standard', pass doctype to get that type's "
        "built-in layout. Read this before writing or changing a template."),
    "create_pdf_template": (create_pdf_template, CreateTemplate, "manager",
        "Create a PDF template (layout) for a document type, as a copy of that type's 'standard' layout or of "
        "another template (based_on; also another type's, e.g. the offer layout to start the invoice layout in "
        "the same style, then adapt it to this type's variables), or from complete HTML. It starts "
        "unpublished; edit it with update_pdf_template, check it with view_pdf_pages, then publish."),
    "update_pdf_template": (update_pdf_template, UpdateTemplate, "manager",
        "Change a PDF template's working copy: complete html (HTML + CSS with Jinja), title or description. "
        "Edits overwrite the working copy in place, as often as needed, without creating versions. "
        "publish=true also publishes it. Always send the whole html, not a fragment."),
    "publish_pdf_template": (publish_pdf_template, PublishTemplate, "manager",
        "Publish a PDF template's working copy after a test render against a document, so new and draft "
        "documents using it print with it. Issued documents keep the layout they were issued with. "
        "Only when the user asked to publish, after you checked the result with view_pdf_pages."),
    "revert_pdf_template_draft": (revert_pdf_template_draft, TemplateRef, "manager",
        "Discard unpublished edits to a PDF template: the working copy goes back to the published layout."),
    "preview_pdf_template": (preview_pdf_template, Preview, "viewer",
        "Render a PDF template's working copy (source='draft') or published layout ('live') against a "
        "document of its type (default: the most recent one) and return a download link for the user. It reports "
        "blocked resources and missing required content; to see the pages yourself, use view_pdf_pages."),
    "set_default_pdf_template": (set_default_pdf_template, SetDefault, "manager",
        "Choose the PDF template documents of a type print with unless one is chosen on the document "
        "('standard' for the built-in layout)."),
    "archive_pdf_template": (archive_pdf_template, ArchiveTemplate, "manager",
        "Archive a PDF template (archived=false restores it): it can no longer be chosen; issued documents "
        "keep printing with it."),
    "delete_pdf_template": (delete_pdf_template, TemplateRef, "manager",
        "Delete a PDF template that no issued document has used. Templates used for issued documents are "
        "kept as their record and can only be archived."),
    "get_pdf_text_defaults": (get_pdf_text_defaults, TextDefaults, "viewer",
        "Show the default intro text and closing text / terms new documents of a type start with (offers, "
        "sales orders, sales invoices, purchase orders, delivery notes), and the placeholders they may use."),
    "set_pdf_text_defaults": (set_pdf_text_defaults, SetTextDefaults, "manager",
        "Set the default intro text (intro_text) and/or closing text / terms (remarks) new documents of a "
        "type start with; an empty string clears one, an omitted field stays unchanged. Existing documents "
        "keep their texts. Same light markup as the notes; placeholders like {{ customer_name }} or {{ valid_till }} "
        "are filled in when the PDF prints."),
    "delete_pdf_template_asset": (delete_pdf_template_asset, DeleteAsset, "manager",
        "Remove an image or font from a PDF template's working copy. Assets are uploaded in the web app or "
        "via POST /api/pdf-templates/{template}/assets."),
}


def _wrap(handler):
    def run(args, user):
        try:
            return handler(args, user)
        except PDFError as exc:
            raise ValueError(str(exc)) from None
    return run


def initialize_pdf_templates():
    for name, (handler, model, role, description) in _ACTIONS.items():
        services.register_action(name, _wrap(handler), description=description,
                                 parameters=model.model_json_schema(), minimum_role=role, pass_user=True,
                                 # The shared demo account edits layouts like a manager;
                                 # uploads (and removing them) stay with real accounts.
                                 allow_public_manager=name != "delete_pdf_template_asset")
    from lambda_erp.hooks import _HOOKS, register_hook
    for doctype in DOCTYPES:
        for event, fn in ((f"{doctype}:before_save", _validate_selection),
                          (f"{doctype}:before_save", _apply_text_defaults),
                          (f"{doctype}:before_submit", _before_submit)):
            if fn not in _HOOKS.get(event, ()):
                register_hook(event, fn)
    if _text_warnings not in services.DOCUMENT_WARNING_PROVIDERS:
        services.DOCUMENT_WARNING_PROVIDERS.append(_text_warnings)
