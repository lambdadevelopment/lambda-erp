"""Files kept with a document or master record.

The customer's order as it arrived, a signed delivery slip, a certificate:
kept next to the record they belong to instead of in a folder. An attachment
never changes the record (no `modified` bump, allowed on submitted documents
too) and is never printed with it.

Whoever may read the record may list and download its files; adding and
removing them needs the manager role. The demo user gets neither.

REST:
    GET    /api/record-attachments?record_type=sales-order&record_id=SO-0001
    POST   /api/record-attachments          (multipart: record_type, record_id, file, description)
    GET    /api/record-attachments/{id}     (the file)
    DELETE /api/record-attachments/{id}

Chat / MCP: actions list_record_attachments and attach_chat_file (keeps a
file that was uploaded to the conversation with a record).

See docs/attachments.md.
"""
import hashlib
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field

from api import mentions, services
from api.auth import get_current_user
from lambda_erp.database import get_db
from lambda_erp.utils import now

TABLE = "Record Attachment"
MAX_SIZE = 15 * 1024 * 1024
MAX_PER_RECORD = 50
# What may be kept: documents, images, mail, spreadsheets. Nothing a browser
# would run; files are always sent back as downloads.
ALLOWED = {
    ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".eml": "message/rfc822", ".msg": "application/vnd.ms-outlook",
    ".txt": "text/plain", ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xml": "application/xml",
}
_UNSAFE = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)

router = APIRouter(prefix="/record-attachments", tags=["attachments"])


def _target(record_type):
    doctype = services.SLUG_TO_DOCTYPE.get(record_type)
    if doctype:
        return doctype
    master = services.MASTER_TABLES.get(record_type)
    return master[0] if master else None


def _require_record(record_type, record_id, user, write=False):
    """The record exists and the caller may read it (and, to write, is at
    least a manager). Refusals do not say whether the record exists."""
    from lambda_erp import permissions
    from lambda_erp.exceptions import PermissionDenied

    table = _target(record_type)
    role = (user or {}).get("role")
    if not table or role not in mentions.REAL_ROLES or not get_db().exists(table, record_id):
        raise HTTPException(404, "Record is unavailable")
    try:
        with permissions.acting_as(user):
            permissions.require_readable(table, record_id)
    except PermissionDenied:
        raise HTTPException(404, "Record is unavailable")
    if write and role not in ("manager", "admin"):
        raise HTTPException(403, "Adding or removing attachments requires the manager role")
    return table


def safe_filename(filename):
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    name = _UNSAFE.sub("_", name).strip(". ")[:150]
    return name or "attachment"


def _public(row):
    return {"id": row["name"], "record_type": row["record_type"], "record_id": row["record_id"],
            "filename": row["filename"], "content_type": row["content_type"], "size": row["size"],
            "description": row.get("description"), "uploaded_by": row.get("uploaded_by_name") or row.get("uploaded_by"),
            "created_at": row.get("creation"), "url": f"/api/record-attachments/{row['name']}"}


def list_attachments(record_type, record_id, user):
    _require_record(record_type, record_id, user)
    rows = get_db().sql(
        f'SELECT name, record_type, record_id, filename, content_type, size, description, uploaded_by, '
        f'uploaded_by_name, creation FROM "{TABLE}" WHERE record_type = ? AND record_id = ? ORDER BY creation, name',
        [record_type, record_id])
    return {"attachments": [_public(row) for row in rows]}


def add_attachment(record_type, record_id, filename, data, user, description=None):
    """Keep a file with a record. Returns its public description."""
    _require_record(record_type, record_id, user, write=True)
    filename = safe_filename(filename)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED:
        raise HTTPException(415, f"{extension or 'This file type'} cannot be attached. "
                                 f"Allowed: {', '.join(sorted(ALLOWED))}")
    if not data:
        raise HTTPException(400, "The file is empty")
    if len(data) > MAX_SIZE:
        raise HTTPException(413, f"The file is too large (at most {MAX_SIZE // (1024 * 1024)} MB)")
    db = get_db()
    count = db.sql(f'SELECT COUNT(*) AS n FROM "{TABLE}" WHERE record_type = ? AND record_id = ?',
                   [record_type, record_id])[0]["n"]
    if count >= MAX_PER_RECORD:
        raise HTTPException(409, f"A record holds at most {MAX_PER_RECORD} attachments")
    name = uuid.uuid4().hex
    db.sql(
        f'INSERT INTO "{TABLE}" (name, record_type, record_id, filename, content_type, size, sha256, data, '
        f'description, uploaded_by, uploaded_by_name, creation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [name, record_type, record_id, filename, ALLOWED[extension], len(data), hashlib.sha256(data).hexdigest(),
         data, (str(description).strip()[:500] or None) if description else None,
         user.get("user_id") or user.get("name"), user.get("full_name") or user.get("email"), now()])
    db.commit()
    row = db.sql(f'SELECT name, record_type, record_id, filename, content_type, size, description, uploaded_by, '
                 f'uploaded_by_name, creation FROM "{TABLE}" WHERE name = ?', [name])[0]
    return _public(row)


def _load(attachment_id, user, write=False):
    rows = get_db().sql(f'SELECT * FROM "{TABLE}" WHERE name = ?', [attachment_id])
    if not rows:
        raise HTTPException(404, "Attachment not found")
    _require_record(rows[0]["record_type"], rows[0]["record_id"], user, write=write)
    return rows[0]


def delete_attachments_for(record_type, record_id):
    """Drop a hard-deleted record's files."""
    get_db().sql(f'DELETE FROM "{TABLE}" WHERE record_type = ? AND record_id = ?', [record_type, record_id])


# -- REST ---------------------------------------------------------------------

@router.get("")
def list_route(record_type: str, record_id: str, user: dict = Depends(get_current_user)):
    return list_attachments(record_type, record_id, user)


@router.post("")
async def upload_route(record_type: str = Form(...), record_id: str = Form(...), file: UploadFile = File(...),
                       description: str | None = Form(None), user: dict = Depends(get_current_user)):
    # Check access before reading the body into memory.
    _require_record(record_type, record_id, user, write=True)
    data = await file.read(MAX_SIZE + 1)
    return add_attachment(record_type, record_id, file.filename, data, user, description)


@router.get("/{attachment_id}")
def download_route(attachment_id: str, user: dict = Depends(get_current_user)):
    row = _load(attachment_id, user)
    data = row["data"]
    if isinstance(data, memoryview):
        data = data.tobytes()
    ascii_name = row["filename"].encode("ascii", "replace").decode().replace('"', "_")
    return Response(content=bytes(data), media_type=row["content_type"], headers={
        # Always a download, never rendered in the application's origin.
        "Content-Disposition": f'attachment; filename="{ascii_name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


@router.delete("/{attachment_id}")
def delete_route(attachment_id: str, user: dict = Depends(get_current_user)):
    row = _load(attachment_id, user, write=True)
    db = get_db()
    db.sql(f'DELETE FROM "{TABLE}" WHERE name = ?', [row["name"]])
    db.commit()
    return {"ok": True}


# -- chat / MCP ---------------------------------------------------------------

class ListAttachments(mentions.Input):
    record_type: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=200)


class AttachChatFile(mentions.Input):
    record_type: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=200)
    attachment_id: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


def _list_action(args, user):
    data = ListAttachments.model_validate(args)
    return list_attachments(data.record_type, data.record_id, user)


def _attach_chat_file(args, user):
    data = AttachChatFile.model_validate(args)
    from api.attachments import get_attachments_by_ids
    found = get_attachments_by_ids([data.attachment_id], user.get("user_id") or user.get("name"))
    if not found:
        raise HTTPException(404, "That file was not uploaded to one of your conversations")
    return add_attachment(data.record_type, data.record_id, found[0]["filename"], found[0]["data"], user,
                          data.description)


def initialize_record_attachments():
    services.register_action(
        "list_record_attachments", _list_action, minimum_role="viewer", pass_user=True,
        parameters=ListAttachments.model_json_schema(),
        description="List the files kept with one document or master record (name, type, size, who added it). "
                    "record_type is the document or master type slug (e.g. sales-order, item) and record_id its "
                    "name. The files themselves are downloaded in the application.")
    services.register_action(
        "attach_chat_file", _attach_chat_file, minimum_role="manager", pass_user=True,
        parameters=AttachChatFile.model_json_schema(),
        description="Keep a file the user uploaded to this conversation with a document or master record, e.g. "
                    "the customer's order e-mail or PDF with the sales order made from it. attachment_id is the "
                    "id of the uploaded file. It does not change the record and is never printed.")
