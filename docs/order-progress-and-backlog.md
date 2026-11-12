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
| open | `qty − delivered_qty`. |
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
