"""MCP (Model Context Protocol) endpoint — the fine-grained ERP tool surface for
LLM agents (Claude, Codex, …) over MCP's Streamable HTTP transport.

Reuses everything the chat already has, so there's almost no new logic:
  * schemas   — build_tools() (the live, plugin-widened tool list).
  * execution — the same TOOL_HANDLERS (which run validate()).
  * auth      — Bearer API keys or resource-bound OAuth tokens, acting as their
                owner with a live role cap. Both use `rest_api_enabled`.
                Ambient session cookies are never MCP credentials.

Because the tool schemas come from the live registries, a plugin that registers
doctypes/masters (e.g. the internal CRM's lead/contact/activity) is exposed here
automatically — the MCP surface is modular by construction.

Transport: a single POST /api/mcp speaking JSON-RPC 2.0 (request → JSON result;
notifications → 202, no body). That's the minimum a tool-only server needs.
"""
import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from lambda_erp import get_app_version
from api.auth import _lookup_api_key, _setting_enabled, make_auth_principal, API_KEY_CREDENTIAL
from api.mcp_oauth import authenticate_access_token, challenge_header, issuer
from lambda_erp.database import get_db
from api import services
from api import chat as chat_mod
from api.chat import TOOL_HANDLERS, build_tools
from api.tool_permissions import tool_allowed

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-06-18"

# Chat-session-only tools have no meaning without a chat session — keep them out
# of the MCP surface (an MCP client owns its own context).
_EXCLUDE = {
    "retrieve_chat_history",
    "list_chat_attachments",
    "retrieve_chat_attachment",
    "create_custom_analytics_report",
    "get_custom_analytics_report",
    "update_custom_analytics_report",
    "plan_company_setup",
    "apply_company_setup",
}

_READ_ONLY = {
    'list_documents', 'get_document_fields', 'get_document', 'get_master_fields',
    'search_masters', 'get_report', 'get_current_time', 'query_dataset',
    'preview_bank_statement_attachments', 'list_bank_reconciliation_queue',
    'suggest_bank_reconciliation',
}


def _allowed(name: str, role) -> bool:
    return name not in _EXCLUDE and tool_allowed(name, role)


def _require_caller(request: Request) -> dict:
    """MCP accepts API keys and OAuth tokens, never ambient browser cookies."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Connect your account or supply a Bearer API key", headers=challenge_header(request))
    token = auth[7:].strip()
    if token.startswith('erp_oauth_'):
        return authenticate_access_token(request, token)
    db = get_db()
    if not _setting_enabled(db, 'rest_api_enabled'):
        raise HTTPException(403, 'REST API / MCP access is disabled')
    try:
        key, owner, role = _lookup_api_key(db, token)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers=challenge_header(request))
    owner = dict(owner, role=role)
    return make_auth_principal(owner, API_KEY_CREDENTIAL, api_key_id=key['id'])


def _tools(role) -> list:
    out = []
    for tool in build_tools():
        fn = tool["function"]
        if not _allowed(fn["name"], role):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            "annotations": {
                "readOnlyHint": fn['name'] in _READ_ONLY,
                # Unknown extension actions are conservatively treated as writes.
                "destructiveHint": fn['name'] not in _READ_ONLY | {'generate_document_pdf'},
                "openWorldHint": fn['name'] in services.REGISTERED_ACTIONS,
            },
            "securitySchemes": [{"type": "oauth2", "scopes": [
                'erp:read' if tool_allowed(fn['name'], 'viewer') else
                'erp:write' if tool_allowed(fn['name'], 'manager') else 'erp:admin'
            ]}],
        })
    return out


def _call(name: str, args: dict, user: dict):
    # Preserve MCP's unknown-tool error without dispatching an unclassified tool.
    if name not in TOOL_HANDLERS and name != "delete_master" and name not in services.REGISTERED_ACTIONS:
        raise KeyError(name)
    role = user.get("role")
    if not _allowed(name, role):
        return {"error": f"'{name}' is not available to a {role or 'viewer'} key."}
    handlers = dict(TOOL_HANDLERS)
    def generate_pdf(args):
        result = chat_mod._handle_generate_document_pdf(args, user)
        if result.get('download_url'):
            # MCP shares the REST switch; the separate chat API may be off.
            result['pdf_url'] = result['download_url']
        return result
    handlers["generate_document_pdf"] = generate_pdf
    # delete_master needs the caller's role (admin-only); handled by the chat's
    # role-aware variant.
    handlers["delete_master"] = lambda a: chat_mod._handle_delete_master(a, user)
    handlers["list_bank_reconciliation_queue"] = (
        lambda a: chat_mod._handle_list_bank_reconciliation_queue(a, user)
    )
    handlers["suggest_bank_reconciliation"] = (
        lambda a: chat_mod._handle_suggest_bank_reconciliation(a, user)
    )
    handlers["reconcile_bank_transaction"] = (
        lambda a: chat_mod._handle_reconcile_bank_transaction(a, user)
    )
    handlers["undo_bank_reconciliation"] = (
        lambda a: chat_mod._handle_undo_bank_reconciliation(a, user)
    )
    handlers.update(services.registered_action_handlers(user))
    handler = handlers.get(name)
    if handler is None:
        raise KeyError(name)
    return handler(args or {})


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _handle(msg: dict, user: dict):
    """Handle one JSON-RPC message. Returns a response dict, or None for a
    notification (no `id`)."""
    if msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
        return _rpc_error(msg.get('id'), -32600, 'Invalid Request')
    if 'params' in msg and not isinstance(msg['params'], dict):
        return _rpc_error(msg.get('id'), -32602, 'Expected object parameters')
    method = msg.get("method")
    mid = msg.get("id")
    is_notification = "id" not in msg

    def result(payload):
        return None if is_notification else {"jsonrpc": "2.0", "id": mid, "result": payload}

    if method == "initialize":
        return result({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "lambda-erp", "version": get_app_version()},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": _tools(user.get("role"))})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        try:
            out = _call(name, params.get("arguments") or {}, user)
        except KeyError:
            return _rpc_error(mid, -32602, f"Unknown tool: {name}")
        except Exception as e:  # noqa: BLE001 — surface as an MCP tool error, not a 500
            out = {"error": str(e)}
        is_error = isinstance(out, dict) and "error" in out
        return result({
            "content": [{"type": "text", "text": json.dumps(out, default=str, ensure_ascii=False)}],
            "isError": is_error,
        })
    if is_notification:
        return None
    return _rpc_error(mid, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    origin = request.headers.get('origin')
    allowed_origins = {issuer(request), *os.environ.get('MCP_ALLOWED_ORIGINS', '').split(',')}
    if origin and origin not in allowed_origins:
        raise HTTPException(403, 'Invalid Origin')
    version = request.headers.get('mcp-protocol-version')
    if version and version not in {'2025-03-26', PROTOCOL_VERSION}:
        raise HTTPException(400, 'Unsupported MCP protocol version')
    user = _require_caller(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)

    # Keep legacy batch support, but reject malformed messages rather than 500.
    if not isinstance(body, (dict, list)) or (isinstance(body, list) and (not body or any(not isinstance(m, dict) for m in body))):
        return JSONResponse(_rpc_error(None, -32600, "Invalid Request"), status_code=400)
    if isinstance(body, list):
        responses = [r for r in (_handle(m, user) for m in body) if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)
    resp = _handle(body, user)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp)


@router.get('/mcp')
def mcp_no_stream():
    # A stateless JSON transport does not offer a server-initiated SSE stream.
    return Response(status_code=405, headers={'Allow': 'POST'})
