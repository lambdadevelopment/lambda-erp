"""Chat attachment upload/download. Files stored on the local filesystem."""

import base64
import io
import logging
import os
import tempfile
import time
import uuid
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openai import OpenAI

from lambda_erp.database import get_db
from lambda_erp.utils import now
from api.auth import require_role, get_current_user
from api.demo_limits import demo_max_attachment_bytes, is_demo_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat-attachments"])

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_ATTACHMENTS_PER_SESSION = 100  # sanity cap

# Binary Office / OpenDocument formats. The chat model reads these directly via
# the OpenAI `file` content part (the same mechanism PDFs use) — the endpoint
# accepts the raw bytes and reasons about them, so there is NO server-side
# conversion/parsing here. Keep this set authoritative: the upload gate, the
# extension map, and build_multimodal_content all read from it.
OFFICE_MIME_TYPES = {
    # Spreadsheets
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",          # xlsx
    "application/vnd.ms-excel",                                                   # xls
    "application/vnd.oasis.opendocument.spreadsheet",                             # ods
    # Word-processing documents
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",    # docx
    "application/msword",                                                         # doc
    "application/vnd.oasis.opendocument.text",                                    # odt
    # Presentations
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
    "application/vnd.ms-powerpoint",                                              # ppt
    "application/vnd.oasis.opendocument.presentation",                           # odp
}

# Plain-text formats — inlined into the prompt as text (no file part needed).
TEXT_MIME_TYPES = {"text/csv", "text/plain"}

ALLOWED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
} | OFFICE_MIME_TYPES | TEXT_MIME_TYPES

# Extension -> canonical mime, so a correctly-named file still uploads when the
# browser/OS mislabels its type (Office files are frequently sent as
# application/octet-stream, and CSV as application/vnd.ms-excel).
_EXT_TO_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "odt": "application/vnd.oasis.opendocument.text",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
    "pdf": "application/pdf",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}
_MIME_TO_EXT = {m: e for e, m in _EXT_TO_MIME.items()}


# --- OpenAI Files API (Office documents) -----------------------------------
#
# The chat model can't read Office bytes from an inline base64 `file_data` (that
# path is PDF-only), but it CAN read them when uploaded to the Files API and
# referenced by file_id. We upload each Office attachment once, cache the id on
# its row, and reuse it across turns. Every upload carries a 30-day
# `expires_after` so OpenAI reclaims the file on its own (users rarely delete
# chats and lambda-erp has no scheduler) — if a stale id is ever referenced we
# simply re-upload, since the bytes stay in our own storage. The per-use cost is
# the file's tokens on the completion, already billed via providers.py; there's
# no separate file fee to book.
_OPENAI_FILE_TTL_SECONDS = 30 * 24 * 3600      # 30 days
_OPENAI_FILE_REUSE_MARGIN = 300               # re-upload if within 5 min of expiry
SPREADSHEET_ROW_WARN_LIMIT = 1000             # OpenAI augments ~1000 rows/sheet

_openai_singleton: Optional[OpenAI] = None
_expires_after_supported = True               # flips off if the API rejects it once

# The single Office spreadsheet mime we can cheaply row-count (openpyxl).
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _openai_client() -> OpenAI:
    """Lazily build a module-level OpenAI client for Files uploads (a separate
    call from the chat completion; same OPENAI_API_KEY)."""
    global _openai_singleton
    if _openai_singleton is None:
        _openai_singleton = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    return _openai_singleton


def _create_openai_file(att: dict):
    """Upload the attachment's bytes to the Files API. Tries once with a 30-day
    `expires_after`; if the SDK/API rejects it (older SDK, or the purpose doesn't
    honor it) we disable it process-wide and fall back to a plain upload so
    attachments keep working (accumulation is then unbounded until a sweep)."""
    global _expires_after_supported
    client = _openai_client()
    file_tuple = (att["filename"], att["data"], att["mime_type"])
    if _expires_after_supported:
        try:
            return client.files.create(
                file=file_tuple,
                purpose="user_data",
                expires_after={"anchor": "created_at", "seconds": _OPENAI_FILE_TTL_SECONDS},
            )
        except Exception as e:  # noqa: BLE001 — unsupported param/purpose -> fall back
            _expires_after_supported = False
            logger.warning("Files API expires_after unsupported (%s); using plain uploads", e)
    return client.files.create(file=file_tuple, purpose="user_data")


def ensure_openai_file(att: dict) -> Optional[str]:
    """Return a currently-valid OpenAI file_id for an Office attachment, uploading
    it (once) when there's no cached id or the cached one is at/near expiry.
    Mutates `att` and persists the id + expiry on the Chat Attachment row.
    Returns None on failure (caller degrades to a text note). Blocking — both
    call sites run it in a worker thread, off the event loop."""
    fid = att.get("openai_file_id")
    exp = att.get("openai_file_expires_at")
    if fid and (not exp or time.time() < int(exp) - _OPENAI_FILE_REUSE_MARGIN):
        return fid
    try:
        uploaded = _create_openai_file(att)
    except Exception as e:  # noqa: BLE001 — surface as "couldn't load", not a 500
        logger.warning("OpenAI file upload failed for %s: %s", att.get("id"), e)
        return None

    new_id = uploaded.id
    new_exp = getattr(uploaded, "expires_at", None)
    att["openai_file_id"] = new_id
    att["openai_file_expires_at"] = new_exp
    try:
        db = get_db()
        db.sql(
            'UPDATE "Chat Attachment" SET openai_file_id = ?, openai_file_expires_at = ? WHERE id = ?',
            [new_id, new_exp, att.get("id")],
        )
        db.conn.commit()
    except Exception as e:  # persistence is best-effort — the id still works this turn
        logger.warning("Could not persist openai_file_id for %s: %s", att.get("id"), e)
    return new_id


def spreadsheet_row_warning(data: bytes, mime: str, filename: str) -> Optional[str]:
    """If an .xlsx has a sheet over the model's ~1000-row augmentation limit,
    return a short user-facing warning (else None). Only .xlsx is inspected —
    openpyxl read-only, dimensions only (NOT a content extraction); other
    spreadsheet formats upload without a row check."""
    if mime != _XLSX_MIME:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        worst, worst_sheet = 0, None
        for ws in wb.worksheets:
            rows = ws.max_row or 0
            if rows > worst:
                worst, worst_sheet = rows, ws.title
        wb.close()
    except Exception as e:  # a warning is best-effort — never block the upload
        logger.info("Row-count check skipped for %s: %s", filename, e)
        return None
    if worst > SPREADSHEET_ROW_WARN_LIMIT:
        return (f"Sheet “{worst_sheet}” has {worst:,} rows — the assistant reads about "
                f"{SPREADSHEET_ROW_WARN_LIMIT:,} rows per sheet, so results may be partial. "
                "Split the file or ask about a specific range.")
    return None

# Where uploaded files are stored. Must be a WRITABLE path — a package-relative
# location (e.g. next to this module) resolves under site-packages when the
# package is pip-installed in a container, which is read-only for the non-root
# app user, so every upload 500s with "[Errno 13] Permission denied". Default to
# the OS temp dir (always writable; chat attachments are session-scoped and
# get_attachments_by_ids tolerates a missing file). Deployments that need the
# files to survive a restart set LAMBDA_ERP_UPLOAD_DIR to a mounted volume.
UPLOAD_ROOT = (
    os.environ.get("LAMBDA_ERP_UPLOAD_DIR")
    or os.path.join(tempfile.gettempdir(), "lambda-erp-uploads")
)


def _ensure_upload_dir(user_id: str) -> str:
    """Create and return the upload directory for a user."""
    path = os.path.join(UPLOAD_ROOT, user_id or "anonymous")
    os.makedirs(path, exist_ok=True)
    return path


def _format_bytes(n: int) -> str:
    """Human-friendly byte count for user-facing error messages."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _safe_ext(filename: str, mime: str) -> str:
    """Return a safe file extension based on filename or mime."""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[1].lower()
        if len(ext) <= 5 and ext.isalnum():
            return ext
    return _MIME_TO_EXT.get(mime, "bin")


@router.post("/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    user: dict = Depends(require_role("viewer")),
):
    """Upload a chat attachment. Returns metadata the client uses to attach it to a message."""
    mime = (file.content_type or "application/octet-stream").lower()
    if mime not in ALLOWED_MIME_TYPES:
        # Browsers/OSes often mislabel Office files (e.g. octet-stream) — fall
        # back to the filename extension so a correctly-named file still uploads.
        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
        if ext in _EXT_TO_MIME:
            mime = _EXT_TO_MIME[ext]
        else:
            raise HTTPException(
                status_code=400,
                detail=(f"Unsupported file type: {mime or ext or 'unknown'}. Allowed: images, PDF, "
                        "spreadsheets (Excel/CSV/ODS), and documents (Word/OpenDocument)."),
            )

    data = await file.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large. Maximum {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    # Tighter cap for public demo visitors: base64-encoded attachments get
    # streamed to the LLM as prompt tokens, so a 10 MB image alone would
    # blow the hourly budget in one call. Reject with a message the
    # frontend surfaces as-is so the visitor can shrink and retry.
    if is_demo_role(user.get("role")):
        demo_cap = demo_max_attachment_bytes()
        if len(data) > demo_cap:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Demo attachments are limited to {_format_bytes(demo_cap)} "
                    f"(your file is {_format_bytes(len(data))}). "
                    "Please upload a smaller image or PDF."
                ),
            )

    db = get_db()
    # Sanity-cap the number of attachments per session
    cnt = db.sql('SELECT COUNT(*) as c FROM "Chat Attachment" WHERE session_id = ?', [session_id])
    if cnt and cnt[0]["c"] >= MAX_ATTACHMENTS_PER_SESSION:
        raise HTTPException(status_code=409, detail="Too many attachments in this chat.")

    att_id = uuid.uuid4().hex
    ext = _safe_ext(file.filename or "", mime)
    user_id = user["name"]
    upload_dir = _ensure_upload_dir(user_id)
    file_path = os.path.join(upload_dir, f"{att_id}.{ext}")

    with open(file_path, "wb") as f:
        f.write(data)

    db.sql(
        'INSERT INTO "Chat Attachment" (id, session_id, user_id, filename, mime_type, size_bytes, file_path, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [att_id, session_id, user_id, file.filename or f"file.{ext}", mime, len(data), file_path, now()],
    )
    db.conn.commit()

    resp = {
        "id": att_id,
        "filename": file.filename or f"file.{ext}",
        "mime_type": mime,
        "size_bytes": len(data),
        "created_at": now(),
    }
    # Advisory (non-blocking): warn if a spreadsheet exceeds the model's
    # per-sheet augmentation limit, so the user knows results may be partial.
    warning = spreadsheet_row_warning(data, mime, file.filename or "")
    if warning:
        resp["warning"] = warning
    return resp


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: str,
    user: dict = Depends(get_current_user),
):
    """Download a chat attachment. Scoped to the owning user."""
    db = get_db()
    rows = db.sql(
        'SELECT filename, mime_type, file_path, user_id FROM "Chat Attachment" WHERE id = ?',
        [attachment_id],
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Attachment not found")
    row = rows[0]

    # Owner or admin (public_manager also allowed if it owns the attachment)
    if row["user_id"] != user["name"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized")

    if not os.path.isfile(row["file_path"]):
        raise HTTPException(status_code=404, detail="File missing on disk")

    with open(row["file_path"], "rb") as f:
        data = f.read()

    return Response(
        content=data,
        media_type=row["mime_type"],
        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'},
    )


# ---------------------------------------------------------------------------
# Helpers used by the chat thinking loop
# ---------------------------------------------------------------------------


def get_attachments_by_ids(attachment_ids: list[str], user_id: str) -> list[dict]:
    """Fetch attachment metadata + binary data for a list of IDs, scoped to user."""
    if not attachment_ids:
        return []
    db = get_db()
    placeholders = ",".join(["?"] * len(attachment_ids))
    rows = db.sql(
        f'SELECT id, filename, mime_type, file_path, size_bytes, '
        f'openai_file_id, openai_file_expires_at FROM "Chat Attachment" '
        f'WHERE id IN ({placeholders}) AND user_id = ?',
        list(attachment_ids) + [user_id],
    )
    result = []
    for row in rows:
        try:
            with open(row["file_path"], "rb") as f:
                data = f.read()
            result.append({
                "id": row["id"],
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "size_bytes": row["size_bytes"],
                "openai_file_id": row.get("openai_file_id"),
                "openai_file_expires_at": row.get("openai_file_expires_at"),
                "data": data,
            })
        except FileNotFoundError:
            continue
    return result


def build_multimodal_content(attachment: dict) -> dict:
    """Convert an attachment dict (with raw data) into an OpenAI multimodal content part.

    Images go as `image_url`; plain-text files (CSV/TXT) inline as text; PDFs go
    as an inline base64 `file` part; binary Office/OpenDocument files are uploaded
    to the Files API (once, cached) and referenced by `file_id` — OpenAI only
    accepts PDF via inline base64, but reads Office bytes fine by file_id.

    May upload to the Files API (blocking) for an Office file, so both call sites
    run it in a worker thread, off the event loop.
    """
    mime = attachment["mime_type"]
    filename = attachment["filename"]
    if mime.startswith("image/"):
        data_b64 = base64.b64encode(attachment["data"]).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data_b64}"},
        }
    if mime in TEXT_MIME_TYPES or mime.startswith("text/"):
        text = attachment["data"].decode("utf-8", errors="replace")
        return {"type": "text", "text": f"[File: {filename}]\n{text}"}
    if mime == "application/pdf":
        data_b64 = base64.b64encode(attachment["data"]).decode("ascii")
        return {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:{mime};base64,{data_b64}",
            },
        }
    if mime in OFFICE_MIME_TYPES:
        # Office bytes are read via the Files API (file_id) — but ONLY the
        # Responses API accepts non-PDF file inputs. On the default Chat
        # Completions backend, degrade gracefully instead of triggering a hard
        # 400 (flip ERP_CHAT_API=responses to actually read Office files).
        if os.environ.get("ERP_CHAT_API", "chat").strip().lower() != "responses":
            return {"type": "text",
                    "text": (f"[Attachment “{filename}” is an Office document; the current chat "
                             "backend can only read PDFs and images. Convert it to PDF, or ask an "
                             "admin to enable the Responses backend.]")}
        file_id = ensure_openai_file(attachment)
        if file_id:
            return {"type": "file", "file": {"file_id": file_id}}
        return {"type": "text",
                "text": f"[Attachment “{filename}” couldn’t be loaded for analysis — please re-upload it.]"}
    return {"type": "text", "text": f"[Unsupported attachment: {filename}]"}


def list_session_attachments(session_id: str, user_id: str) -> list[dict]:
    """Return metadata (no data) for all attachments in a session."""
    db = get_db()
    rows = db.sql(
        'SELECT id, filename, mime_type, size_bytes, created_at FROM "Chat Attachment" '
        'WHERE session_id = ? AND user_id = ? ORDER BY created_at DESC',
        [session_id, user_id],
    )
    return [dict(r) for r in rows]


def delete_session_attachments(session_id: str) -> None:
    """Delete all attachments (DB + files) for a session. Used on chat clear/delete."""
    db = get_db()
    rows = db.sql('SELECT file_path FROM "Chat Attachment" WHERE session_id = ?', [session_id])
    for r in rows:
        try:
            os.remove(r["file_path"])
        except OSError:
            pass
    db.sql('DELETE FROM "Chat Attachment" WHERE session_id = ?', [session_id])
    db.conn.commit()
