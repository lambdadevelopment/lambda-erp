# Accounting

Sign conventions and posting patterns that aren't obvious from any single
file.

## Sign conventions

| Balance | Sign | Meaning |
|---|---|---|
| AR (customer owes us) | positive | normal receivable |
| AP (we owe supplier) | negative | normal payable (credit on a liability) |
| SI.outstanding_amount | positive | customer still owes |
| SI.outstanding_amount on a return SI | negative | we owe the customer |
| PI.outstanding_amount | positive | we still owe |
| PI.outstanding_amount on a return PI | negative | supplier owes us |

Normal settlement reduces `|outstanding|` toward 0. Refund settlement on
a return invoice also reduces `|outstanding|` toward 0, but by *adding*
a positive allocation to the negative starting balance. The
`PaymentEntry._update_outstanding` logic handles both with a sign check.

## The stock-side contra map

```
Flow                             Stock side              Bill side
─────────────────────────────────────────────────────────────────────
PR → PI (standard)               Dr SIH / Cr SRBNB       Dr SRBNB / Cr AP
PI update_stock=1                Dr SIH / Cr AP          (combined, single doc)
DN → SI (no update_stock)        Dr COGS / Cr SIH        Dr AR / Cr Revenue
SI update_stock=1                Dr COGS / Cr SIH        Dr AR / Cr Revenue
POS update_stock=1               Dr COGS / Cr SIH        Dr AR / Cr Revenue + payments
Stock Entry (Material Receipt)   Dr SIH / Cr StockAdj    —
Stock Entry (Material Issue)     Dr StockAdj / Cr SIH    —
Stock Entry (Opening Stock)      Dr SIH / Cr OpeningBE   —
```

The direct-ship flows (SI/POS/PI with update_stock=1, DN) all use
moving-average cost read from SLE `stock_value_difference` rather than
the sell/line rate. `stock_ledger.build_cost_basis_gl` is the helper —
use it, don't reimplement.

## Return flows

- **`make_sales_return(sinv)`** creates a return SI with negative qty +
  negative grand_total + `is_return=1`. It **copies `update_stock`** from
  the original so the stock side also reverses if applicable.
- **`make_purchase_return`** mirror image.
- **`make_pos_return(posi)`** — relaxed payments requirement (refund is
  optional; user can post a separate PE later).

Each submit calls `_update_original_outstanding` to reduce the original's
`outstanding_amount` by the return's grand_total (floored at 0).
Return-value guard (`_validate_return_value`) caps the return's total at
`original_total − already_returned`, covering the case where qty is
in-bounds but the rate is inflated.

## Cancel-chain guards

Run these **before** any ledger mutation in `on_cancel`:

- `PurchaseReceipt._check_no_linked_purchase_invoice()` — PR cancel
  blocked while a PI against the same PO is submitted (would orphan
  SRBNB).
- `SalesInvoice._check_no_linked_payment_entry()` — SI cancel blocked
  while a PE allocation exists (would orphan AR credit).
- `PurchaseInvoice._check_no_linked_payment_entry()` — symmetric for AP.

Pattern for adding new cancel dependencies: cheap existence check at the
top of `on_cancel`, raising cleanly so the outer transaction rolls back.

## Journal Entry against invoices

A JE row with `reference_doctype` + `reference_name` set mirrors the
accounting effect onto the invoice's `outstanding_amount`. Sign:
- Sales Invoice / POS Invoice: `credit − debit` reduces outstanding.
- Purchase Invoice: `debit − credit` reduces outstanding.

This keeps AR/AP aging in sync with GL when bookkeepers do write-offs
via JE. `JournalEntry._update_referenced_outstanding` implements it;
`_validate_references` blocks cross-party and over-reduction.

## What to sanity-check after any accounting change

1. Trial balance at the end of `test_erp_validation` still nets to zero.
2. P&L isn't polluted by stock adjustments, opening balances, or other
   equity-side activity.
3. The SRBNB balance roughly tracks "PRs that don't yet have a PI" —
   drift here is the canary for flow errors.
