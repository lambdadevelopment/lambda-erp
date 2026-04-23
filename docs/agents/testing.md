# Testing

The fastest way to know a change is safe.

## Golden command

```bash
python -m tests.test_erp_validation
```

Runs an in-memory end-to-end simulation of every major flow (company
setup, quotation → SI → payment, PO → PR → PI → payment, returns,
update_stock, POS, journal entries, cancel chains, master-link
validation, account-type constraints). Exits 0 with a balanced trial
balance when healthy. ~30 sections at time of writing.

**Invariant to watch:** the last section prints the Trial Balance. If
debit total ≠ credit total, something in your change broke double-entry.

## Frontend

```bash
cd frontend && npx tsc --noEmit
```

No runtime test suite; if TS compiles and the page loads, it's the bar.

## When "done" actually means done

A change is ready to ship when:

1. `python -m tests.test_erp_validation` exits 0.
2. The trial balance at the end is balanced.
3. `npx tsc --noEmit` exits 0 (if you touched the frontend).
4. If you touched a flow that could produce drift (outstanding,
   billed_qty, received_qty, Bin qty), add a regression assertion in
   the relevant `§N` section rather than trusting the balance as proof.

## Adding a regression test

The validation file uses a numbered-section style (`§1` through `§N`).
Add new scenarios at the end, before `TRIAL BALANCE`. Keep them
self-contained: create the masters and data they need, don't rely on
state from earlier sections beyond what's in the seeded Masters table.

Tests have intentional "this should raise" blocks wrapped in
try/except that asserts a phrase from the error message. When adding a
validation error, update the phrase if it changes — the tests catch
error-message drift.

## What's NOT tested

- Frontend rendering (no Playwright, no Testing Library). The
  `npx tsc --noEmit` check only proves it compiles.
- Multi-user concurrency (the tests run against a single in-memory DB).
- Real Docker deploy / WebSocket behaviour end-to-end.
- The chat WS message protocol (covered by manual UI testing).

If you touch any of the above, plan a manual smoke test.
