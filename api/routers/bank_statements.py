"""Manual CAMT statement preview, import history, and audit-file access."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from api.auth import require_non_public_manager, require_role
from lambda_erp.accounting.bank_statement_import import import_documents, preview_documents
from lambda_erp.accounting.camt import CamtError, MAX_ARCHIVE_BYTES, mask_iban, parse_camt_upload
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError


router = APIRouter(prefix="/bank-statements", tags=["bank-statements"])
MAX_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024


async def _parse_uploads(files: list[UploadFile]):
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one CAMT XML or ZIP file")
    documents = []
    total = 0
    for upload in files:
        data = await upload.read(MAX_ARCHIVE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail=f"File {upload.filename!r} is too large")
        if total > MAX_TOTAL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Combined uploads are too large")
        try:
            documents.extend(parse_camt_upload(data, upload.filename or "statement.xml"))
        except CamtError as exc:
            raise HTTPException(status_code=422, detail=f"{upload.filename}: {exc}") from exc
    return documents


@router.post("/preview")
async def preview_bank_statements(
    files: list[UploadFile] = File(...),
    _user: dict = Depends(require_non_public_manager),
):
    """Parse uploads without writing any statement or transaction data."""
    return preview_documents(await _parse_uploads(files))


@router.post("/import")
async def import_bank_statements(
    files: list[UploadFile] = File(...),
    mappings: str = Form(...),
    user: dict = Depends(require_non_public_manager),
):
    """Re-parse and atomically import the previewed uploads."""
    try:
        parsed_mappings = json.loads(mappings)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="mappings must be a JSON object") from exc
    if not isinstance(parsed_mappings, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed_mappings.items()
    ):
        raise HTTPException(status_code=422, detail="mappings must map statement keys to Bank Accounts")
    try:
        return import_documents(
            await _parse_uploads(files), parsed_mappings, imported_by=user.get("name")
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_bank_statement_imports(
    limit: int = 50,
    _user: dict = Depends(require_role("viewer")),
):
    limit = max(1, min(limit, 200))
    rows = get_db().sql(
        'SELECT i.name, i.bank_account, b.account_name AS bank_account_name, '
        'i.source_filename, i.schema_version, i.account_iban, i.currency, '
        'i.from_date, i.to_date, i.opening_balance, i.closing_balance, '
        'i.imported_entry_count, i.duplicate_entry_count, i.warning_count, '
        'i.status, i.imported_by, i.creation '
        'FROM "Bank Statement Import" i '
        'LEFT JOIN "Bank Account" b ON b.name = i.bank_account '
        'ORDER BY i.creation DESC LIMIT ?',
        [limit],
    )
    result = []
    for row in rows:
        item = dict(row)
        item["account_iban_masked"] = mask_iban(item.pop("account_iban") or "")
        result.append(item)
    return {"rows": result}


@router.get("/{import_name}/source")
def download_bank_statement_source(
    import_name: str,
    _user: dict = Depends(require_non_public_manager),
):
    rows = get_db().sql(
        'SELECT i.source_filename, s.source_data '
        'FROM "Bank Statement Import" i '
        'JOIN "Bank Statement Source" s ON s.import_name = i.name '
        'WHERE i.name = ?',
        [import_name],
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Bank statement source not found")
    filename = rows[0]["source_filename"] or f"{import_name}.xml"
    return Response(
        content=bytes(rows[0]["source_data"]),
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
