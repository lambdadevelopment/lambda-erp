"""Chat attachment upload/download. Files stored on the local filesystem."""

import base64
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from lambda_erp.database import get_db
from lambda_erp.utils import now
from api.auth import require_role, get_current_user
from api.demo_limits import demo_max_attachment_bytes, is_demo_role

router = APIRouter(prefix="/chat", tags=["chat-attachments"])

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_ATTACHMENTS_PER_SESSION = 100  # sanity cap
ALLOWED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
}

UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")


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
    mime_ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "application/pdf": "pdf",
    }
    return mime_ext.get(mime, "bin")


@router.post("/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    user: dict = Depends(require_role("viewer")),
):
    """Upload a chat attachment. Returns metadata the client uses to attach it to a message."""
    mime = (file.content_type or "application/octet-stream").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime}. Allowed: images, PDF.")

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

    return {
        "id": att_id,
        "filename": file.filename or f"file.{ext}",
        "mime_type": mime,
        "size_bytes": len(data),
        "created_at": now(),
    }


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
        f'SELECT id, filename, mime_type, file_path, size_bytes FROM "Chat Attachment" '
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
                "data": data,
            })
        except FileNotFoundError:
            continue
    return result


def build_multimodal_content(attachment: dict) -> dict:
    """Convert an attachment dict (with raw data) into an OpenAI multimodal content part."""
    mime = attachment["mime_type"]
    data_b64 = base64.b64encode(attachment["data"]).decode("ascii")
    if mime.startswith("image/"):
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data_b64}"},
        }
    if mime == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": attachment["filename"],
                "file_data": f"data:{mime};base64,{data_b64}",
            },
        }
    return {"type": "text", "text": f"[Unsupported attachment: {attachment['filename']}]"}


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
