"""Files kept with a document or master record.

The customer's order as it arrived, a signed delivery slip, a certificate:
kept next to the record they belong to instead of in a folder. An attachment
never changes the record (no `modified` bump, allowed on submitted documents
too) and is never printed with it.

Whoever may read the record may list and download its files; adding and
removing them needs the manager role. The demo user gets neither.

A file's content cannot be redacted like a field, so each file carries a
sensitivity class as a whole (lambda_erp.permissions, free content): its own
when one was set, else the class of the record type it is kept with. A file
on a purchase order or a supplier counts as purchase prices and does not
exist for a user who does not see those.

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


def effective_class(row):
    """The sensitivity class of a stored file: its own, else its record type's."""
    from lambda_erp import permissions
    own = row.get("sensitivity")
    if own:
        return None if own == permissions.HARMLESS else own
    return permissions.content_class(_target(row["record_type"]))


def _hidden_from(row, user):
    from lambda_erp import permissions
    with permissions.acting_as(user):
        return permissions.content_hidden(effective_class(row))


def _check_sensitivity(value, record_type, user, current=None):
    """A sensitivity setting a caller may give: empty (the record type's
    class), 'none' (nothing sensitive) or a class. Only someone who sees the
    classes involved can set or lift one."""
    from lambda_erp import permissions
    value = (str(value).strip() or None) if value is not None else None
    if value not in (None, permissions.HARMLESS) and value not in permissions.SENSITIVITY_CLASSES:
        raise HTTPException(422, f"sensitivity must be empty, '{permissions.HARMLESS}' or one of "
                                 f"{', '.join(sorted(permissions.SENSITIVITY_CLASSES))}")
    default = permissions.content_class(_target(record_type))
    with permissions.acting_as(user):
        involved = {default, current, value} - {None, permissions.HARMLESS}
        blind = sorted(cls for cls in involved if permissions.content_hidden(cls))
    if blind and (value is not None or current is not None):
        raise HTTPException(403, "Your access profile hides this kind of content, so you cannot classify files "
                                 "kept with this record")
    return value


def _public(row):
    return {"id": row["name"], "record_type": row["record_type"], "record_id": row["record_id"],
            "filename": row["filename"], "content_type": row["content_type"], "size": row["size"],
            "sensitivity": row.get("sensitivity") or None, "sensitivity_class": effective_class(row),
            "description": row.get("description"), "uploaded_by": row.get("uploaded_by_name") or row.get("uploaded_by"),
            "created_at": row.get("creation"), "url": f"/api/record-attachments/{row['name']}"}


def list_attachments(record_type, record_id, user):
    _require_record(record_type, record_id, user)
    from lambda_erp import permissions
    rows = get_db().sql(
        f'SELECT name, record_type, record_id, filename, content_type, size, description, uploaded_by, '
        f'uploaded_by_name, creation, sensitivity FROM "{TABLE}" WHERE record_type = ? AND record_id = ? '
        f'ORDER BY creation, name', [record_type, record_id])
    # Files of a class hidden from the caller are not there for them, name included.
    return {"attachments": [_public(row) for row in rows if not _hidden_from(row, user)],
            "default_class": permissions.content_class(_target(record_type)),
            "sensitivity_classes": dict(permissions.SENSITIVITY_CLASSES)}


def add_attachment(record_type, record_id, filename, data, user, description=None, sensitivity=None):
    """Keep a file with a record. Returns its public description.

    `sensitivity`: empty for the record type's class, 'none' for "nothing
    sensitive in here", or a sensitivity class."""
    _require_record(record_type, record_id, user, write=True)
    sensitivity = _check_sensitivity(sensitivity, record_type, user)
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
        f'description, uploaded_by, uploaded_by_name, creation, sensitivity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [name, record_type, record_id, filename, ALLOWED[extension], len(data), hashlib.sha256(data).hexdigest(),
         data, (str(description).strip()[:500] or None) if description else None,
         user.get("user_id") or user.get("name"), user.get("full_name") or user.get("email"), now(), sensitivity])
    db.commit()
    row = db.sql(f'SELECT name, record_type, record_id, filename, content_type, size, description, uploaded_by, '
                 f'uploaded_by_name, creation, sensitivity FROM "{TABLE}" WHERE name = ?', [name])[0]
    return _public(row)


def _load(attachment_id, user, write=False):
    rows = get_db().sql(f'SELECT * FROM "{TABLE}" WHERE name = ?', [attachment_id])
    if not rows:
        raise HTTPException(404, "Attachment not found")
    _require_record(rows[0]["record_type"], rows[0]["record_id"], user, write=write)
    if _hidden_from(rows[0], user):
        raise HTTPException(404, "Attachment not found")
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
                       description: str | None = Form(None), sensitivity: str | None = Form(None),
                       user: dict = Depends(get_current_user)):
    # Check access before reading the body into memory.
    _require_record(record_type, record_id, user, write=True)
    _check_sensitivity(sensitivity, record_type, user)
    data = await file.read(MAX_SIZE + 1)
    return add_attachment(record_type, record_id, file.filename, data, user, description, sensitivity)


class Classify(mentions.Input):
    sensitivity: str | None = Field(default=None, max_length=40)


@router.put("/{attachment_id}")
def classify_route(attachment_id: str, body: Classify, user: dict = Depends(get_current_user)):
    """Change what a file counts as: empty (the record type's class), 'none'
    or a sensitivity class."""
    row = _load(attachment_id, user, write=True)
    value = _check_sensitivity(body.sensitivity, row["record_type"], user, current=row.get("sensitivity") or "default")
    db = get_db()
    db.sql(f'UPDATE "{TABLE}" SET sensitivity = ? WHERE name = ?', [value, row["name"]])
    db.commit()
    return _public({**row, "sensitivity": value})


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
    sensitivity: str | None = Field(default=None, max_length=40)


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
                          data.description, data.sensitivity)


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
                    "id of the uploaded file. It does not change the record and is never printed. Leave sensitivity "
                    "empty: the file then counts as what its record type is about (a file on a purchase order or "
                    "supplier is hidden from users who do not see purchase prices). Set a sensitivity class only "
                    "when the user says what the file contains.")
