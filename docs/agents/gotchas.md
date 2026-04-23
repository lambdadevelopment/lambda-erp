# Gotchas

Landmines this codebase has stepped on. If you're about to do one of
these, stop and read the note.

## SQLite

- **Quoted-identifier quirk.** `SELECT "unknown_column" FROM "Table"` in
  SQLite **does not raise** — it returns the string literal
  `"unknown_column"` for every row. This masked a bug for a long time
  where `db.get_value("Company", ..., "stock_in_hand_account")` silently
  returned `None` because the column wasn't in the schema. When reading
  a column whose existence is uncertain, check `PRAGMA table_info(table)`
  first or add a migration.
- **Concurrency requires `self._lock` on every `.sql()` call, reads too.**
  `check_same_thread=False` alone is not enough. Concurrent read+write on
  the same connection throws "bad parameter or other API misuse". The
  lock is in `Database.sql` — don't bypass it with `self.conn.execute(...)`
  directly.
- **`db.set_value` bypasses `save()`.** It writes to disk without running
  `validate()`, `_validate_links`, etc. Use it only for the narrow set of
  post-submit updates that legitimately need to mutate submitted rows
  (`outstanding_amount`, `billed_qty`, `modified`). For anything else,
  the caller should be a DRAFT document going through `save()`.

## Schema / migrations

- **Never renumber or remove existing migrations.** The `_SchemaMigrations`
  table tracks applied versions by integer. Append new migrations with
  the next integer — entries 1-7 are load-bearing.
- **`CREATE TABLE IF NOT EXISTS` doesn't run `ALTER TABLE`.** When adding
  a column to an existing table, add a new numbered migration too,
  otherwise long-lived databases won't pick up the change.

## React

- **`useSearchParams` caches `searchParamsRef`.** Multiple sync
  `setSearchParams` calls in one handler all see the same `prev`, and the
  last one wins. Use the `useUrlPatch` helper from
  `hooks/use-url-state.ts` for multi-param updates — it batches everything
  into a single navigate.
- **StrictMode double-mounts effects.** A `let cancelled = false` flipped
  in cleanup doesn't protect you — the first mount's cleanup flips it,
  the second mount's early-return via `startedRef` means no new
  `cancelled` is created, and when the fetch resolves it sees `cancelled
  = true` and never runs. Use ref-based once-only guards **without** a
  cancelled flag (or put `cancelled` on a ref too).

## Backend / deploy

- **Single uvicorn worker only.** SQLite + in-memory chat session state
  (`session_tasks`, `demo_typing_waiters`) are per-process. The Dockerfile
  CMD pins `--workers 1` explicitly. To scale horizontally, move to
  Postgres + external task storage first.
- **`bootstrap_demo()` runs on every container start when
  `LAMBDA_ERP_AUTO_DEMO=1`.** It's idempotent (checks existence before
  writing), but the historical simulator is deterministic — adding new
  non-deterministic seeding paths will break the demo chat script that
  references specific invoice names from the sim.

## Frontend

- **Only `customer`/`supplier`/`item`/`warehouse` have a MasterForm page.**
  `company`, `cost_center`, `account` are pseudo-masters without CRUD
  UIs. The `linkRefHref` helper in `document-form.tsx` routes `account`
  clicks to `/reports/general-ledger?account=<name>` and renders the
  others as plain text. Don't generate raw `/masters/{type}/{name}` links
  for non-whitelisted types — it'll land the user on a blank page.
- **Don't add `Link` navigation for `master_type` = `account`.** See
  above — send them to the pre-filtered GL report.

## Chat

- **WebSocket auth falls back to `public_manager` when no cookie.** This
  is intentional for demo mode (the `/demo` URL). Before a real deploy,
  disable the public_manager user via the admin UI or remove the fallback
  in `api/main.py:82`.

## Returns / refunds

- **PE `party_type` isn't redundant with `payment_type`.** Four valid
  combos exist: Receive+Customer (normal), Pay+Supplier (normal),
  Pay+Customer (customer refund), Receive+Supplier (supplier refund).
  `_get_gl_entries` reads `self.party_type` — don't hardcode it inside a
  payment_type branch like the original code did.
- **`make_sales_return` / `make_purchase_return` MUST copy `update_stock`
  from the original.** Without it, a return against a direct-ship invoice
  reverses the revenue but leaves stock stranded.
