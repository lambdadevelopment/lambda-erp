"""User-editable PDF templates for issued documents (Quotations for now).

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

The built-in layout ("standard") is not stored: it is whatever document.html
the deployment ships (a plugin's branded override wins). It is pinned and
archived like any other template, but rendered as trusted code.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from jinja2 import TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import Field

from api import services
from api.pdf_contract import PDFError
from lambda_erp.database import get_db
from lambda_erp.utils import new_name, now

logger = logging.getLogger("pdf_templates")

STANDARD = "standard"
STANDARD_TITLE = "Standard"
# Document types whose PDF layout can be chosen. Widen together with the
# per-type pinning column (pdf_template / pdf_template_version).
DOCTYPES = ("Quotation",)
DEFAULT_KEY = "pdf_template:{doctype}"

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

_sandbox = ImmutableSandboxedEnvironment(autoescape=True)


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
        base_url = _standard_file()
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


def _standard_file() -> str:
    from api import pdf
    return pdf._jinja_env.get_template("document.html").filename


def standard_html() -> str:
    from api import pdf
    source, _filename, _uptodate = pdf._jinja_env.loader.get_source(pdf._jinja_env, "document.html")
    return source


def _standard_layout() -> Layout:
    return Layout(STANDARD, STANDARD_TITLE, standard_html(), trusted=True)


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


def _live_layout(template_id: str) -> Layout:
    if template_id == STANDARD:
        return _standard_layout()
    row = _require(template_id)
    html = row["live_html"]
    if html is None:
        raise PDFError(f"PDF template {row['title']!r} has not been published yet", code="pdf_template_error")
    return Layout(template_id, row["title"], html, _assets(row["live_assets"]))


def _version_layout(version: str) -> Layout | None:
    rows = get_db().sql('SELECT * FROM "PDF Template Version" WHERE name = ?', [version])
    if not rows:
        return None
    row = rows[0]
    title = STANDARD_TITLE if row["template"] == STANDARD else ((_row(row["template"]) or {}).get("title") or row["template"])
    return Layout(row["template"], title, row["html"], _assets(row["assets"]), version=version,
                  trusted=row["template"] == STANDARD)


def layout_for(doctype: str, doc: dict) -> Layout | None:
    """The layout a document prints with now, or None for non-template types."""
    if doctype not in DOCTYPES:
        return None
    if doc.get("docstatus") in (1, 2) and doc.get("pdf_template_version"):
        layout = _version_layout(doc["pdf_template_version"])
        if layout:
            return layout
    if doc.get("docstatus") in (1, 2):
        return None  # issued before templates existed: the built-in path, as before
    return _live_layout(doc.get("pdf_template") or default_template(doctype))


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
# Status a document has right after submit, which the archived PDF shows.
_SUBMITTED_STATUS = {"Quotation": "Open"}


def _before_submit(doc):
    """Pin the layout and archive the PDF, inside the submit transaction.

    A custom layout that can't print this document blocks the submit, so every
    issued offer has a printable record. The built-in layout keeps today's
    behaviour: if it can't render, the document is submitted without an
    archive and printed on demand.
    """
    doctype = doc.DOCTYPE
    template = doc.get("pdf_template") or default_template(doctype)
    layout = _live_layout(template)
    version = _pin(layout)
    doc._data["pdf_template"] = template
    doc._data["pdf_template_version"] = version
    layout.version = version
    data = doc.as_dict()
    data["status"] = _SUBMITTED_STATUS.get(doctype, data.get("status"))
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
# Checks and previews
# ---------------------------------------------------------------------------

def _sample_document(doctype: str, name: str | None):
    from api.services import load_document, DOCTYPE_TO_SLUG
    slug = DOCTYPE_TO_SLUG.get(doctype, doctype.lower().replace(" ", "-"))
    if name:
        return load_document(slug, name)
    rows = get_db().sql(
        f'SELECT name FROM "{doctype}" WHERE COALESCE(discarded, 0) = 0 AND docstatus < 2 '
        f'AND name IN (SELECT parent FROM "{doctype} Item") ORDER BY modified DESC LIMIT 1')
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
        layout, doctype = _standard_layout(), doctype or DOCTYPES[0]
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


class GetTemplate(mentions.Input):
    template: str = ID
    include_html: bool = True


class CreateTemplate(mentions.Input):
    title: str = Field(min_length=1, max_length=120)
    doctype: str = Field(default="Quotation", max_length=100)
    based_on: str = Field(default=STANDARD, max_length=100)
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
    doctype: str = Field(default="Quotation", max_length=100)
    template: str = ID


class Preview(mentions.Input):
    template: str = ID
    source: str = Field(default="draft", pattern="^(draft|live)$")
    document: str | None = Field(default=None, max_length=140)


class DeleteAsset(mentions.Input):
    template: str = ID
    filename: str = Field(min_length=1, max_length=100)


def _actor(user) -> str | None:
    return (user or {}).get("user_id") or (user or {}).get("name")


def _doctype(value: str) -> str:
    for doctype in DOCTYPES:
        if value.strip().lower().replace("-", " ") == doctype.lower():
            return doctype
    raise ValueError(f"PDF templates are available for: {', '.join(DOCTYPES)}")


def _used_by(template_id: str) -> int:
    rows = get_db().sql(
        'SELECT COUNT(*) AS n FROM "Quotation" WHERE pdf_template_version IN '
        '(SELECT name FROM "PDF Template Version" WHERE template = ?)', [template_id])
    return int(rows[0]["n"]) if rows else 0


def _versions(template_id: str) -> list[dict]:
    rows = get_db().sql(
        'SELECT v.name, v.created_at, COUNT(q.name) AS documents, MIN(q.name) AS first_document '
        'FROM "PDF Template Version" v LEFT JOIN "Quotation" q ON q.pdf_template_version = v.name '
        'WHERE v.template = ? GROUP BY v.name, v.created_at ORDER BY v.created_at, v.name', [template_id])
    return [{"version": row["name"], "label": f"v{i}", "first_used": row["created_at"],
             "documents": int(row["documents"]), "example_document": row["first_document"]}
            for i, row in enumerate(rows, 1)]


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
        "used_by_documents": _used_by(row["name"]),
    }


def _standard_summary(defaults: dict) -> dict:
    return {"template": STANDARD, "title": STANDARD_TITLE, "doctype": ", ".join(DOCTYPES),
            "description": "The built-in layout this deployment ships. Read-only: create a template "
                           "based on it to change the look.",
            "standard": True, "default": any(v == STANDARD for v in defaults.values()),
            "archived": False, "published": True, "unpublished_changes": False,
            "used_by_documents": _used_by(STANDARD)}


def list_pdf_templates(args, user=None):
    data = ListTemplates.model_validate(args)
    doctype = _doctype(data.doctype) if data.doctype else None
    defaults = {dt: default_template(dt) for dt in DOCTYPES}
    rows = get_db().sql('SELECT * FROM "PDF Template" ORDER BY title, name')
    templates = [_standard_summary(defaults)] + [
        _summary(dict(row), defaults) for row in rows
        if (data.include_archived or not row["archived"]) and (not doctype or row["doctype"] == doctype)]
    return {"templates": templates, "defaults": defaults}


CONTEXT_REFERENCE = """Templates are HTML + CSS with Jinja ({{ }} / {% %}), rendered to PDF by WeasyPrint (supports @page, page counters, flexbox; no JavaScript).
Variables:
- doc: the document (name, transaction_date, valid_till, customer, customer_name, currency, net_total, total_taxes_and_charges, grand_total, discount_amount, remarks, status, docstatus, ...)
- title: document title ("Quotation"); pdf_state_notice: "Draft"/"Cancelled" text or empty; pdf_status
- company_name, company_info (email, phone, address, city, zip_code, country, tax_id, iban)
- party_label ("Customer"), party_name, party_info (email, phone, address, city, zip_code, country, tax_id)
- currency, page_size ("A4"/"Letter"), meta_fields (list of {label, value})
- items: list of {idx, item_code, item_name, description, qty, uom, rate, amount, discount_percentage, frequency}
- taxes: list of {description, rate, tax_amount, total}
- remarks_html: the notes/terms as safe HTML (render with {{ remarks_html|safe }})
- recurring_summary: per billing period [{frequency, label, net_total, grand_total, ...}]; show_frequency: whether any line recurs
- plus whatever deployment plugins add (e.g. qr_bill_img on invoices)
Numbers: format with "%.2f"|format(value) or "{:,.2f}".format(value).
Rules: the PDF must show the document number, the customer, the currency, net and grand total, and every line's name and amount (the export rejects a layout that omits them), plus pdf_state_notice when set. Images and fonts: upload them as template assets and reference them by file name (<img src="logo.png">, url("font.woff2")); external URLs are blocked."""


def get_pdf_template(args, user=None):
    data = GetTemplate.model_validate(args)
    defaults = {dt: default_template(dt) for dt in DOCTYPES}
    if data.template == STANDARD:
        result = _standard_summary(defaults)
        if data.include_html:
            result["html"] = standard_html()
        result["versions"] = _versions(STANDARD)
        result["assets"] = []
    else:
        row = _require(data.template)
        result = _summary(row, defaults)
        if data.include_html:
            result["draft_html"] = row["draft_html"]
            result["live_html"] = row["live_html"]
        result["versions"] = _versions(row["name"])
        result["assets"] = _asset_list(_assets(row["assets"]))
        result["based_on"] = row["based_on"]
        result["created_at"], result["created_by"] = row["created_at"], row["created_by"]
        result["draft_modified_by"], result["published_by"] = row["draft_modified_by"], row["published_by"]
    result["context_reference"] = CONTEXT_REFERENCE
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
        html, assets = standard_html(), {}
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
        drafts = db.sql('SELECT name FROM "Quotation" WHERE pdf_template = ? AND docstatus = 0 '
                        'AND COALESCE(discarded, 0) = 0', [row["name"]])
        if drafts:
            raise ValueError(f"{row['title']!r} is selected on draft offers ({', '.join(r['name'] for r in drafts[:5])}). "
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


def preview_pdf_template(args, user=None):
    """Render the draft (or live) layout against a document, as a download."""
    data = Preview.model_validate(args)
    try:
        rendered, sample, blocked = render_preview(data.template, source=data.source, document=data.document)
    except PDFError as exc:
        raise ValueError(str(exc)) from None
    if rendered is None:
        return {"rendered": False, "note": "There is no document to preview with yet; create an offer first."}
    from api.pdf_exports import create_pdf_export
    from api.services import DOCTYPE_TO_SLUG
    doctype = DOCTYPES[0] if data.template == STANDARD else _require(data.template)["doctype"]
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
    return get_pdf_template({"template": row["name"], "include_html": False})


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
        "List the PDF layouts (templates) documents can print with, including the built-in 'standard' one, "
        "which is the default per document type and how many issued documents used each."),
    "get_pdf_template": (get_pdf_template, GetTemplate, "viewer",
        "Show one PDF template: its working copy (draft_html), the published layout (live_html), assets, the "
        "versions issued documents were printed with, and the reference of variables a template can use. "
        "Read this before writing or changing a template."),
    "create_pdf_template": (create_pdf_template, CreateTemplate, "manager",
        "Create a PDF template (layout) for a document type (Quotation), as a copy of 'standard' or another "
        "template (based_on), or from complete HTML. It starts unpublished; edit it with update_pdf_template, "
        "check it with preview_pdf_template, then publish."),
    "update_pdf_template": (update_pdf_template, UpdateTemplate, "manager",
        "Change a PDF template's working copy: complete html (HTML + CSS with Jinja), title or description. "
        "Edits overwrite the working copy in place, as often as needed, without creating versions. "
        "publish=true also publishes it. Always send the whole html, not a fragment."),
    "publish_pdf_template": (publish_pdf_template, PublishTemplate, "manager",
        "Publish a PDF template's working copy after a test render against a document, so new and draft "
        "documents using it print with it. Issued documents keep the layout they were issued with."),
    "revert_pdf_template_draft": (revert_pdf_template_draft, TemplateRef, "manager",
        "Discard unpublished edits to a PDF template: the working copy goes back to the published layout."),
    "preview_pdf_template": (preview_pdf_template, Preview, "viewer",
        "Render a PDF template's working copy (source='draft') or published layout ('live') against a "
        "document (default: the most recent offer) and return a download link. Use it to check a change "
        "before publishing; it reports blocked resources and missing required content."),
    "set_default_pdf_template": (set_default_pdf_template, SetDefault, "manager",
        "Choose the PDF template documents of a type print with unless one is chosen on the document "
        "('standard' for the built-in layout)."),
    "archive_pdf_template": (archive_pdf_template, ArchiveTemplate, "manager",
        "Archive a PDF template (archived=false restores it): it can no longer be chosen; issued documents "
        "keep printing with it."),
    "delete_pdf_template": (delete_pdf_template, TemplateRef, "manager",
        "Delete a PDF template that no issued document has used. Templates used for issued documents are "
        "kept as their record and can only be archived."),
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
                                 parameters=model.model_json_schema(), minimum_role=role, pass_user=True)
    from lambda_erp.hooks import _HOOKS, register_hook
    for doctype in DOCTYPES:
        for event, fn in ((f"{doctype}:before_save", _validate_selection),
                          (f"{doctype}:before_submit", _before_submit)):
            if fn not in _HOOKS.get(event, ()):
                register_hook(event, fn)
