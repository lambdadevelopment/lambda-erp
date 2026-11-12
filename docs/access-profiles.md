# Access profiles

A user has a **role** (admin, manager, viewer). The role decides what they may
do at all, exactly as before. An **access profile** narrows that for
individual users:

- it **hides classes of sensitive data**: purchase prices, cost, supplier
  balances, margins;
- it can **take away actions** per document or master type (read, create,
  update, submit, cancel, discard, convert, delete, export).

Independently of a profile, a user can be **restricted to assigned records**:
their customers, their warehouses (record scope, below).

A user without a profile and without a record scope is not affected by any of
this. No profile exists after an upgrade and none is assigned (migration 52);
nobody has a record scope after migration 61.

Not included: plugin registration of roles. A user who may count but not post
is set up with the Stock Count document (1.4.12, `stock-counts.md`).

## Setting it up

Settings → Users (admin only): create a profile under *Access Profiles*, then
pick it in the *Access profile* column of a user. Over REST:

```
POST /api/auth/access-profiles
{"name": "Field sales", "description": "No purchase prices, no suppliers",
 "hidden_classes": ["purchase_price"],
 "permissions": {"doctypes": {"Supplier": []}}}

PUT /api/auth/users/{user}/access-profile     {"access_profile": "Field sales"}
```

`GET /api/auth/me` returns the caller's `access_profile` and a `restrictions`
object (hidden classes, action rules, hidden document types, denied reports)
for a client that wants to adapt its interface. It is `null` for an
unrestricted user.

The profile belongs to the user: their browser session, API keys and MCP /
OAuth connections all carry it. An admin cannot assign a profile to their own
account. A profile that is **disabled or missing** locks its users out of
everything instead of lifting the restriction; a profile in use cannot be
deleted.

## Sensitivity classes

| Class | Hides |
|---|---|
| `purchase_price` | Rates, amounts and totals of Purchase Orders, Purchase Receipts and Purchase Invoices (quantities, items, dates and suppliers stay visible); buying price lists, their item prices and buying pricing rules; supplier subscriptions; an asset's purchase value; the purchase datasets and metric. |
| `cost` | Valuation rate and stock value in stock reports and datasets; rates and values of Stock Entries; the stock value metric. |
| `supplier_terms` | Payments to suppliers, the payables report and dataset, payables on the dashboard, the payments dataset. |
| `margin` | Trial balance, profit and loss, balance sheet, general ledger, chart of accounts with balances; Journal Entries, Budgets, bank accounts, bank transactions and the bank screens. |

A class also hides what it could be worked out from:

- `purchase_price` implies `cost` and `supplier_terms` (the stock value gives
  the purchase price away);
- `cost` implies `margin` (the ledger gives the cost away).

So a profile that hides `purchase_price` hides all four.

## What "hidden" means

The rules are applied where data leaves the server, the same way for the web
app, the REST API, the chat (before anything reaches the model), MCP and PDFs.

- **Fields** are left out of documents, lists and search results. A request
  that names a hidden field to filter, sort, select or list values by it is
  refused with 403 instead of being answered without it.
- **Records** that a rule hides (a buying price list, a supplier payment) do
  not appear in lists or counts, and reading one is refused.
- **Document types** that are hidden as a whole are refused.
- **Reports, datasets and metrics** are either refused as a whole or lose
  the hidden columns, which then cannot be selected, filtered, grouped or
  aggregated either.
- **PDFs** (also archived or previously generated ones, and PDF template
  previews) are refused for a document type with hidden fields.
- **Writes:** a restricted user can create and edit documents that have
  fields hidden from them; they just never supply those fields.
  - A value sent for a hidden field is refused (403). Blank values, as a
    form sends them for empty inputs, are ignored.
  - On an edit, every line that stays (same line, same item) keeps its stored
    hidden values; header values are kept as they are. Amounts and totals are
    recalculated by the server.
  - A new line gets its hidden values from the server: a receipt made from an
    order takes the order's prices, a purchase line takes the buying price
    list, a stock transfer takes the value at the source.
  - Where a hidden value is needed and nothing is on record, the write is
    refused instead of posting at zero or at a fallback: a purchase line
    without a buying price (the item's standard rate is not accepted for a
    user who cannot see what was taken), stock brought in from nowhere
    without a rate.
  - Masters with hidden fields (none in the core) still cannot be written by
    a user who does not see them.

Refusals are HTTP 403 with a message starting "Your access profile…"; chat
and MCP tools return the same message as their error.

## Action rules

```json
{"default": ["read"],
 "doctypes": {"Sales Order": ["read", "create", "update"],
              "Quotation": []}}
```

- A type is named as in the application: "Sales Order", "Supplier", "Item".
- The list is what remains allowed. An empty list hides the type.
- `default` applies to every type not listed. Leave it out to allow whatever
  the role allows. `"default": []` plus an explicit list turns the profile
  into an allow-list, which is the safe choice when plugin document types
  should be invisible unless named.
- Rules only take away: a viewer does not gain write access by a rule.

## Record scope: assigned customers and warehouses

Settings → Users, column *Assigned records*, or:

```
PUT /api/auth/users/{user}/record-scope
{"record_scope": {"customer": ["CUST-001", "CUST-007"], "warehouse": ["WH-002"]}}
```

A kind that is left out is not restricted. An empty list means none at all.
`null` or `{}` lifts the restriction. Names must exist. An admin cannot
restrict their own account; a stored scope that cannot be read locks the user
out. It applies to every credential of the user, with or without a profile.

**Restricted by customer**, a user sees and writes only

- the assigned customers themselves, and documents for them: quotations,
  proposals, sales orders, delivery notes, sales and POS invoices,
  consignment withdrawals, stock reservations, payments and subscriptions of
  those customers, journal entries that do not touch another customer;
- customer-specific prices and pricing rules of those customers (general
  ones stay visible);
- consignment warehouses of those customers. The consignment warehouse of
  another customer is hidden, with its stock and every document that moves,
  counts or receives goods there.

Stock in the company's own warehouses, items, suppliers and purchasing stay
visible; hide prices with a profile if needed.

**Restricted by warehouse**, a user sees and writes only the assigned
warehouses, their storage locations, serial numbers lying there, and
documents all of whose lines are in assigned warehouses (sales, purchasing,
stock entries, counts, reservations, consignment withdrawals). A transfer
needs both sides assigned. Customers and payments stay visible.

Rules that hold for both:

- A record outside the scope does not appear in lists, searches, counts or
  value lists; reading, editing, submitting, cancelling, converting, printing
  or annotating it is refused (403).
- A document cannot be saved or submitted with a customer or warehouse
  outside the scope, by whatever path it is written (form, API, chat, an
  action). The user cannot create customers or warehouses.
- **Reports, datasets and metrics are refused unless they can be limited to
  the assigned records.** Limited: stock balance, stock availability, stock
  by location and batch, batch stock (warehouse); sales backlog and AR aging
  (customer); purchase backlog; the datasets for sales and purchase invoices,
  their lines, open items, payments, stock balances and movements. Refused:
  trial balance, ledger, profit and loss, balance sheet, chart of accounts,
  the dashboard, batch movements, batch recipients, serial trace, the ledger
  and bank datasets, and every metric that adds up over customers or
  warehouses. The fleet availability screens are refused as well.
- Bank transactions are closed to a user restricted by customer (they name
  counterparties).

## Extension types are closed until declared

The core knows what its own document and master types contain. A type added
by a plugin is refused for a restricted user until either

- the plugin has said what is in it (`register_sensitive_fields`,
  `register_sensitive_records`, `register_sensitive_doctype`, or
  `register_plain_doctype` for "nothing sensitive"), or
- the profile names the type in its action rules, or sets a `default`.

For a user with a record scope the plugin must also declare how the type
relates to each restricted kind (`register_record_scope`, with
`unrelated=True` when it does not). A plugin that replaces the class of a
core type does not turn it into an extension type.

## Stored conversations and report drafts

A chat conversation and a custom report draft are stamped with the
restrictions they were written under. A restricted user gets one back only if
they may see at least as much now as then. So after a user is restricted (or
restricted further), earlier conversations and drafts disappear for them;
they are not deleted, an admin still reads them, and they come back when the
restriction is lifted. Conversations and drafts from before 1.4.14 count as
written without restrictions.

## Limits to know

- Sales prices are not a sensitivity class.
- The `payments` dataset is refused as a whole under `supplier_terms`,
  including customer payments.
- A user who cannot see purchase prices gets them from the supplier's buying
  price list (`Supplier.default_price_list`), else the company's default one,
  when that is a buying list.
- An item's default warehouse is shown with the item, also to a user who is
  not assigned that warehouse.
- Record scope is by customer and by warehouse. Other kinds are added by a
  plugin (`register_scope_dimension`).
- The menu hides what a restricted user cannot open, and forms and lists
  leave out hidden fields (`restrictions.hidden_fields` of `/api/auth/me`).

## For plugins

`lambda_erp.permissions`:

```python
from lambda_erp import permissions

permissions.register_sensitive_fields("Supplier Contract", {"agreed_rate": "purchase_price"})
permissions.register_sensitive_fields("Supplier Contract", {"rate": "purchase_price"}, child="items")
permissions.register_sensitive_records("Price Agreement", "purchase_price", "COALESCE(buying, 0) = 0")
permissions.register_sensitive_doctype("Commission Run", "margin")
permissions.register_sensitive_artifact("report", "contribution-margin", "margin")
permissions.register_sensitive_artifact("dataset", "stock_ageing", fields={"value": "cost"})
permissions.register_plain_doctype("Visit Note")            # nothing of the classes in here

# Record scope
permissions.register_record_scope("Service Visit", "customer", "customer")
permissions.register_record_scope("Service Visit", "warehouse", child="parts",
                                  child_table="Service Visit Part", child_columns=("warehouse",))
permissions.register_record_scope("Visit Note", "warehouse", unrelated=True)
permissions.register_scoped_artifact("report", "visits-per-customer", customer="customer", warehouse=None)
permissions.register_scoped_artifact("dataset", "service_visits", customer="sv.customer", warehouse=None)
permissions.register_scope_dimension("territory", "Territory")   # a further kind, with its master

# A hidden value a new line must get from the server, or the write is refused
permissions.register_hidden_write_requirement("Supplier Contract", "items", "rate")
```

Plugin code that returns data on its own (a registered action, a custom
route, a report) must apply the rules itself:

```python
permissions.require("Supplier Contract", "read")          # or require_readable(doctype, name)
permissions.require_artifact("report", "contribution-margin")
row = permissions.redact_document("Supplier Contract", row)
rows = permissions.redact_rows("dataset", "stock_ageing", rows)
conditions = permissions.record_conditions("Price Agreement")   # SQL to AND into a query (record rules and scope)
permissions.require_in_scope("customer", customer)            # a customer named in a request
permissions.require_warehouse_in_scope(warehouse)             # a warehouse named in a request
rows = permissions.scope_rows("report", "visits-per-customer", rows)
condition = permissions.scope_sql("customer", "v.customer")   # None when not restricted
permissions.require_no_record_scope("the route planner")      # cannot be limited at all
```

`record_conditions` expects the table to be queried without an alias. A
plugin that writes a document with fields hidden from the caller either goes
through `api.services` (which keeps them) or builds the document on the
server and passes `keeps_hidden_fields=True` to `require_writable`.

Data loaded through `api.services` (`load_document`, `list_documents`,
`list_master_records`, …) is already checked and redacted. The caller is
known inside REST routes that authenticate with the standard dependencies and
inside chat / MCP tool handlers; work done on behalf of a user elsewhere runs
under `with permissions.acting_as(principal):`. Code with no principal
(startup, scheduled jobs, tests of the business logic) is unrestricted.
