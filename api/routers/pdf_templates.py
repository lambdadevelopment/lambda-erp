"""Binary endpoints for PDF templates: previews and asset uploads.

Everything else (list, edit, publish, defaults, delete) is a registered action
in api/pdf_templates.py, shared by REST (/api/actions/...), chat and MCP.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from api import pdf_templates
from api.auth import require_role
from api.pdf_contract import PDFError
from api.pdf_exports import pdf_content_disposition
from lambda_erp.database import get_db

router = APIRouter(prefix="/pdf-templates", tags=["pdf-templates"])
_viewer = Depends(require_role("viewer"))
_manager = Depends(require_role("manager"))


@router.get("/{template}/preview")
def preview(template: str, source: str = "draft", document: str | None = None, doctype: str | None = None,
            user: dict = _viewer):
    """The template's working copy (or published layout) rendered against a document."""
    if source not in ("draft", "live"):
        raise HTTPException(422, "source must be 'draft' or 'live'")
    try:
        rendered, sample, blocked = pdf_templates.render_preview(template, source=source, document=document,
                                                                 doctype=doctype)
    except (ValueError, PDFError) as exc:
        raise HTTPException(422, str(exc))
    if rendered is None:
        raise HTTPException(404, "There is no document of this type to preview with yet; create one first.")
    headers = {"Content-Disposition": pdf_content_disposition(f"{sample}-preview"),
               "Cache-Control": "no-store", "X-PDF-Document": sample}
    if blocked:
        headers["X-PDF-Blocked"] = ", ".join(blocked[:5]).encode("ascii", "replace").decode()
    return Response(rendered.data, media_type="application/pdf", headers=headers)


@router.post("/{template}/assets")
async def upload_asset(template: str, file: UploadFile = File(...), user: dict = _manager):
    if user.get("role") == "public_manager":
        raise HTTPException(403, "Demo accounts can't change PDF templates")
    data = await file.read(pdf_templates.MAX_ASSET_BYTES + 1)
    try:
        return pdf_templates.add_pdf_template_asset(template, file.filename or "", data, user)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/{template}/assets/{filename}")
def get_asset(template: str, filename: str, user: dict = _viewer):
    row = pdf_templates._row(template)
    sha = pdf_templates._assets(row["assets"]).get(filename) if row else None
    rows = get_db().sql('SELECT content_type, data FROM "PDF Template Asset" WHERE sha256 = ?', [sha]) if sha else []
    if not rows:
        raise HTTPException(404, "Asset not found")
    content_type = rows[0]["content_type"]
    # An SVG could carry script; never let the browser run it as a page.
    headers = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"}
    return Response(bytes(rows[0]["data"]), media_type=content_type, headers=headers)
