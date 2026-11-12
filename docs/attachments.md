# Attachments

Any document or master record can keep files: the customer's order as it
arrived, a signed delivery slip, a certificate (1.4.19). They are stored with
the record, shown in an *Attachments* card on its form, and

- never change the record: no `modified` bump, and they can be added to
  submitted documents too;
- are never printed or sent with the record.

## Who may do what

- **List and download:** whoever may read the record. A user restricted by
  an access profile or to assigned customers / warehouses gets the files of
  exactly the records they can open.
- **Add and remove:** the manager role (and admins).
- The demo user gets neither. A refusal does not say whether the record
  exists.

## Files and hidden data

A field can be left out of a record for a user who must not see it. A file
cannot: nobody knows what is written in a supplier's quotation. So a file is
hidden or shown as a whole, by a sensitivity class
(`docs/access-profiles.md`):

- **By default a file takes the class of the record type it is kept with.**
  A file on a purchase order, purchase receipt, purchase invoice or supplier
  counts as purchase prices; on a stock entry as cost; on a journal entry or
  bank transaction as financials. A file on a customer, item or sales
  document has no class and is open to everyone who can open the record.
- For a user whose access profile hides that class the file does not exist:
  it is not listed (name included) and cannot be downloaded. The record
  itself stays open to them, without its hidden fields.
- **Per file**, someone who sees everything can say otherwise in the card:
  *Not sensitive* (a delivery slip on a purchase order) or a class (a
  supplier's price sheet kept with an item). REST: `sensitivity` on upload,
  or `PUT /api/record-attachments/{id}` with `{"sensitivity": ...}`; values
  are empty (the record type's class), `none`, or a class name.
- A user for whom a class involved is hidden can add a file to such a record
  but cannot classify one: their file counts as what the record type is
  about, and they do not get it back.
- Files that existed before 1.4.20 have no setting of their own and take
  their record type's class.

Internal notes on a record are free text too and follow the same rule by
record type: notes on purchasing documents and suppliers are hidden from
users who do not see purchase prices.

A plugin sets the class of what is kept with its own types:
`permissions.register_content_class("Supplier Contract", "purchase_price")`.

## Limits

- 15 MB per file, 50 files per record.
- File types: PDF; PNG, JPEG, GIF, WebP, TIFF; `.eml` and `.msg` mail; TXT,
  CSV, XML; XLSX and DOCX. Nothing a browser would run.
- Files are always sent back as downloads, never displayed inside the
  application.
- They are stored in the database, so they are part of its backup and count
  toward its size.

## REST

```
GET    /api/record-attachments?record_type=sales-order&record_id=SO-0001
POST   /api/record-attachments          multipart: record_type, record_id, file, description
GET    /api/record-attachments/{id}     the file
DELETE /api/record-attachments/{id}
```

`record_type` is the document or master type slug (`sales-order`, `item`,
`customer`, a plugin's type), `record_id` the record's name.

## Chat and MCP

- `list_record_attachments` lists the files of a record.
- `attach_chat_file` keeps a file that was uploaded to the conversation with
  a record: an order that arrived as a PDF or e-mail and became a sales order
  stays with that order.

The assistant reads uploaded files as before; a file kept with a record is
not read back into a conversation.

## Tracking reference

A Delivery Note has `tracking_reference` for the carrier's consignment
number, next to the files.

## For plugins

```python
from api import record_attachments

record_attachments.add_attachment("sales-order", "SO-0001", "order.pdf", data, user, "As received")
record_attachments.list_attachments("sales-order", "SO-0001", user)
```

Both apply the access rules for `user` (the authenticated principal).

## Limits to know

- The core cannot read files. A file with sensitive content on a record type
  without a class (an item, a customer, a sales order) is open until someone
  marks it.

- A file belongs to one record. It is not carried to the documents made from
  it (the order's file is not copied to the delivery note).
- Deleting a master record deletes its files. Documents are discarded or
  cancelled, not deleted, and keep theirs.
- No preview and no full-text search inside files.
