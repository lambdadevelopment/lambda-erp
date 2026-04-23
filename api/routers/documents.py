"""Generic CRUD routes for all document types."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from api.services import (
    create_document,
    load_document,
    update_document,
    submit_document,
    cancel_document,
    convert_document,
    list_documents,
    count_documents,
)
from api.pdf import generate_pdf
from api.auth import require_role

router = APIRouter(prefix="/documents", tags=["documents"])

_viewer = Depends(require_role("viewer"))
_manager = Depends(require_role("manager"))


@router.get("/{doctype_slug}")
def list_docs(
    doctype_slug: str,
    status: str | None = None,
    party: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    docstatus: int | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    _user: dict = _viewer,
):
    filters = {}
    if status:
        filters["status"] = status
    if docstatus is not None:
        filters["docstatus"] = docstatus
    if party:
        filters["customer"] = party
    if from_date:
        filters["from_date"] = from_date
    if to_date:
        filters["to_date"] = to_date
    rows = list_documents(doctype_slug, filters=filters, limit=limit, offset=offset)
    total = count_documents(doctype_slug, filters=filters)
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{doctype_slug}/search")
def search_docs(doctype_slug: str, q: str = "", limit: int = Query(default=10, le=50), _user: dict = _viewer):
    docs = list_documents(doctype_slug, limit=limit)
    if q:
        docs = [d for d in docs if q.lower() in d.get("name", "").lower()]
    return [{"name": d["name"]} for d in docs]


@router.get("/{doctype_slug}/{name}/pdf")
def get_pdf(doctype_slug: str, name: str, _user: dict = _viewer):
    pdf_bytes = generate_pdf(doctype_slug, name)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}.pdf"'},
    )


@router.get("/{doctype_slug}/{name}")
def get_doc(doctype_slug: str, name: str, _user: dict = _viewer):
    return load_document(doctype_slug, name)


@router.post("/{doctype_slug}")
def create_doc(doctype_slug: str, data: dict, _user: dict = _manager):
    return create_document(doctype_slug, data)


@router.put("/{doctype_slug}/{name}")
def update_doc(doctype_slug: str, name: str, data: dict, _user: dict = _manager):
    return update_document(doctype_slug, name, data)


@router.post("/{doctype_slug}/{name}/submit")
def submit_doc(doctype_slug: str, name: str, _user: dict = _manager):
    return submit_document(doctype_slug, name)


@router.post("/{doctype_slug}/{name}/cancel")
def cancel_doc(doctype_slug: str, name: str, _user: dict = _manager):
    return cancel_document(doctype_slug, name)


@router.post("/{doctype_slug}/{name}/convert")
def convert_doc(doctype_slug: str, name: str, data: dict, _user: dict = _manager):
    target = data.get("target_doctype")
    if not target:
        return {"detail": "target_doctype is required"}
    return convert_document(doctype_slug, name, target)
