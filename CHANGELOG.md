# Changelog

All notable changes to Lambda ERP are recorded here.

The two packages released from this monorepo share one version and ship
together: **`lambda-erp`** (PyPI, backend) and
**`@lambda-development/erp-core`** (npm, frontend).

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The extension
seams (registries, hooks, and the public package imports) are the
semver-governed public surface — a breaking change to a seam is a major bump.

## [Unreleased]

## [0.2.3] - 2026-07-15

### Added
- **German localization packs (DATEV).** Two new `country="de"` variants —
  `de.skr03` (process-ordered, the most widely used German SME chart) and
  `de.skr04` (balance-sheet-ordered, DATEV's recommendation for new companies).
  Each hand-authored from the published DATEV standard (cross-checked against the
  LGPL Odoo `l10n_de` data): a curated ~70-account core with German titles
  carrying their SKR number, EUR, and an Umsatzsteuer/Vorsteuer tax hook at the
  federal rates (19 % Regelsteuersatz, 7 % ermäßigt). A bare `country="de"`
  resolves to SKR03; `de.skr04` selects the modern chart. This is the first use
  of the `country[.variant]` key axis the pack registry was built for — no engine
  change, the shared VAT builder lives in `packs/de_common.py`. Sector overlays
  render in German on the DATEV chart, same as the Swiss pack.

## [0.2.2] - 2026-07-15

### Changed
- **Idempotent company setup.** `apply_company_setup` now *converges* toward the
  desired chart instead of refusing when the company already exists. It creates
  only the accounts that are missing, fills only the company defaults that are
  still empty (never overwriting a configured company), and creates the tax
  templates / cost center only if absent — so setup is safe to run alongside an
  existing deployment and to re-run. It returns a reconciliation report
  (`accounts_created` vs `accounts_skipped`, `defaults_set` vs
  `defaults_left_untouched`). When the company is already configured, or its
  currency differs from the setup (which would mix two charts), it returns
  `needs_confirmation` and proceeds only with `confirm_existing` after the user
  insists; `plan_company_setup` surfaces this up front via an `existing` block.
  Replaces the previous hard guard / `force` flag.

### Fixed
- **Chat bold headings.** A line the assistant emits as bold (`**Heading**`) is no
  longer mistaken for a `*` bullet — the chat's list detection now requires
  whitespace after the marker, so the bold renders instead of leaking stray `*`/`•`.

## [0.2.1] - 2026-07-15

### Fixed
- **Localized sector accounts.** Sector-profile overlay accounts are now created
  in the localization pack's language, so on the Swiss (German) chart they read in
  German — e.g. *Beratungserlöse*, *Aufwand für Fremdleistungen* — instead of the
  neutral English names, matching the German base chart.
- **Services income default.** The services profile pointed its income default at
  *Service Revenue*, an account that exists only in the generic chart; it now
  points at the profile's own revenue account, so the default resolves on every
  jurisdiction (it was dangling on the Swiss chart).
- **Chat line breaks.** Multi-line messages now preserve their line breaks in the
  user's own message bubble.

## [0.2.0] - 2026-07-15

### Added
- **Guided, sector-aware company setup.** New `lambda_erp/accounting/setup/`
  package builds a company's chart of accounts through a three-layer design:
  a universal `account_type` spine, pluggable **localization packs** keyed
  `country[.variant]` (a registry with a permanent generic/international
  fallback), and seven jurisdiction-independent **sector profiles** (services,
  retail/POS, hospitality, distribution, import/export, manufacturing,
  construction) that attach accounts to pack *anchors* — never to literal codes,
  so they carry across every jurisdiction unchanged. Adding a country is a new
  pack module with no engine change. Exposed as `plan_company_setup` (preview,
  no writes) and `apply_company_setup` (creates chart + defaults + tax + cost
  center). The registries and profiles are semver-governed extension seams.
- **Switzerland localization pack.** First jurisdiction pack beyond generic: the
  Swiss *Kontenrahmen KMU* (German account names with KMU numbers, CHF), with an
  MWST tax hook that builds Sales/Purchase tax templates at the current rates
  (8.1 % / 2.6 % / 3.8 %, from 2024). Selected via `country="CH"`; the seven
  sector profiles apply on it unchanged.
- **Chat setup wizard.** Admin-only `plan_company_setup` / `apply_company_setup`
  chat tools walk the user through the plan, surface each sector "big decision"
  for explicit confirmation, and only create anything on approval. `POST
  /api/setup/company` now routes through the engine (the company `country`
  selects the jurisdiction, an optional `sector` applies the overlay); with
  neither it is byte-identical to the previous chart. Added `GET
  /api/setup/profiles` and `POST /api/setup/plan`.
- **Tutorial "Get started".** A prominent guided-setup card on `/tutorial` with a
  "Get started in chat" button that launches the wizard (en/de/fr).
- **API keys grouped by owner.** The admin API-keys page now groups keys by the
  user they belong to (your own first, clearly labelled), with an in-page
  confirmation dialog before revoking or deleting another user's key.

## [0.1.38] - 2026-07-13

### Changed
- **Channel-aware chat replies.** `run_session_turn` now takes a `channel`
  (`web` | `api`); `POST /api/v1/chat` runs as `api`. On that channel the agent is
  told its reply is relayed to an external application (not the ERP web UI), so it
  names records in plain text and stops emitting `/app`, `/masters`, and
  `/reports` links that are dead outside the ERP. It still emits the canonical
  `/api/documents/{slug}/{name}/pdf` reference for documents (phrased as an
  attachment, not a link to click). The web/WebSocket channel is unchanged.

### Added
- **Structured `documents` in the chat API response.** `POST /api/v1/chat` now
  returns a `documents` array — each referenced PDF as
  `{doctype, name, pdf_url}`, where `pdf_url` is an absolute, Bearer-gated
  `/api/v1/documents/.../pdf` URL the caller can fetch directly. This is the
  machine-readable contract an orchestrator uses to attach PDFs, instead of
  re-parsing the reply prose.

## [0.1.37] - 2026-07-10

### Added
- **Document read endpoints on the chat API.**
  `GET /api/v1/documents/{doctype-slug}/{name}/pdf` (rendered PDF) and
  `GET /api/v1/documents/{doctype-slug}/{name}` (structured JSON), Bearer-key-gated
  like the rest of the chat API (inherit the `chat_api_enabled` flag + key role).
  Chat replies link to a document's PDF, but the web `/api/documents/...` route is
  cookie-gated and unreachable by an API caller — these mirror it so an
  orchestrator (and the iOS app) can fetch the bytes with its key. Read-only
  (`viewer` role suffices); missing document → 404, unknown doctype → 422.

### Fixed
- **Self-host the Inter font.** The Tailwind preset asks for `Inter`, but the font
  was only loaded by the demo's Google Fonts `<link>` (not part of the published
  package), so consumer apps fell back to system-ui. `@fontsource/inter` is now a
  runtime dependency and `@import`ed in `src/index.css` (shipped via
  `exports["./styles.css"]`), so every consumer inherits Inter with no extra setup.

## [0.1.36] - 2026-07-10

### Added
- **Programmatic chat API (opt-in).** A synchronous REST surface over the chat
  agent so an external application can hold a conversation with an ERP instance.
  Off by default — an admin enables it (Settings → Chat API, `chat_api_enabled`)
  and issues **Bearer API keys** (hashed at rest, shown once, per-key role
  `viewer`/`manager`/`admin`, revocable). New `POST /api/v1/chat` plus
  `GET`/`DELETE /api/v1/chat/sessions`, and admin `GET`/`POST /auth/api-keys`
  (+ `.../revoke`). Conversations are **stateless by default** — each call answers
  from the current message only (persisted to a rolling session for audit, not
  replayed); passing a `session_id` opts into replaying that session's history.
  The WebSocket chat and the REST API now share one `run_session_turn` driver
  (the agent loop is unchanged). Disabled instances 404 the whole surface. New
  `Api Key` table (SQLite + Postgres); CI covers the API on both backends. i18n
  en/de/fr. See [`docs/chat-api.md`](docs/chat-api.md).

## [0.1.35] - 2026-07-01

### Added
- **"Set a password" for social-login-only accounts.** A user created via Google
  (or Apple) has no password; Settings now shows a "Set a Password" card (no
  "current password" field) so they can add an email+password fallback in case
  they lose access to their provider. New `POST /auth/set-password` (authenticated;
  refuses if a real password already exists — that's change-password — or for the
  demo account). `/auth/me`, `/auth/login`, and `/auth/register` now return
  `has_password`, and Settings shows "Set a Password" when it's false, otherwise
  the usual "Change Password". i18n en/de/fr.

## [0.1.34] - 2026-07-01

### Changed
- **No signup screen on invite-only instances.** When registration is closed
  (not first-run and public signup off), the login page no longer shows the
  "Register" link, which previously led to a dead-end signup form that failed on
  submit. Invite-only instances now present sign-in only (password + any
  configured social providers); invited users still register via their invite
  link, which can be completed with a password or by continuing with Google /
  Apple.

## [0.1.33] - 2026-07-01

### Fixed
- **Social-login table crashed startup on PostgreSQL.** The new
  `User OAuth Identity` table (0.1.32) had a column named `user`, which is a
  reserved word in PostgreSQL — `CREATE TABLE` failed at boot on Postgres
  deployments (SQLite accepts it, so it slipped through local testing). Renamed
  the column to `user_name`. SQLite-only deployments were unaffected. Verified
  against the PostgreSQL validation suite.

## [0.1.32] - 2026-07-01

### Added
- **Social login with Google and Apple (`api/oauth.py`).** Users can sign in
  with Google or Apple instead of an email + password. OAuth/OIDC is layered on
  top of the existing password auth without changing what a session is: once a
  provider proves identity, the same `lambda_erp_token` JWT cookie is minted, so
  everything downstream is unchanged and password login keeps working. New
  endpoints `GET /auth/{provider}/login` and `GET|POST /auth/{provider}/callback`
  (Google and Apple), plus `GET /auth/oauth/providers` and
  `GET /auth/oauth/identities`. No new dependency — httpx does OIDC discovery +
  token exchange, python-jose validates the ID token against the provider JWKS
  and signs Apple's ES256 client secret. Provider config is read from env; a
  provider is simply disabled (its button hidden) when its vars are absent, so
  local/dev needs no OAuth setup.
- **Login page "Continue with Google / Apple" buttons** and a **Linked Accounts**
  panel in Settings to link a provider to an existing account (the sanctioned
  password → social switch, keeping the same user id and history). Invites can be
  accepted via OAuth. New table `User OAuth Identity` (created at boot — no
  migration; `hashed_password` stays NOT NULL, OAuth-only users carry a
  non-matchable sentinel). i18n in en/de/fr. See `docs/social-login-plan.md`.

### Fixed
- **Read-only Notes / Terms preserved line breaks.** After a quotation became
  Open, the read-only display of the Notes / Terms field collapsed its line
  breaks; textarea fields now render with `whitespace-pre-line`.

## [0.1.31] - 2026-06-25

### Added
- **Horizontal rule in the Notes / Terms markup.** A line of 3+ dashes (`---`)
  now renders as a full-width thin divider in the notes block. Like the rest of
  the markup, the core emits a semantic `<div class="rm-hr">` and templates
  style it (the default is a light grey; a branded template can match it to its
  own separators). Documented in the chat system prompt and the formatting-help
  tooltip (en/de/fr).

## [0.1.30] - 2026-06-24

### Fixed
- **Link-field dropdown was clipped inside the line-items table.** The
  search-as-you-type picker (item, customer, warehouse, account…) is now
  rendered in a portal with fixed positioning, so it's no longer occluded by
  the line-items table's horizontal-scroll container. It follows the input on
  scroll/resize.

### Changed
- **Chat assistant now knows about quotation line `frequency` and the notes
  pipe rule.** The system prompt documents the recurring-offer `frequency`
  field (One-time/Monthly/Quarterly/Half-Yearly/Yearly) and when to use it
  versus the cosmetic `>>` note, and spells out the `>> left | amount` markup
  rule explicitly (the `|` pipe splits the frequency column from the amount
  column; both are literal, not interpreted).

## [0.1.29] - 2026-06-24

### Added
- **Billing frequency on quotation lines (recurring offers).** A quotation
  line now carries a `frequency` — `One-time` (default) or a recurring cadence
  (`Monthly`/`Quarterly`/`Half-Yearly`/`Yearly`, matching the Subscription
  billing intervals). When an offer mixes one-time and recurring lines, the
  headline totals (and tax rows) reflect the **one-time** part only, and each
  recurring cadence is totalled separately with its own MWSt — so a recurring
  line never inflates the one-time grand total. `generate_pdf()` exposes the
  per-period breakdown as `recurring_summary` and a `show_frequency` flag for
  templates. Quotations still post nothing to the ledger; this is presentation
  + captured intent only. New `frequency` column on `Quotation Item` (migration
  18) and a Frequency picker on quotation line items in the form.

## [0.1.28] - 2026-06-24

### Added
- **Editable per-line description on document line items.** The line-item
  table now has a Description column, so a quotation/order/invoice line can
  carry its own blurb (like a Proposal position). It defaults from the Item
  master on save — leaving it blank falls back to the master, so it never
  wipes the default — and a typed value overrides it for that document only.
  The backend already stored and defaulted this field; this exposes it in the
  form.

## [0.1.27] - 2026-06-24

### Changed
- **Notes / Terms field is now prominent in the document form.** On
  quotations, sales orders, invoices, etc. the field moves onto its own
  full-width row below the other fields and is taller (7 rows), with an info
  icon beside the label that explains the supported markup (`# heading`,
  `*italic*`/`**bold**`, and the right-aligned `>> Period | Amount` price line)
  on hover or click. Translated for en/de/fr.

## [0.1.26] - 2026-06-24

### Added
- **Lightweight markup for document Notes / Terms.** The `remarks` field on
  quotations, sales orders, and invoices now renders on the PDF from a small
  markup subset — `# Heading`, `*italic*`/`**bold**`, and a right-aligned
  `>> Period | Amount` price line that sits beside the description above it —
  so a closing block (recurring services, conditions, a sign-off) reads like a
  real offer instead of flat text. `generate_pdf()` exposes the result as
  `remarks_html` (safe, HTML-escaped); templates render it with `| safe` and
  style the emitted classes (`.rm-block`, `.rm-h`, `.rm-p`, `.rm-amt`), so a
  branded template can restyle the same markup. The chat assistant knows the
  syntax and uses it when composing customer-facing notes. See
  `api/remarks_md.py`.

### Changed
- **Chat assistant now discovers master fields instead of guessing.** The
  `get_master_fields` tool and the system prompt steer the assistant to look up
  a master's real columns before a `create_master`/`update_master` when it's
  unsure where a value belongs, and the "fields ignored" warning points back to
  it. Fixes cases like a customer's contact person being dropped (or a contact
  phone misfiled into the company `phone`) because the model assumed there was
  no field for it — `create_master` now spells out the contact-person mapping
  with a worked example.

## [0.1.25] - 2026-06-23

### Fixed
- **Master search was case-sensitive in production.** `search_masters` used a
  bare `LIKE`, which SQLite treats case-insensitively but Postgres does not — so
  a search that worked in dev returned nothing in prod unless the exact stored
  casing was typed. Matching now lowercases both sides
  (`lower(col) LIKE lower(?)`), identical on both backends.

### Added
- **Master search now covers all text columns and tolerates typos.** It searches
  every text field (name, display name, and address fields like city/zip),
  discovered from the live schema so new columns are searchable automatically,
  and falls back to fuzzy matching for misspellings. Large free-text columns
  (e.g. item `description`, templates) are skipped by default and searchable on
  demand via the new optional `fields` argument, which also narrows a search to
  specific columns.
- **`get_master_fields` chat tool.** Lists a master type's real columns (and
  which are searched by default), so the assistant targets existing fields
  instead of guessing.

## [0.1.24] - 2026-06-22

### Added
- **Discard a Proposal from the UI.** The Proposal builder now has a Discard
  button (existing proposals only, with a confirm). It soft-deletes the proposal
  (status `Discarded`, hidden from the list) via the standard discard path —
  the custom builder page previously had no way to remove a proposal.

## [0.1.23] - 2026-06-20

### Fixed
- **Chat assistant crashed on every message** in 0.1.22. The Proposal section
  added to the system prompt contained a literal `{…}` JSON example, but the
  prompt is an f-string, so the braces were parsed as format fields and
  `build_system_prompt()` raised `ValueError: Invalid format specifier`. The
  example is now brace-free prose. Also taught the assistant the German names
  (Sammelofferte/Sammelofferten) so "zeig mir unsere Sammelofferten" maps to the
  proposal doctype instead of asking which order type is meant.

## [0.1.22] - 2026-06-20

### Added
- **Proposals in the chat assistant.** The LLM can now create, read, update, and
  list Proposals (Sammelofferten) through the document tools — e.g. "combine
  QTN-0001 and QTN-0003 into one offer for Plus Medica AG". The system prompt
  documents the Proposal shape (the `quotations[]` child table referencing
  existing quotations, not `items`) and that it is print-only.

### Changed
- A Proposal can no longer be submitted (it's print-only); an attempted submit
  now raises a clear error instead of silently flipping docstatus.

### Added
- **Proposal (Sammelofferte).** A new print-only document that assembles several
  *independent* quotations into one branded, customer-facing PDF — each rendered
  as a lettered position (A, B, C…) with an optional "recommendation" badge, a
  cover letter (pre-filled from a per-company template), and an optional uploaded
  appendix PDF stapled on the end (`pypdf`). It has no financial behaviour and
  never mutates the quotations it references; saving lets a user reopen and
  duplicate it. New **Proposals** page under Quotation in the nav. New tables
  `Proposal` / `Proposal Item` / `Proposal Appendix` (the appendix stored as a
  blob so it survives restarts); `Company.proposal_cover_template` (migration 17).
  Deployments override the look via a `proposal.html` template, same seam as
  `document.html`.

### Added
- **Contact person (Ansprechperson) on Customer.** The Customer master gains
  three optional fields — `contact_person` (a named contact at the customer),
  `contact_email`, and `contact_phone` — kept separate from the company-level
  email/phone. They appear in the master create/edit form (en/de/fr labels) and
  are settable via the chat `create_master`/`update_master` tools. Additive
  migration (16); existing customers keep the fields empty until edited.

## [0.1.19] - 2026-06-17

### Added
- **Voice-to-text in chat.** A microphone button in the chat composer records a
  spoken message, transcribes it with OpenAI `gpt-4o-transcribe`, and drops the
  text into the input for review before sending (it does not auto-send). Click
  to start, click to stop; a short accidental tap is ignored without an API
  call. Transcription is biased with ERP domain vocabulary and guards against
  silence/prompt-echo hallucinations. The spend is recorded to the Demo Spend
  Log (per-minute STT pricing in `api.providers.cost_of_transcription`) and
  demo visitors are rate-limited the same as LLM turns. New WebSocket message
  `transcribe` → `transcription_result`.

## [0.1.18] - 2026-06-05

### Added
- **Discard draft (void).** An unwanted *draft* can now be discarded instead of
  the old "submit then cancel" dance — a soft delete that keeps the row for the
  audit trail (no hard delete anywhere). `Document.discard()` sets
  `discarded = 1` / status `'Discarded'` (drafts only; submitted docs must still
  be cancelled), `submit()` refuses a discarded draft, and discarded documents
  are hidden from default lists (`include_discarded` to show them, plus a "Show
  discarded" toggle on the list and a `discarded` column on every submittable
  doctype via migration `_m015`). Exposed end to end: `POST
  /documents/{type}/{name}/discard`, the **Discard draft** button (drafts render
  terminal/read-only once voided), and a `discard_document` **chat tool**.

### Changed
- **Document cancel now needs a deliberate two-step confirmation.** "Cancel"
  (Stornieren) no longer fires immediately; it arms a confirmation whose
  confirm button renders in a *different place* while the original spot becomes
  a safe "Don't cancel" — so a double-click can't trigger an irreversible
  posting reversal by accident.

## [0.1.17] - 2026-06-05

### Fixed
- **Change Password form now works with password managers.** Added
  `autocomplete="current-password"` / `new-password` (on both the new and
  confirm fields, so managers fill the generated password into both), unique
  `id`/`name` per field, and a hidden `username` anchor input
  (`autocomplete="username"`, `display:none`) so 1Password et al. associate and
  save the credential against the right account.

### Changed
- **Settings page reordering:** the Public Access (Demo Mode) card moved to the
  very bottom (it's the most consequential toggle).
- **Users page:** the pending-invite "Copy link" and "Revoke" actions are now
  proper buttons (shared `Button` component — secondary / danger) instead of
  text links, matching "Create Invite".

## [0.1.16] - 2026-06-05

### Added
- **Change password.** Signed-in users can change their own password from
  General Settings (a card reachable by every role). New endpoint
  `POST /auth/change-password` verifies the current password, requires the new
  one to be at least 6 characters, and rejects the shared `public_manager` demo
  account. Wrong current → 403, too short → 422.

## [0.1.15] - 2026-06-05

### Fixed
- **Sidebar logo broken in consumer deployments.** `.lambda-logo-icon` masked
  its gradient with `url('/logo_lad_erp.png')` — an absolute path to an asset
  that was only in the core's own `public/` and not shipped in the npm package,
  so every downstream deployment got a broken (empty) logo. The mark now lives
  at `src/logo_lad_erp.png`, is referenced relatively (`url('./logo_lad_erp.png')`)
  so each consumer's bundler emits and rewrites it, and is included in the
  package `files`. Frontend only; backend bumps for version lockstep.

## [0.1.14] - 2026-06-04

### Added
- **Opt-in public signup.** A new admin setting `allow_public_signup` (default
  off) under General Settings. When enabled, anyone may self-register — as a
  **viewer** only. The first user still bootstraps as admin, and with the toggle
  off registration stays invite-only. `setup-status` now returns `first_run`,
  `public_signup`, and `registration_open` so the signup page can distinguish
  first-run admin creation from open viewer signup.
- **Invite management** on the Users page: copy a pending invite's link again,
  and revoke a pending invite via `DELETE /auth/invites/{token}` (admin-only;
  404 if unknown, 409 if already used).

### Fixed
- **Token Spend (formerly "Demo Spend") crashed on Postgres.** A double-quoted
  SQL string literal (`role = "public_manager"`) was read as an identifier by
  Postgres, 500-ing the `/admin/demo-spend` endpoint. Now single-quoted. The
  card is relabeled **Token Spend**, stays admin-only, and on non-demo
  deployments leads with total LLM spend (cap details show only in demo mode).
- The double-quoted-literal CI guard now also scans continuation lines of
  multi-line SQL (`CASE/WHEN/THEN`, aggregates), which previously slipped past.

## [0.1.13] - 2026-06-04

### Added
- **`register_pdf_context(fn)`** seam (`api.pdf`): a deployment plugin can
  register a provider `fn(doctype, name, context)` that returns extra keys
  merged into the PDF render context just before rendering — e.g. a computed
  Swiss QR-bill image for invoices. Providers run after the built-in context is
  assembled; an exception in one is swallowed so it can't break PDF generation.
- **`Company.iban`** field (schema + migration `_m014_company_iban`) and an
  IBAN input on the company master form. A generic bank-account field for
  payment instructions; consumed by deployments that render payment slips
  (e.g. the Swiss QR-bill in the example/internal plugins). The company info
  passed to PDF templates now includes `iban`.

## [0.1.12] - 2026-06-04

### Added
- PDF templates can reference **sibling assets** (logo, fonts, CSS) by relative
  path: `generate_pdf` now sets WeasyPrint's `base_url` to the rendered
  template's own directory. A `register_pdf_template_dir` override can drop a
  `document.html` next to its `logo.png` and use `<img src="logo.png">`.

## [0.1.11] - 2026-06-04

### Added
- **PDF template override seam** (`register_pdf_template_dir(path)` in
  `api/pdf.py`). A deployment plugin can register a directory whose templates
  (e.g. `document.html`) override the built-in invoice/document PDF layout —
  registered dirs are searched before the core's. The custom template gets the
  same render context generate_pdf() builds (doc, company_info, party_info,
  items, taxes, currency, page_size, ...), so it just restyles the same data.
  No behaviour change when nothing is registered.

### Fixed
- **New customers/suppliers now inherit the company's base currency instead of
  defaulting to USD.** The Customer/Supplier `default_currency` column defaults
  to `'USD'`, so for a non-USD company (e.g. CHF) a customer created without an
  explicit currency was stored as USD — which then forced its sales/purchase
  documents to USD and failed to save without a USD->base exchange rate.
  `create_master_record` now fills `default_currency` from the company when none
  is given (explicit values still win). Also exposed the **Currency** field on
  the Customer and Supplier master forms (previously only settable via the
  API/chat, so it was invisible in the UI).

## [0.1.10] - 2026-06-04

### Changed
- **Company Setup no longer invents a fake address for real companies.** The
  `/setup/company` endpoint used to fill any missing contact field with a
  deterministic pseudo-random US address (so demo PDFs looked complete) — which
  meant every real company got a bogus address printed on its invoices. Now the
  Company Setup form collects address / city / ZIP / country / tax-id / email /
  phone (optional), and the backend only auto-fills when the caller opts in
  (`autofill_address`), which the public demo seeding does. Real setups keep
  unprovided fields blank. Backend + frontend; ships at 0.1.10.

## [0.1.9] - 2026-06-04

### Fixed
- **Sidebar highlighted two nav rows at once.** A NavLink to a path that is a
  prefix of a sibling's (`/setup` "Company Setup" vs `/setup/opening-balances`
  "Opening Balances") matched as active on the longer path's page, so both rows
  showed the active grey background on the Opening Balances page. Items whose
  path is nested under a sibling now use exact (`end`) matching. Backend
  unchanged; ships at 0.1.9 for lockstep.

## [0.1.8] - 2026-06-04

### Added
- **Company Setup: "Create empty company, no demo data" option.** The setup
  page now offers a third seed mode alongside "Quick demo" and "Simulate 3 years
  of history" that creates only the company (and its chart of accounts) and
  seeds nothing — for real deployments starting from scratch. Frontend only;
  the backend already exposed company creation and seeding as separate calls.

### Changed
- **Company Setup nav link hides once setup is complete.** The sidebar drops the
  "Company Setup" (`/setup`) item once a company exists (`setup_complete`), since
  it's a one-time step. Getting Started and Opening Balances are unaffected.

## [0.1.7] - 2026-06-03

### Fixed
- **`db.sql()` raised on Postgres for write statements.** It always called
  `fetchall()`; after an `INSERT`/`UPDATE`/`DELETE` psycopg raises "the last
  operation didn't produce records" (SQLite harmlessly returns `[]`). This broke
  the chat/WebSocket path — which runs `UPDATE`/`DELETE` through `db.sql()` —
  causing constant disconnect/reconnect loops, and affected other `db.sql()`
  write call sites (settings, attachments, invites, report drafts). `db.sql()`
  now fetches only when the statement produced a result set
  (`cursor.description is not None`). `test_db_portability` extended to assert
  write-via-`db.sql()` returns `[]` on both backends. Frontend unchanged; ships
  at 0.1.7 for lockstep.

## [0.1.6] - 2026-06-03

### Fixed
- **Double-quoted string literals in SQL broke on Postgres.** Several queries
  used `WHERE x = "value"` / `IN ("a","b")` — SQLite quietly reads an unknown
  double-quoted token as a string literal, but Postgres treats `"..."` as an
  identifier and errors (`column "value" does not exist`). This 500'd the auth
  (`get_current_user`, register/admin checks), chat-history, PDF, and master
  delete-guard paths on Postgres. All converted to single-quoted values.
- **A failed query no longer poisons the Postgres connection.** With
  `autocommit=False`, one failing statement left the (thread-local) connection
  in an aborted transaction, so every later request reusing it failed with
  `InFailedSqlTransaction`. The connection wrapper now rolls back on error
  before re-raising.

### Added
- `tests/test_db_portability.py`: a static scan that fails on double-quoted SQL
  literals, plus a functional auth smoke test (`setup-status`/register/login +
  an unauthenticated protected request) run against both backends. Wired into
  CI. Frontend unchanged; ships at 0.1.6 for lockstep.

## [0.1.5] - 2026-06-03

### Added
- **Optional PostgreSQL backend.** The data layer (`lambda_erp/database.py`) now
  supports Postgres in addition to SQLite. SQLite stays the default (and is what
  the test suite and local dev use); select Postgres at runtime by setting
  `LAMBDA_ERP_DB` to a `postgresql://…` URL. Install the driver with the new
  extra: `pip install lambda-erp[postgres]`. This unblocks deployments whose
  durable storage can't host a SQLite file with working file locks (e.g. Azure
  Files SMB volumes), where `PRAGMA journal_mode=WAL` and even ordinary writes
  fail with "database is locked".
- CI now runs the full validation suite against a real Postgres (service
  container) on every push, alongside SQLite, so the accounting/stock invariants
  are verified on the production backend.

### Changed
- A handful of queries were made dialect-portable (no behaviour change on
  SQLite): `IFNULL`→`COALESCE`, `strftime` date bucketing → `substr`, and
  `get_value`/`get_all` now select only columns that exist (padding the rest as
  `NULL`) instead of relying on SQLite returning unknown quoted identifiers as
  string literals. Frontend (`@lambda-development/erp-core`) is unchanged; it
  ships at 0.1.5 to keep the two packages in lockstep.

## [0.1.4] - 2026-06-03

### Added
- **`LAMBDA_ERP_SQLITE_JOURNAL_MODE`** env var (default `WAL`) to choose the
  SQLite journal mode. WAL relies on a memory-mapped `-shm` file and therefore
  cannot be used when the database lives on a **network filesystem** (SMB/NFS
  Azure Files, NFS shares): `PRAGMA journal_mode=WAL` fails outright with
  "database is locked" at startup. A deployment that persists its DB on such a
  share now sets `LAMBDA_ERP_SQLITE_JOURNAL_MODE=DELETE` (rollback-journal mode,
  which uses only byte-range locks the share supports). Single-replica /
  single-worker deployments lose nothing by using DELETE. Frontend unchanged;
  ships at 0.1.4 to keep the two packages in lockstep.

## [0.1.3] - 2026-06-03

### Fixed
- **`@lambda-development/erp-core` could not be production-built by a consumer.**
  The analytics report worker was referenced with a bare
  `new Worker(new URL("…", import.meta.url))`, so the library build emitted an
  **absolute** `/assets/report-runtime.worker-*.js` URL. A downstream app
  bundling the package resolved that leading-slash path against its own
  `public/` dir and failed with "Could not resolve entry module". The worker is
  now imported with `?worker&inline`, embedding it as a blob in `dist/index.js`
  — no external asset for consumers to resolve. Backend (`lambda-erp`) is
  unchanged; it ships at 0.1.3 only to keep the two packages in lockstep.

## [0.1.2] - 2026-05-26

### Fixed
- npm publish failed provenance verification on 0.1.1 because
  `frontend/package.json` declared no `repository` field. Added it (plus
  `description`, `license`, `homepage`, `bugs`). No functional changes since
  0.1.1. Net effect on versions: **npm** goes 0.1.0 → 0.1.2 (npm 0.1.1 never
  shipped); **PyPI** published 0.1.1 normally, so PyPI includes 0.1.1 and npm
  does not. Both registries are aligned again at 0.1.2.

## [0.1.1] - 2026-05-26

First public release of `lambda-erp` to PyPI via the keyless CI pipeline. (npm
0.1.1 failed provenance verification and shipped as 0.1.2 instead — see above.)
Same code as the 0.1.0 npm bootstrap noted below.

### Added
- **Core ERP engine** (backend) — document lifecycle (quotation → sales order →
  delivery note / sales invoice → payment, plus the buying side), double-entry
  GL posting, a stock ledger with moving-average valuation, and tax calculation.
- **Multi-currency** (backend + frontend) — per-document transaction currency
  with a historical exchange-rate snapshot on each document, realized FX
  gain/loss on settlement, period-end revaluation of open foreign balances, and
  presentation-currency translation of financial statements. New sales/purchase
  documents default their currency from the party (or company) and expose a
  currency picker in the UI.
- **Stock-movement analytics** — a `stock_movements` semantic dataset over the
  stock ledger, queryable from the chat assistant (e.g. "what item moves the
  most").
- **Internationalization** — the full UI in English, German, and French with a
  language switcher; the choice is persisted per browser.
- **Open-core packaging** — the backend ships as `lambda-erp` on PyPI; the
  frontend ships as `@lambda-development/erp-core` on npm with additive override
  seams (`registerDoctype`, `registerRoute`, the nav and component registries,
  `configureBranding`, `configureApiBase`) and a `bootstrap()` entry, so a
  customer deployment depends on the packages instead of forking the repo.

## [0.1.0] - 2026-05-26

Internal npm bootstrap that created `@lambda-development/erp-core` on the
registry — required before OIDC trusted publishing can be enabled for a new npm
package. No PyPI release and no functional changes; superseded by 0.1.1.

[Unreleased]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.12...HEAD
[0.1.12]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.11...v0.1.12
[0.1.11]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.2
[0.1.1]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.1
[0.1.0]: https://www.npmjs.com/package/@lambda-development/erp-core/v/0.1.0
