# Pricing and external documents

Price Lists name prices in one currency. Item Prices match an item, list,
optional customer or supplier, unit, minimum quantity and validity dates.
Configure lists and prices under Settings. Select a default list on a Customer
or Company. Customer defaults take precedence; disabled, wrong-currency or
wrong-trade-side lists are skipped. A company buying list can price purchases;
a supplier-specific default-list selector is not provided.

For a line without a rate, resolution is party-specific Item Price → general
Item Price → Item.standard_rate. Leave Rate blank in the transaction form to
resolve it when saving; enter zero for a free line. Saving fills the rate, so
later master-price changes do not automatically reprice an existing draft.

Pricing Rules modify the base price. Match by item or item group and optionally
customer, customer group, territory or supplier, with date, quantity and line
amount limits. Highest priority wins, then specificity, then the tie-breakers
listed under "Resolution, in full" below. One rule applies per line. A discount retains its base across repeated
saves. Use `ignore_pricing_rule=1` on a document or line to preserve agreed prices.
Tax, explicit line discounts and document discounts still affect calculated
amounts; this flag is not a guarantee that an imported total matches.

## Resolution, in full (1.4.9)

Every priced document (quotation, sales order, delivery note, sales / POS
invoice, purchase order, purchase receipt, purchase invoice) runs the same two
steps when it is saved, whatever created it: the form, REST, chat, MCP or an
import. Delivery notes and purchase receipts do so since 1.4.16; before, a
line typed in by hand took the item's standard rate only. Lines that come
from an order keep the order's price.

The price list is the customer's or supplier's `default_price_list` (a
supplier has one since 1.4.16), else the company's, and it must be marked for
the right side (selling / buying) and quoted in the document's currency.

**1. Base price.** For a line without a rate: the Item Price of the
document's price list, else `Item.standard_rate`. Among the Item Prices that
apply, in this order: a row for this customer / supplier; a row for this unit;
the highest `min_qty` reached; the narrower validity window; the newer
`valid_from`; the name.

**2. One pricing rule on top.** Among the rules that match, in this order:
the higher `priority`; the more specific match (one customer beats a group
beats everybody; an item beats an item group); the higher `min_qty`; the
narrower validity window; the newer `valid_from`; the name. Rules do not
stack. The same inputs always give the same result.

With the setting `pricing_strict_conflicts` = 1, a line is refused when two
rules of the same priority and specificity apply to it, instead of being
decided by the remaining tie-breakers.

### The pricing date

Prices and rules are looked up for the document's `pricing_date` when set,
else its posting / transaction date. A price or rule valid from next month
does not apply to a document dated today, whenever it is saved, and a
document dated before a change keeps the earlier price when it is saved again.
The forms show it as *Pricing Date*.

### What is kept on the line

| Field | |
|---|---|
| `price_source` | `Item Price`, `Standard Rate`, `Entered` (the rate came with the line), `Manual` (changed by hand on a saved line) or `None` (no Item Price and no standard rate: the line is priced at 0 and the document carries a `price_missing` warning). |
| `price_reference` | The Item Price, when that is the source. |
| `pricing_rule` | The rule applied on top, if any. |
| `price_list_rate` | The base price the rule was applied to. |

Lines created before 1.4.9 have an empty `price_source`.

### When a price changes, and when it does not

- On every save of a **draft**, the pricing rule of each line is chosen again
  for the document's pricing date, so a changed, new or disabled rule reaches
  the draft the next time it is saved (unless the line is manual or carried).
  The base price is filled once: a later change of an Item Price does not
  move it. Clear the line's rate to resolve it again.
- A rate the user changes on a saved line is **Manual**: no rule touches it
  again.
- Lines **converted** from another document (quotation → order → invoice,
  returns) keep the price agreed there, and its source.
- **Submitted** documents are never repriced.
- Delivery notes and purchase receipts do not run pricing rules: converted
  lines carry the order's price, lines entered directly use the rate given or
  the item's standard rate.

### Explaining a price

The action `explain_price` (REST `POST /api/actions/explain_price`, chat,
MCP; `lambda_erp.controllers.price_resolution` in code) answers two questions
without saving anything:

- **What would it cost?** `{"item_code": ..., "customer": ..., "qty": ...,
  "date": ...}` (or `supplier` with a purchase `doctype`). It builds the
  document in memory and runs the two steps above, so the answer is what a
  real document gets. It returns the rate, the base source, the rule applied,
  every other rule that matched and `tied_with`.
- **Why is this line priced like this?** `{"doctype": ..., "name": ...,
  "line": ...}` returns the decision stored on the line, with the validity of
  the Item Price and rule it names.

Explanations of purchase prices are refused for a user whose access profile
hides purchase prices.

### Deployment-defined rule keys (price families, purchasing groups)

A pricing rule matches an item by Item Code or Item Group, and a party by
Customer, Customer Group, Territory, Supplier or Supplier Group. A deployment
adds its own keys:

```python
from lambda_erp.controllers.price_resolution import register_price_dimension

def price_family(ctx):          # ctx: item_code, item_group, customer, supplier, side, date, ...
    column = "price_family" if ctx["side"] == "selling" else "buying_price_family"
    value = get_db().get_value("Item", ctx["item_code"], column)
    return [value] if value else []

def purchasing_groups(ctx):
    return [row["group_name"] for row in get_db().sql(
        'SELECT group_name FROM "Purchasing Group Member" WHERE customer = ?', [ctx["customer"]])]

register_price_dimension("Price Family", "item", price_family)
register_price_dimension("Purchasing Group", "party", purchasing_groups, party_type="Customer")
```

A rule then uses `apply_on = "Price Family"` with `apply_on_value`, or
`applicable_for = "Purchasing Group"` with `applicable_for_value`. The
deployment only says which values an item or party has; matching, ranking and
the price stay in the rule engine. Buying and selling can be assigned
differently (`ctx["side"]`). `specificity` places a key between the built-in
ones when priorities are equal (defaults: like Item Group, like Customer
Group).

Typical levels, lowest to highest, with default priorities:

| Level | Rule |
|---|---|
| List price | Item Price (or a Rate rule on the family for everybody) |
| Family price | `apply_on` = family |
| Group price | family + `applicable_for` = purchasing group |
| Customer price | family + `applicable_for` = Customer |
| Individual exception | `apply_on` = Item Code + Customer |

Each level beats the one above it by specificity alone; `priority` is there
for the deliberate override (a campaign).

## External identities and recovery

On quotations, sales/purchase orders, sales/purchase invoices and POS invoices:

- `external_source` identifies a configured source namespace. Include the
  connection/organisation when IDs can overlap, e.g. `findmee/baurent-ost`.
- `external_reference` identifies the imported document within that namespace
  and ERP document type. The pair has a database unique index, including
  discarded and cancelled documents. A reference requires a source.
- `external_line_reference` identifies an imported line within a document and
  cannot occur twice in that document. Namespace it by export if combining
  lines whose IDs are only unique within an export.
- Use null for absent references. Blank or whitespace-padded identities are
  rejected. Once assigned, source and document reference cannot be changed.

An external source defaults to ignoring pricing rules, but explicit
`ignore_pricing_rule=0` requests local pricing. External invoices persist
`update_stock=0` and reject `update_stock=1`; record any required inventory
movement separately through the selected stock workflow.

After a timeout or duplicate response (HTTP 409), look up the same document type
by source/reference, including discarded documents, and compare the saved
content and state before continuing. Do not invent a new reference just to
bypass the duplicate. The unique constraint prevents duplicate creation; it
does not replay an HTTP response or check payload equivalence.

Conversions preserve source, pricing protection and line references but leave
`external_reference` unset. Partial invoices and corrections need their own
identities, assigned by the caller. All generated invoice returns bypass current
pricing rules so they reverse the historical rate. Original-voucher links remain
available through `return_against` and the order/quotation links.

These fields are ERP primitives, not a complete connector. A FindMee connector
still needs durable connection/export/version/line-to-document mappings (including
splits and consolidation), source snapshots, amount/tax comparison, retry work,
acknowledgement sequences and authorised correction/acceptance flows. No live
FindMee calls or connector are included in this release.

## Accounting and upgrades

Sales Invoice and POS Invoice income postings aggregate by both income account
and cost centre. Returns and cancellation preserve that attribution. Historical
postings are not rewritten; any historical repair requires a separate review.

Migrations 32–37 add the fields and indexes. Migration 36 retries external-index
creation for pre-release databases and adds per-document line uniqueness.
Migration 37 adds draft/discard fields for the pricing screens. These and
subsequent migrations fail startup visibly if they fail. Resolve conflicting
identities explicitly before retrying an upgrade; migrations never delete or
rewrite duplicates automatically. Existing installations without external IDs
remain unaffected by the uniqueness constraints.
