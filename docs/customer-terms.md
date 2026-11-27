# Customer billing terms

Available from 1.4.32 (migration 73). On the customer master; all optional.

| Field | Values | Effect |
|---|---|---|
| `customer_type` | `Company`, `Individual` (private person) | For statistics and the deployment's rules; changes no calculation. |
| `billing_cycle` | `Per Delivery` (default when empty), `Monthly` | Monthly: deliveries are collected and billed together with the collective invoice. The `unbilled-deliveries` report shows each row's cycle and filters `billing_cycle=Monthly`. Core does not invoice deliveries by itself either way; a deployment can refuse per-delivery invoicing for monthly customers. |
| `payment_terms_days` | 0–365, e.g. 30, 60, 90 | A new Sales Invoice without a due date gets posting date + these days (default 30). |
| `credit_limit` | amount, 0 = none | A **warning only** (`credit_limit_exceeded`) on draft sales orders and invoices, and on submitted orders not yet invoiced, when open receivables plus the document exceed the limit. Nothing is blocked. |

The warning carries `limit`, `open`, `this_document` and `total` (company
currency). Open receivables are the outstanding amounts of the customer's
submitted sales invoices, credit notes netted.

`lambda_erp.customer_terms` has `billing_cycle(customer)`,
`payment_terms_days(customer)`, `due_date(customer, posting_date)`,
`open_receivables(customer)` and `credit_check(doc)` for plugins.

Not yet: prices including VAT for private customers and domestic/export tax
treatment per customer. Both depend on how the accounting treats them and
come separately.
