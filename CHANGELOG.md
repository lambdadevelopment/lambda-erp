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

[Unreleased]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.7...HEAD
[0.1.7]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/lambdadevelopment/lambda-erp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.2
[0.1.1]: https://github.com/lambdadevelopment/lambda-erp/releases/tag/v0.1.1
[0.1.0]: https://www.npmjs.com/package/@lambda-development/erp-core/v/0.1.0
