"""Shared, deterministic guidance for the native chat and direct ERP clients."""

from api import services
from api.pdf_profiles import pdf_metadata


WORKFLOW_GUIDANCE = """Discover record types and inspect get_document_fields/get_master_fields before
writes; use their canonical field names, required fields, link targets and PDF requirements.
Resolve existing records by their returned name; never invent identifiers or silently choose
between ambiguous matches. Custom document records use document tools, not master tools.
Ground every written fact in the user's message or a retrieved record for the specific
target entity. This applies to free-text notes, descriptions and activity bodies as well
as structured fields. When the user switches company, lead or document, re-establish the
target and its relationships; do not carry a person's name, employer, sender identity,
contact details or other attributes over from the previous topic without explicit evidence.
A known contact at a company is not proof that they sent an unsigned message. If the source
only identifies a team/company, attribute it to that team/company, leave the person/contact
unset and preserve the source wording. Ask only if identifying the person is necessary to
perform the requested operation; never invent a sender to fill the gap.
Keep dates, times and time zones consistent between structured fields, notes and the final
answer. Convert the source time once; if the source zone is ambiguous, ask before writing.
Create/update, submit, cancel and convert are separate business operations. A successful
draft creation does not imply submission, stock movement or ledger posting. Check returned
status and warnings. Correct validation errors using the field metadata and user information.
Use list/search filters and pagination; an empty or partial search is not proof of zero
stock. Use query_dataset/get_report for totals and stock, with the requested scope and dates.
Generate a fresh PDF after edits. Only a successful artifact descriptor proves generation;
download that artifact, check its SHA-256 and deliver it through the calling application's
attachment mechanism. Do not invent download URLs or reuse a stale document attachment.
After a write timeout the outcome is unknown: inspect the affected records before retrying.
Do not automatically replay the operation through another tool or the chat API.
"""


def get_erp_context(args=None):
    """No LLM, secrets, session state or business-record snapshots are involved."""
    documents = []
    for slug, doctype in sorted(services.SLUG_TO_DOCTYPE.items()):
        cls = services.DOCUMENT_CLASSES.get(doctype)
        meta = services.CHAT_DOCTYPES.get(slug, {})
        documents.append({
            "type": slug, "doctype": doctype,
            "description": meta.get("description", doctype),
            "key_fields": meta.get("fields", []),
            "links": getattr(cls, "LINK_FIELDS", None) or {},
            "pdf": {k: v for k, v in pdf_metadata(doctype).items()
                    if k in {"supported", "reason", "kind", "required_fields", "required_any"}},
            "describe_tool": "get_document_fields",
        })
    masters = []
    for slug, (doctype, display_field) in sorted(services.MASTER_TABLES.items()):
        meta = services.MASTER_METADATA.get(slug, {})
        masters.append({
            "type": slug, "doctype": doctype, "key_field": "name",
            "display_field": display_field,
            "description": meta.get("description", doctype),
            "key_fields": meta.get("fields", []),
            "describe_tool": "get_master_fields",
        })
    return {
        "contract_version": 1,
        "instructions": WORKFLOW_GUIDANCE,
        "documents": documents,
        "masters": masters,
        "capability_source": "tools/list is the caller's permitted operation catalogue; never infer write permission from this type list.",
        "limitations": [
            "Native ERP chat history and attachments are not the calling application's history or attachments.",
            "File-ID operations require files uploaded to this ERP; external application attachment IDs cannot be substituted.",
            "Chat-based custom analytics generation and guided company setup are not exposed through MCP. Use an explicit chat fallback if enabled.",
            "Registered extension actions may call external services or models; inspect each action's description.",
        ],
    }
