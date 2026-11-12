# Consignment

Own stock that lies at a customer until the customer uses it. Available from
1.4.11 (migration 57). No warehouse becomes a consignment warehouse by the
upgrade.

It is a module of the core (`lambda_erp/stock/consignment.py`), built on the
existing stock, batch, storage location, reservation and invoice mechanisms.
There is no second stock system and no special posting path.

## The model

A **consignment warehouse** is one of our warehouses with a customer on it:
`Warehouse.consignment_customer`. The goods in it belong to us. They stay in
our stock quantity and in our stock value, by batch and storage location like
any other stock. The customer is where they are, not who owns them.

| Step | What moves | Billing | Document |
|---|---|---|---|
| **Stocking** | own warehouse → consignment warehouse | none | Stock Entry, Material Transfer |
| **Withdrawal** | out of the consignment warehouse (used) | billed | Consignment Withdrawal → one Sales Invoice |
| **Recall** | consignment warehouse → own warehouse | none | Stock Entry, Material Transfer |
| **Withdrawal with replacement** | a withdrawal, plus a stocking linked to it | the withdrawal, once | the two above, each with its own status |

- A customer can have several consignment warehouses (sites, departments). A
  consignment warehouse belongs to exactly one customer; it cannot be changed
  while stock lies in it.
- `Warehouse.replenishment_warehouse` names the own warehouse a consignment
  warehouse is stocked from by default.
- **Goods in a consignment warehouse can only be sold to its customer.** A
  delivery note or direct-stock invoice for another customer out of it is
  refused: recall the goods first.
- The stock view `stock-detail` shows `consignment_customer` per row and
  filters by it (`consignment_customer=…`, or `consignment=true/false`).

## Stocking

A stocking is a Material Transfer into the consignment warehouse. It is not a
sale: there is no invoice, not even one over zero. Total owned quantity,
stock value and revenue are unchanged.

`prepare_consignment_stocking` (REST `POST /api/actions/…`, chat, MCP) drafts
it, with batches and source locations proposed first-expired-first-out:

```json
{"warehouse": "CONSI-HOSPITAL", "items": [{"item_code": "IMPLANT-7", "qty": 6}]}
```

Nothing moves until the draft is submitted. Where the source cannot supply a
line in full, the line is left without a batch for the picker and the draft
cannot be submitted until it is complete.

## Withdrawal

A **Consignment Withdrawal** records what the customer reported as used: the
consignment warehouse and lines with item, quantity, the reported batch and,
where the warehouse uses them, the storage location. A rate is optional;
without it the customer's price on the posting date applies.

Submitting it creates and submits **one Sales Invoice** for the warehouse's
customer with `update_stock`. That invoice bills the customer and takes
exactly the reported batch out of exactly that consignment warehouse.
Quantity, batch, cost of goods and revenue are each booked once, by that one
document; the withdrawal itself posts nothing. There is no delivery note in
this flow, so the goods cannot be taken out a second time by one.

The stock checks are the ordinary ones: a batch that does not lie in that
warehouse, an unknown batch, a missing batch on a tracked item or more than
is there are refused, and nothing is recorded or billed.

### A report received twice

`record_consignment_withdrawal` is the entry point for reports that arrive
from outside (an e-mail, an ordering system, an import). Pass the customer's
report identity:

```json
{"warehouse": "CONSI-HOSPITAL",
 "external_source": "hospital-ordering", "external_reference": "ENT-1001",
 "items": [{"item_code": "IMPLANT-7", "qty": 1, "batch_no": "LOT-2291"}]}
```

- **Key:** `external_source` + `external_reference`, among withdrawals that
  are not cancelled.
- **Same key, same content:** nothing is posted again; the answer is the
  first result with `replayed: true`. Content means warehouse, the lines
  (item, batch, location, quantity, rate; in any order) and the replenish
  flag.
- **Same key, different content:** refused with HTTP 409. Nothing is
  overwritten. The correction is explicit: cancel the recorded withdrawal,
  then record the corrected report under the same number.
- **No key:** every call is a new withdrawal.

Recognising a customer's e-mail or form and turning it into this call is the
deployment's job.

### Replacement

`replenish = true` on a withdrawal also drafts a stocking for the withdrawn
quantities from `replenish_from_warehouse` (default: the warehouse's
`replenishment_warehouse`). The two belong together and are linked
(`replacement_stock_entry`), but each has its own status:

- the withdrawal is **Billed** as soon as it is submitted;
- `replenishment_status` is **Pending** while the replacement is a draft,
  **Done** once it is submitted, **Cancelled** if it is discarded or
  cancelled.

So a withdrawal is billed even when the replacement cannot be shipped yet.

## Recall

`prepare_consignment_recall` drafts the transfer back to an own warehouse,
for everything in the consignment warehouse or for given items and batches:

```json
{"warehouse": "CONSI-HOSPITAL", "to_warehouse": "QUARANTINE"}
```

It is a transfer. There is no credit note, because nothing was sold.

## Cancelling and returning a billed withdrawal

- **Not paid:** cancel the Consignment Withdrawal. That cancels its invoice,
  which puts the goods back into the consignment warehouse and reverses
  revenue and cost. A replacement that is still a draft is discarded; one
  that was already submitted stays (recall it if it should come back).
- The invoice of a withdrawal cannot be cancelled on its own; cancel the
  withdrawal.
- **Paid:** the cancellation is refused. Either cancel the payment first, or
  credit the invoice with a return invoice (*Create Credit Note*): it returns
  the goods into the consignment warehouse and credits the customer, and the
  withdrawal stays as the record of what was reported.
- **Price only:** an amount-only credit note corrects the price without
  moving goods.

## For plugins

`lambda_erp.stock.consignment`: `record_withdrawal`, `find_withdrawal`,
`make_consignment_stocking`, `make_consignment_recall`,
`consignment_customer(warehouse)` and `content_hash(...)`. Hooks on
`Consignment Withdrawal:before_submit / on_submit / after_submit` work as for
any document, for example to add a shipping line or to notify someone.

## Limits to know

- A withdrawal bills at the price on its posting date; there is no price
  fixed at stocking.
- Consignment stock is booked on the stock account of its warehouse. Give the
  consignment warehouse its own `account` to show it separately in the
  balance sheet.
- A withdrawal names one consignment warehouse. A report covering several
  sites is several withdrawals.
