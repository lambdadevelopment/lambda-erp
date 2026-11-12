# Storage locations

Places inside a warehouse (a shelf, a bin, a cupboard): every stock movement
in a warehouse that uses them names the location, and quantities are kept and
checked per location. Available from 1.4.7 (migration 53). Nothing changes for
a warehouse until it is switched to storage locations.

A warehouse and a storage location are different things. The `Bin` table,
despite its name, is the stock total and value of an item in a warehouse, not
a place.

## Data model

| Record | Meaning |
|---|---|
| `Warehouse.use_locations` | The warehouse has storage locations. |
| `Storage Location` (master `storage-location`) | One row per **warehouse + location label** (`warehouse`, `location`, `description`, `disabled`). The same label can exist in several warehouses. The record id (`LOC-00001`) is internal. |
| `location` on lines | Delivery Note, Purchase Receipt, Sales / Purchase / POS Invoice lines: the label, read together with the line's warehouse. |
| `s_location`, `t_location` on Stock Entry lines | The location on the source side and on the target side. |
| `Stock Ledger Entry.location` | The location of each movement. |
| `Stock Balance Detail.location` | Quantities are kept per item × warehouse × batch × location. `''` is stock without a location. |

Several items and batches can lie in one location, and one item in several
locations. The rows of a warehouse still add up to its `Bin`, and
`stock_ledger_agreement()` checks it. Valuation is unchanged: per item and
warehouse, not per location.

## Rules

Enforced where the stock ledger is written, so identical for the UI, REST,
chat, MCP and plugins.

- **Warehouse with locations: location required** on every line that moves
  stock there. Drafts may be saved without it; submit is refused.
- **Warehouse without locations: no location.** A location on such a line is
  refused.
- The location must exist in that warehouse. A **disabled** location takes no
  new stock but can still be emptied.
- **Stock is checked per location** (and per batch within it): an issue cannot
  take more from a location than lies there.
- **Moves inside a warehouse** are a Stock Entry of type Material Transfer with
  the same warehouse on both sides and different `s_location` / `t_location`.
  They change neither the warehouse's total quantity nor its value, and post
  no GL.
- **A failure rolls back the whole document.**
- Returns propose the location of the original line; it can be changed.
- **Master:** `warehouse` and `location` of a storage location cannot be
  changed once the stock ledger has it. Storage locations cannot be switched
  off for a warehouse while stock lies in a location.

## Moving a whole location

The action `prepare_location_move` (REST `POST /api/actions/prepare_location_move`,
chat and MCP) creates a **draft** Material Transfer with one row per item and
batch that lies in a location:

```json
{"warehouse": "Main", "from_location": "A83", "to_location": "B4"}
```

Nothing moves until the draft is submitted. Submitting moves everything or,
if something has left the location in the meantime, nothing. `to_warehouse`
moves the content to another warehouse.

## Switching an existing warehouse to storage locations

Set `use_locations` on the warehouse and create its locations. Stock that is
already there stays **without a location**: no location is invented. It cannot
leave the warehouse until it is put away, with a Material Transfer inside the
warehouse that leaves `s_location` empty:

```json
{"stock_entry_type": "Material Transfer", "company": "Example Co",
 "items": [{"item_code": "ITEM-1", "qty": 6, "s_warehouse": "Main",
            "t_warehouse": "Main", "t_location": "A12"}]}
```

Documents posted before the switch can still be cancelled; their reversal goes
to stock without a location.

## The combined view

`stock-detail` shows where stock is in one query: item, item group, warehouse,
storage location, batch, expiry date and quantity.

| | |
|---|---|
| REST | `GET /api/reports/stock-detail` |
| Chat / MCP | `get_report` with `report_type: "stock-detail"` |
| UI | Reports → Stock by Location & Batch |

Filters: `item_code`, `item_prefix` (item codes starting with…), `item_group`,
`warehouse`, `location`, `batch_no`, `expiring_before`, `expiring_after`,
`include_empty`. `batch-stock` remains the same view added up over locations;
`batch-movements` now carries the location of each movement.

## For plugins

- Read with `stock_ledger.get_stock_details(item_code, warehouse, batch_no, location)`
  and `stock_ledger.get_storage_location(warehouse, location)`.
- A plugin document that moves stock passes `location` in its stock ledger
  entries (the shared builders take it from the line's `location`).
- Add columns to `Storage Location` with `db.ensure_column` in a registered
  migration; the master API picks them up.
