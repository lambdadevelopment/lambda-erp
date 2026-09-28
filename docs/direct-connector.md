# Direct ERP clients (no ERP chat loop)

`POST /api/mcp` accepts the existing ERP API key when REST API access is enabled.
Standard business tools execute their existing handlers without starting an ERP
chat or invoking its model. Registered extension actions retain their own
implementation, which can include external services or models.

Start with `initialize`, `tools/list`, then `get_erp_context`. The latter returns
contract version 1, live registered document/master types, relationships, compact
PDF capabilities and shared workflow guidance. The native chat uses the same
workflow guidance. Tool availability is determined by the caller's effective role,
not by the type catalogue. Use `get_document_fields`/`get_master_fields` to retrieve
current field schemas and validation requirements before writes.

New registered types and actions are discovered at runtime. No client-side list
of known types should be maintained. Refresh schemas after changing connections,
permissions or deploying extensions. Metadata is guidance, never authorization.

Direct clients own their conversation context. Native chat history, attachments,
guided company setup and chat-based custom analytics generation are not provided
by this contract. External attachment identifiers are not ERP attachment IDs.
Use an explicit, self-contained chat request only when such a fallback is enabled
and appropriate; never automatically repeat a failed or timed-out write in chat.

PDFs: call `generate_document_pdf`, then fetch the exact artifact from its REST
URL using the same ERP credential. Verify its SHA-256 before delivery. A failed
render or download is not successful attachment delivery.

Run `python -m tests.test_erp_context` and `python -m tests.test_mcp`.
