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

- A file belongs to one record. It is not carried to the documents made from
  it (the order's file is not copied to the delivery note).
- Deleting a master record deletes its files. Documents are discarded or
  cancelled, not deleted, and keep theirs.
- No preview and no full-text search inside files.
