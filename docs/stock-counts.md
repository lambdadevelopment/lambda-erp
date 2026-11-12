# Stock counts

Compare what is in a warehouse with what the system says, and correct the
difference with a reason on record. Available from 1.4.12 (migration 58).

## The document

A **Stock Count** covers one warehouse. It can be an own warehouse or a
customer's consignment warehouse.

| Field | |
|---|---|
| `warehouse`, `posting_date` | Where and when. |
| `reason` | Why stock is corrected. Required to post. |
| `counted_lines`, `difference_lines` | How many lines were counted and how many differ (filled by the server). |
| Lines: `item_code`, `batch_no`, `location` | One line per item, batch and storage location. |
| Lines: `system_qty` | What the system expected when the count was prepared. |
| Lines: `counted_qty` | What was counted. Empty = not counted; the line is left alone. |
| Lines: `difference_qty` | `counted_qty − system_qty` (filled by the server). |
| Lines: `expiry_date` | For a batch the system does not know yet. |

The document carries quantities only. It has no prices, values or cost, so a
user whose access profile hides cost can still fill one in.

## Three steps

**1. Prepare.** The action `prepare_stock_count` (REST
`POST /api/actions/prepare_stock_count`, chat, MCP) creates a draft with a
line for everything the system has in the warehouse:

```json
{"warehouse": "Main", "location": "A12"}
```

Optional `location`, `item_code` and `item_prefix` limit it to one storage
location, one item or item codes starting with a prefix: a full count, or a
small one for one shelf. The draft prints as a count sheet (PDF).

**2. Count.** Enter `counted_qty` on the lines that were counted and save.
Add a line for stock the system does not list (a batch found on the shelf);
its `system_qty` is filled in. Saving a draft changes no stock, however often.

**3. Post.** Submitting books `counted_qty − system_qty` per line into the
stock ledger, with the count as the document behind each movement and its
`reason` kept. Lines without a counted quantity are not touched.

- All stock rules apply: batches on batch-tracked items (a new batch needs
  its expiry date), a storage location where the warehouse uses them, no
  quantity below zero.
- Differences are valued at the item's current moving-average cost in the
  warehouse and booked against the company's stock adjustment account. A
  correction between two batches or locations of the same item changes no
  value and posts nothing to the general ledger.
- Posting is **refused when stock on a counted line has moved** since the
  count was prepared, naming the lines. Refresh the draft
  (`prepare_stock_count` with `stock_count`): system quantities are brought
  up to date and counted quantities kept. Check those lines again, then post.
- Stock that is **reserved** for a sales order cannot be counted away;
  release the reservation first.
- A posted count can be cancelled; that reverses its movements.

## Who counts and who posts

Counting and posting are different actions, so an access profile can allow
one without the other:

```json
{"default": ["read"],
 "doctypes": {"Stock Count": ["read", "create", "update"]}}
```

A user with this profile (and the manager role) can prepare a count, enter
what they find and save it. They cannot submit it, and cannot correct stock
any other way. Someone who may submit Stock Counts reviews the differences
and posts them.

## What a count is not

A count corrects quantities from now on. It does not change delivery notes,
invoices or any other earlier document, and it should not be used to record
where missing goods are believed to have gone: that is not something a count
can know. The history of a batch (`batch-movements`) shows the count as a
movement of its own, next to the deliveries.

## Stock from before batches or storage locations

Stock that has no batch (the item became batch-tracked later) or no storage
location (the warehouse got locations later) appears on the count as a line
without one. Counting it as 0 and the same goods on a line with their batch or
location assigns them in one posting.

## For plugins

`lambda_erp.stock.stock_count.prepare_stock_count(...)` returns the draft
document; `StockCount` is an ordinary submittable document, so
`Stock Count:before_submit` / `on_submit` hooks work, for example to require
a second person for large differences.
