# Document validation across channels

Document writes from the web form, REST, chat tools and MCP share
`api.services.create_document` / `update_document`. These reject unknown parent
and child fields before writing. The document's `validate()` and shared
`validate_document_requirements()` run on **save and submit**, followed by link
validation. Conversion and other code using `Document.save()` / `submit()` get
the same business checks. Invalid requests raise `ValidationError`; document
REST returns HTTP 422, and chat/MCP receive an error rather than a saved record.

## Enforced minimums

- Active reservations must identify an `asset`, or explicitly choose
  `allocation_mode: "Pool"` with item, yard and positive whole quantity.
  Setting an asset sets mode `Unit` and quantity one. Ambiguous units must be
  resolved with the user; omission is not an implicit pool booking.
- An active reservation needs a party, or a purpose for an internal block.
  Customer hire uses `party_type: "Customer"` and a valid customer ID.
  `Out` requires an asset. The asset must be usable, and its yard must match
  the reservation. Voucher type and number must be supplied together and
  resolve to a supported document.
- Assets need an item and home yard (the item's default yard can fill it).
- Quotations, sales/purchase orders, sales/purchase/POS invoices, delivery
  notes and purchase receipts require company and valid item quantities.
  Each row needs an item code or a description/name. Quantities must be finite
  and positive, except negative quantities on returns. Zero-priced lines are
  still supported. Existing party and nonempty-items checks remain in force.
- Stock-moving rows referencing stock items require a warehouse, including
  invoices with `update_stock`. Missing warehouses cannot silently omit ledger
  entries. This is checked on drafts and again before submission.
- Payment reference rows require both document type and name. An empty
  reference list remains a supported on-account payment.

## Metadata, prompts and feedback

`GET /api/documents/{type}/fields` and the `get_document_fields` chat/MCP tool
return the same metadata, including `requirements`, `dynamic_link_fields`,
`input_fields` and `child_input_fields`. Registered document requirements are
also inserted into the ERP system prompt on every turn. The prompt instructs
the assistant to ask about ambiguity and communicate errors and warnings.
Conditional descriptions accompany the actual checks; descriptions alone do
not implement a validator. Keep both in sync when adding a rule.

Create, update and get responses contain the **persisted document** plus
`_validation.warnings`. An active pool reservation returns warning code
`asset_unassigned`; assignment clears it. The form shows this warning and the
fleet calendar displays unassigned bookings on separate rows. A successful
pool booking reserves capacity; it does not assign a particular machine.

`POST /api/v1/chat` adds `tool_results`, with tool name, success, error and
warnings. HTTP 200 means the chat turn completed, not that every attempted
business operation succeeded. Callers should inspect these results alongside
the reply; an error may be followed by a successful corrected attempt.
Actual tool arguments and results are persisted in `Chat Message` under
`role="tool"`, with `message_type="tool_call"` / `"tool_result"`.
These audit records are excluded from conversational history replay and the
normal transcript. They record observable actions, not private model reasoning.

## Compatibility and extensions

Migration 27 adds `Reservation.allocation_mode`, marking existing rows `Unit`
when an asset is set and `Pool` otherwise. It does not guess missing parties,
purposes, yards or machines, and it does not repair historical documents.
Existing invalid active bookings must be completed when edited. Returned and
cancelled bookings can still release legacy holds without selecting a now
retired asset or supplying a missing party.

Integrations must send supported field names. The response-only `_validation`
annotation is accepted and stripped on round trips. Plugins that intentionally
consume transient fields must declare `INPUT_FIELDS` or `CHILD_INPUT_FIELDS`;
misspellings remain errors. Static frontend field configuration does not
automatically gain new UI controls from backend metadata.

Direct SQL, `db.insert` / `set_value`, master CRUD, and custom plugin endpoints
that bypass the document lifecycle do **not** inherit these checks. A new
document type can declare `REQUIRED_FIELDS`, implement conditional checks in
`validate()`, and expose them in `CONDITIONAL_REQUIREMENTS`. This change is not
a completeness audit of all application workflows.

Both the Python core and frontend package must be released and their versions
updated in consuming deployment repositories before these changes run live.

## Verification

`tests.test_document_requirements` covers lifecycle validation, persisted
responses, metadata/prompt propagation, MCP, trace records and migration
idempotence on SQLite and PostgreSQL. `tests.test_availability_api` exercises
the authenticated REST path and pool-to-unit assignment;
`tests.test_chat_api` checks structured tool feedback. The main ledger suite
also asserts missing warehouses and incomplete payment references cannot post.
