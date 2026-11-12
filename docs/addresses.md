# Addresses

A customer has one main address on the customer record, as always. Since
1.4.17 it can have any number of further **addresses**, and a sales document
carries the two it uses: where the invoice goes and where the goods go.

Nothing changes for a customer without address records: documents stay
without addresses of their own and print the customer's main address
(migration 63 creates no address and changes no document).

## Address records

Masters → Address (master type `address`). One record per address of a
customer:

| Field | Meaning |
|---|---|
| `customer` | Whose address it is. |
| `label` | What it is: Accounting, Central store, Radiology. |
| `is_billing`, `is_shipping` | Usable for invoices, for deliveries, or both (the default). |
| `is_default_billing`, `is_default_shipping` | The one used on new documents. One per customer and kind: setting it on an address takes it off the previous one. |
| `recipient_name` | The name printed above the address; empty means the customer's name. |
| `contact_person`, `address`, `zip_code`, `city`, `country` | The address as printed. |
| `email`, `phone` | Contact for this address. |
| `instructions` | Delivery instructions, printed on delivery notes. |

The title shown when picking an address is made from customer, label and
town, so typing the customer's name finds its addresses. An address that
documents use is disabled instead of deleted.

`Customer.invoice_email` holds where invoices are sent by e-mail. It is a
plain field for the deployment's sending step; the core does not send mail.

## On sales documents

Quotation, Sales Order, Delivery Note and Sales Invoice carry

- `billing_address` and `shipping_address`: links to address records, and
- `billing_address_display` and `shipping_address_display`: the text as it
  is printed.

When a document is saved:

1. **Given on the document:** a link must be an address of the document's
   customer, enabled and marked for that use.
2. **Not given:** the document takes the addresses of the document it was
   made from (order from quotation, delivery note from order, invoice from
   delivery note or order).
3. **Still none:** the customer's default address of that kind.
4. **Still none:** it stays empty, and the customer's main address prints.

The text is copied from the address when it is picked and then belongs to the
document. Changing the address record later does not change documents that
exist. On a draft:

- picking another address replaces the text;
- the text can be edited for this one document;
- clearing the text fills it again from the linked address;
- an address that is not on record can be typed in without a link, which is
  what to do when an order names where the goods must go. What the document
  says is what prints.

Changing the customer of a draft drops the old customer's addresses.

## PDFs

- Quotation, order and invoice are addressed to the invoice address; when the
  delivery address differs it is printed below as *Delivery address*.
- The delivery note is addressed to the delivery address and prints that
  address's delivery instructions.
- Templates get `address_lines`, `delivery_lines`, `delivery_label` and
  `delivery_instructions` in their context; a template that does not use
  them prints the customer's main address as before.

## Access

A user restricted to assigned customers sees and writes only the addresses of
those customers (`docs/access-profiles.md`).

## For plugins

```python
from lambda_erp import addresses

addresses.register_address_document("Service Visit", sources=(("sales_order", None, "Sales Order"),))
text = addresses.format_address(address_row, customer_name)
name = addresses.default_address(customer, "shipping")
```

A registered document type needs the four columns on its table
(`db.ensure_column` in a migration).

## Limits to know

- Addresses are for customers. Suppliers keep their single address.
- POS invoices and proposals do not carry document addresses.
- The *Delivery address* heading on PDFs is English in the built-in
  templates; a deployment's own template words it as it likes.
- The address picker lists addresses of all customers the user may see; one
  that belongs to another customer is refused when the document is saved.
