# Reservations, availability and FEFO

Available from 1.4.8 (migration 54). Nothing is reserved or blocked by the
upgrade; sales orders reserve stock only when asked to.

## The four quantities

Per item and warehouse, every unit of stock is in exactly one of the last
three:

| Quantity | Meaning |
|---|---|
| **physical** | What the stock ledger says is there. |
| **blocked** | Physical stock that may not be sold: an **expired** batch (expiry date before today) or a batch marked **blocked**. |
| **reserved** | Stock set aside by open Stock Reservations. Reservations are made against stock that is not blocked. |
| **available** | physical − blocked − reserved. |

The report `stock-availability` (REST `GET /api/reports/stock-availability`,
chat / MCP `get_report`, UI Reports → Stock Availability) shows them, next to
`open_sales_order_qty` (ordered by customers, not yet delivered) and
`on_purchase_order_qty` (ordered from suppliers, not yet received).

`Bin.reserved_qty` is that open sales order quantity. It is planning
information and never held stock back; it keeps its meaning. A Stock
Reservation is the thing that does.

## Stock Reservation

Document type `stock-reservation`. It has no submit; it lives by its status.

| Field | |
|---|---|
| `item_code`, `warehouse`, `reserved_qty` | What is set aside, and where. |
| `sales_order`, `sales_order_item` | The order line it is for. One reservation per line; `reserved_qty` is the total reserved for the line. |
| `remarks` | Required when there is no sales order: what the stock is set aside for. |
| `status` | Reserved, Partially Delivered, Delivered, Released, Cancelled. |
| `customer`, `reserved_by` | Filled by the server. |

- **For an order:** the action `reserve_sales_order` (REST
  `POST /api/actions/reserve_sales_order`, chat, MCP) reserves what is
  available for the undelivered part of each stock line and reports
  `short_qty` for the rest. Calling it again after goods arrive tops the
  reservation up. A Sales Order with `reserve_stock = 1` does this on submit.
- **Without an order:** create a Stock Reservation with `remarks` (stock set
  aside for a demonstration, a field rep, a quality check).
- **A reservation is hard.** It is refused when more than the available
  quantity is asked for, so two orders cannot reserve the same unit. And no
  delivery, direct-stock invoice, issue, transfer out of the warehouse or
  cancellation of a receipt may take stock that is reserved for someone else:
  the document is refused as a whole, naming the orders it is reserved for.
- **Deliveries consume it.** What a reservation still holds is its
  `reserved_qty` minus the delivered quantity of the order line (delivery
  notes and direct-stock invoices). It is derived from the order, not counted
  separately: a partial delivery, a return and a cancelled delivery all show
  up at once. Fully delivered lines become Delivered.
- **Giving stock back:** set status to Released, or lower `reserved_qty`.
  Cancelling the sales order cancels its reservations.

Reservations are per item and warehouse, not per batch or storage location:
which batch goes out is decided when picking.

## Blocked batches and shelf life

| Field | |
|---|---|
| `Batch.blocked`, `Batch.blocked_reason` | A blocked batch (recall, quality check). |
| `Item.min_shelf_life_days` | A sale needs a batch with at least this many days left. 0 = it only must not be expired. |
| `shelf_life_exception` on delivery note and invoice lines | The approved reason for shipping a batch with less shelf life left. The user who submits is stored in `shelf_life_exception_by`. |

- A **sale** (Delivery Note, Sales or POS Invoice with stock update; not a
  return) never ships a blocked or expired batch. There is no exception for
  those.
- A batch with **less than the required shelf life** left needs
  `shelf_life_exception` on the line. A batch without an expiry date counts as
  too short when the item requires shelf life.
- "Not sellable" is not "immovable": expired and blocked stock can be
  transferred (for example to a quarantine warehouse), returned to the
  supplier or written off with a Stock Entry.
- The date that counts is the later of the document's posting date and today.

A deployment adjusts the requirement per customer or item with a rule:

```python
from lambda_erp.stock.reservation import register_availability_rule

def short_dated_customers(item_code, customer, days):
    return 0 if customer in ACCEPTS_SHORT_DATES else None   # None = no change

register_availability_rule(short_dated_customers)
```

## Picking: first expired, first out

`suggest_picks` (REST `POST /api/actions/suggest_picks`, chat, MCP;
`lambda_erp.stock.reservation.suggest_picks` in code) proposes which batches
and storage locations to take a quantity from.

- Order: expiry date ascending, batches without a date last, then batch label,
  then storage location. The same inputs give the same picks.
- Blocked and expired batches are never proposed. Batches with too little
  shelf life are listed under `excluded` with the reason, or offered with
  `needs_exception` when `include_short_dated` is set.
- Stock reserved for other orders is not offered; pass `sales_order_item` to
  pick against an order's own reservation.
- It is **read-only**: no reservation, no stock movement, no invoice.

Converting a Sales Order to a Delivery Note uses it: for a batch-tracked item
or a warehouse with storage locations, the draft gets one line per batch and
location in that order, for as much as can be picked now. The rest of the
order line stays open. It is a draft; lines can be changed before submit.

FEFO is a picking strategy. Valuation is unaffected: it stays moving average
per item and warehouse.

## Limits to know

- Reservations do not name a batch or storage location.
- A batch that expires while it backs a reservation makes the reservation
  uncovered; the availability report then shows `available_qty` 0 and
  documents that would make it worse are refused, but nothing is released
  automatically.
- Blocking is per batch. Storage locations and warehouses have no block flag;
  use a separate warehouse for quarantine stock.
