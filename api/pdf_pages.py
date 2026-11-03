"""Let the chat model (and MCP clients) look at rendered PDF pages.

`view_pdf_pages` renders a document's PDF, a PDF template's working copy or
published layout against a document, or a PDF/image the caller uploaded to the
chat (a reference to compare with), and returns the requested pages as PNG
images. The model asks for the pages it wants to check; nothing is stored for
the user (preview_pdf_template and generate_document_pdf do that).

Images ride in the result under ``_images`` (base64 PNG). Each surface turns
them into its own image format: the chat loop shows them to the model for the
rest of the turn without persisting them, MCP returns image content, and REST
returns the JSON as is.
"""

from __future__ import annotations

import base64
import io
from typing import Literal

from pydantic import Field

from api import mentions, services
from api.pdf_contract import PDFError

MAX_PAGES_PER_CALL = 4
# A whole page at 110 dpi is ~910 x 1290 px for A4, about what vision models
# downscale to anyway. A band (a third of the page) renders at 220 dpi, so a
# 2 mm rule or 7 pt footer text is still several pixels tall.
PAGE_DPI = 110
AREA_DPI = 220
AREAS = {"page": (0.0, 1.0), "top": (0.0, 0.36), "middle": (0.32, 0.68), "bottom": (0.64, 1.0)}


class ViewPages(mentions.Input):
    doctype: str | None = Field(default=None, max_length=100,
                                description="Document type slug, e.g. 'quotation' or 'sales-invoice'. With a "
                                            "template it is only needed for 'standard' (default: quotation).")
    name: str | None = Field(default=None, max_length=140,
                             description="Document to render. With a template, the document of the template's "
                                         "type to render it against (default: the most recent one).")
    template: str | None = Field(default=None, max_length=100,
                                 description="PDF template id to render instead of the document's own layout.")
    source: Literal["draft", "live"] = Field(default="draft",
                                             description="With template: the working copy (draft) or the published layout.")
    pages: list[int] = Field(default_factory=lambda: [1], max_length=MAX_PAGES_PER_CALL,
                             description=f"1-based page numbers, at most {MAX_PAGES_PER_CALL} per call.")
    attachment_id: str | None = Field(default=None, max_length=64,
                                      description="A PDF or image you were given in the chat (list_chat_attachments), "
                                                  "e.g. a reference to compare a template with. An image is one page.")
    area: Literal["page", "top", "middle", "bottom"] = Field(
        default="page", description="'page' for the whole page, or a third of it at double resolution "
                                    "to check small details (rules, footers, logos).")


def _png(image, number: int, area: str) -> dict:
    if area != "page":
        top, bottom = AREAS[area]
        image = image.crop((0, int(image.height * top), image.width, int(image.height * bottom)))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG", optimize=True)
    return {"page": number, "area": area, "mime_type": "image/png", "width": image.width,
            "height": image.height, "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def _check_pages(pages: list[int], total: int, what: str = "The PDF"):
    missing = [p for p in pages if not 1 <= p <= total]
    if missing:
        raise ValueError(f"{what} has {total} page(s); there is no page {missing[0]}.")


def _rasterize(pdf: bytes, pages: list[int], area: str) -> tuple[int, list[dict]]:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError as exc:
        raise ValueError(f"Not a readable PDF ({exc})") from None
    try:
        total = len(doc)
        _check_pages(pages, total)
        scale = (PAGE_DPI if area == "page" else AREA_DPI) / 72
        images = []
        for number in dict.fromkeys(pages):
            page = doc[number - 1]
            try:
                images.append(_png(page.render(scale=scale).to_pil(), number, area))
            finally:
                page.close()
        return total, images
    finally:
        doc.close()


def _image_pages(data: bytes, pages: list[int], area: str) -> tuple[int, list[dict]]:
    """An uploaded image as a one-page document, at most as large as a rendered page."""
    from PIL import Image
    _check_pages(pages, 1, "An image")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 — unreadable/oversized upload; the user's file, not a fault here
        raise ValueError(f"Not a readable image ({exc.__class__.__name__})") from None
    longest = 2 * 1290 if area != "page" else 1290  # what a rendered A4 page would be
    image.thumbnail((longest, longest))
    return 1, [_png(image, 1, area)]


def _attachment(attachment_id: str, user) -> dict:
    from api.attachments import get_attachments_by_ids
    owner = (user or {}).get("name")
    atts = get_attachments_by_ids([attachment_id], owner) if owner else []
    if not atts:
        raise ValueError("Attachment not found or access denied.")
    return atts[0]


def _doctype_name(value: str) -> str:
    from api.services import SLUG_TO_DOCTYPE
    return SLUG_TO_DOCTYPE.get(value.strip().lower(), value)


def view_pdf_pages(args, user=None):
    data = ViewPages.model_validate(args)
    if not data.pages:
        raise ValueError("Ask for at least one page.")
    if data.attachment_id and (data.template or data.doctype):
        raise ValueError("Pass one of: attachment_id, template, or doctype + name.")
    if data.attachment_id:
        att = _attachment(data.attachment_id, user)
        if att["mime_type"] == "application/pdf":
            total, images = _rasterize(att["data"], data.pages, data.area)
        elif att["mime_type"].startswith("image/"):
            total, images = _image_pages(att["data"], data.pages, data.area)
        else:
            raise ValueError(f"{att['filename']} is neither a PDF nor an image.")
        return _result({"attachment_id": att["id"], "filename": att["filename"]}, total, images, data.area, [])
    try:
        if data.template:
            from api import pdf_templates
            rendered, name, blocked = pdf_templates.render_preview(
                data.template, source=data.source, document=data.name,
                doctype=_doctype_name(data.doctype) if data.doctype else None)
            if rendered is None:
                raise ValueError("There is no document of the template's type to render it with yet; create one first.")
            subject = {"template": data.template, "source": data.source, "document": name}
        else:
            if not (data.doctype and data.name):
                raise ValueError("Pass doctype and name of a document, or a template.")
            from api.pdf import render_document_pdf
            rendered, blocked = render_document_pdf(data.doctype, data.name), []
            subject = {"doctype": data.doctype, "document": data.name, "status": rendered.status}
    except PDFError as exc:
        raise ValueError(str(exc)) from None
    total, images = _rasterize(rendered.data, data.pages, data.area)
    return _result(subject, total, images, data.area, blocked)


def _result(subject: dict, total: int, images: list[dict], area: str, blocked: list) -> dict:
    shown = ", ".join(str(i["page"]) for i in images)
    return {
        **subject,
        "pages_total": total,
        "pages_shown": [i["page"] for i in images],
        "area": area,
        "blocked_resources": blocked,
        "note": (f"Page(s) {shown} of {total} ({area}) are attached as images. Check them against "
                 "what you intended (and any reference the user gave) before saying the change is done."),
        "_images": images,
    }


DESCRIPTION = (
    "Look at a PDF: returns the requested pages as images you can see. Render a document (doctype + "
    "name, exactly as it downloads), a PDF template (template, source draft|live, optionally name = the "
    "document of its type to render it against; for 'standard' also doctype), or a PDF or image the user uploaded in the chat (attachment_id), e.g. "
    "a reference layout. Use area top/middle/bottom to see a third of the page at double resolution for "
    "small details. After changing a PDF template, look at the result before telling the user it is "
    "done; check every page you changed. To match a reference, view the same page and area of the "
    "reference and of your render and compare them. Nothing is shown to the user."
)


def initialize_pdf_pages():
    services.register_action("view_pdf_pages", view_pdf_pages, description=DESCRIPTION,
                             parameters=ViewPages.model_json_schema(), minimum_role="viewer",
                             allow_public_manager=True, pass_user=True)


def image_parts(images: list[dict]) -> list[dict]:
    """Chat-completions content parts for `_images` (the chat loop injects them)."""
    parts = []
    for image in images:
        parts.append({"type": "text", "text": f"Page {image['page']} ({image['area']}):"})
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{image['mime_type']};base64,{image['data']}", "detail": "high"}})
    return parts
