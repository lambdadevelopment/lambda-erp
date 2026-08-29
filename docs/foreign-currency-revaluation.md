# Foreign-currency revaluation

Period-end revaluation restates open foreign-currency monetary balances at a
closing rate without changing the original posted entries. The implementation
lives in `lambda_erp/accounting/revaluation.py` and is available through
`POST /api/accounting/revaluation` and the ERP chat tool.

## Scope

The operation includes non-base-currency balances that are both monetary and
open:

- submitted Sales and POS Invoices with a non-zero outstanding amount;
- submitted Purchase Invoices with a non-zero outstanding amount;
- foreign-currency Bank and Cash accounts with a non-zero currency balance.

It excludes inventory, fixed assets, recognized income and expenses, base
currency balances, and other non-monetary items.

## Calculation

For each balance:

```text
current_base = base-currency carrying value already posted
closing_base = open foreign amount × closing exchange rate
unrealized   = closing_base − current_base
```

The closing rate comes from `Currency Exchange` through
`get_exchange_rate(foreign_currency, base_currency, date)`. A missing rate is
an error rather than an implicit fallback.

For receivables and payables, the open foreign amount is the invoice's
`outstanding_amount`; its current base value uses the snapshotted invoice
conversion rate. For Bank and Cash accounts, both balances come from
`get_account_balances`.

## Posting behavior

- Revaluation never edits an existing GL entry. It posts a separate `Period
  Revaluation` voucher against the company's configured `Unrealized Exchange
  Gain/Loss` account.
- An automatic reversal is posted on the following day so later settlement
  recognizes realized FX without double counting the period-end estimate.
- Only the base-currency carrying value changes. The debit and credit amounts
  in account currency are explicitly zero.
- Asset gains debit the control account and credit unrealized FX; asset losses
  reverse that direction. Liability gains and losses use the opposite natural
  direction.
- Differences smaller than 0.005 base-currency units are not posted.

`run_period_revaluation(company, date, post=False)` returns the complete
per-balance preview without touching the ledger. With `post=True`, it also
returns the revaluation voucher, reversal voucher and reversal date.

## API

Managers may preview or post through:

```http
POST /api/accounting/revaluation
Content-Type: application/json

{"company": "Example AG", "date": "2026-12-31", "post": false}
```

`company` and `date` default to the first company and the current date.
`post` defaults to `false` on the HTTP endpoint.

Regression coverage is in `tests/test_erp_validation.py`, including foreign
receivables, foreign cash, reversal behavior, immutable original GL entries
and trial-balance integrity.
