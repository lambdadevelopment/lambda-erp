# Multi-currency — Phase 4c: period-end revaluation (unrealized FX)

Status: **not started**. Phases 1–4b are shipped (see
`tests/test_erp_validation.py` sections 35–38). 4c is the last planned piece.

## What 4c is

At period end, any **open foreign-currency monetary balance** is still carried
on the books at the historical rate(s) it was booked at. Its value in base
currency has drifted from what it's worth at today's rate. 4c restates those
open balances to the **closing rate** and books the difference as an
**unrealized** FX gain/loss.

This is distinct from the realized FX we already do:

| | When | Booked by | Account |
|---|---|---|---|
| Realized (done, 4a/4b) | on settlement / conversion | Payment Entry | `Exchange Gain/Loss` |
| **Unrealized (4c)** | at period end, on *open* balances | a revaluation run | a separate unrealized FX account (TBD) |

Key invariant to preserve (see `docs/agents/` and
`feedback-currency-design-principles` in memory): **never edit posted entries.**
Revaluation posts *new* GL entries; the originals stay frozen.

## Which balances get revalued (monetary, foreign, open)

Revalue only **monetary** items in a non-base currency with a live balance:

- **Foreign receivables** — Sales / POS Invoices where `currency != base` and
  `outstanding_amount != 0` (outstanding is in document currency).
- **Foreign payables** — Purchase Invoices, same condition.
- **Foreign bank/cash accounts** — Accounts with `account_currency != base` and a
  non-zero balance (use `get_account_balances` → the account-currency balance).

Do **not** revalue non-monetary items (inventory, fixed assets, revenue/expense
already recognized) or base-currency balances.

## The math (per balance)

For each open foreign balance:

```
current_base   = its base carrying value already on the books
closing_base   = (foreign amount still open) * closing_rate
unrealized     = closing_base - current_base
```

- **Foreign bank account:** `current_base, foreign_amt = get_account_balances(acct)`;
  `closing_base = foreign_amt * closing_rate`.
- **Foreign invoice (AR/AP):** the open foreign amount is `outstanding_amount`
  (document currency); `current_base = outstanding_amount * invoice.conversion_rate`
  (the snapshotted booking rate); `closing_base = outstanding_amount * closing_rate`.

`closing_rate = get_exchange_rate(foreign_ccy, base_ccy, period_end_date)`
(`lambda_erp/controllers/currency.py`).

Posting (sign depends on asset vs liability):
- Asset side (AR, bank) — `unrealized > 0` → Dr Asset-control / Cr Unrealized FX gain; `< 0` → reverse.
- Liability side (AP) — `unrealized > 0` (liability worth more in base) → Dr Unrealized FX loss / Cr AP-control; `< 0` → reverse.

## Implementation tasks

1. **Account(s).** Add an unrealized FX account to the chart
   (`lambda_erp/accounting/chart_of_accounts.py`) + a `Company` default field +
   a backfill migration (mirror `_m012_exchange_gain_loss_account`). Decide
   whether to reuse the existing `Exchange Gain/Loss` account or keep realized
   vs unrealized separate (see open decisions).
2. **Revaluation run.** A new operation (a `Period Revaluation` document, or a
   function callable from an API endpoint / chat tool) that takes a
   `period_end_date`, gathers the open foreign balances above, computes the
   per-balance unrealized amount, and posts one balanced voucher (one GL entry
   per control account/account + the FX plug). Reuse `make_gl_entries`.
3. **Reversal.** Post an auto-reversing entry dated the first day of the next
   period (standard practice) so the unrealized estimate doesn't double-count
   when the balance later settles and books *realized* FX. Alternative:
   track a running revalued carrying value. Pick one (see open decisions).
4. **Idempotency / re-runs.** Re-running revaluation for the same period must
   not stack entries — either reverse-and-replace, or guard on
   `(voucher_type, period_end_date)`.
5. **Tests** (`tests/test_erp_validation.py`, new section ~39):
   - Open EUR invoice booked @1.10, closing rate 1.20 → unrealized gain on AR;
     1.00 → unrealized loss. Original invoice GL unchanged.
   - EUR bank holding 50 EUR carried at 53.25 (from section 38) revalued at a
     closing rate → unrealized gain/loss vs carrying value.
   - Reversal next period nets the unrealized entry back out.
   - Then settle the invoice → realized FX is correct and not double-counted.
   - Trial balance still nets to zero before and after.

## Open design decisions (confirm before building)

- **Separate unrealized account** vs reuse `Exchange Gain/Loss`? (Separate is
  cleaner for reporting realized vs unrealized; reuse is simpler.)
- **Auto-reverse next period** vs **cumulative revalued carrying value**?
  (Auto-reverse is the common, simpler approach and matches "never edit
  originals".)
- **Trigger:** manual run / API endpoint / scheduled? And per-company.
- **Where do closing rates come from** — a Currency Exchange row dated at/after
  the period end must exist, else `get_exchange_rate` raises (intended guard).

## Out of scope for 4c

- Cash-flow hedge / OCI treatment of FX (advanced; not needed here).
- Multi-currency *consolidation* across companies with different base currencies.
- Foreign↔foreign conversions (4b already restricts to one base side).

## Relevant code

- `lambda_erp/controllers/currency.py` — `get_exchange_rate` (closing rate).
- `lambda_erp/accounting/general_ledger.py` — `get_account_balances`, `make_gl_entries`.
- `lambda_erp/accounting/payment_entry.py` — realized FX (the pattern to mirror).
- `lambda_erp/accounting/chart_of_accounts.py` + `_m012` migration — how the
  realized FX account was added/backfilled.
