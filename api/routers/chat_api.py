"""Programmatic chat API (v1).

A small, synchronous REST surface over the ERP's chat agent, authenticated by
Bearer API keys and gated by the `chat_api_enabled` Settings flag (off by
default). Lets an external orchestrator (Lambda's own lambda-web infra, then the
iPhone app) hold a conversation with an ERP instance the way a connector script
talks to Dynamics NAV.

Statefulness (see docs/chat-api-plan.md):
  - no `session_id`  -> stateless reasoning: the agent answers using only the
    current message; the turn is still persisted to a rolling audit session for
    visibility, but prior turns are NOT replayed. This suits a caller (the
    orchestrator) that owns the real conversation and sends self-contained
    prompts.
  - with `session_id` -> stateful: that session's history is replayed —
    caller-owned, ephemeral working memory for a single multi-step ERP task.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from api.auth import get_api_caller
from api.chat import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
    run_session_turn,
    save_chat_message,
)
from api.pdf import generate_pdf
from api.services import load_document

router = APIRouter(prefix="/v1", tags=["chat-api"])


class ChatApiRequest(BaseModel):
    message: str
    session_id: str | None = None


async def _noop_event(event: dict) -> None:
    """Discard streamed loop events — the REST response is the final reply only."""
    return None


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/chat")
async def chat(payload: ChatApiRequest, request: Request, caller: dict = Depends(get_api_caller)):
    """Send one message to the ERP chat agent and return its final reply.

    Blocks until the agent finishes (it may run several tool calls internally).
    """
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message cannot be empty")

    owner = caller["name"]

    if payload.session_id:
        session = get_session(payload.session_id)
        if not session or session.get("user_id") != owner:
            raise HTTPException(status_code=404, detail="Session not found")
        target_session_id = payload.session_id
        replay_history = True  # opt-in continuity
    else:
        # Rolling audit session: append to the caller's most-recent session, or
        # open one. Stateless reasoning — persisted for visibility, not replayed.
        existing = list_sessions(user_id=owner)
        target_session_id = existing[0]["id"] if existing else create_session(user_id=owner)["id"]
        replay_history = False

    save_chat_message(target_session_id, "user", message)

    reply = await run_session_turn(
        target_session_id,
        message,
        caller,
        _noop_event,
        client_ip=_client_ip(request),
        replay_history=replay_history,
    )

    session = get_session(target_session_id)
    return {
        "reply": reply or "",
        "session_id": target_session_id,
        "title": session["title"] if session else None,
    }


@router.get("/documents/{doctype_slug}/{name}/pdf")
def document_pdf(doctype_slug: str, name: str, caller: dict = Depends(get_api_caller)):
    """Render a document's PDF for a Bearer-key caller.

    The chat agent's replies link to `/api/documents/{slug}/{name}/pdf`, but that
    route is cookie-gated (require_role) and so unreachable by an API caller. This
    mirrors it for the programmatic surface so an orchestrator (lambda-web → the
    iPhone app) can fetch the actual bytes. A missing document raises
    ValidationError("… not found") → 404, an unknown doctype ValueError → 422, via
    the global handlers.
    """
    pdf_bytes = generate_pdf(doctype_slug, name)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}.pdf"'},
    )


@router.get("/documents/{doctype_slug}/{name}")
def document_json(doctype_slug: str, name: str, caller: dict = Depends(get_api_caller)):
    """Return a document's structured data for a Bearer-key caller (read-only)."""
    return load_document(doctype_slug, name)


@router.get("/chat/sessions")
def sessions(caller: dict = Depends(get_api_caller)):
    """List the caller's chat sessions (id, title, timestamps)."""
    return list_sessions(user_id=caller["name"])


@router.delete("/chat/sessions/{session_id}")
def delete(session_id: str, caller: dict = Depends(get_api_caller)):
    """Delete one of the caller's sessions. The next stateless call opens a fresh one."""
    session = get_session(session_id)
    if not session or session.get("user_id") != caller["name"]:
        raise HTTPException(status_code=404, detail="Session not found")
    delete_session(session_id)
    return {"id": session_id, "deleted": True}
