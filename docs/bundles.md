# Item bundles

A bundle is one item code that stands for several items: a set, a kit, a
standard combination that is always ordered together. Entering the bundle on
a sales document produces one line per item of the bundle (1.4.18).

No item is a bundle after an upgrade (migration 64).

## Defining a bundle

1. Create the item that names the bundle. It is not a stock item
   (`is_stock_item` = 0) and has no batch or serial tracking: it is never
   stocked, priced or delivered itself.
2. Create an **Item Bundle** for it (Masters → Item Bundle, document type
   `item-bundle`): `item_code` is the bundle, `items` lists the items it
   stands for with the quantity per one bundle.

```json
{"item_code": "SET-KNEE", "items": [
   {"item_code": "IMPLANT-A", "qty": 1},
   {"item_code": "SCREW-4",   "qty": 4}]}
```

- One bundle per item code.
- An item of a bundle can itself be a bundle; a bundle cannot contain itself.
- A disabled bundle no longer expands; its item is an ordinary unstocked
  item again.

## On sales documents

On a quotation, sales order, delivery note, sales invoice or POS invoice, a
line with a bundle's item code is replaced by the lines of its items when the
document is saved. Two of the bundle above become `IMPLANT-A` × 2 and
`SCREW-4` × 8.

- The new lines take the bundle line's warehouse and delivery date.
- **They are ordinary lines from then on.** Each is priced by the price list
  and pricing rules, reserved, picked by batch, delivered and invoiced on its
  own, and can be changed or removed on the draft.
- Each keeps `bundle_item` and `bundle_qty`: the bundle it came from and how
  many of it were entered.
- A bundle has no price, discount, batch or serial numbers of its own. A
  bundle line that carries one is refused.
- What differs per customer (the left-handed or the right-handed tool) is
  left out of the bundle and added as its own line.
- Changing a bundle changes what new lines expand to. Lines on existing
  documents stay as they are.

Purchasing and stock documents do not expand bundles.

## For plugins

```python
from lambda_erp import bundles

bundles.components("SET-KNEE", 2)          # [("IMPLANT-A", 2.0), ("SCREW-4", 8.0)]
bundles.register_bundle_document("Service Visit")   # its line table needs bundle_item, bundle_qty
```

## Limits to know

- A bundle has no price of its own. A set price is expressed through the
  prices of its items (price lists or pricing rules).
- The lines are not kept together afterwards: removing one item of an
  expanded bundle is allowed, and documents do not print a bundle heading.
