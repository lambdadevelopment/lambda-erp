# Units of measure

An item is stocked in one unit, its `stock_uom` (pieces). Customers and
suppliers often order and price it in another: a carton of 100, a pack of 6.
Since 1.4.22 an item can have further units, and a document line can be
entered in one of them.

No item has a further unit after an upgrade, and no existing line changes
(migration 67).

## Giving an item a further unit

Create an **Item Unit** (Masters → Item Unit, document type `item-unit`):

```json
{"item_code": "GLOVE-M", "uom": "Karton", "conversion_factor": 100}
```

- `conversion_factor` is how many stock units one of this unit holds. It is
  a positive number; it need not be whole (a unit smaller than the stock
  unit has a factor below 1).
- `uom` is the name as it is written on orders. It cannot be the item's own
  `stock_uom`, and an item has each unit once.
- `enabled` = 0 takes the unit out of use without deleting it.

## Entering a line in a unit

Send the quantity and the rate in the unit and name it in `uom`:

```json
{"item_code": "GLOVE-M", "qty": 2, "uom": "Karton", "rate": 290}
```

When the document is saved the line is converted to the stock unit and keeps
what was entered:

| | qty | uom | rate | amount |
|---|---|---|---|---|
| entered | 2 | Karton | 290.00 | |
| stored | 200 | Stk | 2.90 | 580.00 |

plus `order_uom` = Karton, `conversion_factor` = 100, `order_qty` = 2.

This works on Quotation, Sales Order, Delivery Note, Sales Invoice, POS
Invoice, Purchase Order, Purchase Receipt, Purchase Invoice and Stock Entry
(there with `basic_rate`).

Rules:

- **A line is converted when its `uom` names one of the item's units.**
  Sending a stored line back unchanged (its `uom` is the stock unit) converts
  nothing. Sending it with the unit in `uom` again converts again, so send
  either the entered unit with quantity and rate in that unit, or stock
  units. Never both mixed.
- **An unknown unit is refused** once the item has any Item Unit: "VE is not
  a unit of item GLOVE-M". The factor is never guessed. Items without an
  Item Unit keep accepting any text in `uom`, as before.
- **`order_qty` is derived**: always `qty / conversion_factor`. Changing the
  stored quantity to 150 shows 1.5 Karton. To drop the ordered unit from a
  line, clear `order_uom`.
- **The factor is a snapshot.** Changing or disabling an Item Unit changes
  new lines only.

## Why the stored line is per stock unit

Everything that reads a line's quantity or rate needs no knowledge of units,
and so cannot get it wrong: the stock ledger and valuation, reservations and
picking by expiry, partial deliveries and the backlog, invoicing, returns,
consignment and stock counts all work in stock units, as before.

- Buying 3 Karton at 240.00 receives 300 pieces at a cost of 2.40 each.
- Delivering 100 of an order for 2 Karton leaves 100 pieces (1 Karton) open;
  the delivery note line says `order_qty` 1.
- Batch and serial rules apply to the stock quantity: a serial-tracked item
  needs a whole number of pieces.

The ordered unit follows the line into the documents made from it (order →
delivery note → invoice, returns).

## Prices

- A rate entered with the line is per the entered unit.
- Without a rate the line is priced from the price list: an **Item Price
  with `uom` set to the entered unit** (290.00 per Karton) comes first, with
  its `min_qty` counted in that unit. Otherwise the price per stock unit
  applies, with volume breaks counted in stock units.
- An Item Price without `uom` is a price per stock unit. A line in stock
  units never takes a price quoted for another unit.
- Pricing rules see the stored line: their quantities and amounts are per
  stock unit.

Amounts stay exact. The rate per stock unit is not rounded to the cent on a
converted line (10.00 for a pack of 3 is 3.3333… a piece; 7 packs are
70.00), and a discounted rate keeps six decimal places there. The line
amount is rounded once, from quantity × rate.

## Printing

The built-in layouts print a converted line as it was ordered, 2 Karton at
290.00, with the stock quantity below. A custom PDF template gets
`item.ordered_in_unit`, `item.order_qty`, `item.order_uom`,
`item.conversion_factor` and `item.order_rate` (the price per ordered unit).

## Not covered

- Consignment withdrawals and stock counts are entered in stock units.
- Reports show stock units.
- There is no per-unit rule for whole numbers: 1.5 Karton is accepted where
  the item itself allows 150 pieces.
