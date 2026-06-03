# Decisions

Chronological log of non-obvious judgment calls. Format: what, why,
what we chose not to do.

## Single-replica Azure Container App

**What:** The Terraform deploys with `min_replicas = max_replicas = 1`
and the Dockerfile pins `uvicorn --workers 1`.

**Why:** SQLite can't be shared across processes, and in-memory chat
state (`session_tasks`, `demo_typing_waiters`) is per-process.
Horizontal scaling would split that state.

**Rejected:** Moving to Postgres + Redis as part of the first deploy.
Deferred until someone actually hits the capacity wall; adds operational
complexity ~5× for no demo benefit.

## SQLite by default, optional Postgres

**What:** Default backend is a file-backed SQLite DB with a process-wide
`self._lock` for concurrency. Since 0.1.5 the same data layer also speaks
**Postgres**, selected at runtime by setting `LAMBDA_ERP_DB` to a
`postgresql://…` URL (install `lambda-erp[postgres]`). One code path, two
dialects (see the dual-backend gotcha).

**Why SQLite is still the default:** zero-ops. `docker run` on a laptop and the
in-memory test suite both just work, no second container.

**Why Postgres was added (not "from day one"):** a managed container host whose
only durable storage is a **network file share** (e.g. Azure Files SMB) can't
run SQLite — the share doesn't honour the file locks SQLite needs, so writes
fail with `database is locked`. Rather than hack around the share (NFS, etc.),
the cloud deployment uses Postgres; SQLite stays for dev/test/demo. Doing the
port while the prod DB was still empty avoided a live SQLite→Postgres migration.

**Still single-replica regardless of backend:** in-memory chat state pins the
app to one replica until that moves to a shared store — Postgres lifts the
SQLite single-writer limit but not this one.

## Opening Balance Equity (not Stock Adjustment) for seed stock

**What:** `stock_entry_type = "Opening Stock"` credits the company's
`default_opening_balance_equity` account.

**Why:** The earlier approach (Material Receipt) credited Stock
Adjustment, which is an *expense* account. That inflated Y1 profit by
the full opening-stock value (~$155k in the sim). Opening inventory is
day-one equity, not earned income.

**Rejected:** A one-off JE after opening-stock to reclassify. Works but
requires the simulator (and the opening-balances wizard) to post two
docs instead of one; the dedicated stock_entry_type is cleaner.

## Cost basis via `stock_value_difference` lookup, not sell rate

**What:** Sell-side docs (DN, SI update_stock, POS) pass `outgoing_rate=0`
on SLEs, then GL reads the moving-average cost back from
`stock_value_difference` on the just-persisted SLE rows.

**Why:** Previous code passed the invoice sell rate as `outgoing_rate`,
posting COGS at revenue value. That inflated COGS by 100% of gross
margin.

**Rejected:** Valuing outgoing stock via a separate pre-query of Bin
rates. Works but duplicates what the stock ledger already computes; the
sub-query into SLE is one SQL call and tracks cancellations correctly.

## No multi-company isolation

**What:** `Warehouse`, `Cost Center`, `Account` all carry a `company`
field but nothing in the codebase validates that a document at Company
A references only Company A's children.

**Why:** Demo is single-company. Cross-company corruption would be
invisible in Lambda Demo Corp. Adding the validation everywhere is
~15 touch points and zero observable benefit today.

**Open:** If multi-company is ever enabled (an admin setting, or a
second seeded company in the simulator), this becomes a blocker.

## Dynamic (party, reference_name) links validated at runtime

**What:** `PaymentEntry.party` and `JournalEntry.accounts[].party` etc.
use `DYNAMIC_LINK_FIELDS` declarations: base class reads the sibling
`party_type` / `reference_doctype` and validates against the mapped
master table.

**Why:** A `LINK_FIELDS: {"party": "Customer"}` wouldn't match supplier
refunds. A `LINK_FIELDS: {"party": "Customer|Supplier"}` doesn't exist.

**Rejected:** Hand-written validators in every affected document —
copy-paste that drifts. The base class mechanic is strictly more
constrained and applies uniformly to PE, JE, and future additions.

## Query-string state in the frontend (not path params)

**What:** `?page=3&per_page=100&from=2026-01-01` etc. driven by the
`useUrlState` / `useUrlPatch` hooks. Pagination 1-indexed in URL,
0-indexed internally; filter changes reset page to 1.

**Why:** Shareable URLs, Back button doesn't step through filter
tweaks (`replace: true`), and defaults stripped from the URL to keep
them short.

**Rejected:** Path segments like `/app/sales-invoice/3/50`. Positional
encoding breaks on optional filters; query string is the standard.
