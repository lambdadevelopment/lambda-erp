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
amount limits. Highest priority wins, then specificity, then minimum quantity
and name. One rule applies per line. A discount retains its base across repeated
saves. Use `ignore_pricing_rule=1` on a document or line to preserve agreed prices.
Tax, explicit line discounts and document discounts still affect calculated
amounts; this flag is not a guarantee that an imported total matches.

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
