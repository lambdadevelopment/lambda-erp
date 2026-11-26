# Order progress, partial quantities and backlog

Available from 1.4.10 (migration 56).

## Quantities per order line

Each is derived from the submitted documents every time one of them is
submitted or cancelled. None is a counter that could drift, and none can be
set by a client.

| Sales order line | Meaning |
|---|---|
| `qty` | Ordered. |
| `delivered_qty` | Delivered and not returned: delivery notes and direct-stock invoices, net of returns. |
| `returned_qty` | How much of what was delivered came back (new). |
| `billed_qty` | Invoiced, net of credit notes for returned goods. Amount-only credit notes do not change it. |
| `closed_qty` | Closed by hand: will not be delivered any more (1.4.31, see below). Set only by the close / reopen actions. |
| open | `qty − closed_qty − delivered_qty`. |
| reserved | What the line's stock reservation still holds (see `reservations-and-fefo.md`). |

Purchase order lines mirror this with `received_qty`, `returned_qty` and
`billed_qty`.

Existing order lines get their `returned_qty` the next time a document of
the order is submitted or cancelled.

## Partial deliveries

A delivery for less than ordered leaves the rest of the line open. Converting
the order again proposes exactly the remainder. Converters only propose;
submit rechecks against the order line under a lock, so a repeated or
concurrent conversion cannot deliver or bill more than was ordered. A return
or a cancelled delivery reopens the quantity.

A delivery can leave out lines entirely: remove them from the converted
draft (or set nothing to deliver for them) and they stay open for the next
delivery. A line can be delivered in as many steps as needed; each step is
its own delivery note.

## Closing the rest of a line

Available from 1.4.31 (migration 71).

When a customer cancels what is still open after a partial delivery (or a
supplier will never send the rest), close it instead of cancelling or
amending the order:

```json
POST /api/actions/close_order_lines
{"order": "SO-0093", "lines": ["3"], "reason": "Customer no longer needs it"}
```

- `doctype` is `Sales Order` (default) or `Purchase Order`. Without `lines`
  it closes everything open on the order; `lines` takes row names or
  positions; `qty` closes only part of one line. A reason is required.
- The line keeps its ordered `qty`. `closed_qty`, `close_reason`,
  `closed_by` and `closed_at` record the closing. Progress (`per_delivered`,
  `per_billed`, status), the backlog reports and stock planning (Bin
  `reserved_qty` / `ordered_qty`) then count against `qty − closed_qty`. An
  order whose rest is closed is Completed once what was delivered is billed.
- Deliveries, receipts and invoices posted before stay unchanged. Later ones
  cannot take the closed quantity: converters stop proposing it and submit
  refuses it, also when billing straight from the order.
- Only what is neither delivered nor invoiced can be closed. An invoiced
  quantity needs a credit note first.
- A stock reservation for the line is cut back to what can still be
  delivered (released if nothing was delivered).
- `reopen_order_lines` (`order`, optional `lines`) undoes a closing; a
  released reservation is not restored.

UI: a submitted sales or purchase order shows the quantities per line
(ordered, delivered / received, billed, closed, open). Select open lines,
give a reason and close them there; a closed line has *Reopen*.

## Deliver first, invoice afterwards

A submitted Delivery Note converts to a Sales Invoice (UI: *Create Invoice*;
REST / chat / MCP: convert `delivery-note` to `Sales Invoice`).

- It bills what that delivery delivered and has not been billed yet. Its
  lines carry `delivery_note` / `dn_detail` and the order line behind them, so
  order billing progress follows too.
- It never moves stock: `update_stock` is refused on an invoice that bills a
  delivery.
- A delivery cannot be billed for more than it delivered. The delivery note
  line keeps `billed_qty`; the note's `per_billed` and status follow
  (Submitted → To Bill → Completed).

The purchasing side works the same way (1.4.16): a submitted Purchase Receipt
converts to a Purchase Invoice (*Create Invoice*; convert `purchase-receipt`
to `Purchase Invoice`). It bills what was received and not billed yet, at the
received price, carries `purchase_receipt` / `pr_detail` and the order line,
never moves stock, and the receipt line keeps `billed_qty` with the receipt's
`per_billed` and status following.

### One invoice for several deliveries (collective / monthly invoice)

From 1.4.31. Deliver through the month without invoicing, then bill
everything delivered to the customer on one invoice:

- Report **`unbilled-deliveries`** (REST `GET /api/reports/unbilled-deliveries`,
  chat / MCP `get_report`, UI Reports → Unbilled Deliveries): per line of a
  submitted delivery note (not a return) what is not billed yet:
  `unbilled_qty`, `rate`, `unbilled_amount`, the order behind it. Filters:
  `customer`, `company`, `from_date`, `to_date` (posting date),
  `delivery_note`.
- Action **`prepare_collective_invoice`**: one DRAFT Sales Invoice for the
  customer's unbilled delivery lines in the period, or for the named
  `delivery_notes`, or only the delivery note `lines` (row names). In the UI,
  pick the customer and period in the report, untick lines to leave them for
  later, and *Create invoice for selected lines*.
- Every invoice line keeps `delivery_note` / `dn_detail` and the order line,
  so the delivery notes and the orders follow (To Bill → Completed). A line
  already billed is never taken again; two drafts over the same deliveries
  cannot both be submitted.
- One invoice bills one customer, company and currency; delivery notes with
  different taxes or exchange rates are refused (invoice them separately).
- `lambda_erp.stock.delivery_note.unbilled_delivery_lines(...)` and
  `make_collective_sales_invoice(...)` are the functions behind them.

Whether an invoice is created right after packing, at month end or by a
person is the deployment's choice. For a step that must happen together with
a document, use the in-transaction hooks:

```python
from lambda_erp.hooks import register_hook

def after_packing(delivery_note):       # runs inside the submit transaction
    ...                                 # raise to roll the delivery back

register_hook("Delivery Note:on_submit", after_packing)
```

`<DocType>:on_submit` and `:on_cancel` run after the document has posted its
ledgers and order progress has been recalculated, still inside the
transaction. `after_submit` / `after_cancel` run after commit, as before.

Converting a Sales Order directly to a Sales Invoice still bills the ordered
quantity regardless of delivery, and a direct-stock invoice (`update_stock`)
still delivers and bills in one step; the order line limits apply to all
three paths together.

## Supplier-confirmed dates

A purchase order line carries `confirmed_date`, `confirmed_qty`,
`confirmation_reference`, `confirmed_by` and `confirmed_at`. On a draft they
are ordinary fields. On a submitted order the action `confirm_purchase_order`
(REST `POST /api/actions/confirm_purchase_order`, chat, MCP) records them:

```json
{"purchase_order": "PO-0042", "confirmed_date": "2026-12-03",
 "confirmation_reference": "AB-4711"}
```

Without `lines` it applies to every open line; with `lines` (row names or
positions) to those. A confirmation is stored on the order lines it belongs
to, with the supplier's reference: there is no free-floating confirmation.
Recording it again replaces the date.

## Backlog reports

Both are read-only. Asking them delivers, reserves and invoices nothing.

**`sales-backlog`**: open sales order lines (REST
`GET /api/reports/sales-backlog`, chat / MCP `get_report`, UI Reports → Sales
Backlog). Per line: ordered, delivered, returned, billed, open, reserved,
`deliverable_qty`, `short_qty`, `on_purchase_order_qty`,
`next_expected_date` and the purchase orders behind it. Filters: `customer`,
`item_code`, `item_prefix`, `warehouse`, `sales_order`, `only_deliverable`.

`deliverable_qty` is what could be delivered now. Stock is shared out in the
order the lines are due (delivery date, order date, order, line): a line
first gets what its own reservation holds, then free stock as far as it
lasts. The sum over all lines never exceeds what is there. After a goods
receipt, `only_deliverable=true` lists the open orders that can now go out.

**`purchase-backlog`**: open purchase order lines with `expected_date` (the
confirmed date, else the order's schedule date), `confirmed`, `overdue` and
`days_overdue`. Filters: `supplier`, `item_code`, `warehouse`,
`overdue_only`. It shows quantities and dates, no prices.

## For plugins

- `validation.register_order_reference(doctype, order_type, parent_field,
  line_field, party_field)`: a plugin document whose lines fulfil or bill
  order lines is checked against them like the built-in documents.
- `workflow.register_return_type(doctype)`: a plugin document that supports
  returns.
- `lambda_erp.backlog.sales_backlog(...)`, `purchase_backlog(...)` and
  `confirm_purchase_order_lines(...)` are the functions behind the reports
  and the action.
- `lambda_erp.order_closing.close_order_lines(...)` /
  `reopen_order_lines(...)` close and reopen order lines;
  `order_closing.effective_qty(row)` is what a line still stands for. A
  plugin that computes open order quantities itself must subtract
  `closed_qty`.
