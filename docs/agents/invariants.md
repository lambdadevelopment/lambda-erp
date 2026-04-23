# Invariants

Rules the system relies on. Some are enforced in code; some are enforced
only by convention and will bite you silently if broken. Check this list
before touching any accounting, stock, or lifecycle path.

## Enforced — don't try to bypass

- **Submitted documents are immutable.** `Document.save()` raises if
  `docstatus != DRAFT`. Post-submit field updates (`outstanding_amount`,
  `billed_qty`, `modified`) go through `db.set_value` directly, never
  round-trip via `.save()`. (`lambda_erp/model.py`)
- **Every voucher that posts GL must balance to the cent.** Enforced by
  `general_ledger.process_debit_credit_difference`. Small rounding residues
  post to the company's Round Off account; gaps > 0.5 raise.
- **Cancel chain guards.** A PR cannot be cancelled while a PI clears
  SRBNB against the same PO. SI/PI cannot be cancelled while a Payment
  Entry is allocated against them. Add a guard to `on_cancel` **before**
  any ledger mutation — raising after a partial write leaves the books
  half-reversed until the outer transaction rolls back.
- **Return doubles.** `_validate_return` caps qty at `original − already
  returned`, and `_validate_return_value` caps grand_total the same way.
  Never bypass either — the floor-at-0 in `_update_original_outstanding`
  is defense-in-depth, not correctness.

## Convention — easy to break, hard to spot

- **Sign convention for outstanding.** Normal invoices carry positive
  outstanding; return invoices carry negative. `PaymentEntry._update_outstanding`
  switches direction based on the sign. If you add a new settlement path
  (e.g., on Journal Entry — see `_update_referenced_outstanding`), mirror
  the same sign logic.
- **Stock-moving documents share helpers.** DN, SI `update_stock`, POS
  `update_stock`, PI `update_stock` all go through
  `stock_ledger.build_sell_side_sles` / `build_buy_side_sles` /
  `build_cost_basis_gl` / `reverse_stock_sles`. Before these existed, POS
  silently drifted (sell-rate SLE, no GL). If you add a fifth path, **use
  the helpers** or the drift pattern repeats.
- **Material Receipt vs Opening Stock.** Material Receipt contras to
  `stock_adjustment_account` (Expense — write-offs, found stock). Opening
  Stock contras to `default_opening_balance_equity` (Equity — day-one
  inventory). Don't conflate; opening stock via Material Receipt would
  pad the P&L.
- **PI `items.expense_account` routes three ways.** For stock items with
  `update_stock=1` → Stock In Hand. For stock items with `update_stock=0`
  → SRBNB (to clear a prior PR). For non-stock → default_expense_account.
  If you touch `_set_missing_accounts`, preserve all three branches.
- **Billing recalc beats incremental.** `_update_sales_order_billing`
  (SI side) recalculates from `SUM(qty)` across all submitted SIs. PI
  billing is incremental (`+= qty`) and therefore drift-prone on failed
  submits. Prefer the SUM pattern in new code.
- **Opening Stock + opening-balances wizard are deterministic.** The
  simulator pins `start=2023-04-20 end=2026-04-20 seed=42`. The demo chat
  script references specific invoice names produced by that sim. Breaking
  determinism breaks the live demo.

## Declarative validations — declare them on every new document

The `Document` base class auto-validates four things from class attributes.
When you add a new `Document` subclass, declare whatever applies:

- `LINK_FIELDS = {field: "Master Doctype"}` — static FK-like links.
- `CHILD_LINK_FIELDS = {child_key: {field: "Master Doctype"}}` — same on
  child table rows.
- `DYNAMIC_LINK_FIELDS = {field: (type_field, {type_value: doctype})}` —
  links whose target doctype depends on a sibling type field (party/party_type).
- `ACCOUNT_TYPE_CONSTRAINTS = {field: {"root_type"|"account_type": "..."}}`
  — prevent income going to an expense account and similar footguns.

Existing SI/PI/PE/JE classes are the examples.
