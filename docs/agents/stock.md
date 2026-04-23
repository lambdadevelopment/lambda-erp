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

## Moving-average cost

- The `Bin` table holds current `valuation_rate` per item+warehouse.
- Incoming SLEs with `incoming_rate=0` fall back to the current
  `valuation_rate` (symmetric with outgoing's behaviour). This is what
  lets customer returns land at cost.
- Outgoing SLEs with `outgoing_rate=0` use the current `valuation_rate`.
  **All sell-side docs (DN, SI update_stock, POS update_stock) pass 0** —
  passing the sell rate instead posts COGS at revenue value.

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
  `SalesInvoice._validate_no_double_shipment` blocks `update_stock=1`
  when a DN already shipped from the same SO line;
  `PurchaseInvoice._validate_no_double_receipt` mirrors for PR/PO.
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
