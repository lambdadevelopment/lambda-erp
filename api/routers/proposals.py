"""Proposal (Sammelofferte) extras: the uploaded appendix PDF and the
cover-letter default.

The Proposal itself is a normal document — list/get/create/update go through the
generic `/documents/proposal` routes. Only the appendix (a binary blob stored
out of the Proposal row) and the cover-letter pre-fill need dedicated endpoints.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from lambda_erp.database import get_db
from lambda_erp.utils import now
from api.auth import require_role

router = APIRouter(prefix="/proposals", tags=["proposals"])

_viewer = Depends(require_role("viewer"))
_manager = Depends(require_role("manager"))

MAX_APPENDIX_SIZE = 15 * 1024 * 1024  # 15 MB


def _fill(template: str, customer: dict) -> str:
    """Resolve the handful of placeholders a cover-letter template may use."""
    contact = (customer.get("contact_person") or "").strip()
    return (template or "").format(
        customer_name=customer.get("customer_name") or "",
        contact_person=contact,
        salutation=f"Sehr geehrte Damen und Herren" if not contact else f"Sehr geehrte/r {contact}",
    )


@router.get("/cover-default")
def cover_default(company: str = "", customer: str = "", _user: dict = _viewer):
    """The default cover-letter text a new proposal pre-fills, taken from the
    company's `proposal_cover_template` with placeholders resolved against the
    chosen customer. Returns "" when no template is configured."""
    db = get_db()
    template = ""
    if company:
        template = db.get_value("Company", company, "proposal_cover_template") or ""
    cust = {}
    if customer:
        row = db.get_value("Customer", customer, ["customer_name", "contact_person"])
        if row:
            cust = dict(row)
    try:
        text = _fill(template, cust)
    except (KeyError, IndexError, ValueError):
        # A template with an unknown {placeholder} shouldn't 500 — hand back raw.
        text = template
    return {"cover_letter": text}


@router.post("/{name}/appendix")
async def upload_appendix(name: str, file: UploadFile = File(...), user: dict = _manager):
    """Attach (or replace) the appendix PDF stapled to the end of the proposal."""
    db = get_db()
    if not db.exists("Proposal", name):
        raise HTTPException(status_code=404, detail=f"Proposal {name} not found")

    mime = (file.content_type or "").lower()
    if mime != "application/pdf":
        raise HTTPException(status_code=400, detail="Appendix must be a PDF.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > MAX_APPENDIX_SIZE:
        raise HTTPException(status_code=413, detail=f"Appendix too large (max {MAX_APPENDIX_SIZE // (1024*1024)} MB).")

    filename = file.filename or "appendix.pdf"
    # Upsert the 1:1 appendix row, then mark the filename on the proposal so the
    # UI can show what's attached.
    db.delete("Proposal Appendix", filters={"parent": name})
    db.sql(
        'INSERT INTO "Proposal Appendix" (parent, filename, data, uploaded) VALUES (?, ?, ?, ?)',
        [name, filename, data, now()],
    )
    db.set_value("Proposal", name, "appendix_filename", filename)
    db.commit()
    return {"appendix_filename": filename, "size": len(data)}


@router.delete("/{name}/appendix")
def delete_appendix(name: str, user: dict = _manager):
    """Remove the appendix PDF from the proposal."""
    db = get_db()
    if not db.exists("Proposal", name):
        raise HTTPException(status_code=404, detail=f"Proposal {name} not found")
    db.delete("Proposal Appendix", filters={"parent": name})
    db.set_value("Proposal", name, "appendix_filename", None)
    db.commit()
    return {"ok": True}


@router.get("/{name}/appendix")
def download_appendix(name: str, _user: dict = _viewer):
    """Download the stored appendix PDF (for previewing what's attached)."""
    db = get_db()
    rows = db.sql('SELECT filename, data FROM "Proposal Appendix" WHERE parent = ?', [name])
    if not rows or not rows[0].get("data"):
        raise HTTPException(status_code=404, detail="No appendix attached.")
    data = rows[0]["data"]
    if isinstance(data, memoryview):
        data = data.tobytes()
    filename = rows[0].get("filename") or "appendix.pdf"
    return Response(
        content=bytes(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
