# Stock

Moving-average valuation, three contra accounts, five documents that
move stock. Getting one piece wrong drifts inventory silently.

## The three contra accounts

| Contra account | Used by | Semantics |
|---|---|---|
| `stock_received_but_not_billed` (Asset) | PR (as credit), PI `update_stock=0` (as debit) | PR→PI clearing account. Balance shown = goods received but not yet billed. |
| `stock_adjustment_account` (Expense) | Stock Entry Material Receipt/Issue | Manual adjustments, write-offs, found stock. P&L-neutral in the long run. |
| `default_opening_balance_equity` (Equity) | Stock Entry "Opening Stock" | One-time day-one inventory. Stays in equity, doesn't touch P&L. |

**Common mistake:** using Material Receipt for opening stock. It works
for the stock ledger but credits Stock Adjustment (an expense account),
inflating Y1 profit by the full opening inventory value. Use the
"Opening Stock" `stock_entry_type` instead.

## Which Stock account

The stock side of every stock GL entry goes through
`stock_account_for(company, warehouse)`: the warehouse's own `account`
(validated as a leaf Stock account of the company), else the company's
`stock_in_hand_account`, else its first Stock account. Never read
`stock_in_hand_account` directly in a new document; before 1.4.2 only Stock
Entry honoured `Warehouse.account` and the GL drifted from the warehouses'
stock value. Sell-side cost (`build_cost_basis_gl`) and Purchase Receipt are
booked per warehouse.

Cancels go through `make_cancel_sl_entries`, which refuses to drive stock
negative ("goods have already left the warehouse").

## Batches

Full description: [`docs/batches.md`](../batches.md). What to keep in mind
when touching stock code:

- Batch rules live in `stock_ledger.make_sl_entries` (`resolve_entry_batch`,
  `update_stock_balance_detail`), not in the documents. A new stock-moving
  path gets them by passing `batch_no` (and `expiry_date` on incoming
  entries) through; the shared builders already do.
- `Stock Balance Detail` (item × warehouse × batch, `''` = no batch) must
  always add up to `Bin`. Never write either table outside the ledger.
  `stock_ledger_agreement()` is the check; tests assert it returns `[]`.
- Valuation is not per batch. Batch Assignment and same-value moves must
  leave `Bin.stock_value` unchanged and post no GL.
- An entry without a batch for a tracked item is only legal with
  `allow_unassigned_batch` (cancel of a pre-tracking voucher, the source leg
  of a Batch Assignment). Do not set it anywhere else.
- Returns: `workflow.validate_return` checks the batch against the original
  document; `returnable_rows` consumes returned quantities per batch.

## Storage locations

Full description: [`docs/storage-locations.md`](../storage-locations.md).

- A second dimension of `Stock Balance Detail` (`location`, `''` = none),
  checked in `stock_ledger.resolve_entry_location`. Same pattern as batches:
  required where the warehouse uses them, refused elsewhere, enforced at the
  ledger.
- A Material Transfer with the same warehouse on both sides is legal only
  between two different locations; it must leave Bin quantity and value
  unchanged and post no GL.
- `allow_unassigned_location` is set only by the cancel path and by the
  source leg of a move inside one warehouse.
- `get_batch_balances` adds up over locations; use `get_stock_details` for
  the per-location rows.

## Reservations and FEFO

Full description: [`docs/reservations-and-fefo.md`](../reservations-and-fefo.md).

- `stock/reservation.py`. available = physical − blocked − reserved, per
  item × warehouse. What a reservation still holds is derived from the order
  line's `delivered_qty`; never add a counter.
- `Document.submit` / `cancel` wrap every posting in
  `reservation.begin_guard` / `finish_guard`: a document may not leave
  reservations less covered than it found them. The guard is a no-op unless
  an open reservation exists for a touched item. It runs after
  `refresh_order_progress`, which is what consumes reservations.
- `workflow.refresh_order_progress` calls `sync_sales_order` (reserve on
  request at submit, statuses, cancel with the order).
- Sell-side documents call `validate_sellable` before posting (expired,
  blocked, shelf life). A new sell-side path must call it too.
- FEFO (`suggest_picks`) never reads valuation, and nothing in valuation
  reads expiry dates.

## Consignment

Full description: [`docs/consignment.md`](../consignment.md).

- A consignment warehouse is an own warehouse with `consignment_customer`.
  Stocking and recall are plain Material Transfers.
- A Consignment Withdrawal posts nothing itself: its `on_submit` creates one
  Sales Invoice with `update_stock`, which is the only thing that moves stock
  and books revenue. Never add a second movement for the same withdrawal.
- Idempotency is `(external_source, external_reference)` among active
  withdrawals plus `content_hash`; see `record_withdrawal`.
- Sell-side documents call `validate_consignment_sale` (only the warehouse's
  customer buys out of it).

## Moving-average cost

- The `Bin` table holds current `valuation_rate` per item+warehouse.
- Incoming SLEs with `incoming_rate=0` fall back to the current
  `valuation_rate` (symmetric with outgoing's behaviour). This is what
  lets customer returns land at cost.
- Outgoing SLEs with `outgoing_rate=0` use the current `valuation_rate`.
  **All sell-side docs (DN, SI update_stock, POS update_stock) pass 0** —
  passing the sell rate instead posts COGS at revenue value.
- Stock Entry receipts explicitly distinguish a supplied zero rate from a
  missing rate. Issues use current cost; transfers copy actual outgoing value.
  Their GL is built from posted SLE values per warehouse. Cancellation passes
  the original rates explicitly so later valuation changes do not alter the
  reversal. Transient `*_rate_is_explicit` flags never become database columns.

## The helpers

All stock-moving documents share four functions in `stock_ledger.py`:

```python
build_sell_side_sles(doc, items)     # Negative qty at moving-average cost.
                                     # Used by DN, SI update_stock, POS.
build_buy_side_sles(doc, items)      # Positive qty at supplier rate.
                                     # Used by PI update_stock only.
build_cost_basis_gl(doc, remarks=…)  # Read stock_value_difference from
                                     # posted SLEs, build Dr COGS / Cr SIH.
                                     # Run AFTER make_sl_entries.
reverse_stock_sles(sl_entries)       # Flip qty + rate for on_cancel.
```

Order of operations in `on_submit` is load-bearing:

1. Build SLEs with `build_*_sles`.
2. `make_sl_entries(...)` → SLEs persist with `stock_value_difference`.
3. Extend `gl_entries` with `build_cost_basis_gl(...)` — it **reads**
   from the just-posted SLEs.
4. `make_gl_entries(gl_entries)`.

If you swap 2 and 3, the cost-basis GL query returns 0 and GL is wrong.

## update_stock on invoices

- **`update_stock=1` means "this invoice also ships/receives goods".**
- Enabling it requires a `warehouse` on every stock-item line. See
  `PurchaseInvoice._validate_stock_warehouses` for the pattern (mirror on
  SI for any new flow).
- **Exclusivity guards** prevent double-ship/double-receive:
  `workflow.validate_order_quantities` lets DNs/PRs plus direct-stock
  invoices move at most the ordered quantity per order line (the rest of a
  partly delivered line can ship with an `update_stock` invoice).
  `SalesInvoice._validate_no_double_shipment` /
  `PurchaseInvoice._validate_no_double_receipt` additionally block
  `update_stock=1` on lines that name the order but not the order line,
  when a DN/PR exists for it.
- Returns (`is_return=1`) are exempt from those guards — the return
  reverses its own original, not a separate DN/PR.

## Adding a new stock-moving document

1. Use the shared helpers. Do **not** roll your own SLE builder — POS
   did this initially and silently posted COGS at sell value.
2. Register a voucher_type so `make_reverse_gl_entries` can find it on
   cancel.
3. Add a regression test that asserts `stock_value_difference` matches
   the GL cost-basis amount.
4. Decide and document: does this new path need a "no double move" guard
   against the existing paths? (Usually yes.)
