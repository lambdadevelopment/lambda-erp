# Recurring billing: quote → Subscription → posted invoices

Status: **plan** (decisions locked; not yet implemented). Owner: TBD.

## Goal
Make a recurring quotation line actually bill and post on its own, using the
existing `Subscription` primitive — **without ever putting recurrence on an
invoice**. When a quote with recurring lines is accepted, its recurring lines
spawn Subscription(s) that generate + post one ordinary invoice per period.
One-time lines flow to Sales Order/Invoice exactly as today.

**Accounting invariant:** every generated invoice is a normal one-time invoice
(Dr AR / Cr Revenue / Cr Tax) that posts correctly. The Subscription itself
posts nothing. So double-entry integrity is never at risk — the same reason the
quotation `frequency` split is safe.

## Locked decisions
- **Frequency stays on quotations only.** Not on Sales Invoice (conceptually
  wrong — an invoice bills one period — and excluding a "recurring" line from a
  posted grand total would break the ledger). Sales Order only if a concrete
  need appears.
- **Start/anchor date:** user-chosen at conversion time.
- **Generated invoices:** auto-submit (post) by default; a setting allows
  leaving them as drafts for review.
- **Scheduler:** external, via an Azure **Container Apps Job** on a cron
  schedule (NOT an in-process scheduler — the app runs multiple replicas in
  some deployments, and an in-process timer would fire once per replica). The
  job calls an idempotent, DB-locked `process_all_due()`.
- **Conversion trigger:** manual "Create subscription(s)" action on the
  quotation (mirrors the existing Sales Order / Invoice conversions). Also
  invokable from chat.

## Current state (what already exists)
`lambda_erp/accounting/subscription.py` — a working `Subscription` +
`Subscription Plan` doctype:
- party / company / `start_date` / `end_date` / `billing_interval`
  (Monthly / Quarterly / Half-Yearly / Yearly — matches quote `frequency`
  exactly) / `current_invoice_start` / `current_invoice_end` / `status`.
- `process()` → `_create_invoice()` builds a Sales/Purchase Invoice from the
  plan rows and advances the period window.

Gaps to close: pull-based only (no scheduler); generated invoices saved as
drafts; the `subscription` back-link is passed but **not persisted** (no column
on the invoice); no quote→subscription path.

## Phase 0 — Make the existing Subscription trustworthy
1. **Persist the back-link.** Add `subscription` column to `Sales Invoice`
   (migration); optionally `Purchase Invoice`. `_create_invoice` already sets
   the value.
2. **Auto-submit setting.** Add a flag (e.g. `Settings.subscription_auto_submit`,
   default on). When on, `_create_invoice` submits the invoice so it posts; when
   off, it stays a draft.
3. **Idempotency + concurrency guard.** Wrap each subscription's billing in a
   DB row lock (`SELECT … FOR UPDATE` on the Subscription, Postgres) so two
   overlapping runs (cron + manual, or a stuck prior run) can't both create the
   period's invoice. `process()` already advances the window after billing;
   add a test: two `process()` calls in one period → exactly one invoice.

## Phase 1 — Make it recur on its own
4. **`process_all_due()`** in core — iterate Active subscriptions with
   `current_invoice_end <= today`, `process()` each under the Phase 0.3 lock,
   return a summary (counts, created invoice names, errors). Pure, safe to call
   repeatedly.
5. **CLI entrypoint** — `python -m api.jobs.run_due_subscriptions` (or similar)
   that calls `process_all_due()` and exits non-zero on error. No HTTP/auth
   needed; the job connects straight to Postgres via the shared connection.
6. **Container Apps Job (internal Terraform).** Add `azurerm_container_app_job`
   to `terraform/app/` (next to `container_app.tf`):
   - same image as the app (the job image is updated by `deploy.yml` alongside
     the app, or via a shared image variable — mirror the app's
     `ignore_changes` on image),
   - same `database-url` secret + `LAMBDA_ERP_PLUGINS=internal` env,
   - `schedule_trigger_config { cron_expression = "0 6 * * *" }` (daily 06:00;
     tune as needed), `replica_timeout`, and a small `replica_retry_limit`,
   - command runs the Phase 1.5 entrypoint.
   Runs as its own short-lived container → fires exactly once per tick
   regardless of app replica count. Document the cron + what it does in the
   `.tf` file header.

## Phase 2 — Quote → Subscription conversion
7. **`make_subscription_from_quotation(quotation_name)`** (mirrors
   `make_sales_order`): take the recurring lines, **group by frequency** (one
   Subscription per cadence, since a Subscription has a single
   `billing_interval`), map each line → a Subscription Plan (item_code / qty /
   rate), set party / company / `billing_interval` / user-chosen `start_date`.
8. **One-time lines** still convert to Sales Order / Sales Invoice as today. A
   single quote can yield: 1 order/invoice (one-time) + N subscriptions (one
   per recurring cadence).
9. **UI:** a "Create subscription(s)" conversion action on a quotation that has
   recurring lines (extend the existing `conversions` config). The action
   collects the start_date.
10. **Chat:** document the `Subscription` doctype in the system prompt (so
    "set up a CHF 380/month subscription for medynex" works via
    `create_document`), and the quote→subscription conversion (so "turn the
    recurring lines on this quote into subscriptions" works). A Subscription
    created via chat then auto-bills through Phase 1.

## Phase 3 — UX / reporting (incremental)
- Subscription list: next-invoice date, simple MRR.
- Invoice detail: "from Subscription X" via the Phase 0.1 back-link.
- Quotation: a hint that recurring lines will create subscription(s) on accept.

## Explicitly out of scope (v1)
- **Deferred revenue / revenue recognition** for prepaid annual contracts
  (book to a liability, recognize ratably). Bigger accounting feature; only
  matters when billing annually upfront. Invoice-monthly needs none of it.
- Proration, trial periods, mid-term plan changes, dunning/retries beyond a
  basic retry limit.

## Suggested sequencing
Phase 0 (small, safe — makes Subscription trustworthy) → Phase 1 (the actual
"set and forget") → Phase 2 (ties it to the quote flow). Each phase is
independently shippable and accounting-safe.
