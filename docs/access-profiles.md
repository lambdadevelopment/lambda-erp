# Access profiles

A user has a **role** (admin, manager, viewer). The role decides what they may
do at all, exactly as before. An **access profile** narrows that for
individual users:

- it **hides classes of sensitive data**: purchase prices, cost, supplier
  balances, margins;
- it can **take away actions** per document or master type (read, create,
  update, submit, cancel, discard, convert, delete, export).

A user without a profile is not affected by any of this. No profile exists
after an upgrade and none is assigned (migration 52).

Not in this release: restricting a user to assigned customers or warehouses
(record scope), a stock count document, and plugin registration of roles.

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
- **Writes:** a restricted user cannot create or update a document type that
  has fields hidden from them, since saving from a copy without those fields
  would lose them. They can still submit or cancel such documents if their
  role and action rules allow.

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

## Limits to know

- Chat conversations and custom report drafts saved **before** a user was
  restricted keep what was shown then.
- Sales prices are not a sensitivity class.
- The `payments` dataset is refused as a whole under `supplier_terms`,
  including customer payments.
- Plugin document types are visible unless the profile's action rules or a
  registration (below) say otherwise.

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
```

Plugin code that returns data on its own (a registered action, a custom
route, a report) must apply the rules itself:

```python
permissions.require("Supplier Contract", "read")          # or require_readable(doctype, name)
permissions.require_artifact("report", "contribution-margin")
row = permissions.redact_document("Supplier Contract", row)
rows = permissions.redact_rows("dataset", "stock_ageing", rows)
conditions = permissions.record_conditions("Price Agreement")   # SQL to AND into a query
```

Data loaded through `api.services` (`load_document`, `list_documents`,
`list_master_records`, …) is already checked and redacted. The caller is
known inside REST routes that authenticate with the standard dependencies and
inside chat / MCP tool handlers; work done on behalf of a user elsewhere runs
under `with permissions.acting_as(principal):`. Code with no principal
(startup, scheduled jobs, tests of the business logic) is unrestricted.
