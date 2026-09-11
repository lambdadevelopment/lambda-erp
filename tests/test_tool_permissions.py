"""Authorization at the real chat/MCP dispatcher with synthetic ERP records.

The LLM response is simulated, including calls absent from the advertised tool
list. Business handlers stay real in the persistence checks; no external model
is called. Run: python -m tests.test_tool_permissions (SQLite or test Postgres).
"""

import asyncio
import json
import os
from types import SimpleNamespace as NS
from unittest.mock import patch


async def _turn(calls, user, *, session_id=None):
    from api import chat

    responses = iter([
        (NS(content="", tool_calls=[
            NS(id=str(i), function=NS(name=name, arguments=json.dumps(args)))
            for i, (name, args) in enumerate(calls)
        ]), None),
        (NS(content="Done", tool_calls=[]), None),
    ])
    messages, events = [], []

    async def event(payload):
        events.append(payload)

    # Dispatch scheduling is irrelevant to permission decisions. Inline the
    # thread hop to keep this test deterministic and its fixture DB local.
    async def inline_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch.object(chat, "OpenAI", return_value=NS()), \
         patch.object(chat, "_orchestrator_turn", side_effect=lambda *a: next(responses)), \
         patch.object(chat.asyncio, "to_thread", new=inline_thread), \
         patch.object(chat.demo_limiter, "settle"), \
         patch.object(chat.demo_limiter, "reserve", return_value=(None, None)):
        await chat.run_thinking_loop(
            messages, event, session_id=session_id, user_info=user, max_iterations=2,
        )
    results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    assert len(results) == len(calls), events
    return results, events


def _run(calls, role, **kwargs):
    user = {"name": "permission-test", "role": role} if role is not None else None
    return asyncio.run(_turn(calls, user, **kwargs))


def _snapshot(db):
    tables = (
        "Journal Entry", "Journal Entry Account", "GL Entry", "Stock Ledger Entry",
        "Sales Invoice", "Purchase Invoice", "Payment Entry", "Customer", "Account",
        "Item", "Bank Transaction", "Bank Reconciliation",
    )
    return {table: sorted(json.dumps(dict(row), sort_keys=True, default=str)
                          for row in db.sql(f'SELECT * FROM "{table}"'))
            for table in tables}


def check_tool_permissions():
    os.environ["OPENAI_API_KEY"] = "test-not-used"
    os.environ["LAMBDA_ERP_PLUGINS"] = ""
    from lambda_erp.database import setup
    from lambda_erp.accounting.journal_entry import JournalEntry
    from tests.test_bank_reconciliation import _database_path, _seed
    from api import chat, services
    from api.routers import mcp
    from api.tool_permissions import TOOL_ROLES, tool_allowed

    db = setup(_database_path())
    db.insert("Chat Session", {"id": "permission-test-session", "title": "Permission test"})
    _seed(db)
    journal_data = {
        "company": "Synthetic Co", "posting_date": "2025-02-01",
        "accounts": [
            {"account": "Expense - SYNT", "debit": 10, "credit": 0},
            {"account": "Bank - SYNT", "debit": 0, "credit": 10},
        ],
    }
    draft = JournalEntry(journal_data).save()
    submitted = JournalEntry(journal_data).submit()
    db.commit()

    # Adding an unclassified built-in must fail CI, not silently grant access.
    builtins = {t["function"]["name"] for t in chat.TOOLS} | set(chat.TOOL_HANDLERS)
    assert builtins <= set(TOOL_ROLES), builtins - set(TOOL_ROLES)

    forbidden = [
        ("create_document", {"doctype": "journal-entry", "data": journal_data}),
        ("update_document", {"doctype": "journal-entry", "name": draft.name,
                             "data": {"remarks": "unauthorized"}}),
        ("batch_update_documents", {"doctype": "journal-entry", "updates": [
            {"name": draft.name, "data": {"remarks": "unauthorized"}}]}),
        ("submit_document", {"doctype": "journal-entry", "name": draft.name}),
        ("cancel_document", {"doctype": "journal-entry", "name": submitted.name}),
        ("discard_document", {"doctype": "journal-entry", "name": draft.name}),
        ("convert_document", {"doctype": "sales-invoice", "name": "SINV-TEST",
                              "target_doctype": "delivery-note"}),
        ("create_master", {"master_type": "customer", "data": {"customer_name": "Forbidden"}}),
        ("update_master", {"master_type": "customer", "name": "CUST-001",
                           "data": {"customer_name": "Forbidden"}}),
        ("revalue_currencies", {"company": "Synthetic Co", "date": "2025-02-01", "post": True}),
        ("delete_master", {"master_type": "customer", "name": "CUST-001", "confirmed": True}),
        ("apply_company_setup", {"company": "Synthetic Co", "confirmed": True}),
        ("import_bank_statement_attachments", {"confirmed": True}),
        ("reconcile_bank_transaction", {"confirmed": True}),
        ("undo_bank_reconciliation", {"confirmed": True}),
    ]
    before = _snapshot(db)
    for role in ("viewer", None, "unrecognized"):
        # Session scoping must not overwrite permission guards.
        results, events = _run(forbidden, role, session_id="permission-test-session")
        assert all("not available to role" in r.get("error", "") for r in results), results
        assert all(not e["success"] for e in events if e["type"] == "tool_result"), events
        assert _snapshot(db) == before, role
        for name, args in forbidden:
            result = mcp._call(name, args, {"role": role})
            assert "error" in result, (role, name, result)
        assert _snapshot(db) == before, role

    # Explicit expectations protect the role policy itself, not just its use.
    writers = {"create_document", "update_document", "batch_update_documents",
               "submit_document", "cancel_document", "discard_document", "convert_document"}
    managers = {"create_master", "update_master", "revalue_currencies",
                "import_bank_statement_attachments", "list_bank_reconciliation_queue",
                "suggest_bank_reconciliation", "reconcile_bank_transaction", "undo_bank_reconciliation"}
    admins = {"delete_master", "plan_company_setup", "apply_company_setup"}
    for role in ("viewer", "manager", "admin", "public_manager", None, "unrecognized"):
        names = {t["function"]["name"] for t in chat.build_tools({"role": role})}
        for name in writers | managers | admins | {"get_document"}:
            expected = (
                role in {"admin", "manager", "public_manager"} if name in writers else
                role in {"admin", "manager"} if name in managers else
                role == "admin" if name in admins else
                role in {"viewer", "manager", "admin", "public_manager"}
            )
            assert tool_allowed(name, role) == expected, (name, role)
            assert (name in names) == expected, ("advertised", name, role)

    # Confirm the dispatcher reaches allowed writes for manager/admin/demo,
    # and never reaches new unclassified handlers even if the model invents one.
    for role in ("viewer", "manager", "admin", "public_manager", None, "unrecognized"):
        reached = []
        probe_names = ["create_document", "create_master", "revalue_currencies", "future_write"]
        def probe(name):
            def handler(args):
                reached.append(name)
                return {"ok": True}
            return handler
        with patch.dict(chat.TOOL_HANDLERS, {name: probe(name) for name in probe_names}):
            _run([(name, {}) for name in probe_names], role)
        expected = [name for name in probe_names if (
            name == "create_document" and role in {"manager", "admin", "public_manager"}
            or name in {"create_master", "revalue_currencies"} and role in {"manager", "admin"}
        )]
        assert reached == expected, (role, reached, expected)
        assert not mcp._allowed("future_write", role)

    # Extension actions retain their declared permissions through both surfaces.
    with patch.dict(services.REGISTERED_ACTIONS, {}, clear=True):
        services.register_action(
            "permission_test_action", handler=lambda args: {"ok": True},
            description="Synthetic manager action", parameters={}, minimum_role="manager",
        )
        for role, allowed in (("viewer", False), ("manager", True), ("admin", True),
                              ("public_manager", False), (None, False)):
            results, _ = _run([("permission_test_action", {})], role)
            assert (results[0].get("ok") is True) == allowed, (role, results)
            assert mcp._allowed("permission_test_action", role) == allowed

    # Real writes still work, including actual balanced GL postings and reversal.
    for role in ("manager", "admin"):
        results, _ = _run([("create_document", {"doctype": "journal-entry", "data": journal_data})], role)
        assert "name" in results[0], results
        name = results[0]["name"]
        results, _ = _run([("submit_document", {"doctype": "journal-entry", "name": name})], role)
        assert results[0]["docstatus"] == 1, results
        assert len(db.sql('SELECT name FROM "GL Entry" WHERE voucher_no = ?', [name])) == 2
        results, _ = _run([("cancel_document", {"doctype": "journal-entry", "name": name})], role)
        assert results[0]["docstatus"] == 2, results
    results, _ = _run([("get_document", {"doctype": "journal-entry", "name": draft.name})], "viewer")
    assert results[0]["name"] == draft.name
    print("PASS: chat/MCP execution permissions, role matrix, plugin actions, unchanged records, and real GL writes")


if __name__ == "__main__":
    check_tool_permissions()
