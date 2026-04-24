"""
WebSocket chat interface with agentic reasoning loop.

The LLM receives tool definitions that map to existing ERP service functions.
It reasons about what to do, calls tools, gets results, and iterates until
it has a final answer — all streamed back to the browser via WebSocket.

Supports multiple chat sessions, each with their own history and auto-generated title.
"""

import asyncio
import json
import logging
import os
import traceback
import uuid
from datetime import date

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Depends as _Depends, HTTPException, WebSocket, WebSocketDisconnect
from openai import OpenAI

from api import services
from api.demo_limits import (
    demo_call_reserve_usd,
    demo_max_completion_tokens,
    demo_max_message_chars,
    is_demo_role,
    limiter as demo_limiter,
)
from api.providers import cost_of_anthropic_call, cost_of_openai_call
from api.routers.masters import create_master_record, update_master_record
from lambda_erp.database import get_db
from lambda_erp.utils import flt, now, nowdate

load_dotenv()

from api.auth import require_role, get_current_user

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[_Depends(require_role("viewer"))])
logger = logging.getLogger("chat")
DEMO_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "live-demo-script.json")
DEMO_TYPE_MS_PER_CHAR = 14
DEMO_TYPE_INITIAL_MS = 90
DEMO_AFTER_TYPED_USER_MS = 180

SESSION_SELECT = """
    SELECT
        cs.id,
        cs.title,
        cs.user_id,
        cs.created_at,
        cs.updated_at,
        (
            SELECT MAX(cm.created_at)
            FROM "Chat Message" cm
            WHERE cm.session_id = cs.id
              AND cm.role IN ('user', 'assistant')
        ) AS last_message_at
    FROM "Chat Session" cs
"""

# ---------------------------------------------------------------------------
# Chat session & message persistence
# ---------------------------------------------------------------------------


def create_session(user_id: str | None = None) -> dict:
    """Create a new chat session."""
    db = get_db()
    session_id = str(uuid.uuid4())[:8]
    # Use now() (server-local ISO) to match the message `created_at` format;
    # SQLite's CURRENT_TIMESTAMP default is UTC with a space separator, which
    # would cause the sidebar to sort a brand-new session below older ones
    # whose last_message_at came from now().
    created_at = now()
    db.sql(
        'INSERT INTO "Chat Session" (id, title, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        [session_id, "New Chat", user_id, created_at, created_at],
    )
    db.conn.commit()
    return get_session(session_id) or {"id": session_id, "title": "New Chat"}


def list_sessions(user_id: str | None = None, role: str | None = None) -> list[dict]:
    """List chat sessions for a user."""
    db = get_db()
    clauses: list[str] = []
    params: list = []
    if user_id:
        clauses.append("cs.user_id = ?")
        params.append(user_id)
    if role == "public_manager":
        # Hide demo-only and empty sessions — they pile up from drive-by visitors.
        # Only surface sessions with at least one real (non-demo) message.
        clauses.append(
            'EXISTS (SELECT 1 FROM "Chat Message" cm WHERE cm.session_id = cs.id AND cm.message_type != ?)'
        )
        params.append("demo")
    where = f" WHERE {' AND '.join(f'({c})' for c in clauses)}" if clauses else ""
    rows = db.sql(f"{SESSION_SELECT}{where} ORDER BY created_at DESC", params)
    return [dict(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    db = get_db()
    rows = db.sql(f"{SESSION_SELECT} WHERE cs.id = ?", [session_id])
    return dict(rows[0]) if rows else None


def can_access_session(session: dict | None, user: dict | None) -> bool:
    if not session or not user:
        return False
    if user.get("role") == "admin":
        return True
    return session.get("user_id") == user.get("name")


def delete_session(session_id: str):
    """Delete a chat session, its messages, and its attachments."""
    try:
        from api.attachments import delete_session_attachments
        delete_session_attachments(session_id)
    except Exception:
        pass
    db = get_db()
    db.sql('DELETE FROM "Chat Message" WHERE session_id = ?', [session_id])
    db.sql('DELETE FROM "Chat Session" WHERE id = ?', [session_id])
    db.conn.commit()


def update_session_title(session_id: str, title: str):
    db = get_db()
    db.sql('UPDATE "Chat Session" SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', [title, session_id])
    db.conn.commit()


def touch_session(session_id: str):
    db = get_db()
    db.sql('UPDATE "Chat Session" SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', [session_id])
    db.conn.commit()


def save_chat_message(session_id: str, role: str, content: str, message_type: str = "chat", metadata: dict = None):
    """Save a chat message to the database."""
    db = get_db()
    metadata_json = json.dumps(metadata, default=str) if metadata else None
    db.sql(
        'INSERT INTO "Chat Message" (session_id, role, message_type, content, metadata_json) VALUES (?, ?, ?, ?, ?)',
        [session_id, role, message_type, content, metadata_json],
    )
    db.conn.commit()
    touch_session(session_id)


def load_chat_history(session_id: str, limit: int = 50, before_id: int | None = None) -> list[dict]:
    """Load recent chat messages for a session.

    When `before_id` is provided, load messages strictly older than that id
    (used for "Load older messages" pagination).
    """
    db = get_db()
    if before_id:
        rows = db.sql(
            'SELECT id, role, message_type, content, metadata_json, created_at '
            'FROM "Chat Message" WHERE session_id = ? AND id < ? ORDER BY id DESC LIMIT ?',
            [session_id, int(before_id), limit],
        )
    else:
        rows = db.sql(
            'SELECT id, role, message_type, content, metadata_json, created_at '
            'FROM "Chat Message" WHERE session_id = ? ORDER BY id DESC LIMIT ?',
            [session_id, limit],
        )
    rows.reverse()
    return [dict(r) for r in rows]


def build_conversation(session_id: str, limit: int = 20) -> list[dict]:
    """Build the user/assistant conversation context for a session.

    Demo-replay messages are included: the docs they narrate actually exist
    (bootstrap's ensure_demo_chat_records creates them), so the history is an
    accurate summary the LLM can build on when the user's first real message
    refers back to "that quotation" or similar.
    """
    conversation = []
    for row in load_chat_history(session_id, limit=max(limit * 2, 50)):
        role = row["role"]
        content = row.get("content", "")
        if role in ("user", "assistant") and content:
            conversation.append({"role": role, "content": content})
    return conversation[-limit:]


def serialize_chat_message(
    role: str,
    content: str,
    created_at: str | None = None,
    attachments: list | None = None,
    message_id: int | None = None,
) -> dict:
    msg = {
        "role": role,
        "content": content,
        "created_at": created_at or now(),
    }
    if attachments:
        msg["attachments"] = attachments
    if message_id is not None:
        msg["id"] = message_id
    return msg


def load_serialized_chat_history(
    session_id: str,
    limit: int = 20,
    before_id: int | None = None,
) -> dict:
    """Return a page of chat history plus pagination metadata.

    Returns {messages, has_more, oldest_id}. `has_more` is True when older
    messages exist beyond this page.
    """
    rows = load_chat_history(session_id, limit=limit, before_id=before_id)
    history_messages = []
    for row in rows:
        role = row["role"]
        content = row.get("content", "") or ""
        if role not in ("user", "assistant"):
            continue
        attachments = None
        meta_json = row.get("metadata_json")
        if meta_json:
            try:
                meta = json.loads(meta_json)
                attachments = meta.get("attachments")
            except (json.JSONDecodeError, AttributeError):
                pass
        if content or attachments:
            history_messages.append(
                serialize_chat_message(
                    role, content, row.get("created_at"), attachments, row.get("id"),
                )
            )

    oldest_id = history_messages[0].get("id") if history_messages else None
    has_more = False
    if oldest_id is not None:
        db = get_db()
        older = db.sql(
            'SELECT 1 FROM "Chat Message" WHERE session_id = ? AND id < ? LIMIT 1',
            [session_id, int(oldest_id)],
        )
        has_more = bool(older)

    return {
        "messages": history_messages,
        "has_more": has_more,
        "oldest_id": oldest_id,
    }


def clear_chat_history(session_id: str):
    """Delete all messages (and attachments) in a session."""
    db = get_db()
    db.sql('DELETE FROM "Chat Message" WHERE session_id = ?', [session_id])
    db.conn.commit()
    try:
        from api.attachments import delete_session_attachments
        delete_session_attachments(session_id)
    except Exception:
        pass


def count_assistant_messages(session_id: str) -> int:
    """Count assistant messages in a session (used to decide when to generate title)."""
    db = get_db()
    rows = db.sql(
        'SELECT COUNT(*) as cnt FROM "Chat Message" WHERE session_id = ? AND role = "assistant" AND message_type = "chat"',
        [session_id],
    )
    return rows[0]["cnt"] if rows else 0


def load_demo_script() -> list[dict]:
    """Load the scripted-chat replay and substitute runtime placeholders.

    The JSON template references docs/values that only exist after the demo
    bootstrap has run (quotation name, PO name, top-customer analytics…).
    Those are written to the Settings table by api/bootstrap.py, so we pull
    them here and do a simple {{KEY}} replacement on the string contents.
    """
    with open(DEMO_SCRIPT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = get_db()
    settings_rows = db.sql(
        'SELECT key, value FROM "Settings" WHERE key LIKE "demo_chat_%"'
    )
    key_map = {
        "demo_chat_company": "COMPANY",
        "demo_chat_quotation": "DEMO_QUOTATION",
        "demo_chat_purchase_order": "DEMO_PO",
        # Top 3 customer ranking
        "demo_chat_top1_id": "DEMO_TOP1_ID",
        "demo_chat_top1_name": "DEMO_TOP1_NAME",
        "demo_chat_top1_revenue": "DEMO_TOP1_REVENUE",
        "demo_chat_top1_invoices": "DEMO_TOP1_INVOICES",
        "demo_chat_top2_id": "DEMO_TOP2_ID",
        "demo_chat_top2_name": "DEMO_TOP2_NAME",
        "demo_chat_top2_revenue": "DEMO_TOP2_REVENUE",
        "demo_chat_top3_id": "DEMO_TOP3_ID",
        "demo_chat_top3_name": "DEMO_TOP3_NAME",
        "demo_chat_top3_revenue": "DEMO_TOP3_REVENUE",
        # Per-customer last invoice snapshots
        "demo_chat_top1_last_inv": "DEMO_TOP1_LAST_INV",
        "demo_chat_top1_last_inv_date": "DEMO_TOP1_LAST_INV_DATE",
        "demo_chat_top1_last_inv_items": "DEMO_TOP1_LAST_INV_ITEMS",
        "demo_chat_top2_last_inv": "DEMO_TOP2_LAST_INV",
        "demo_chat_top2_last_inv_date": "DEMO_TOP2_LAST_INV_DATE",
        "demo_chat_top2_last_inv_items": "DEMO_TOP2_LAST_INV_ITEMS",
        "demo_chat_top3_last_inv": "DEMO_TOP3_LAST_INV",
        "demo_chat_top3_last_inv_date": "DEMO_TOP3_LAST_INV_DATE",
        "demo_chat_top3_last_inv_items": "DEMO_TOP3_LAST_INV_ITEMS",
        # Analytics draft + follow-up sales invoice
        "demo_chat_top7_report_id": "DEMO_TOP7_REPORT_ID",
        "demo_chat_redstone_sinv": "DEMO_REDSTONE_SINV",
        "demo_chat_redstone_sinv_date": "DEMO_REDSTONE_SINV_DATE",
        "demo_chat_redstone_due_date": "DEMO_REDSTONE_DUE_DATE",
    }
    substitutions = {placeholder: "" for placeholder in key_map.values()}
    for row in settings_rows:
        placeholder = key_map.get(row["key"])
        if placeholder:
            substitutions[placeholder] = row["value"] or ""

    def _sub(text: str) -> str:
        for placeholder, value in substitutions.items():
            text = text.replace("{{" + placeholder + "}}", value)
        return text

    result = []
    for m in data:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        entry = dict(m)
        entry["content"] = _sub(content)
        # Also substitute inside flash.item so placeholders like the custom
        # analytics report id resolve to real identifiers the UI can match.
        flash = entry.get("flash")
        if isinstance(flash, dict):
            entry["flash"] = {
                k: _sub(str(v)) if isinstance(v, str) else v
                for k, v in flash.items()
            }
        result.append(entry)
    return result


def load_demo_history(session_id: str) -> list[dict]:
    db = get_db()
    rows = db.sql(
        'SELECT role, content, created_at FROM "Chat Message" '
        'WHERE session_id = ? AND message_type = "demo" ORDER BY id',
        [session_id],
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# REST endpoints for session management
# ---------------------------------------------------------------------------


@router.get("/sessions")
def api_list_sessions(user: dict = _Depends(get_current_user)):
    return list_sessions(user_id=user["name"], role=user.get("role"))


@router.post("/sessions")
def api_create_session(user: dict = _Depends(get_current_user)):
    return create_session(user_id=user["name"])


@router.get("/sessions/{session_id}")
def api_get_session(session_id: str, user: dict = _Depends(get_current_user)):
    session = get_session(session_id)
    if not can_access_session(session, user):
        return {"detail": "Session not found"}
    return session


@router.delete("/sessions/{session_id}")
def api_delete_session(session_id: str, user: dict = _Depends(get_current_user)):
    session = get_session(session_id)
    if not can_access_session(session, user):
        return {"detail": "Session not found"}
    delete_session(session_id)
    return {"ok": True}


@router.put("/sessions/{session_id}/title")
def api_rename_session(session_id: str, data: dict, user: dict = _Depends(get_current_user)):
    session = get_session(session_id)
    if not can_access_session(session, user):
        return {"detail": "Session not found"}
    title = data.get("title", "").strip()
    if title:
        update_session_title(session_id, title)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

DOCUMENT_SLUGS = [
    "quotation", "sales-order", "sales-invoice",
    "purchase-order", "purchase-invoice",
    "payment-entry", "journal-entry", "stock-entry",
    "delivery-note", "purchase-receipt", "pos-invoice",
    "pricing-rule", "budget", "subscription", "bank-transaction",
]

MASTER_TYPES = ["customer", "supplier", "item", "warehouse", "account", "company", "cost-center"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "List or search documents of a given type. Returns an array of document summaries (header fields only — child tables like 'items' and 'taxes' are NOT included to keep results compact). Use get_document to drill into a single document's line items. Results are ordered by creation DESC (newest first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS, "description": "Document type slug"},
                    "filters": {"type": "object", "description": "Optional filters like {\"status\": \"Draft\", \"customer\": \"CUST-001\"}", "default": {}},
                    "limit": {"type": "integer", "description": "Max results (default 20, max 500)", "default": 20},
                },
                "required": ["doctype"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "Load a specific document by its name/ID. Returns the full document with all fields and child tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS},
                    "name": {"type": "string", "description": "Document name/ID, e.g. 'QTN-0001'"},
                },
                "required": ["doctype", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a new draft document (docstatus=0). The document is saved but NOT submitted. IMPORTANT: you MUST pass the 'data' object with ALL document fields — doctype alone is not enough. Example: {\"doctype\": \"purchase-order\", \"data\": {\"supplier\": \"SUPP-001\", \"company\": \"My Co\", \"transaction_date\": \"2026-04-14\", \"items\": [{\"item_code\": \"ITEM-001\", \"qty\": 10, \"rate\": 100}]}}",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS},
                    "data": {"type": "object", "description": "REQUIRED. All document fields. Must include: supplier/customer, company, date, and items array. Without this the call will fail."},
                },
                "required": ["doctype", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": "Update fields on an existing draft document (docstatus=0). Only drafts can be edited. You MUST include the `data` object with the fields to change. For parent fields: {\"data\": {\"company\": \"X\"}}. For child table fields (like setting warehouse on items): first call get_document to see the current items, then pass the full items array back with your changes: {\"data\": {\"items\": [{\"item_code\": \"ITEM-001\", \"qty\": 10, \"rate\": 100, \"warehouse\": \"WH-001\"}]}}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS},
                    "name": {"type": "string", "description": "Document name/ID to update"},
                    "data": {"type": "object", "description": "Fields to change. MUST be provided. For child tables like items, pass the complete array."},
                },
                "required": ["doctype", "name", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_document",
            "description": "Submit a draft document (changes docstatus from 0 to 1). This posts GL entries, stock entries, etc. Only works on drafts (docstatus=0). Once submitted, the document is locked and can only be cancelled, not edited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS},
                    "name": {"type": "string"},
                },
                "required": ["doctype", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_document",
            "description": "Cancel a submitted document (reverses GL/stock entries). Only works on submitted documents (docstatus=1). To void a draft, submit it first then cancel it. There is no delete — cancel is the only way to void a document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS},
                    "name": {"type": "string"},
                },
                "required": ["doctype", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_document",
            "description": "Convert a submitted document to the next type in the workflow, OR create a return. Forward: Quotation -> Sales Order or Sales Invoice or Delivery Note, Sales Order -> Sales Invoice or Delivery Note, Purchase Order -> Purchase Invoice or Purchase Receipt. Returns: convert to the SAME type (e.g. Sales Invoice -> Sales Invoice creates a Credit Note, Purchase Invoice -> Purchase Invoice creates a Debit Note, Delivery Note -> Delivery Note or Purchase Receipt -> Purchase Receipt for stock returns).",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctype": {"type": "string", "enum": DOCUMENT_SLUGS, "description": "Source document type slug"},
                    "name": {"type": "string", "description": "Source document name"},
                    "target_doctype": {"type": "string", "description": "Target document type DISPLAY NAME, e.g. 'Sales Order', 'Delivery Note'"},
                },
                "required": ["doctype", "name", "target_doctype"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_masters",
            "description": "Search master data (customers, suppliers, items, warehouses, accounts, companies, cost centers). Returns matching records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "master_type": {"type": "string", "enum": MASTER_TYPES},
                    "query": {"type": "string", "description": "Search term (empty string returns all)", "default": ""},
                },
                "required": ["master_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_master",
            "description": (
                "Create a new master record. You MUST include the full `data` object using the EXACT field names listed below — unknown fields are silently dropped.\n\n"
                "**Customer fields:** customer_name (required), customer_group, territory, default_currency, credit_limit, email, phone, address, city, country, tax_id.\n"
                "**Supplier fields:** supplier_name (required), supplier_group, default_currency, email, phone, address, city, country, tax_id.\n"
                "**Item fields:** item_name (required), item_group, stock_uom, standard_rate, is_stock_item, default_warehouse, description.\n"
                "**Warehouse fields:** warehouse_name (required), company, parent_warehouse (omit or null when not needed).\n"
                "**Company fields:** company_name (required), default_currency, email, phone, address, city, country, tax_id.\n\n"
                "Example: {\"master_type\":\"supplier\",\"data\":{\"supplier_name\":\"Schlafteq\",\"email\":\"jacob@schlafteq.ch\",\"phone\":\"+1 555-0104\",\"address\":\"145 Harbor Rd\",\"city\":\"Seattle\",\"country\":\"US\",\"tax_id\":\"98-7654321\"}}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "master_type": {"type": "string", "enum": MASTER_TYPES},
                    "data": {"type": "object", "description": "REQUIRED. Full master data payload using the field names listed in the tool description."},
                },
                "required": ["master_type", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_master",
            "description": (
                "Update an existing master record. You MUST include the `name` of the existing record and a `data` object with the fields to change. Use the same field names listed in create_master (customer: customer_name, email, phone, address, city, country, tax_id, etc.). Example: {\"master_type\":\"customer\",\"name\":\"CUST-001\",\"data\":{\"address\":\"123 New Street\",\"city\":\"Boston\"}}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "master_type": {"type": "string", "enum": MASTER_TYPES},
                    "name": {"type": "string", "description": "Existing master record ID/name to update."},
                    "data": {"type": "object", "description": "REQUIRED. Fields to change on the existing master record."},
                },
                "required": ["master_type", "name", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_report",
            "description": "Run a financial or stock report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {"type": "string", "enum": ["trial-balance", "general-ledger", "stock-balance", "dashboard-summary", "profit-and-loss", "balance-sheet", "ar-aging", "ap-aging"]},
                    "filters": {"type": "object", "description": "Optional filters: company, account, from_date, to_date, item_code, warehouse", "default": {}},
                },
                "required": ["report_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_chat_history",
            "description": "Retrieve earlier messages from the conversation history. Use this when the user references something said earlier, asks a follow-up to a previous topic, or you need context from past messages. The most recent messages are already in context — this tool fetches older ones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "num_messages": {"type": "integer", "description": "Number of recent messages to retrieve (default 20, max 50)."},
                    "date_from": {"type": "string", "description": "Start of date range (ISO 8601)."},
                    "date_to": {"type": "string", "description": "End of date range (ISO 8601)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_chat_attachments",
            "description": "List all files (PDFs, images) the user has uploaded in this chat session. Returns metadata including id, filename, mime type, size, and upload date. Use this when the user references a previously uploaded file or you need to find an attachment to retrieve.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_chat_attachment",
            "description": "Retrieve a previously uploaded file (PDF or image) by its id and inject it back into the conversation so you can read/analyze it. Use this when the user asks a follow-up about a file they uploaded earlier in the chat but it's no longer in the immediate context. Call list_chat_attachments first if you don't know the id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "attachment_id": {"type": "string", "description": "The id of the attachment (from list_chat_attachments)."},
                },
                "required": ["attachment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_dataset",
            "description": (
                "Run a deterministic SQL GROUP BY aggregation over a semantic "
                "dataset and return the aggregated rows. Use this for factual "
                "answers that require aggregating many rows (top customer by "
                "revenue, total sales this month, outstanding AR by customer, "
                "count of invoices per supplier, etc.) — instead of calling "
                "`list_documents` and adding things up in your head.\n\n"
                "Available datasets (same as for custom analytics):\n"
                "- `sales_invoices` — submitted sales invoices (includes returns)\n"
                "- `sales_invoice_lines` — submitted sales invoice line items\n"
                "- `purchase_invoices` — submitted purchase invoices (includes returns)\n"
                "- `purchase_invoice_lines` — submitted purchase invoice line items\n"
                "- `payments` — submitted payment entries\n"
                "- `ar_open_items` — outstanding sales invoice amounts\n"
                "- `ap_open_items` — outstanding purchase invoice amounts\n"
                "- `stock_balances` — current stock on hand by item × warehouse\n\n"
                "Shape your call like: `{dataset, group_by, measures, filters?, "
                "order_by?, limit?}`. `group_by` is a list of field names. "
                "`measures` is an object of `{alias: [op, field]}` where `op` "
                "is one of sum, count, avg, min, max (count may omit field). "
                "`filters` is a dict keyed by field name — same shape as for "
                "`create_custom_analytics_report` (equality, list for IN, "
                "`{from, to}` for ranges). `order_by` is a list of "
                "`{field, direction}` where `field` is a group_by field or a "
                "measure alias and `direction` is asc/desc.\n\n"
                "Example — top 5 customers by revenue:\n"
                "```json\n"
                "{\"dataset\": \"sales_invoices\", "
                "\"group_by\": [\"customer\", \"customer_name\"], "
                "\"measures\": {\"revenue\": [\"sum\", \"net_total\"], "
                "\"invoices\": [\"count\"]}, "
                "\"filters\": {\"is_return\": 0}, "
                "\"order_by\": [{\"field\": \"revenue\", \"direction\": \"desc\"}], "
                "\"limit\": 5}\n"
                "```\n\n"
                "Only reference fields that are exposed by the chosen dataset. "
                "Only filter on the dataset's `filter_fields`. Do not invent "
                "fields. The tool returns `{rows: [{…group fields, …measure "
                "aliases}], row_count, truncated}` — you can cite these numbers "
                "directly in your reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "enum": [
                            "sales_invoices",
                            "sales_invoice_lines",
                            "purchase_invoices",
                            "purchase_invoice_lines",
                            "payments",
                            "ar_open_items",
                            "ap_open_items",
                            "stock_balances",
                        ],
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "measures": {
                        "type": "object",
                        "description": "Map of alias -> [op, field]. op ∈ sum|count|avg|min|max.",
                    },
                    "filters": {"type": "object"},
                    "order_by": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "direction": {"type": "string", "enum": ["asc", "desc"]},
                            },
                            "required": ["field"],
                        },
                    },
                    "limit": {"type": "integer"},
                },
                "required": ["dataset", "measures"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_custom_analytics_report",
            "description": (
                "Create a draft custom analytics report that opens in "
                "/reports/analytics?report_id=... . Use this when the user wants "
                "a bespoke chart/table that goes beyond the preset metric × "
                "group_by analytics.\n\n"
                "**Preferred usage:** provide `intent` as a plain-language "
                "description of what the user wants. The backend will hand "
                "`intent` to the code specialist model (Anthropic) to generate "
                "`data_requests` + `transform_js` for you, then persist the "
                "draft. You do NOT need to write the code yourself — your job "
                "is to understand the user's request and pass a clear `intent`. "
                "Only pass `data_requests` + `transform_js` yourself if you "
                "explicitly want to bypass the code specialist.\n\n"
                "Available semantic datasets include: sales_invoices, "
                "sales_invoice_lines, purchase_invoices, purchase_invoice_lines, "
                "payments, ar_open_items, ap_open_items, stock_balances.\n\n"
                "Each data_request may include: name, dataset, fields, filters, "
                "limit. The JS runs client-side over the fetched datasets only; "
                "it does NOT have SQL, network, or DOM access. The transform "
                "should end with `return { ... }`. These datasets already focus "
                "on the accounting-relevant submitted/open records described in "
                "their names, so avoid redundant filters like `docstatus = 1` "
                "unless you truly need to surface that field in the output. "
                "Only request fields that are explicitly exposed by the chosen "
                "semantic dataset; do not invent fields like `base_grand_total` "
                "or other ERP-style variants unless the dataset metadata showed "
                "that exact field name.\n\n"
                "Choose chart types deliberately. Use `bar` for ranked lists, "
                "category comparisons, month-by-month business totals, and most "
                "discrete bucketed ERP reporting. Use `line` when the main goal "
                "is to show a continuous trend over time across many periods, "
                "especially when the user explicitly asks for a trend line. Use "
                "`pie` only for simple part-of-whole breakdowns with a small "
                "number of categories. If unsure between `bar` and `line`, "
                "prefer `bar`. If the user asks for a graph, chart, visual, "
                "breakdown, or comparison, include at least one chart in "
                "`charts[]` rather than returning only a table. Table-only "
                "output is appropriate only when the user explicitly asked for "
                "just a table or list.\n\n"
                "Use only the supported runtime helper patterns: "
                "`helpers.sum(rows, 'field')` or `helpers.sum(rows, row => ...)`; "
                "`helpers.sortBy(rows, 'field', 'asc'|'desc'|true)`; "
                "`helpers.topN(rows, 'field', n)` or `helpers.topN(rows, n)` if "
                "already sorted; `helpers.group(rows, ['field1', ...], "
                "{ alias: ['sum'|'count', 'field'] })` or "
                "`helpers.group(rows, row => key)` which returns "
                "`[{ key, rows }]`; plus `helpers.monthKey(...)`, "
                "`helpers.quarterKey(...)`, `helpers.yearKey(...)`, "
                "`helpers.leftJoin(...)`, and `helpers.pivot(...)`. Do not use "
                "unsupported shapes like `helpers.sortBy(rows, row => ...)` or "
                "`helpers.group(rows, keyFn, reducerFn)`.\n\n"
                "Return charts and tables in the supported shape. A chart should "
                "use `{ title, type, x, y, dataTable? , data? }` where `type` is "
                "`bar`, `line`, or `pie`. Prefer `dataTable` when the chart is "
                "based on one of your returned tables, otherwise use inline "
                "`data`. The `y` field MUST be a single string field name such "
                "as `revenue` or `net_sales` — never an array, never `['revenue']`, "
                "and never multi-series keys. Do not use unsupported keys like "
                "`x_key` or `y_keys`. "
                "A table should use `{ title, columns, rows }` where each column "
                "uses `{ key, label, type? }` and `type` is things like "
                "`currency`, `number`, `string`, or `date`.\n\n"
                "Canonical example: top customers by revenue. Use "
                "`sales_invoices` with fields like `posting_date`, `customer`, "
                "`grand_total`; map rows into `{ customer, revenue }`; group with "
                "`helpers.group(rows, ['customer'], { revenue: ['sum', 'revenue'] })`; "
                "sort descending by `revenue`; take `helpers.topN(..., 'revenue', 10)`; "
                "and return a bar chart with `x: 'customer'`, `y: 'revenue'`, "
                "and inline `data: top10`.\n\n"
                "Canonical example: best selling items by quantity. Use "
                "`sales_invoice_lines` with fields `posting_date`, `item_code`, `qty`; "
                "clean into `{ item_code, qty }`; group with "
                "`helpers.group(clean, ['item_code'], { qty_sold: ['sum', 'qty'], "
                "line_count: ['count', 'qty'] })`; sort descending by `qty_sold`; "
                "take `helpers.topN(sorted, 10)`; and return a bar chart with "
                "`x: 'item_code'`, `y: 'qty_sold'`, and inline `data: top10`. "
                "Do not request unsupported line fields like `item_name`, `amount`, "
                "or `grand_total` from `sales_invoice_lines`.\n\n"
                "For the first version, prefer simple transforms using helpers "
                "like helpers.group(...), helpers.sortBy(...), helpers.topN(...), "
                "helpers.monthKey(...), helpers.sum(...). Always include the URL "
                "returned by this tool verbatim in your response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "description": (
                            "Plain-language description of what the user wants "
                            "in this report — the code specialist will use this "
                            "to generate data_requests + transform_js. Include "
                            "any specific filters, groupings, sort orders, or "
                            "chart preferences the user mentioned."
                        ),
                    },
                    "data_requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "dataset": {
                                    "type": "string",
                                    "enum": [
                                        "sales_invoices",
                                        "sales_invoice_lines",
                                        "purchase_invoices",
                                        "purchase_invoice_lines",
                                        "payments",
                                        "ar_open_items",
                                        "ap_open_items",
                                        "stock_balances",
                                    ],
                                },
                                "fields": {"type": "array", "items": {"type": "string"}},
                                "filters": {"type": "object"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["dataset"],
                        },
                    },
                    "transform_js": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_custom_analytics_report",
            "description": (
                "Load an existing custom analytics draft by report_id. Use this "
                "when the user says a draft report is broken, wants to refine an "
                "existing report, or references a /reports/analytics?report_id=... link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_id": {"type": "string"},
                },
                "required": ["report_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_custom_analytics_report",
            "description": (
                "Update an existing custom analytics draft in place while "
                "keeping the same report_id and URL. Load the current draft "
                "with `get_custom_analytics_report` first so you understand "
                "what it does.\n\n"
                "**Preferred usage:** provide `report_id` and `feedback` — a "
                "plain-language description of what needs to change (e.g. "
                "\"chart is empty because grand_total is not in "
                "sales_invoice_lines; use amount instead\", or \"add a line "
                "chart showing monthly trend\"). The backend will hand both "
                "the current draft and your feedback to the code specialist "
                "model (Anthropic) to rewrite the spec. You do NOT need to "
                "write code yourself.\n\n"
                "Only pass `data_requests` or `transform_js` directly if you "
                "want to bypass the code specialist for a trivial change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_id": {"type": "string"},
                    "feedback": {
                        "type": "string",
                        "description": (
                            "Description of what should change — the code "
                            "specialist receives this alongside the existing "
                            "draft and rewrites the spec."
                        ),
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "data_requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "dataset": {
                                    "type": "string",
                                    "enum": [
                                        "sales_invoices",
                                        "sales_invoice_lines",
                                        "purchase_invoices",
                                        "purchase_invoice_lines",
                                        "payments",
                                        "ar_open_items",
                                        "ap_open_items",
                                        "stock_balances",
                                    ],
                                },
                                "fields": {"type": "array", "items": {"type": "string"}},
                                "filters": {"type": "object"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["dataset"],
                        },
                    },
                    "transform_js": {"type": "string"},
                },
                "required": ["report_id"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers — dispatch to existing service functions
# ---------------------------------------------------------------------------


def _handle_list_documents(args):
    # List views don't need child tables — strip them so the LLM can see more rows
    # within the tool-result budget. Use get_document to drill into one doc.
    cls_entry = services.DOCUMENT_CLASSES.get(services.SLUG_TO_DOCTYPE.get(args["doctype"], ""))
    child_keys = list(cls_entry.CHILD_TABLES.keys()) if cls_entry and cls_entry.CHILD_TABLES else []

    rows = services.list_documents(
        args["doctype"],
        filters=args.get("filters"),
        limit=args.get("limit", 20),
    )
    for row in rows:
        for key in child_keys:
            row.pop(key, None)
    return rows


def _handle_get_document(args):
    return services.load_document(args["doctype"], args["name"])


def _handle_create_document(args):
    data = args.get("data")
    if not data:
        return {"error": "You must pass a 'data' object with the document fields (supplier/customer, company, items, etc.). You only passed the doctype."}
    return services.create_document(args["doctype"], data)


def _handle_update_document(args):
    return services.update_document(args["doctype"], args["name"], args.get("data", {}))


def _handle_submit_document(args):
    return services.submit_document(args["doctype"], args["name"])


def _handle_cancel_document(args):
    return services.cancel_document(args["doctype"], args["name"])


def _handle_convert_document(args):
    return services.convert_document(args["doctype"], args["name"], args["target_doctype"])


def _handle_search_masters(args):
    db = get_db()
    master_type = args["master_type"]
    query = args.get("query", "")

    entry = services.MASTER_TABLES.get(master_type)
    if not entry:
        return {"error": f"Unknown master type: {master_type}"}

    doctype, name_field = entry
    active_prefix = 'disabled = 0 AND ' if "disabled" in db._get_table_columns(doctype) else ""
    if not query:
        filters = {"disabled": 0} if "disabled" in db._get_table_columns(doctype) else None
        return db.get_all(doctype, filters=filters, fields=["*"], limit=20)

    rows = db.sql(
        f'SELECT * FROM "{doctype}" WHERE {active_prefix}(name LIKE ? OR "{name_field}" LIKE ?) LIMIT 20',
        [f"%{query}%", f"%{query}%"],
    )
    return [dict(r) for r in rows]


def _ignored_master_fields(master_type: str, data: dict) -> list[str]:
    """Return field names in `data` that aren't valid columns on the master's table."""
    entry = services.MASTER_TABLES.get(master_type)
    if not entry:
        return []
    doctype, _ = entry
    from lambda_erp.database import get_db
    valid = get_db()._get_table_columns(doctype)
    return [k for k in data.keys() if k not in valid]


def _handle_create_master(args):
    master_type = args["master_type"]
    data = args.get("data")
    if not data:
        return {"error": "You must pass a 'data' object with the master fields. For a warehouse, include at least 'warehouse_name' and 'company'. 'parent_warehouse' is optional."}

    if master_type == "warehouse" and data.get("parent_warehouse") in ("", "-", "none", "None", None):
        data = {k: v for k, v in data.items() if k != "parent_warehouse"}

    ignored = _ignored_master_fields(master_type, data)

    try:
        result = dict(create_master_record(master_type, data))
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        return {"error": detail or str(exc)}

    if ignored:
        result["_warning"] = (
            f"These fields were IGNORED because they are not valid columns on the {master_type}: "
            f"{ignored}. Retry the call using the correct field names from the create_master tool description."
        )
    return result


def _handle_update_master(args):
    master_type = args["master_type"]
    name = args.get("name")
    data = args.get("data")
    if not name:
        return {"error": "You must pass the existing master record 'name' to update. Example: {\"master_type\":\"supplier\",\"name\":\"SUPP-001\",\"data\":{\"email\":\"new@example.com\"}}"}
    if not data:
        return {"error": f"You must pass a 'data' object with the fields to change. Example: {{\"master_type\":\"{master_type}\",\"name\":\"{name}\",\"data\":{{\"email\":\"foo@bar.com\",\"phone\":\"+1 555-0100\"}}}}. Valid fields for {master_type} are listed in the create_master tool description."}

    if master_type == "warehouse" and data.get("parent_warehouse") in ("", "-", "none", "None", None):
        data = {k: v for k, v in data.items() if k != "parent_warehouse"}

    ignored = _ignored_master_fields(master_type, data)

    try:
        result = dict(update_master_record(master_type, name, data))
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        return {"error": detail or str(exc)}

    if ignored:
        result["_warning"] = (
            f"These fields were IGNORED because they are not valid columns on the {master_type}: "
            f"{ignored}. Retry the call using the correct field names."
        )
    return result


def _handle_get_current_time(args):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return {
        "utc_time": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": now.strftime("%A"),
    }


def _handle_retrieve_chat_history(args, session_id=None):
    db = get_db()
    date_from = args.get("date_from")
    date_to = args.get("date_to")

    session_clause = 'AND session_id = ?' if session_id else ''
    session_params = [session_id] if session_id else []

    if date_from:
        params = session_params + [date_from]
        date_clause = 'AND created_at >= ?'
        if date_to:
            date_clause += ' AND created_at <= ?'
            params.append(date_to)
        rows = db.sql(
            f'SELECT role, content, created_at FROM "Chat Message" '
            f'WHERE role IN ("user", "assistant") {session_clause} {date_clause} '
            f'ORDER BY id ASC LIMIT 50',
            params,
        )
    else:
        num = min(int(args.get("num_messages", 20)), 50)
        rows = db.sql(
            f'SELECT role, content, created_at FROM "Chat Message" '
            f'WHERE role IN ("user", "assistant") {session_clause} '
            f'ORDER BY id DESC LIMIT ?',
            session_params + [num],
        )
        rows.reverse()

    return [
        {"role": m["role"], "content": (m.get("content") or "")[:2000], "created_at": m.get("created_at")}
        for m in rows
    ]


def _handle_get_report(args):
    report_type = args["report_type"]
    filters = args.get("filters", {})
    db = get_db()

    if report_type == "trial-balance":
        from api.routers.reports import _trial_balance
        return _trial_balance(db, filters.get("company"), filters.get("from_date"), filters.get("to_date"))
    elif report_type == "general-ledger":
        from api.routers.reports import _general_ledger
        return _general_ledger(db, filters)
    elif report_type == "stock-balance":
        from api.routers.reports import _stock_balance
        return _stock_balance(db, filters.get("item_code"), filters.get("warehouse"))
    elif report_type == "dashboard-summary":
        from api.routers.reports import _dashboard_summary
        return _dashboard_summary(db, filters.get("company"))
    elif report_type == "profit-and-loss":
        from api.routers.reports import _profit_and_loss
        return _profit_and_loss(db, filters.get("company"), filters.get("from_date"), filters.get("to_date"))
    elif report_type == "balance-sheet":
        from api.routers.reports import _balance_sheet
        return _balance_sheet(db, filters.get("company"), filters.get("as_of_date"))
    elif report_type == "ar-aging":
        from api.routers.reports import _ar_aging
        return _ar_aging(db, filters.get("company"), filters.get("as_of_date"))
    elif report_type == "ap-aging":
        from api.routers.reports import _ap_aging
        return _ap_aging(db, filters.get("company"), filters.get("as_of_date"))
    return {"error": f"Unknown report type: {report_type}"}


def _handle_list_chat_attachments(_args, session_id: str | None = None, user_id: str | None = None):
    if not session_id or not user_id:
        return {"error": "No active chat session for attachments."}
    from api.attachments import list_session_attachments
    items = list_session_attachments(session_id, user_id)
    return {"attachments": items, "count": len(items)}


def _handle_retrieve_chat_attachment(args, session_id: str | None = None, user_id: str | None = None):
    if not session_id or not user_id:
        return {"error": "No active chat session for attachments."}
    attachment_id = args.get("attachment_id")
    if not attachment_id:
        return {"error": "attachment_id is required."}
    from api.attachments import get_attachments_by_ids, build_multimodal_content
    atts = get_attachments_by_ids([attachment_id], user_id)
    if not atts:
        return {"error": "Attachment not found or access denied."}
    att = atts[0]
    return {
        "id": att["id"],
        "filename": att["filename"],
        "mime_type": att["mime_type"],
        "size_bytes": att["size_bytes"],
        "_multimodal_content": build_multimodal_content(att),
    }


def _handle_query_dataset(args):
    from api.routers.analytics import aggregate_semantic_dataset

    dataset = args.get("dataset")
    if not dataset:
        return {"error": "dataset is required"}
    measures = args.get("measures") or {}
    if not measures:
        return {"error": "measures is required — e.g. {\"revenue\": [\"sum\", \"net_total\"]}"}
    try:
        result = aggregate_semantic_dataset(
            dataset=dataset,
            group_by=args.get("group_by") or [],
            measures=measures,
            filters=args.get("filters") or {},
            order_by=args.get("order_by") or [],
            limit=args.get("limit"),
        )
    except HTTPException as e:
        return {"error": str(e.detail)}
    except Exception as e:
        return {"error": f"Aggregation failed: {e}"}
    return result


def _handle_create_custom_analytics_report(args, user_info: dict | None = None, session_id: str | None = None, client_ip: str | None = None):
    from api.routers.analytics import create_report_draft_record

    # GPT may pass intent + (optionally) a sketch. If the code spec is
    # missing, delegate to the Anthropic code specialist.
    if not args.get("transform_js") or not args.get("data_requests"):
        intent = args.get("intent") or args.get("description") or args.get("title")
        if not intent:
            return {"error": "Provide either `intent` or a complete spec (data_requests + transform_js)."}
        try:
            spec = _generate_report_spec_via_anthropic(
                intent,
                client_ip=client_ip,
                user_role=(user_info or {}).get("role"),
            )
        except Exception as e:
            return {"error": f"Code specialist failed: {e}"}
        args["data_requests"] = spec["data_requests"]
        args["transform_js"] = spec["transform_js"]
        if not args.get("title"):
            args["title"] = spec.get("title") or (intent[:60] if intent else "Custom Report")
        if not args.get("description") and spec.get("description"):
            args["description"] = spec["description"]

    if not args.get("title"):
        return {"error": "title is required"}
    return create_report_draft_record(args, user_info, source_chat_session_id=session_id)


def _handle_get_custom_analytics_report(args, user_info: dict | None = None):
    from api.routers.analytics import get_report_draft_record

    report_id = args.get("report_id")
    if not report_id:
        return {"error": "report_id is required"}
    row = get_report_draft_record(report_id, user_info)
    if not row:
        return {"error": f"Report draft '{report_id}' not found"}
    return row


def _handle_update_custom_analytics_report(args, user_info: dict | None = None, client_ip: str | None = None):
    from api.routers.analytics import get_report_draft_record, update_report_draft_record

    report_id = args.get("report_id")
    if not report_id:
        return {"error": "report_id is required"}

    feedback = args.get("feedback") or args.get("intent")
    # If the caller provided a feedback/intent hint and didn't already hand-roll
    # a transform_js or data_requests change, let the code specialist rewrite
    # the spec based on the existing draft.
    if feedback and "transform_js" not in args and "data_requests" not in args:
        existing = get_report_draft_record(report_id, user_info)
        if not existing:
            return {"error": f"Report draft '{report_id}' not found"}
        try:
            spec = _generate_report_spec_via_anthropic(
                intent=existing.get("description") or existing.get("title") or "",
                existing_spec=existing,
                feedback=feedback,
                client_ip=client_ip,
                user_role=(user_info or {}).get("role"),
            )
        except Exception as e:
            return {"error": f"Code specialist failed: {e}"}
        args["data_requests"] = spec["data_requests"]
        args["transform_js"] = spec["transform_js"]
        if spec.get("title") and "title" not in args:
            args["title"] = spec["title"]
        if spec.get("description") and "description" not in args:
            args["description"] = spec["description"]

    payload = {key: value for key, value in args.items() if key not in ("report_id", "feedback", "intent")}
    if not payload:
        return {"error": "At least one field to update (or a `feedback` hint) is required"}
    row = update_report_draft_record(report_id, payload, user_info)
    if not row:
        return {"error": f"Report draft '{report_id}' not found"}
    return row


TOOL_HANDLERS = {
    "list_documents": _handle_list_documents,
    "get_document": _handle_get_document,
    "create_document": _handle_create_document,
    "update_document": _handle_update_document,
    "submit_document": _handle_submit_document,
    "cancel_document": _handle_cancel_document,
    "convert_document": _handle_convert_document,
    "search_masters": _handle_search_masters,
    "create_master": _handle_create_master,
    "update_master": _handle_update_master,
    "get_report": _handle_get_report,
    "get_current_time": _handle_get_current_time,
    "retrieve_chat_history": lambda args: _handle_retrieve_chat_history(args),
    "list_chat_attachments": _handle_list_chat_attachments,
    "retrieve_chat_attachment": _handle_retrieve_chat_attachment,
    "query_dataset": _handle_query_dataset,
    "create_custom_analytics_report": lambda args: _handle_create_custom_analytics_report(args),
    "get_custom_analytics_report": lambda args: _handle_get_custom_analytics_report(args),
    "update_custom_analytics_report": lambda args: _handle_update_custom_analytics_report(args),
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _prompt_company_context() -> str:
    try:
        companies = get_db().get_all("Company", fields=["name"], order_by="name asc", limit=20)
    except Exception:
        return "## Companies In This ERP\n- Company names could not be loaded for this prompt.\n"
    if not companies:
        return "## Companies In This ERP\n- No companies found.\n"
    names = [row.get("name") for row in companies if row.get("name")]
    if not names:
        return "## Companies In This ERP\n- No companies found.\n"
    bullets = "\n".join(f"- {name}" for name in names)
    return (
        "## Companies In This ERP\n"
        "Use these exact company names when you need a company filter or document company value. "
        "Do not invent company names.\n"
        f"{bullets}\n"
    )


def _prompt_analytics_context() -> str:
    db = get_db()
    datasets = [
        ("sales invoices", "Sales Invoice", "posting_date", "docstatus = 1"),
        ("purchase invoices", "Purchase Invoice", "posting_date", "docstatus = 1"),
        ("payments", "Payment Entry", "posting_date", "docstatus = 1"),
    ]
    lines: list[str] = []
    for label, table, date_field, where in datasets:
        try:
            row = db.sql(
                f'''
                SELECT
                    MIN({date_field}) AS min_date,
                    MAX({date_field}) AS max_date,
                    COUNT(*) AS row_count
                FROM "{table}"
                WHERE {where}
                '''
            )[0]
        except Exception:
            continue
        row_count = int(row["row_count"] or 0)
        if row_count <= 0:
            lines.append(f"- {label}: no submitted records")
            continue
        lines.append(
            f"- {label}: {row_count} submitted records from {row['min_date']} to {row['max_date']}"
        )
    if not lines:
        return "## Analytics Data Coverage\n- Coverage could not be loaded for this prompt.\n"
    return (
        "## Analytics Data Coverage\n"
        "Use these date ranges when drafting analytics. Do not invent narrow date filters outside the "
        "known coverage unless the user explicitly asks for them.\n"
        f"{chr(10).join(lines)}\n"
    )


def build_system_prompt(user_info: dict | None = None):
    user_name = user_info.get("full_name", "User") if user_info else "User"
    user_role = user_info.get("role", "viewer") if user_info else "viewer"
    company_context = _prompt_company_context()
    analytics_context = _prompt_analytics_context()

    if user_role == "admin":
        role_desc = "You have **admin** access — full permissions to create, edit, submit, cancel documents, manage master data, run reports, and manage users."
    elif user_role == "manager":
        role_desc = "You have **manager** access — you can create, edit, submit, and cancel documents, create and edit master data, and run reports. You cannot manage users or company setup."
    elif user_role == "public_manager":
        role_desc = "You are in **public demo mode** — you can create, edit, submit, and cancel documents and run reports, but you cannot create, edit, or delete master data, and you cannot manage users or company setup."
    else:
        role_desc = "You have **viewer** access — you can view documents, master data, and reports, but you cannot create or modify data. If the user asks you to create or change something, let them know they need a manager or admin to do that."

    return f"""You are an ERP assistant for Lambda ERP. Today's date is {date.today().isoformat()}.

You help users manage their business by creating documents, looking up data, and running reports — all through natural conversation.

## Answering data questions — three paths

**Path 1: single-record lookup → `list_documents` / `get_document`.** For "is SINV-0042 paid", "what did customer X order last", "show me the latest 5 purchase orders" — fetch the rows directly. Don't try to aggregate in your head unless there are only a handful of rows in front of you.

**Path 2: aggregated facts → `query_dataset`.** For "who is our top customer by revenue", "total sales this month", "outstanding AR by customer", "average invoice size", "count of POs per supplier" — anything that requires summing, counting, ranking, or grouping across many rows — call `query_dataset`. It runs a deterministic SQL aggregation server-side and returns the actual aggregated numbers you can cite in chat. NEVER try to compute a top-N or sum by eyeballing a `list_documents` sample — it defaults to 20 rows and will give a wrong answer on any meaningful dataset.

**Path 3: charts / complex reports → `create_custom_analytics_report`.** Only call this when the user **explicitly asks for** a chart, graph, visualization, dashboard, trend, pivot, breakdown, or saved report — or when the analysis genuinely combines multiple datasets (e.g. purchases vs sales joined by month). Do **not** invoke it for a factual question `query_dataset` could answer in one call.

Important constraint on the analytics report tool: the JS transform runs **client-side only** — you never see its output. So you cannot "open the report to read the numbers." If the user asks you to summarise or interpret the result after the fact, tell them you don't have access to the executed data and either (a) re-answer via `query_dataset`, or (b) ask them what they see on the page. Never claim you'll look at the report yourself.

When you do build a custom report, pass a plain-language `intent` to `create_custom_analytics_report` (a specialist model writes the code for you) and reply with the returned `/reports/analytics?report_id=…` link as a markdown link. The draft appears under **Custom Analytics** in the sidebar so the user can reopen or share it.

## Always use markdown links
Every URL you mention in chat MUST be written as a markdown link `[label](url)` — never a bare URL on its own. The chat UI only turns `[label](url)` into a proper clickable link. A bare `/reports/analytics?report_id=...` still works (a fallback linkifier catches it), but markdown form is the expected shape.

Examples:
- Correct: `[Open report](/reports/analytics?report_id=RPT-AB12CD34)`
- Correct: `[SINV-0012](/app/sales-invoice/SINV-0012)`
- Avoid:   `/reports/analytics?report_id=RPT-AB12CD34` (bare URL)

## Current User
You are speaking with **{user_name}** (role: **{user_role}**).
{role_desc}

{company_context}
{analytics_context}

## User Roles
Lambda ERP has four roles:
- **admin**: Full access to everything — documents, masters, reports, company setup, and user management (inviting team members, changing roles).
- **manager**: Can create, edit, submit, and cancel documents. Can create and edit master data. Can run all reports and use the chat. Cannot manage users or company setup.
- **public_manager**: Demo-mode access. Can create, edit, submit, and cancel documents and use reports/chat, but cannot create, edit, or delete master data. Cannot manage users or company setup.
- **viewer**: Read-only access to documents, masters, and reports. Can use the chat but cannot create or modify data.

When a user asks you to do something they don't have permission for, explain what role is needed instead of attempting the action (the API will reject it anyway).

## Available Document Types (use the slug when calling tools)
- **Selling:** quotation, sales-order, sales-invoice, pos-invoice
- **Buying:** purchase-order, purchase-invoice
- **Accounting:** payment-entry, journal-entry, budget, subscription, bank-transaction
- **Stock:** stock-entry, delivery-note, purchase-receipt
- **Settings:** pricing-rule

## Document Workflow & What Each Document Does

### Sales Cycle
Quotation → Sales Order → Delivery Note (shipping) / Sales Invoice (billing) → Payment Entry
Shortcuts: Quotation can also convert directly to Sales Invoice or Delivery Note (skipping Sales Order) for quick deals.

- **Quotation:** Non-binding offer. No financial or stock impact.
- **Sales Order:** Confirmed customer commitment. No financial impact, but reserves stock for planning.
- **Delivery Note:** Ships goods to customer. **Posts stock entries** (inventory decreases). Requires warehouse on each item. No GL impact on its own.
- **Sales Invoice:** Bills the customer. **Posts GL entries:** Debit Accounts Receivable, Credit Sales Revenue (+ Credit Tax Payable if taxes). Creates outstanding amount.
- **Payment Entry (Receive):** Records customer payment. **Posts GL entries:** Debit Bank, Credit Accounts Receivable. Reduces invoice outstanding.

### Purchase Cycle
Purchase Order → Purchase Receipt (receiving) / Purchase Invoice (billing) → Payment Entry

- **Purchase Order:** Commitment to buy from supplier. No financial or stock impact.
- **Purchase Receipt:** Receives goods into warehouse. **Posts stock entries** (inventory increases). Requires warehouse on each item.
- **Purchase Invoice:** Records supplier bill. Always **posts GL entries** and creates outstanding amount.
  For non-stock/services: Debit Expense, Credit Accounts Payable.
  For stock after a prior Purchase Receipt: Debit Stock Received But Not Billed, Credit Accounts Payable.
  If `update_stock=1`: the Purchase Invoice also receives the goods into stock, so stock items require a warehouse and the posting is Debit Stock In Hand, Credit Accounts Payable.
- **Payment Entry (Pay):** Pays the supplier. **Posts GL entries:** Debit Accounts Payable, Credit Bank. Reduces invoice outstanding.

### Items vs. charges on an invoice

A supplier (or sales) invoice has **two different kinds of lines**, and the LLM MUST keep them separate:

- **Items** (`items[]`): physical goods or distinct services that have an Item master record and a unit × rate shape. Example lines: "10 × Bolt Pack M8", "1 × Engineering Consultation (4 hours)".
- **Charges** (`taxes[]`): non-item monetary lines like **freight, shipping, postage, handling, customs, import duties, insurance**, plus actual taxes (VAT, GST, sales tax). These do NOT need an Item master. They're rows on the invoice that point at a GL account directly.

Do NOT try to create a new Item master for a line like "Shipping" or "Customs duty". Use the charges table instead.

Each charge row has:
- `charge_type`: `"Actual"` for a fixed amount (freight, shipping, handling, customs), `"On Net Total"` for a percentage (most VAT/GST), `"On Previous Row Total"` for a percentage applied on top of earlier charges (EU VAT that includes freight in its base).
- `account_head`: the GL account to debit. Use the company's defaults:
  - Freight / shipping / postage → `default_freight_in_account` on the Company record
  - Customs / duty / tariff → `default_customs_account`
  - VAT / GST / sales tax → the company's Tax Payable account (or similar)
- `description`: the freeform label from the supplier invoice ("Shipping", "Customs duty", "VAT 19%").
- `rate` (for percentage charges) or `tax_amount` (for `Actual` charges).
- `add_deduct_tax`: `"Add"` for most charges; `"Deduct"` for discounts/rebates on the bill.

When you parse a supplier invoice PDF that has both products and a shipping line, create ONE Purchase Invoice with the items in `items[]` and the shipping as a row in `taxes[]` with `charge_type="Actual"`, the freight amount, and the freight account. Same structure for customs duties.

If multiple charges stack (freight + VAT on total incl. freight), put freight first as `Actual`, then VAT second as `On Previous Row Total` referencing the freight row's `idx`.

### Returns (Credit Notes / Debit Notes)
Returns use the SAME document type with negative quantities and `is_return=1`. Create them by "converting" a document to a return of the same type.

- **Credit Note (Sales Return):** Convert a submitted Sales Invoice to return it. Creates a new Sales Invoice with negative quantities. On submit, reverses the original GL entries (credits Receivable, debits Income) and reduces the original invoice's outstanding_amount.
- **Debit Note (Purchase Return):** Convert a submitted Purchase Invoice. Reverses AP/Expense entries and reduces original outstanding.
- **Delivery Note Return:** Convert a submitted Delivery Note. Stock comes back into the warehouse, reverses COGS entries.
- **Purchase Receipt Return:** Convert a submitted Purchase Receipt. Stock goes back out, reverses stock-in-hand entries.

To create a return: use convert_document with the SAME doctype as both source and target.
  Example: convert_document(doctype="sales-invoice", name="SINV-0001", target_doctype="Sales Invoice")

A return is a draft when created — submit it to take effect. It can be cancelled like any other document.

For a complete sales return (financial + stock): create and submit both a Credit Note and a Delivery Note return.

### Stock Entry (manual inventory, NOT for purchases/sales)
- **Opening Stock:** One-time seed of initial inventory at company setup. Posts Dr Stock In Hand / Cr Opening Balance Equity so day-one stock doesn't hit the P&L.
- **Material Receipt:** Manual adjustment that adds stock (found stock, inventory count corrections). Posts Dr Stock In Hand / Cr Stock Adjustment. Not for purchased goods — use Purchase Receipt or Purchase Invoice with update_stock=1 for those. Not for opening balances — use Opening Stock.
- **Material Issue:** Removes stock for write-offs or internal consumption. Posts Dr Stock Adjustment / Cr Stock In Hand.
- **Material Transfer:** Moves stock between warehouses. No GL impact.

### Salary / Payroll (manual flow)
There is no dedicated payroll module. Handle salary payments with two steps:
1. **Accrue salary:** Create a Journal Entry — Debit "Salary Expense" (the expense), Credit "Salary Payable" (the liability). Add a remark like "April 2026 salaries".
2. **Pay salary:** Create a Payment Entry — payment_type "Pay", party_type "Supplier" (or use a Journal Entry: Debit "Salary Payable", Credit bank account).
The accounts "Salary Expense - {{ABBR}}" and "Salary Payable - {{ABBR}}" exist in the Chart of Accounts for this purpose (where ABBR is the company abbreviation, e.g. LAMB for Lambda Corp).

### Key principle
For purchased goods, prefer Purchase Receipt when receiving separately from billing because it gives the best audit trail and clears cleanly into Purchase Invoice later. If the user wants one step to both receive goods and record the supplier bill, use Purchase Invoice with `update_stock=1` and a warehouse on each stock item line.

## Document Lifecycle Rules
- **Draft (docstatus=0):** Document is editable. Has NO financial impact — no GL entries, no stock movement. Can be updated freely.
- **Submitted (docstatus=1):** Document is locked. GL entries and stock ledger entries are posted. Cannot be edited — only cancelled.
- **Cancelled (docstatus=2):** GL and stock entries are reversed. Document is permanently archived. Cannot be reused or edited.
- **There is NO delete operation.** Documents are permanent records. This is by design for audit integrity.
- **To void a draft you no longer need:** submit it first (docstatus 0→1), then cancel it (docstatus 1→2). Do NOT tell the user to "delete" anything.
- **To correct a submitted document:** cancel it and create a new one with the correct values.
- Only submitted documents can be converted (e.g. Sales Order → Sales Invoice). Draft or cancelled documents cannot.
- Cancelled documents cannot be re-submitted or modified.

## Master Data Types
customer, supplier, item, warehouse, account, company, cost-center

## Warehouse Master Rules
- To create a warehouse via `create_master`, you MUST pass a `data` object.
- `warehouse_name` is required.
- `company` should be provided when the warehouse belongs to a company.
- `parent_warehouse` is optional. Do not ask for it unless the user explicitly wants a parent or warehouse tree placement.
- If there is no parent warehouse, omit `parent_warehouse` or set it to `null`. Do not use `"-"` as a value.

## Reports
- **trial-balance** — All account balances (debit, credit, net). Filters: company, from_date, to_date.
- **general-ledger** — Individual GL entries with running balance. Filters: account, party, voucher_type, from_date, to_date, company.
- **profit-and-loss** — Income vs Expense summary with net profit. Filters: company, from_date, to_date.
- **balance-sheet** — Assets, Liabilities, Equity as of a date (includes retained earnings). Filters: company, as_of_date.
- **ar-aging** — Outstanding receivables bucketed by overdue days (Current, 1-30, 31-60, 61-90, 90+). Filters: company, as_of_date.
- **ap-aging** — Outstanding payables bucketed by overdue days. Filters: company, as_of_date.
- **stock-balance** — Inventory quantities and valuations. Filters: item_code, warehouse.
- **dashboard-summary** — KPI overview (revenue, receivables, payables, stock value).
- **Custom analytics drafts** — reserved for when the user **explicitly asks
  for** a chart, graph, trend, pivot, breakdown, or saved report, or when the
  analysis genuinely requires combining multiple datasets. See the
  "Answering data questions — two paths" section at the top: simple factual
  lookups (top customer, outstanding invoices, etc.) should be answered in
  chat via `list_documents`, not by building a report.

  When you do call `create_custom_analytics_report`, pass `title` and
  `intent` — a clear plain-language description (filters, groupings, sort
  orders, chart type). A specialist model writes the JS transform for you.
  Relay the returned `/reports/analytics?report_id=…` link as a markdown
  link. You will NOT see the executed data — the transform runs in the
  user's browser. So do not promise to "read" or "interpret" the report
  yourself; if the user wants a narrative answer, use `list_documents`
  instead.

  For refinements or repairs, first load the existing draft with
  `get_custom_analytics_report` so you can summarize what it does, then call
  `update_custom_analytics_report` with `report_id` and `feedback` — a plain-
  language description of what should change. The specialist rewrites the
  spec while keeping the same `report_id` and URL.

  Do not invent a `company` filter unless the user specified one (or use one
  of the exact company names listed above). Do not invent restrictive date
  windows unless the user asked for them.

## Stock Entry specifics
When creating a stock-entry, the `data` object MUST include:
- `stock_entry_type`: "Opening Stock", "Material Receipt", "Material Issue", or "Material Transfer"
- `company`, `posting_date`
- `items` array where each item uses `basic_rate` (NOT `rate`) and warehouse fields:
  - Opening Stock: items need `t_warehouse` (target). Set `to_warehouse` on the parent too.
  - Material Receipt: items need `t_warehouse` (target). Set `to_warehouse` on the parent too.
  - Material Issue: items need `s_warehouse` (source). Set `from_warehouse` on the parent too.
  - Material Transfer: items need both `s_warehouse` and `t_warehouse`

## Payment Entry specifics
When creating a payment-entry, the `data` object MUST include:
- `payment_type`: "Receive" (customer pays you) or "Pay" (you pay supplier)
- `company`, `posting_date`, `party_type` ("Customer" or "Supplier"), `party` (the party name)
- `paid_from`, `paid_to` (account names), `paid_amount`, `received_amount`
- **Always include references** when paying against an invoice:
  `"references": [{{"reference_doctype": "Sales Invoice", "reference_name": "SINV-0001", "allocated_amount": 5000}}]`
- For partial payments, set `allocated_amount` to less than the invoice total

## Delivery Note & Purchase Receipt specifics
- Items MUST have a warehouse set. For Delivery Notes use the source warehouse. For Purchase Receipts use the target warehouse.
- Look up the available warehouse first (search_masters with master_type "warehouse") and set it on each item row.
- When converting from a Sales Order or Purchase Order, the warehouse is often missing — always check and update it before submitting.

## Master keys vs display names — CRITICAL

Every master record has a primary key (the `name` column) and a human-readable display field:
- **Item:** key = `item_code` (e.g. `SVC-005`), display = `item_name` (e.g. "Project Management")
- **Customer:** key = `name` (e.g. `CUST-007`), display = `customer_name` (e.g. "Redstone Automotive")
- **Supplier:** key = `name` (e.g. `SUPP-003`), display = `supplier_name`
- **Warehouse / Company / Account / Cost Center:** key = `name`

When you fill in `item_code`, `customer`, `supplier`, `warehouse`, `company`, etc. in a document or child-table row, you MUST use the **primary key**, never the display name. `"item_code": "Project Management"` is ALWAYS wrong — it's a name, not a code.

If the user refers to something by its human name ("bill them 8 hours of project mgmt", "add Redstone to the quote"), resolve the key first:
- `search_masters(master_type="item", q="project management")` → returns `[{{name: "SVC-005", item_name: "Project Management"}}]`. Use `name` as `item_code`.
- Same for customers, suppliers, warehouses, etc. — `search_masters` matches **both** the key and the display field.

When you list masters back to the user (items on an invoice, customers on a report), include the key in parentheses so follow-ups are unambiguous. Example: "Project Management (SVC-005) — 16 Hour".

If a `create_document` / `update_document` call fails with an "item not found" / "master does not exist" kind of error, do NOT blame permissions — first call `search_masters` to find the correct key and retry. Only report a permission error if the API explicitly returns a 403 / "cannot create or update master data" message.

## Rules
- Always search for existing master data before creating documents (verify customer/item keys exist).
- When calling `create_master`, always include the `data` object. Never call it with only `master_type`.
- When calling `update_master`, always search the master first if the exact record name is uncertain, then pass the existing `name` plus a `data` object with only the fields to change.
- If the current user role is `public_manager`, do not call `create_master` or `update_master`; explain that demo mode cannot modify master data. **But `public_manager` CAN still create, edit, submit, and cancel documents** — don't cite demo mode as the reason a document creation failed unless the error literally says so.
- Use today's date ({date.today().isoformat()}) as default for posting_date/transaction_date
- When creating documents, you MUST always include the `company` field. Search for companies first if you don't know the name.
- When creating documents with items, you MUST include the items array with `item_code` (primary key, NOT `item_name`), qty, and rate.
- After creating a document, tell the user its name and key details, and include links (see below)
- When the user says "submit it" or "convert it", refer to the most recently discussed document
- Use markdown for formatting responses
- Be concise but helpful
- If a tool call fails, explain the error clearly and suggest how to fix it

## Document & Master Links
When referencing records, always use clickable markdown links so the user can open them directly.

**Documents** (quotations, invoices, orders, deliveries, receipts, payments, journal entries, stock entries):
- **View/edit link:** `/app/{{doctype-slug}}/{{name}}` — e.g. [SINV-0001](/app/sales-invoice/SINV-0001)
- **PDF link:** `/api/documents/{{doctype-slug}}/{{name}}/pdf` — e.g. [Download PDF](/api/documents/sales-invoice/SINV-0001/pdf)
The doctype slug is the lowercase, hyphenated form: sales-invoice, purchase-order, delivery-note, etc.

**Master records** (customer, supplier, item, warehouse, company):
- **View/edit link:** `/masters/{{master-type}}/{{name}}` — e.g. [SUPP-001](/masters/supplier/SUPP-001), [CUST-003](/masters/customer/CUST-003), [ITEM-001](/masters/item/ITEM-001)
- NEVER use `/app/...` for masters — that path is only for transactional documents.

Always include the view link after creating, submitting, converting, or updating a record. Include the PDF link when the user asks for a printable version or when sharing an invoice/quotation.

## File Attachments
Users can attach PDFs and images (receipts, bills, contracts, screenshots) to their messages using the paperclip button in the chat input. When attachments are sent, they appear as multimodal content in the current message and you can directly see their contents.

- **For the current message** — any attached images/PDFs are already in your context; read them carefully and use the content (e.g., parse a supplier invoice into a Purchase Invoice).
- **For files uploaded earlier in the chat** — if the user asks about a previously-uploaded file that is no longer in your immediate context, call `list_chat_attachments` to see what's available, then `retrieve_chat_attachment(attachment_id)` to load it. The retrieved content will appear in the next turn as a user message you can read.
- **When a user uploads a bill/invoice and asks you to "add it" or "create a purchase invoice"**, extract supplier, line items, quantities, rates, and totals from the document and call `create_document("purchase-invoice", {...})`. Confirm details back to the user and ask about anything ambiguous (e.g., which existing supplier it matches)."""


# ---------------------------------------------------------------------------
# Anthropic code specialist (sub-agent)
#
# GPT-5.4 stays as the planner/orchestrator. When it decides a report needs
# code generated (the user wants a custom analytics view) it calls the
# create/update custom analytics tools. Those handlers delegate the actual
# JS spec generation to Anthropic `ANTHROPIC_CODE_MODEL` — a specialist that
# is stronger at producing the runtime spec than the planner.
# ---------------------------------------------------------------------------


def _code_model() -> str:
    return (
        os.environ.get("ANTHROPIC_CODE_MODEL")
        or os.environ.get("ANTHROPIC_REPORT_REPAIR_MODEL")  # legacy fallback
        or "claude-sonnet-4-20250514"
    )


def _anthropic_available() -> tuple[bool, str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key == "your-anthropic-key-here":
        return False, "ANTHROPIC_API_KEY is not configured"
    try:
        import anthropic  # noqa: F401  # type: ignore
    except Exception as e:
        return False, f"Anthropic SDK not available: {e}"
    return True, ""


_REPORT_CODE_SYSTEM_PROMPT = """You are a report-code specialist for the Lambda ERP analytics runtime.

Your only job is to return a strict JSON object describing a custom analytics report. You NEVER write prose, commentary, markdown, or code fences — only the raw JSON object.

## Output shape

Return JSON with these top-level fields:
- `title` (string, short)
- `description` (string, optional, one sentence)
- `data_requests` (array of 1+ objects)
- `transform_js` (string)

Each `data_requests[]` is `{ name, dataset, fields, filters?, limit? }`. `dataset` must be one of the semantic datasets listed below. `fields` must be a subset of that dataset's exposed field list. Never invent fields.

`transform_js` is a function body (NOT a function declaration). It receives the requested datasets injected as top-level variables (named after `data_requests[].name`, or the dataset name if `name` is omitted). It must end with `return { ... }`.

The returned object supports:
- `kpis: [{ label, value, format? }]`
- `tables: [{ title, columns: [{ key, label, type? }], rows }]` where `type` is one of `currency`, `number`, `string`, `date`
- `charts: [{ title, type, x, y, dataTable? , data? }]` where `type` is `bar`, `line`, or `pie`. `y` MUST be a single string — never an array. Prefer `dataTable: '<table title>'` when the chart is based on a returned table; otherwise use inline `data`.
- `summary: "..."` (string)

## Semantic datasets

Use only these datasets. Exposed fields will be listed in the user message; do not invent others.
- `sales_invoices`
- `sales_invoice_lines`
- `purchase_invoices`
- `purchase_invoice_lines`
- `payments`
- `ar_open_items`
- `ap_open_items`
- `stock_balances`

These datasets already scope to submitted/open records, so do not add `docstatus = 1` filters.

## Filters shape

`data_requests[].filters` MUST be an object (dict) keyed by field name. It is NOT a list of triples and NOT a SQL expression. Supported value shapes per key:

- **Equality:** `{ "customer": "CUST-001" }` → `WHERE customer = 'CUST-001'`
- **IN list:** `{ "item_code": ["ITEM-A", "ITEM-B"] }` → `WHERE item_code IN (...)`
- **Date / number range:** `{ "posting_date": { "from": "2025-06-20", "to": "2026-04-20" } }` — use this shape for any from/to range. Either side can be omitted.

A complete example:
```json
"filters": {
  "posting_date": { "from": "2025-01-01", "to": "2025-12-31" },
  "customer": "CUST-001",
  "is_return": 0
}
```

Do NOT produce `[["posting_date", ">=", "2025-06-20"], ...]`. That shape will be rejected by the backend.

Only filter on fields listed in the dataset's `filter_fields`. If a date range is needed, always use the `{ from, to }` sub-object under the date field, never operator strings like `>=`.

## Supported runtime helpers

Only these patterns work inside `transform_js`:
- `helpers.sum(rows, 'field')` or `helpers.sum(rows, row => ...)`
- `helpers.sortBy(rows, 'field', 'asc'|'desc'|true)`
- `helpers.topN(rows, 'field', n)` or `helpers.topN(rows, n)` when already sorted
- `helpers.group(rows, ['field1', ...], { alias: ['sum'|'count', 'field'] })`
- `helpers.group(rows, row => key)` returns `[{ key, rows }]`
- `helpers.monthKey(value)`, `helpers.quarterKey(value)`, `helpers.yearKey(value)`
- `helpers.leftJoin(left, right, 'leftKey', 'rightKey')`
- `helpers.pivot(rows, rowKey, colKey, valueKey)`

Do NOT use unsupported shapes like `helpers.sortBy(rows, row => ...)` or `helpers.group(rows, keyFn, reducerFn)`.

## Chart type selection

- `bar` — ranked lists, category comparisons, month-by-month totals, most discrete bucketed reports. Default when unsure.
- `line` — continuous trend over many periods when the user explicitly asks for a trend.
- `pie` — simple part-of-whole with few categories only.

If the user asks for a graph, chart, visual, breakdown, or comparison — include at least one chart. Table-only output is only appropriate if the user explicitly asked for just a table.

## Rules

- Return ONLY the JSON object. No prose, no code fences, no commentary.
- `transform_js` must be a function body ending in `return { ... }`.
- Never hallucinate field names — only use fields explicitly listed for the chosen dataset.
- Prefer simple transforms: group → sortBy → topN → chart.
"""


def _dataset_catalog_text() -> str:
    from api.routers.analytics import SEMANTIC_DATASETS
    lines = []
    for ds, spec in SEMANTIC_DATASETS.items():
        fields = ", ".join(spec.get("fields", []))
        filter_fields = ", ".join(sorted(spec.get("filter_fields", []) or []))
        lines.append(
            f"- `{ds}` ({spec.get('label', '')}):\n"
            f"    fields        = [{fields}]\n"
            f"    filter_fields = [{filter_fields}]"
        )
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Code specialist did not return a JSON object.")
    candidate = text[start : end + 1]
    return json.loads(candidate)


def _generate_report_spec_via_anthropic(
    intent: str,
    existing_spec: dict | None = None,
    feedback: str | None = None,
    client_ip: str | None = None,
    user_role: str | None = None,
) -> dict:
    ok, reason = _anthropic_available()
    if not ok:
        raise RuntimeError(reason)
    import anthropic  # type: ignore

    model = _code_model()
    api_key = os.environ["ANTHROPIC_API_KEY"]

    user_parts: list[str] = []
    user_parts.append(f"## Today's date\n{date.today().isoformat()}")
    user_parts.append("## Available datasets\n" + _dataset_catalog_text())
    if existing_spec:
        existing_payload = {
            "title": existing_spec.get("title"),
            "description": existing_spec.get("description"),
            "data_requests": existing_spec.get("data_requests"),
            "transform_js": existing_spec.get("transform_js"),
        }
        user_parts.append(
            "## Existing draft to refine\n"
            + json.dumps(existing_payload, indent=2, default=str)
        )
    if feedback:
        user_parts.append("## User feedback / change request\n" + feedback)
    if intent:
        user_parts.append("## Intent\n" + intent)
    user_parts.append(
        "Return the updated (or new) report spec as strict JSON "
        "with fields `title`, `description`, `data_requests`, `transform_js`. "
        "No prose — JSON only."
    )
    user_msg = "\n\n".join(user_parts)

    print(
        f"[chat_llm] provider=anthropic role=code_specialist model={model}",
        flush=True,
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
    reservation_id = None
    if is_demo_role(user_role):
        _blocked, reservation_id = demo_limiter.reserve(
            client_ip or "unknown",
            estimated_usd=demo_call_reserve_usd(),
            role=user_role,
        )
        if _blocked:
            raise RuntimeError(_blocked)

    # try/finally + `settled` flag guarantees reservation release even if
    # a CancelledError slips between the SDK call returning and settle()
    # completing. Without this, a cancelled coroutine could leak the
    # reservation for the process lifetime (or until TTL sweep).
    settled = False
    try:
        response = client.messages.create(
            model=model,
            system=_REPORT_CODE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=4096,
        )
        # Log every call for the admin dashboard. Only public_manager rows
        # count against the demo cap — other roles are logged for
        # visibility but exempt from rate limiting.
        usage = getattr(response, "usage", None)
        demo_limiter.settle(
            reservation_id,
            actual_cost_usd=cost_of_anthropic_call(model, usage),
            ip=client_ip or "unknown",
            role=user_role,
            provider="anthropic",
            model=model,
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
        )
        settled = True
    finally:
        if not settled:
            demo_limiter.release(reservation_id)

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    spec = _extract_json_object(text)
    if not spec.get("transform_js") or not spec.get("data_requests"):
        raise RuntimeError("Code specialist returned an incomplete spec.")
    return spec


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


async def generate_title(
    session_id: str,
    user_message: str,
    assistant_message: str,
    client_ip: str | None = None,
    user_role: str | None = None,
):
    """Generate a short title for the chat based on the first exchange.

    Called after the first assistant reply. Runs in the background so it
    doesn't block the response.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or api_key == "sk-your-key-here":
        return

    try:
        client = OpenAI(
            api_key=api_key,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        title_model = "gpt-4.1-nano"
        reservation_id = None
        if is_demo_role(user_role):
            _blocked, reservation_id = demo_limiter.reserve(
                client_ip or "unknown",
                estimated_usd=demo_call_reserve_usd(),
                role=user_role,
            )
            if _blocked:
                return

        settled = False
        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=title_model,
                messages=[
                    {"role": "system", "content": "Generate a very short title (3-6 words, no quotes) for this chat based on the first exchange. Just the title, nothing else."},
                    {"role": "user", "content": f"User: {user_message[:200]}\nAssistant: {assistant_message[:200]}"},
                ],
                max_completion_tokens=30,
            )
            usage = getattr(response, "usage", None)
            demo_limiter.settle(
                reservation_id,
                actual_cost_usd=cost_of_openai_call(title_model, usage),
                ip=client_ip or "unknown",
                role=user_role,
                provider="openai",
                model=title_model,
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
                session_id=session_id,
            )
            settled = True

            title = response.choices[0].message.content.strip().strip('"\'')
            if title:
                update_session_title(session_id, title)
        finally:
            if not settled:
                demo_limiter.release(reservation_id)
    except Exception:
        pass  # Title generation is best-effort


# ---------------------------------------------------------------------------
# Reasoning loop
# ---------------------------------------------------------------------------


async def run_thinking_loop(
    messages: list[dict],
    on_event,
    session_id: str = None,
    max_iterations: int = 8,
    user_info: dict | None = None,
    client_ip: str | None = None,
):
    """Run the agentic reasoning loop.

    The orchestrator is always OpenAI (gpt-5.4). When GPT decides to call
    `create_custom_analytics_report` or `update_custom_analytics_report`
    with an intent/feedback hint, the tool handler itself delegates the
    code-generation step to Anthropic (ANTHROPIC_CODE_MODEL). We emit an
    `llm_provider` event around that delegation so the UI can surface it.
    """
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_api_key or openai_api_key == "sk-your-key-here":
        await on_event({"type": "error", "content": "Error: OPENAI_API_KEY is not configured. Please set it in the .env file."})
        return

    openai_client = OpenAI(
        api_key=openai_api_key,
        timeout=httpx.Timeout(120.0, connect=10.0),
    )

    tool_handlers = dict(TOOL_HANDLERS)

    user_role = user_info.get("role") if user_info else None
    demo_mode = is_demo_role(user_role)
    # Demo visitors get a tighter completion budget to bound worst-case
    # cost per turn; logged-in managers/admins keep the original cap.
    max_completion = demo_max_completion_tokens() if demo_mode else 4096

    if demo_mode:
        denied = lambda _args: {"error": "Demo mode cannot create or update master data."}
        tool_handlers["create_master"] = denied
        tool_handlers["update_master"] = denied

    # Scope session-aware tools without mutating globals.
    if session_id:
        tool_handlers["retrieve_chat_history"] = lambda args: _handle_retrieve_chat_history(args, session_id)
        user_id_for_tools = user_info.get("name") if user_info else None
        tool_handlers["list_chat_attachments"] = lambda args: _handle_list_chat_attachments(args, session_id, user_id_for_tools)
        _scoped_retrieve_attachment = (
            lambda args: _handle_retrieve_chat_attachment(args, session_id, user_id_for_tools)
        )
        tool_handlers["create_custom_analytics_report"] = (
            lambda args: _handle_create_custom_analytics_report(args, user_info, session_id, client_ip=client_ip)
        )
        tool_handlers["get_custom_analytics_report"] = (
            lambda args: _handle_get_custom_analytics_report(args, user_info)
        )
        tool_handlers["update_custom_analytics_report"] = (
            lambda args: _handle_update_custom_analytics_report(args, user_info, client_ip=client_ip)
        )

        # Demo sessions get a per-turn cap on attachment retrieval: each
        # retrieval injects ~33k tokens of base64, and the LLM will
        # happily pull N attachments on one turn if asked. Cap at 1/turn
        # — visitors who need another retrieval can send a new message.
        # Counter is scoped per run_thinking_loop invocation, so it
        # resets naturally between user messages.
        if demo_mode:
            demo_retrieval_budget = [1]

            def _demo_capped_retrieve(args, _inner=_scoped_retrieve_attachment, _budget=demo_retrieval_budget):
                if _budget[0] <= 0:
                    return {
                        "error": (
                            "Demo mode allows at most 1 attachment retrieval per message. "
                            "Ask about this attachment in a new message to retrieve another."
                        )
                    }
                _budget[0] -= 1
                return _inner(args)

            tool_handlers["retrieve_chat_attachment"] = _demo_capped_retrieve
        else:
            tool_handlers["retrieve_chat_attachment"] = _scoped_retrieve_attachment

    for iteration in range(max_iterations):
        # Pre-flight spend check — catches both the first call and any
        # overshoot from the previous iteration's tokens.
        if demo_mode and client_ip:
            blocked = demo_limiter.check(client_ip)
            if blocked:
                await on_event({"type": "error", "content": blocked})
                return

        await on_event({"type": "thinking", "iteration": iteration + 1})

        reservation_id = None
        if demo_mode:
            blocked, reservation_id = demo_limiter.reserve(
                client_ip or "unknown",
                estimated_usd=demo_call_reserve_usd(),
                role=user_role,
            )
            if blocked:
                await on_event({"type": "error", "content": blocked})
                return

        # try/finally + settled flag guarantees the reservation is released
        # on ANY exit path — including asyncio.CancelledError raised after
        # the SDK call has already returned but before settle() ran.
        settled = False
        model_name = "gpt-5.4"
        try:
            print(f"[chat_llm] provider=openai model={model_name} session_id={session_id or '-'} iter={iteration + 1}", flush=True)
            await on_event({"type": "llm_provider", "provider": "openai", "model": model_name})
            try:
                response = await asyncio.to_thread(
                    openai_client.chat.completions.create,
                    model=model_name,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    max_completion_tokens=max_completion,
                )
            except Exception as e:
                await on_event({"type": "error", "content": f"Error calling LLM: {e}"})
                return

            usage = getattr(response, "usage", None)
            demo_limiter.settle(
                reservation_id,
                actual_cost_usd=cost_of_openai_call(model_name, usage),
                ip=client_ip or "unknown",
                role=user_role,
                provider="openai",
                model=model_name,
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
                session_id=session_id,
            )
            settled = True
        finally:
            if not settled:
                demo_limiter.release(reservation_id)

        choice = response.choices[0]
        message = choice.message

        if not message.tool_calls:
            content = message.content or ""
            messages.append({"role": "assistant", "content": content})
            await on_event({"type": "complete", "content": content})
            return

        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ],
        })
        tool_calls = message.tool_calls

        pending_multimodal: list = []
        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}
            tool_call_id = tc.id

            await on_event({"type": "tool_call", "tool": fn_name, "args": fn_args})

            # If GPT is delegating report code-gen to the Anthropic specialist,
            # surface the handoff in the UI.
            will_delegate_to_code_specialist = (
                fn_name in ("create_custom_analytics_report", "update_custom_analytics_report")
                and not fn_args.get("transform_js")
                and not fn_args.get("data_requests")
            )
            if will_delegate_to_code_specialist:
                await on_event({
                    "type": "llm_provider",
                    "provider": "anthropic",
                    "model": _code_model(),
                    "role": "code_specialist",
                })

            handler = tool_handlers.get(fn_name)
            if not handler:
                result = {"error": f"Unknown tool: {fn_name}"}
                success = False
            else:
                try:
                    result = await asyncio.to_thread(handler, fn_args)
                    success = True
                except Exception as e:
                    result = {"error": str(e)}
                    success = False

            # Pull any multimodal content out of the tool result before serializing.
            # retrieve_chat_attachment returns a {"_multimodal_content": {...}} block
            # which we inject as a fresh user message so the LLM can "see" the file.
            multimodal_block = None
            if isinstance(result, dict):
                multimodal_block = result.pop("_multimodal_content", None)

            result_str = json.dumps(result, default=str)
            summary = result_str[:500] + "..." if len(result_str) > 500 else result_str

            event_payload: dict = {
                "type": "tool_result",
                "tool": fn_name,
                "success": success,
                "summary": summary,
            }
            # Surface the report id so the sidebar can flash the specific draft.
            if (
                success
                and fn_name in ("create_custom_analytics_report", "update_custom_analytics_report")
                and isinstance(result, dict)
                and result.get("id")
            ):
                event_payload["report_id"] = result["id"]
            await on_event(event_payload)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_str[:50000],
            })

            if multimodal_block:
                pending_multimodal.append(multimodal_block)

        # After this batch of tool calls, inject any retrieved attachments as
        # a new user message so the LLM can actually see them on the next turn.
        if pending_multimodal:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here are the attachments you requested:"},
                    *pending_multimodal,
                ],
            })

    content = "I've reached the maximum number of reasoning steps. Here's what I've done so far — please check the results above."
    messages.append({"role": "assistant", "content": content})
    await on_event({"type": "complete", "content": content})


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


async def chat_websocket(
    websocket: WebSocket,
    user_info: dict | None = None,
    client_ip: str | None = None,
):
    """Handle a shared WebSocket chat connection across sessions."""
    await websocket.accept()
    ws_user_id = user_info.get("name") if user_info else None
    ws_user_role = user_info.get("role") if user_info else None

    session_tasks: dict[str, asyncio.Task] = {}
    demo_typing_waiters: dict[str, tuple[int, asyncio.Future]] = {}
    send_lock = asyncio.Lock()

    async def send_event(event_type: str, request_id: str | None = None, **payload):
        event = {"type": event_type, **payload}
        if request_id:
            event["request_id"] = request_id
        try:
            async with send_lock:
                await websocket.send_json(event)
        except Exception:
            pass

    async def send_error(content: str, request_id: str | None = None, session_id: str | None = None):
        payload = {"content": content}
        if session_id:
            payload["session_id"] = session_id
        await send_event("error", request_id=request_id, **payload)

    async def send_message_added(
        session_id: str,
        role: str,
        content: str,
        created_at: str | None = None,
        attachments: list | None = None,
    ):
        await send_event(
            "message_added",
            session_id=session_id,
            message=serialize_chat_message(role, content, created_at, attachments),
        )

    async def replay_demo_session(target_session_id: str):
        try:
            demo_script = load_demo_script()
            demo_messages = load_demo_history(target_session_id)
            message_idx = 0

            for entry in demo_script:
                role = entry.get("role")
                content = str(entry.get("content", ""))
                stored_message = demo_messages[message_idx] if message_idx < len(demo_messages) else None
                created_at = stored_message.get("created_at") if stored_message else None
                rendered_content = stored_message.get("content", content) if stored_message else content

                if role == "user":
                    typing_ms = min(DEMO_TYPE_INITIAL_MS + len(rendered_content) * DEMO_TYPE_MS_PER_CHAR, 6000)
                    typing_seq = uuid.uuid4().int & 0x7FFFFFFF
                    typing_waiter = asyncio.get_running_loop().create_future()
                    demo_typing_waiters[target_session_id] = (typing_seq, typing_waiter)
                    await send_event(
                        "demo_typing",
                        session_id=target_session_id,
                        role=role,
                        content=rendered_content,
                        typing_ms=typing_ms,
                        seq=typing_seq,
                    )
                    try:
                        await asyncio.wait_for(typing_waiter, timeout=(typing_ms + 4000) / 1000)
                    except asyncio.TimeoutError:
                        pass
                    finally:
                        if demo_typing_waiters.get(target_session_id, (None,))[0] == typing_seq:
                            demo_typing_waiters.pop(target_session_id, None)
                    await asyncio.sleep(DEMO_AFTER_TYPED_USER_MS / 1000)
                else:
                    await asyncio.sleep(0.6)

                await send_message_added(target_session_id, role, rendered_content, created_at)
                message_idx += 1

                flash = entry.get("flash")
                if isinstance(flash, dict):
                    group = str(flash.get("group", "")).strip()
                    item = str(flash.get("item", "")).strip()
                    if group:
                        payload = {"group": group}
                        if item:
                            payload["item"] = item
                        await send_event("navigation_flash", session_id=target_session_id, **payload)

                await asyncio.sleep(0.35 if role == "user" else 0.75)

            await send_event("demo_replay_complete", session_id=target_session_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await send_error(str(e), session_id=target_session_id)
        finally:
            session_tasks.pop(target_session_id, None)

    async def process_session_message(target_session_id: str, user_content: str, attachment_ids: list | None = None):
        try:
            is_first_reply = count_assistant_messages(target_session_id) == 0

            messages = [{"role": "system", "content": build_system_prompt(user_info)}]
            conversation = build_conversation(target_session_id, limit=20)

            # If the user attached files with this message, replace the last user
            # message in the conversation with a multimodal content array so the
            # LLM can see the images/PDFs directly.
            if attachment_ids and ws_user_id:
                from api.attachments import get_attachments_by_ids, build_multimodal_content
                atts = get_attachments_by_ids(attachment_ids, ws_user_id)
                if atts and conversation and conversation[-1].get("role") == "user":
                    parts = []
                    text = conversation[-1].get("content") or ""
                    if text:
                        parts.append({"type": "text", "text": text})
                    for att in atts:
                        parts.append(build_multimodal_content(att))
                    conversation[-1] = {"role": "user", "content": parts}

            messages.extend(conversation)

            async def on_event(event: dict):
                await send_event(event["type"], session_id=target_session_id, **{
                    key: value for key, value in event.items() if key != "type"
                })

            await run_thinking_loop(
                messages, on_event,
                session_id=target_session_id,
                user_info=user_info,
                client_ip=client_ip,
            )

            assistant_content = None
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    assistant_content = msg["content"]
                    save_chat_message(target_session_id, "assistant", assistant_content)
                    await send_message_added(target_session_id, "assistant", assistant_content)
                    break

            if is_first_reply and assistant_content:
                async def _gen_and_notify():
                    await generate_title(
                        target_session_id,
                        user_content,
                        assistant_content,
                        client_ip=client_ip,
                        user_role=ws_user_role,
                    )
                    session = get_session(target_session_id)
                    if session:
                        await send_event(
                            "session_title_updated",
                            session_id=target_session_id,
                            title=session["title"],
                        )

                asyncio.create_task(_gen_and_notify())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await send_error(str(e), session_id=target_session_id)
        finally:
            session_tasks.pop(target_session_id, None)

    try:
        await send_event("sessions_list", sessions=list_sessions(user_id=ws_user_id, role=ws_user_role))

        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send_error("Invalid JSON")
                continue

            msg_type = data.get("type")
            request_id = data.get("request_id")

            if msg_type == "list_sessions":
                await send_event("sessions_list", request_id=request_id, sessions=list_sessions(user_id=ws_user_id, role=ws_user_role))
                continue

            if msg_type == "create_session":
                session = create_session(user_id=ws_user_id)
                await send_event("session_created", request_id=request_id, session=session)
                continue

            if msg_type in ("load_history", "join_session"):
                session_id = data.get("session_id")
                if not session_id:
                    await send_error("No session_id provided", request_id=request_id)
                    continue

                session = get_session(session_id)
                if not can_access_session(session, user_info):
                    await send_error(f"Session {session_id} not found", request_id=request_id, session_id=session_id)
                    continue

                # Pagination: "before_id" for loading older messages; default page size 20.
                before_id = data.get("before_id")
                page_size = int(data.get("limit") or 20)
                page = load_serialized_chat_history(session_id, limit=page_size, before_id=before_id)

                await send_event(
                    "history_loaded",
                    request_id=request_id,
                    session_id=session_id,
                    title=session["title"],
                    messages=page["messages"],
                    has_more=page["has_more"],
                    oldest_id=page["oldest_id"],
                    before_id=before_id,
                )
                continue

            if msg_type == "delete_session":
                session_id = data.get("session_id")
                if not session_id:
                    await send_error("No session_id provided", request_id=request_id)
                    continue
                session = get_session(session_id)
                if not can_access_session(session, user_info):
                    await send_error("Session not found", request_id=request_id, session_id=session_id)
                    continue

                active_task = session_tasks.get(session_id)
                if active_task and not active_task.done():
                    await send_error(
                        "Wait for the current response to finish before deleting this chat.",
                        request_id=request_id,
                        session_id=session_id,
                    )
                    continue

                delete_session(session_id)
                await send_event("session_deleted", request_id=request_id, session_id=session_id)
                continue

            if msg_type == "clear_history":
                session_id = data.get("session_id")
                if not session_id:
                    await send_error("No session_id provided", request_id=request_id)
                    continue
                session = get_session(session_id)
                if not can_access_session(session, user_info):
                    await send_error("Session not found", request_id=request_id, session_id=session_id)
                    continue

                active_task = session_tasks.get(session_id)
                if active_task and not active_task.done():
                    await send_error(
                        "Wait for the current response to finish before clearing this chat.",
                        request_id=request_id,
                        session_id=session_id,
                    )
                    continue

                clear_chat_history(session_id)
                await send_event("history_cleared", request_id=request_id, session_id=session_id)
                continue

            if msg_type == "demo_typing_done":
                session_id = data.get("session_id")
                seq = data.get("seq")
                if not session_id or not isinstance(seq, int):
                    await send_error("Invalid demo typing completion event", request_id=request_id, session_id=session_id)
                    continue

                waiter_info = demo_typing_waiters.get(session_id)
                if waiter_info and waiter_info[0] == seq and not waiter_info[1].done():
                    waiter_info[1].set_result(True)
                continue

            if msg_type == "start_demo":
                session_id = data.get("session_id")
                if not session_id:
                    await send_error("No session_id provided", request_id=request_id)
                    continue

                if not user_info or user_info.get("role") != "public_manager":
                    await send_error("Demo replay is only available in public demo mode.", request_id=request_id, session_id=session_id)
                    continue

                session = get_session(session_id)
                if not session:
                    await send_error(f"Session {session_id} not found", request_id=request_id, session_id=session_id)
                    continue

                if session.get("user_id") != ws_user_id:
                    await send_error("Session not found", request_id=request_id, session_id=session_id)
                    continue

                active_task = session_tasks.get(session_id)
                if active_task and not active_task.done():
                    await send_error(
                        "Wait for the current response to finish before starting the demo.",
                        request_id=request_id,
                        session_id=session_id,
                    )
                    continue

                # Refuse to overwrite a session that already contains real
                # (non-demo) user/assistant messages.
                non_demo_rows = get_db().sql(
                    'SELECT COUNT(*) as cnt FROM "Chat Message" '
                    'WHERE session_id = ? AND message_type != "demo"',
                    [session_id],
                )
                if non_demo_rows and non_demo_rows[0]["cnt"] > 0:
                    await send_error(
                        "This chat already contains non-demo messages.",
                        request_id=request_id,
                        session_id=session_id,
                    )
                    continue


                # Always re-seed the demo messages so template or Settings
                # updates show up on the next replay. Without this, stale
                # placeholder values (e.g. empty customer names from an older
                # bootstrap) would be baked into the DB forever.
                db = get_db()
                db.sql(
                    'DELETE FROM "Chat Message" '
                    'WHERE session_id = ? AND message_type = "demo"',
                    [session_id],
                )
                db.conn.commit()
                for message in load_demo_script():
                    save_chat_message(session_id, message["role"], message["content"], message_type="demo")

                session = get_session(session_id)
                await send_event("demo_started", request_id=request_id, session_id=session_id, title=session["title"] if session else "New Chat")
                session_tasks[session_id] = asyncio.create_task(replay_demo_session(session_id))
                continue

            if msg_type != "send_message":
                await send_error(f"Unknown message type: {msg_type}", request_id=request_id)
                continue

            session_id = data.get("session_id")
            if not session_id:
                await send_error("No session_id provided", request_id=request_id)
                continue

            session = get_session(session_id)
            if not can_access_session(session, user_info):
                await send_error(f"Session {session_id} not found", request_id=request_id, session_id=session_id)
                continue

            active_task = session_tasks.get(session_id)
            if active_task and not active_task.done():
                await send_error(
                    "Wait for the current response to finish before sending another message.",
                    request_id=request_id,
                    session_id=session_id,
                )
                continue

            user_content = data.get("content", "").strip()
            attachment_ids = data.get("attachment_ids") or []
            if not isinstance(attachment_ids, list):
                attachment_ids = []
            # Cap attachments at 5 per message
            attachment_ids = [str(a) for a in attachment_ids[:5]]
            if is_demo_role(ws_user_role) and len(attachment_ids) > 1:
                await send_error(
                    "Demo mode allows at most 1 attachment per message.",
                    request_id=request_id,
                    session_id=session_id,
                )
                continue

            if not user_content and not attachment_ids:
                await send_error("Message content cannot be empty.", request_id=request_id, session_id=session_id)
                continue

            # Demo-only: cap raw message length BEFORE the LLM sees it.
            # The per-call `max_completion_tokens` already bounds output
            # cost, but input tokens are uncapped — a pasted 100k-char
            # wall would otherwise burn the global hourly budget in a
            # single turn. Reject rather than truncate so the visitor
            # knows what happened.
            if is_demo_role(ws_user_role):
                max_chars = demo_max_message_chars()
                if len(user_content) > max_chars:
                    await send_error(
                        f"Demo messages are limited to {max_chars} characters "
                        f"(your message is {len(user_content):,}). Please shorten it and try again.",
                        request_id=request_id,
                        session_id=session_id,
                    )
                    continue

            # Short-circuit rate-limited demo visitors before we persist
            # the user message and kick off the LLM task. `run_thinking_loop`
            # re-checks on every iteration — this is just the friendly UX path.
            if is_demo_role(ws_user_role) and client_ip:
                blocked = demo_limiter.check(client_ip)
                if blocked:
                    await send_error(blocked, request_id=request_id, session_id=session_id)
                    continue

            # Resolve attachment metadata for persistence + display
            from api.attachments import list_session_attachments
            session_attachments = {
                a["id"]: a for a in list_session_attachments(session_id, ws_user_id or "")
            } if ws_user_id else {}
            attached_meta = [
                {
                    "id": aid,
                    "filename": session_attachments[aid]["filename"],
                    "mime_type": session_attachments[aid]["mime_type"],
                }
                for aid in attachment_ids
                if aid in session_attachments
            ]

            save_chat_message(
                session_id, "user", user_content or "",
                metadata={"attachments": attached_meta} if attached_meta else None,
            )
            await send_message_added(
                session_id, "user", user_content or "",
                attachments=attached_meta if attached_meta else None,
            )

            # Emit the thinking indicator eagerly. The async task below still
            # has to read history, build the system prompt, and call the LLM
            # before run_thinking_loop fires its own "thinking" event, and on
            # a freshly-seeded 3-year DB that gap is visibly long. Sending it
            # up-front gives the user immediate UI feedback.
            await send_event("thinking", session_id=session_id, iteration=1)

            session_tasks[session_id] = asyncio.create_task(
                process_session_message(session_id, user_content, attachment_ids)
            )

    except WebSocketDisconnect:
        for task in session_tasks.values():
            task.cancel()
    except Exception as e:
        await send_error(str(e))
