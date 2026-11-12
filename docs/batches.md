# Batches (lot numbers)

Batch-tracked stock: every movement of a tracked item names its batch, and
quantities are kept and checked per batch. Available from 1.4.5 (migration 51).
Nothing changes for an item until it is switched to batch tracking.

Not included: serial numbers, a stock count / reconciliation document,
blocking of batches, picking suggestions (FEFO). Storage locations (1.4.7)
are a second dimension next to the batch: see `docs/storage-locations.md`.

## Data model

| Record | Meaning |
|---|---|
| `Item.has_batch_no` | The item is batch-tracked. |
| `Item.has_expiry_date` | Every batch of the item needs an expiry date. Requires `has_batch_no`. |
| `Batch` (master `batch`) | One row per **item + batch label** (`item_code`, `batch_no`, `expiry_date`, `manufacturing_date`, `description`, `disabled`). The label alone is not unique: two items can carry the same label, and they are two batches. The record id (`BATCH-00001`) is internal. |
| `batch_no` on lines | Stock Entry, Delivery Note, Purchase Receipt, Sales / Purchase / POS Invoice lines. The label, read together with the line's item. |
| `expiry_date` on receiving lines | Stock Entry, Purchase Receipt, Purchase Invoice lines: the expiry date of a batch that is new. |
| `Stock Ledger Entry.batch_no` | The batch of each movement. |
| `Stock Balance Detail` | Quantity per item × warehouse × batch (× storage location from 1.4.7). `batch_no = ''` is stock without a batch. |

`Bin` stays the quantity and moving-average value per item × warehouse.
Valuation is not per batch. For every item × warehouse the `Stock Balance
Detail` rows add up to the `Bin`, and both equal the sum of the stock ledger;
`lambda_erp.stock.stock_ledger.stock_ledger_agreement()` returns the rows
where they do not (empty when healthy).

## Rules

All of these are enforced where the stock ledger is written
(`stock_ledger.make_sl_entries`), so they hold for the UI, REST, chat, MCP and
plugins alike.

- **One batch per line.** A quantity from two batches is two lines.
- **Tracked item: batch required** on every line that moves stock. Drafts may
  be saved without it (an order converted to a delivery note does not know its
  batch yet); submit is refused until it is there.
- **Untracked item: no batch.** A `batch_no` on such a line is refused.
- **Incoming stock creates the batch** if the label is new for the item. For
  an item with expiry dates the line must then carry `expiry_date`. A known
  batch needs no date; a different date than the batch has is refused.
- **Outgoing stock needs an existing batch with enough quantity** in that
  warehouse. 2 of batch A and 8 of batch B is 10 in stock, and an issue of 3
  from A is refused.
- **A failure rolls back the whole document.** No line is posted.
- **Transfers** move a batch as it is (`batch_no` on the line).
- **Returns** (delivery, receipt, direct-stock invoice) must name a batch that
  was on the original document, and at most the quantity of it that the
  original moved, less earlier returns. The return converters carry the batch.
- **Cancel** reverses the document's batches and is refused when a received
  batch has since left the warehouse.
- **Master:** `item_code` and `batch_no` of a batch cannot be changed once the
  stock ledger has it. Batch tracking cannot be switched off for an item with
  batches in the ledger. Only stock items can be tracked.

On PostgreSQL, submit and cancel lock the document's items (as before) and the
affected `Stock Balance Detail` rows, so two documents cannot draw the same
batch twice. SQLite is single-writer.

## Switching an existing item to batch tracking

Set `has_batch_no` on the item. Stock that is already there stays where it is,
**without a batch**: no batch is invented. It cannot leave the warehouse until
it has one. Give it one with a Stock Entry of type **Batch Assignment**:

```json
{"stock_entry_type": "Batch Assignment", "company": "Example Co",
 "items": [{"item_code": "ITEM-1", "qty": 6, "t_warehouse": "Main",
            "batch_no": "LOT-2026-04", "expiry_date": "2028-04-30"}]}
```

It moves the quantity from "without batch" to the batch in the same warehouse,
at the same value, and posts no GL. It is refused for more than the quantity
that has no batch. Documents posted before the switch can still be cancelled;
their reversal goes to stock without a batch.

## Queries

| Report | REST | Chat / MCP `get_report` |
|---|---|---|
| Stock per item × warehouse × batch with expiry | `GET /api/reports/batch-stock` | `batch-stock` |
| Movements of a batch with document, line and customer / supplier | `GET /api/reports/batch-movements?batch_no=…` | `batch-movements` |

`batch-stock` filters: `item_code`, `warehouse`, `batch_no`, `item_prefix`
(item codes starting with…), `expiring_before` (date), `include_empty`.
A lookup by `batch_no` alone returns every item that has a batch with that
label. `batch-movements` needs `batch_no`; add `item_code` to narrow it.

The batch master is available like any other master: `/api/masters/batch`,
`search_masters` with `master_type="batch"`.

## For plugins

- Read balances with `stock_ledger.get_batch_balances(item_code, warehouse,
  batch_no)` and batches with `stock_ledger.get_batch(item_code, batch_no)`.
- A plugin document that moves stock uses the shared builders
  (`build_sell_side_sles` / `build_buy_side_sles`) or passes `batch_no` (and
  `expiry_date` on incoming entries) in its own entries to `make_sl_entries`;
  the rules above then apply to it unchanged.
- Add columns to `Batch` with `db.ensure_column` in a registered migration
  (for example a certificate reference); the master API picks them up.
