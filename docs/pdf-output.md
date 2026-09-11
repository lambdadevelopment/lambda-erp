# PDF output contracts

All print entry points use `api.pdf.render_document_pdf`. A successful response
requires a registered profile, complete output data, successful rendering and
an extracted-text check of required content. The checks do not save, submit,
post or revalidate live availability. Draft/cancelled/discarded state is printed.
No fallback commercial template or invented currency/amount is allowed.

## Profiles and extensions

The 19 built-in document types have explicit profiles in `api/pdf_profiles.py`:
commercial, logistics, reservation, payment, journal, proposal or record. This
registry feeds `/api/documents/{slug}/fields` and `/api/v1/documents/{slug}/fields`,
`get_document_fields`, the chat prompt and the frontend PDF button.

New plugin doctypes must explicitly declare a profile:

```python
from api.services import register_doctype
from api.pdf_profiles import PDFProfile, DisabledPDF

register_doctype("Visit", Visit, pdf_profile=PDFProfile(
    "record", "Visit summary", fields=("subject", "occurred_at", "body"),
    required=("subject", "occurred_at"),
))
# Or: pdf_profile=DisabledPDF("Internal processing record; no printable output")
```

Existing core overrides inherit their profile. Startup checks profile field and
child-table declarations against the database schema. This tightens the public
registration contract; update plugins before upgrading the core. This change
must be reflected in release versioning, not shipped as an unnoticed patch.
Record profiles are explicit field allowlists, not generic row dumps. Internal
CRM output excludes qualification tasks, contact notes and raw activity metadata.

`record.html` may extend `_record_base.html` and override branding blocks. Existing
`document.html` and `proposal.html` overrides must print the required content,
including `pdf_status` and all relevant amounts. Context providers return `None`
when inapplicable and raise when required content cannot be produced; exceptions
now fail the export. The content check detects omitted text, but does not prove
visual correctness: add actual rendered-PDF tests for each custom layout.

## Generation and downloads

- `POST /api/documents/{slug}/{name}/pdf`: interactive/REST authentication.
- `POST /api/v1/documents/{slug}/{name}/pdf`: chat API authentication.
- `generate_document_pdf`: native chat, API chat and MCP; available to readers.
- Result: artifact ID, owner-scoped URLs, SHA-256, size, MIME, document modification
  stamp, status, warnings and expiry. No descriptor is returned on failure.
- GET with `artifact_id` returns the exact stored bytes. GET without it remains
  a compatible direct render, with the same validation.
- Native chat uses `download_url`; MCP exposes the REST URL as `pdf_url`, so it
  works when the independent chat API is disabled. API chat provides an absolute
  v1 URL. Existing authentication switches and roles still apply.
- PDF failures return HTTP 422 with `detail.code`, `message`, `fields`; generation
  tools return corresponding structured errors. Another owner's artifact is 404;
  an expired artifact is 410.

The private `Generated PDF` table is created on SQLite and PostgreSQL; migration
30 adds the expiry index. Each PDF is capped at 20 MiB. Downloads expire after
seven days and expired rows are deleted during the next generation. Backups
follow normal database retention. This table is outside generic document APIs.
Company/customer labels are resolved at generation time. The saved PDF then
remains unchanged if the source or master data changes.

## Lambda-web delivery and rollout

The chat response's `documents` list contains only successful generation results,
with the latest version per document. A failed final attempt suppresses older
versions. `document_errors` and `tool_results` expose failed attempts. A claimed
PDF URL in prose is insufficient and ungenerated links are removed.

Deploy the matching Lambda-web connector from
`docs/connectors/lambda_erp_chat.py`: it forwards the artifact ID through the
existing ERP proxy, checks SHA-256, treats even an empty `documents` list as
authoritative, and reports delivery errors to the outer LLM. The proxy already
forwards query parameters. Repository template changes do not update users'
saved copies of this script; update those during rollout.

Release backend and frontend together, then upgrade deployment package pins and
plugins. Internal deployment templates/profiles are a companion change that
requires this new core API. No production data correction is part of rollout.

## Verification

`python -m tests.test_pdf_contract` renders every built-in type and covers missing
data, historic records, zero/return amounts, recurring/taxed proposals, custom
layout/provider failures, appendices, multi-page content and artifact ownership,
immutability and expiry. Run on SQLite and PostgreSQL (`LAMBDA_ERP_TEST_DB`).
`python -m tests.test_pdf_channels` exercises authenticated REST/MCP plus actual
chat dispatch with deterministic LLM stubs. Internal `tests.test_pdf_output`
checks the three CRM types, excluded fields and multi-page notes; run the core
PDF suite with `PDF_TEST_INTERNAL=1` and both checkouts on `PYTHONPATH` to test LAD
branding. These tests call no external model and send no customer documents.
