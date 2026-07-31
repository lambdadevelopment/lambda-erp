"""MCP (Model Context Protocol) endpoint — the fine-grained ERP tool surface for
LLM agents (Claude, Codex, …) over MCP's Streamable HTTP transport.

Reuses everything the chat already has, so there's almost no new logic:
  * schemas   — build_tools() (the live, plugin-widened tool list).
  * execution — the same TOOL_HANDLERS (which run validate()).
  * auth      — get_current_user: a Bearer API key acts AS its user at the key's
                role, exactly like the REST API, gated by the same
                `rest_api_enabled` Settings flag. No separate MCP credential.

Because the tool schemas come from the live registries, a plugin that registers
doctypes/masters (e.g. the internal CRM's lead/contact/activity) is exposed here
automatically — the MCP surface is modular by construction.

Transport: a single POST /api/mcp speaking JSON-RPC 2.0 (request → JSON result;
notifications → 202, no body). That's the minimum a tool-only server needs.
"""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from lambda_erp import get_app_version
from api.auth import get_current_user
from api import chat as chat_mod
from api.chat import TOOL_HANDLERS, build_tools

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
# Mirror the REST permission model: reads are viewer+, writes are manager+,
# delete_master is admin-only (the handler also re-checks).
_WRITE = {
    "create_document", "update_document", "submit_document", "cancel_document",
    "discard_document", "convert_document", "create_master", "update_master",
}
_ADMIN = {"delete_master"}


def _can_write(role) -> bool:
    return role in ("manager", "admin", "public_manager")


def _allowed(name: str, role) -> bool:
    if name in _EXCLUDE:
        return False
    if name in _ADMIN:
        return role == "admin"
    if name in _WRITE:
        return _can_write(role)
    return True


def _require_caller(request: Request) -> dict:
    """A valid Bearer API key is mandatory for MCP — no cookie/public fallback.
    get_current_user then validates the key and applies `rest_api_enabled`."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="MCP requires a Bearer API key")
    return get_current_user(request)


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
        })
    return out


def _call(name: str, args: dict, user: dict):
    role = user.get("role")
    if not _allowed(name, role):
        return {"error": f"'{name}' is not available to a {role or 'viewer'} key."}
    handlers = dict(TOOL_HANDLERS)
    # delete_master needs the caller's role (admin-only); handled by the chat's
    # role-aware variant.
    handlers["delete_master"] = lambda a: chat_mod._handle_delete_master(a, user)
    handler = handlers.get(name)
    if handler is None:
        raise KeyError(name)
    return handler(args or {})


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _handle(msg: dict, user: dict):
    """Handle one JSON-RPC message. Returns a response dict, or None for a
    notification (no `id`)."""
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
    user = _require_caller(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)

    # JSON-RPC batch (a list) or a single message.
    if isinstance(body, list):
        responses = [r for r in (_handle(m, user) for m in body) if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)
    resp = _handle(body, user)
    if resp is None:
        return Response(status_code=202)
    return JSONResponse(resp)
