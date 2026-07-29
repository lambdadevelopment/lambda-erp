"""Generic CRUD routes for all document types."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from api.services import (
    create_document,
    load_document,
    update_document,
    submit_document,
    cancel_document,
    discard_document,
    convert_document,
    list_documents,
    count_documents,
    document_columns,
    adjacent_documents,
)
from api.pdf import generate_pdf
from api.auth import require_role

router = APIRouter(prefix="/documents", tags=["documents"])

_viewer = Depends(require_role("viewer"))
_manager = Depends(require_role("manager"))

# Query params the list endpoint interprets itself — everything else is treated
# as an ad-hoc column=value filter (validated against the doctype's columns).
_LIST_RESERVED = {
    "status", "party", "from_date", "to_date", "docstatus",
    "include_discarded", "limit", "offset", "order_by", "order",
    "date_field", "search", "search_fields",
}


@router.get("/{doctype_slug}")
def list_docs(
    doctype_slug: str,
    request: Request,
    status: str | None = None,
    party: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    docstatus: int | None = None,
    include_discarded: bool = False,
    order_by: str | None = None,
    order: str = "desc",
    date_field: str | None = None,
    search: str | None = None,
    search_fields: str | None = None,
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

    # Ad-hoc equality filters: any remaining query param that names a real column
    # of this doctype (e.g. /documents/activity?lead_id=LEAD-3316). Validate
    # against the live columns so an unknown field is a 400, never interpolated.
    columns = document_columns(doctype_slug)
    for key, value in request.query_params.items():
        if key in _LIST_RESERVED:
            continue
        if key not in columns:
            raise HTTPException(status_code=400, detail=f"Unknown filter field: {key}")
        filters[key] = value

    # Which column from_date/to_date filter on. The frontend passes its declared
    # dateField so plugin doctypes get working date filters without a server-side
    # map. Passed through untouched here; list_documents ignores it unless it's a
    # real column (so a config with a synthetic dateField degrades to no date
    # filter rather than 400-ing the whole list), and never interpolates it blind.
    if date_field:
        filters["date_field"] = date_field

    # Free-text search across the doctype's declared search_fields (+ any
    # registered related-table expansion). search_fields is a CSV validated to
    # real columns, mirroring the arbitrary-filter / order_by contract.
    if search:
        filters["search"] = search
        fields = [f.strip() for f in (search_fields or "").split(",") if f.strip()]
        bad = [f for f in fields if f not in columns]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown search field(s): {', '.join(bad)}")
        if fields:
            filters["search_fields"] = fields

    if order_by is not None and order_by not in columns:
        raise HTTPException(status_code=400, detail=f"Unknown order_by field: {order_by}")
    if order.lower() not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    rows = list_documents(doctype_slug, filters=filters, limit=limit, offset=offset,
                          include_discarded=include_discarded, order_by=order_by, order=order)
    total = count_documents(doctype_slug, filters=filters, include_discarded=include_discarded)
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{doctype_slug}/{name}/adjacent")
def adjacent_doc(
    doctype_slug: str,
    name: str,
    request: Request,
    status: str | None = None,
    party: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    docstatus: int | None = None,
    include_discarded: bool = False,
    order_by: str | None = None,
    order: str = "desc",
    date_field: str | None = None,
    search: str | None = None,
    search_fields: str | None = None,
    _user: dict = _viewer,
):
    """Prev/next record around `name` in the same order+filters the list uses.
    Returns {"prev": name|None, "next": name|None}. Filters mirror the list
    endpoint so navigation stays within the list the user came from."""
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
    columns = document_columns(doctype_slug)
    for key, value in request.query_params.items():
        if key in _LIST_RESERVED:
            continue
        if key not in columns:
            raise HTTPException(status_code=400, detail=f"Unknown filter field: {key}")
        filters[key] = value
    if date_field:
        filters["date_field"] = date_field
    if search:
        filters["search"] = search
        fields = [f.strip() for f in (search_fields or "").split(",") if f.strip()]
        bad = [f for f in fields if f not in columns]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown search field(s): {', '.join(bad)}")
        if fields:
            filters["search_fields"] = fields
    if order_by is not None and order_by not in columns:
        raise HTTPException(status_code=400, detail=f"Unknown order_by field: {order_by}")
    if order.lower() not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")
    return adjacent_documents(doctype_slug, name, filters=filters,
                              include_discarded=include_discarded, order_by=order_by, order=order)


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


@router.post("/{doctype_slug}/{name}/discard")
def discard_doc(doctype_slug: str, name: str, _user: dict = _manager):
    """Void an unwanted draft (soft delete — kept for the audit trail, hidden
    from default lists). Only valid on drafts; submitted docs must be cancelled."""
    return discard_document(doctype_slug, name)


@router.post("/{doctype_slug}/{name}/convert")
def convert_doc(doctype_slug: str, name: str, data: dict, _user: dict = _manager):
    target = data.get("target_doctype")
    if not target:
        return {"detail": "target_doctype is required"}
    return convert_document(doctype_slug, name, target)
