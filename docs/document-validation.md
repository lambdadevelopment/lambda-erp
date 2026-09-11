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

Direct SQL, `db.insert` / `set_value`, and custom plugin endpoints
that bypass the document lifecycle do **not** inherit these checks. Master
create/update now delegates to the document lifecycle when the same table is
registered as a document; deletion uses discard when that table supports it.
Other masters reject unknown fields, require their display field and check
declared links. A new
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

## Follow-up workflow guards

The follow-up audit cases are covered by `tests.test_workflow_guards` and the
internal repository's test module of the same name:

- Stock Entries require company, supported movement type, item codes and
  positive finite quantities. Source/target warehouses must match the movement
  and belong to the company; a transfer needs two different warehouses.
- Delivery Notes, Sales Invoices, Purchase Receipts and Purchase Invoices
  require both order and order-line reference when either is given. References
  must identify a submitted order and its line, with matching company, party
  and item. Standalone documents remain supported; converters fill the pair.
- Subscriptions reject unsupported intervals, invalid party/item links, missing
  or invalid plan quantities/rates, and ambiguous company defaults. A zero rate
  is supported explicitly. Processing revalidates and creates the invoice and
  advances the period atomically, with a row lock on PostgreSQL.
- Proposal drafts may be incomplete and return `proposal_incomplete`. Supplied
  quotations must match customer/company and be active. PDF output rechecks
  these relationships and requires customer, company and at least one quote;
  unreadable references are no longer silently omitted. The form shows warnings.
- Master field discovery is also available at `GET /api/masters/{type}/fields`.
  Requirements feed the generated prompt; document-backed masters expose
  transient inputs. NOT NULL and FK violations return 422; duplicates remain
  409. Unknown fields are rejected before SQL on create and update.
- Batch writes retain warnings per item and aggregate them under `_validation`,
  including successful items in a partially failed batch.
- Internal Lead merges validate source/target before moving dependents, reject
  inactive/missing identities or loss of a customer link, and roll back on
  failure. The complete Lead save (including CRM hooks) is atomic. Master writes
  therefore also create customers and timeline entries on conversion.
- New internal call/email activities require explicit direction and a subject
  or body. Supplied contacts must belong to the activity's Lead. Existing
  direction-less records retain legacy behavior on edits. Call/email notes
  attached to Lead writes use `_note_direction` together with `_note_type`.

`Database.atomic()` composes these workflow writes with savepoints. Document
persistence respects its transaction boundary, and PostgreSQL errors inside an
explicit transaction are left to the owner to roll back. Plugins must not call
unconditional commits inside an atomic workflow. This does not make arbitrary
plugin code or all document hooks transactional automatically.

Existing posted records are not rewritten. Invalid legacy drafts must be
completed before their next save/submit/process/export. Roll out the backend
and internal plugin together; the plugin now needs the core `atomic()` method.

## Voucher identity, cumulative quantities and stock valuation

`tests.test_workflow_integrity` exercises the second audit's six groups on
SQLite and PostgreSQL, including concurrent submits and REST responses:

- New document instances insert new identities. An existing name is a conflict
  (HTTP 409); updating requires a loaded draft. Save/submit/cancel/discard check
  the stored status and the loaded modification timestamp under the transaction
  lock. Stale objects cannot overwrite a newer draft or post/cancel twice.
- All document lifecycle writes and their database hooks use `Database.atomic`.
  Ledger helpers respect commit ownership. SQLite takes its write reservation
  before validation; PostgreSQL locks referenced originals, orders and items
  through posting. Nested lifecycle calls leave commit to their outer owner.
- Delivery/receipt and invoice returns require the original company/party,
  negative quantities and original order-line references. Quantities aggregate
  across duplicate rows and previous submitted returns. Invoice stock mode must
  match the original. Converters propose remaining quantities; submit rechecks.
  Active returns block cancellation of their original.
- Referenced order quantities are capped across submitted documents. Physical
  fulfillment includes direct-stock invoices; billing is tracked separately.
  There is currently no implicit overdelivery tolerance. Cancelling a return
  cannot overfill an order after a replacement shipment consumed its capacity.
- Order progress and Bin reserved/ordered quantities are recalculated from
  submitted vouchers, including partial fulfillment, returns and cancellations.
  Migration 28 rebuilds these derived counters and missing stock planning bins
  from existing references. It does not change posted financial or stock ledger
  entries and cannot infer missing historical order references.
- Stock receipts/opening entries require an explicit non-negative finite rate
  in company currency; zero is an intentional zero-valued receipt. Issues use
  current cost; transfers carry the source's actual value into the destination.
  GL entries use posted SLE values per warehouse account. Missing accounts block
  valued posting atomically. Cancellation reverses the posted cost, including
  explicit zero, even if the average cost changed afterwards.
- Payment types are restricted to Receive, Pay and Internal Transfer. Accounts
  must belong to the company, be distinct, non-group and of the required types;
  amounts must be positive and finite. Invalid legacy drafts fail on submit too.

These requirements feed REST field discovery and the generated chat/MCP tool
metadata. They are enforced in the shared model/services, independent of the
prompt. Historical overwritten vouchers or mismatched ledgers require a
separate reconciliation; the migration never guesses corrective postings.

## Settlement references and explicit zero prices

The settlement follow-up is covered by `tests.test_settlement_guards` on SQLite
and PostgreSQL, including simultaneous payment/journal submissions:

- Payments sum allocations per invoice. Journals sum the signed net movement
  per invoice. The result must reduce the invoice/return outstanding without
  crossing zero. Payment direction follows the original invoice's return flag,
  including when a fully settled refund is cancelled. Invalid historical signs
  are reported for reconciliation rather than increased by another settlement.
- Payment and journal references lock their target invoices through validation
  and posting, including cancellation. Company, party, original receivable/
  payable account and payment currency must match. Journals use actual account
  currency amounts; company-currency amounts are translated at the invoice's
  booked rate. A foreign settlement account must match invoice currency and its
  base amount must agree with that booked rate. Other FX effects need separate
  gain/loss rows.
- Sales, purchase and POS invoices cannot be cancelled while a submitted
  Payment Entry or Journal Entry references them. Reverse the settlement first.
  Legacy Journal Entry `reference_type` aliases are normalized on writes and
  recognized by locks, outstanding updates and cancellation checks. Conflicting
  old/new discriminator values are rejected.
- Declared Warehouse, Account and Cost Center document links must belong to the
  document company; this applies to all document write paths, including stock
  movement through delivery/receipt notes and invoices.
- Transaction rates distinguish missing/blank from explicit zero. Missing
  prices may use defaults; zero is preserved across item defaults, price lists,
  pricing rules and tax calculation. Supplied prices must be finite and
  non-negative. Zero-valued purchase receipts/direct-stock invoices keep zero
  as an explicit incoming cost; reversals preserve that distinction too.

These rules are advertised in field metadata and the generated prompt and
enforced on both save and submit. No historical settlement or financial ledger
is automatically rewritten by these changes.
