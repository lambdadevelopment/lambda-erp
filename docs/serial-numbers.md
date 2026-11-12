# Serial numbers

One number, one unit. Available from 1.4.13 (migration 60). Nothing changes
for an item until it is switched to serial tracking.

## The rules

- A serial number is unique **per item**. Two items may carry the same
  number; they are two units. A lookup by number alone returns both.
- A serial number identifies **exactly one unit**, and that unit is in at
  most one place at a time.
- Every stock movement of a serial-tracked item lists its serial numbers,
  **as many as its quantity**, each once. The quantity is a whole number.
- A unit **comes in** only if it is not in stock (a new number, or one that
  left earlier and returns). It **goes out** only from the warehouse it is
  in, and from its batch and storage location where the line names them.
- A unit that left (delivered, issued, returned to the supplier, written
  off) keeps its record and can come back, for example on a customer return.
- A failure rolls back the whole document.

These are enforced where the stock ledger is written, so they hold for the
UI, REST, chat, MCP and plugins alike.

## Data model

| Record | Meaning |
|---|---|
| `Item.has_serial_no` | The item is serial-tracked. Can be combined with batch tracking. |
| `serial_no` on lines | Stock Entry, Delivery Note, Purchase Receipt, invoice, Stock Count and Consignment Withdrawal lines. One or more numbers, separated by line breaks, commas or semicolons. |
| `Serial No` (master `serial-no`) | One row per item and serial number: where the unit is now (`warehouse`, `location`, `batch_no`) or that it is gone (`status`: In Stock, Delivered, Issued, Returned to Supplier, Written Off) and to which `customer`. Written by the stock ledger only. |
| `Serial Movement` | Every movement of every unit with its document and line. |

Quantities and values stay where they were (`Bin`, `Stock Balance Detail`):
serial numbers add identity, not another stock count.
`lambda_erp.stock.serial.serial_agreement()` returns the units whose
movements do not match their state, and warehouses with more numbered units
than stock (empty when healthy).

## On documents

- **Receipts** create the units. A number that is already in stock is refused.
- **Deliveries and invoices with stock update** take units out and record the
  customer on them. Converting a Sales Order to a Delivery Note proposes the
  units together with batch and storage location (first expired first out,
  then by serial number).
- **Transfers** move units between warehouses and storage locations.
- **Returns** must name units that were on the original document.
- **Cancel** reverses the units of the document; cancelling a receipt whose
  unit has since left is refused.
- **Stock counts**: a line of a serial-tracked item that differs lists the
  serial numbers found in addition, or missing.

## Switching an existing item to serial tracking

Set `has_serial_no` on the item. Units already in stock stay there **without
a number**: none is invented. They cannot leave until they have one. Give
them their numbers with a Stock Entry of type **Serial Assignment**:

```json
{"stock_entry_type": "Serial Assignment", "company": "Example Co",
 "items": [{"item_code": "DEVICE-1", "qty": 2, "t_warehouse": "Main",
            "serial_no": "SN-1001\nSN-1002"}]}
```

It changes neither quantity nor value and posts no GL. It is refused for more
units than are without a number. Serial tracking cannot be switched off for
an item that has serial numbers.

## Finding a unit

| | |
|---|---|
| REST | `GET /api/reports/serial-trace?serial_no=…` (optionally `item_code`) |
| Chat / MCP | `get_report` with `report_type: "serial-trace"` |
| Master | `/api/masters/serial-no`, `search_masters` with `master_type="serial-no"` |

`serial-trace` returns, per matching item, where the unit is now and every
movement with its document and the customer or supplier.

## For plugins

`lambda_erp.stock.serial`: `get_serial(item_code, serial_no)`,
`serials_in_stock(item_code, warehouse, batch_no, location)`,
`serial_trace(serial_no, item_code)` and `parse_serials(text)`. A plugin
document that moves stock passes `serial_no` in its stock ledger entries
(the shared builders take it from the line).
