# PDF output contracts

All print entry points use `api.pdf.render_document_pdf`. A successful response
requires a registered profile, complete output data, successful rendering and
an extracted-text check of required content. The checks do not save, submit,
post or revalidate live availability. Draft, cancelled and discarded state is
printed; issued documents carry no state line.
Each line prints the item name written on it; the Item master's name only
fills a line that has none.
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
including `pdf_state_notice` (set only for Draft, Cancelled and Discarded; empty
for issued documents) and all relevant amounts. A deployment can localise the
notice with `register_pdf_state_labels({"Draft": "Entwurf"})`. Context providers return `None`
when inapplicable and raise when required content cannot be produced; exceptions
now fail the export. The content check detects omitted text, but does not prove
visual correctness: add actual rendered-PDF tests for each custom layout.

## PDF templates

Offers, sales orders, sales invoices (incl. credit notes), purchase orders,
delivery notes, proposals and reservations print with a PDF template
(`api/pdf_templates.py` `DOCTYPES`): HTML + CSS with Jinja. A template belongs to
one document type and receives the same context as that type's built-in layout
(`STANDARD_FILES`): `document.html` for offers, orders, invoices and purchase
orders, `delivery_note.html` (the same context, no prices), `proposal.html` and
`reservation.html`. `standard` is the deployment's own file for the type (a
plugin override wins); it is read-only and rendered as trusted code. A template
may `{% include %}` / `{% extends %}` the built-in files (the sandbox's loader
reads only those). Other templates live in the database:

- `PDF Template` holds a working copy (`draft_html`, `assets`), edited in place
  without versions, and the published layout (`live_html`, `live_assets`).
  Publishing test-renders the working copy against an offer and rejects syntax
  errors, missing required content and blocked resources.
- A draft prints with its `pdf_template` (else the default from the
  `pdf_template:<Doctype>` setting, else `standard`) and the published layout.
  Proposals and reservations are never submitted, so they always print this
  way and nothing is pinned or archived for them.
- Delivery notes and reservations check only their core content (number,
  state, party, lines: `core_tokens` in `pdf_contract.prepare`), not every
  field of the generic record layout they used before.
- Content a deployment's context provider adds (e.g. `qr_bill_img` on
  invoices) is learned from the provider's results, or registered with
  `pdf_templates.register_pdf_template_requirement`; publishing reports a
  layout that never uses it (`missing_variables`) and the document warns
  (`pdf_variable_not_printed`).
- Submitting pins `pdf_template_version` (server-managed) to a `PDF Template
  Version` row keyed by the layout's content hash, created on first use, and
  stores the PDF as issued in `PDF Archive`, inside the submit transaction. A
  custom layout that can't print the offer blocks the submit; the built-in one
  logs and submits without an archive, as before. A submitted offer returns the
  archived file; a cancelled one renders its pinned version with the notice.
- Bulk seeding (the demo simulation, `/api/setup/seed-history`) runs inside
  `pdf_templates.pin_only()`: offers are pinned but not rendered or archived,
  and print on demand from their pinned version.
- Versions are never deleted. A template is deletable only while no version and
  no draft offer references it; otherwise it can be archived.
- Assets (images, fonts) are content-addressed in `PDF Template Asset`, so a
  version keeps the exact bytes it printed with.

User templates render in Jinja's `ImmutableSandboxedEnvironment`. WeasyPrint
gets a URL fetcher that serves `data:` URLs, the template's assets and non-HTML
files beside the built-in templates (a plugin logo); everything else (http,
file, internal hosts) is blocked and reported. Rendered HTML is capped at 5 MB.
There is no render timeout: editing is limited to managers, admins and the
shared demo account (`public_manager`), which can't upload or remove assets.

`view_pdf_pages` (api/pdf_pages.py) lets the chat model and MCP clients see a
rendered PDF: a document as it downloads, a template's draft/live layout
against an offer, or a PDF or image the caller uploaded to the chat
(`attachment_id`, only their own uploads; an image counts as one page). It rasterizes up to four pages per call with pypdfium2 (110
dpi; `area` top/middle/bottom crops a third at 220 dpi) and returns them under
`_images`. The chat loop shows them to the model as image input for the rest
of the turn and drops them before saving the tool trace; MCP returns them as
image content; REST returns base64 PNG. Template `context_reference` carries
the renderer notes the chat needs (fonts, page backgrounds, margin boxes).

Printed texts: an offer has `intro_text` and `remarks` (closing text /
terms). Both render through the notes markup (api/remarks_md.py) into
`intro_html` / `remarks_html`, after the placeholders in
`api.pdf.TEXT_PLACEHOLDERS` are substituted (plain substitution, not Jinja).
Templates choose where each prints. The defaults new offers start with are
Settings rows `pdf_text_default:Quotation:<field>`, copied in by a
`Quotation:before_save` hook on first save when the field wasn't sent. A
document warning (`pdf_<field>_not_printed`) flags an offer whose layout never
uses a text it has; publishing reports the same as `missing_texts`.

Line deductions: a line with `discount_label` and `discount_amount` > 0
prints its full price (`item.gross_rate` / `item.gross_amount`, from
`api.pdf_contract.line_deduction`) and a sub-line with the label and
`−item.discount_total` (the deduction for the whole line). The export check
accepts either the line's net amount or the full amount, deduction and label
(`AnyOf` tokens), so older layouts keep exporting; a document warning
(`pdf_line_deduction_not_printed`) and `missing_texts: ["line_deduction"]` on
publish flag a layout that never uses `discount_label`.

Fonts (api/pdf_fonts.py): WeasyPrint finds fonts through fontconfig, so what a
template can print with is what is installed (the Docker image adds the
`fonts-*` packages) plus fonts uploaded as template assets. The field
reference lists the installed families from `fc-list`, and which common names
print as a metric-compatible twin (`fc-match`: Helvetica/Arial → Liberation
Sans, Calibri → Carlito, ...). Outside Docker the list is whatever the host
has. In the chat, `inspect_pdf_fonts` reads the fonts an uploaded PDF uses
from its font dictionaries and embedded font programs, and
`add_attachment_to_pdf_template` turns an uploaded font or image into an
asset, returning an `@font-face` rule. Fonts are never extracted from a PDF:
embedded fonts are usually subsets, and embedding rights don't cover reuse.

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
