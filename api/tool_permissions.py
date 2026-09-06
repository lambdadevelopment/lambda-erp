"""Shared execution policy for ERP tools exposed through chat and MCP.

Every built-in tool must be classified here. Unknown tools and unknown callers
are denied; registered extension actions carry their own explicit policy.
Session ownership and other resource checks still belong to their handlers.
"""

from api import services


_READERS = frozenset({"viewer", "manager", "admin", "public_manager"})
_DOCUMENT_WRITERS = frozenset({"manager", "admin", "public_manager"})
_MANAGERS = frozenset({"manager", "admin"})
_ADMINS = frozenset({"admin"})

TOOL_ROLES = {
    **dict.fromkeys((
        "list_documents", "get_document_fields", "get_document",
        "get_master_fields", "search_masters", "get_report", "get_current_time",
        "retrieve_chat_history", "list_chat_attachments", "retrieve_chat_attachment",
        "preview_bank_statement_attachments", "query_dataset",
        # Personal report drafts follow the viewer-accessible reporting API.
        # Their handlers enforce ownership; these do not post ERP transactions.
        "create_custom_analytics_report", "get_custom_analytics_report",
        "update_custom_analytics_report",
    ), _READERS),
    **dict.fromkeys((
        "create_document", "update_document", "batch_update_documents",
        "submit_document", "cancel_document", "discard_document", "convert_document",
    ), _DOCUMENT_WRITERS),
    **dict.fromkeys((
        "create_master", "update_master", "revalue_currencies",
        "import_bank_statement_attachments", "list_bank_reconciliation_queue",
        "suggest_bank_reconciliation", "reconcile_bank_transaction",
        "undo_bank_reconciliation",
    ), _MANAGERS),
    **dict.fromkeys((
        "delete_master", "plan_company_setup", "apply_company_setup",
    ), _ADMINS),
}


def tool_allowed(name: str, role: str | None) -> bool:
    if role not in _READERS:
        return False
    if name in TOOL_ROLES:
        return role in TOOL_ROLES[name]
    return services.registered_action_allowed(name, role)


def tool_permission_error(name: str, role: str | None) -> dict:
    return {"error": f"Tool '{name}' is not available to role '{role or 'unauthenticated'}'."}
