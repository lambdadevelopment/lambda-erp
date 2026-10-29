"""Internal notes on any ERP document or master record.

Notes are private to the team: they never print and never change the record
itself (no `modified` bump, so open forms are not marked stale). They plug into
the personal mention inbox as a fallback source, so every document and master
type supports @mentions without registering anything. Record types with their
own notes (a plugin's explicit mention source, e.g. CRM leads) keep theirs.
"""
import uuid
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import Field

from api import mentions, services
from lambda_erp.database import get_db
from lambda_erp.utils import now

TABLE = "Record Note"
_DISABLED: set[str] = set()


def disable_record_notes(record_type):
    """Opt a record type out, e.g. one whose rows are themselves notes."""
    _DISABLED.add(record_type)


def _target(record_type):
    """(table, kind) for a record type that takes generic notes, else None."""
    if record_type in _DISABLED or record_type in mentions.SOURCES:
        return None
    doctype = services.SLUG_TO_DOCTYPE.get(record_type)
    if doctype:
        return doctype, "document"
    master = services.MASTER_TABLES.get(record_type)
    if master:
        return master[0], "master"
    return None


def record_types():
    return sorted({*services.SLUG_TO_DOCTYPE, *services.MASTER_TABLES} - _DISABLED - mentions.SOURCES.keys())


def _reader(user):
    return (user or {}).get("role") in mentions.REAL_ROLES


def _existing(table, ids):
    db, found = get_db(), {}
    ids = list(ids)
    for start in range(0, len(ids), 250):
        chunk = ids[start:start + 250]
        found.update({row["name"]: row for row in db.sql(
            f'SELECT * FROM "{table}" WHERE name IN (' + ','.join('?' for _ in chunk) + ')', chunk)})
    return found


def _title(record_type, record):
    table, kind = _target(record_type)
    if kind == "master":
        label = record.get(services.MASTER_TABLES[record_type][1])
        return f"{table} {record['name']}" + (f" · {label}" if label and label != record["name"] else "")
    party = record.get("customer_name") or record.get("supplier_name") or record.get("party_name")
    return f"{table} {record['name']}" + (f" · {party}" if party else "")


def view_url(record_type, record_id, note_id=None):
    _, kind = _target(record_type)
    base = "/app" if kind == "document" else "/masters"
    url = f"{base}/{record_type}/{quote(record_id, safe='')}"
    return url + (f"#note-{note_id}" if note_id else "")


def _can_access(record_type, record_id, user):
    target = _target(record_type)
    return bool(target and _reader(user) and get_db().exists(target[0], record_id))


def _create(record_type, record_id, body, user):
    note_id, stamp = uuid.uuid4().hex, now()
    get_db().insert(TABLE, {
        "name": note_id, "record_type": record_type, "record_id": record_id, "body": body,
        "author_id": user.get("user_id") or user["name"], "created_at": stamp, "updated_at": stamp,
    })
    return note_id


def _resolve(record_type, note_ids, user):
    target = _target(record_type)
    if not note_ids or not target or not _reader(user):
        return {}
    rows = get_db().sql(f'SELECT name, record_id, body FROM "{TABLE}" WHERE record_type = ? AND name IN ('
                        + ','.join('?' for _ in note_ids) + ')', [record_type, *note_ids])
    records = _existing(target[0], {row["record_id"] for row in rows})
    return {row["name"]: {
        "record_id": row["record_id"], "title": _title(record_type, records[row["record_id"]]),
        "body": row["body"], "view_url": view_url(record_type, row["record_id"], row["name"]),
    } for row in rows if row["record_id"] in records}


def _source(record_type):
    target = _target(record_type)
    if not target:
        return None
    return dict(
        label=target[0],
        can_access=lambda record_id, user: _can_access(record_type, record_id, user),
        create_note=lambda record_id, body, user: _create(record_type, record_id, body, user),
        resolve_note=lambda note_id, user: _resolve(record_type, [note_id], user).get(note_id),
        resolve_notes=lambda note_ids, user: _resolve(record_type, note_ids, user),
    )


class ListNotes(mentions.Input):
    record_type: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class EditNote(mentions.Input):
    note_id: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=8000)


class DeleteNote(mentions.Input):
    note_id: str = Field(min_length=1, max_length=100)


def _require_record(record_type, record_id, user):
    if not _target(record_type):
        raise ValueError(f"{record_type!r} does not take internal notes. "
                         f"Supported record types: {', '.join(record_types())}.")
    if not _can_access(record_type, record_id, user):
        raise HTTPException(404, "Record is unavailable")


def list_record_notes(args, user):
    data = ListNotes.model_validate(args)
    if not _reader(user):
        raise PermissionError("Sign in with an ERP account to read internal notes.")
    _require_record(data.record_type, data.record_id, user)
    db, me = get_db(), user.get("user_id") or user.get("name")
    where = [data.record_type, data.record_id]
    total = db.sql(f'SELECT COUNT(*) AS n FROM "{TABLE}" WHERE record_type = ? AND record_id = ?', where)[0]["n"]
    rows = db.sql(f'SELECT * FROM "{TABLE}" WHERE record_type = ? AND record_id = ? '
                  'ORDER BY created_at DESC, name DESC LIMIT ? OFFSET ?', [*where, data.limit, data.offset])
    authors = _existing("User", {row["author_id"] for row in rows})
    notes = [{
        "id": row["name"], "body": row["body"], "author_id": row["author_id"],
        "author_name": (authors.get(row["author_id"]) or {}).get("full_name")
        or (authors.get(row["author_id"]) or {}).get("email") or row["author_id"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "edited": row["updated_at"] != row["created_at"],
        "can_edit": row["author_id"] == me,
        "can_delete": row["author_id"] == me or user.get("role") == "admin",
        "view_url": view_url(data.record_type, data.record_id, row["name"]),
    } for row in rows]
    return {"record_type": data.record_type, "record_id": data.record_id, "notes": notes,
            "total": total, "has_more": data.offset + len(notes) < total}


def _load_note(note_id):
    rows = get_db().sql(f'SELECT * FROM "{TABLE}" WHERE name = ?', [note_id])
    if not rows:
        raise HTTPException(404, "Note not found")
    return rows[0]


def update_record_note(args, user):
    data, actor = EditNote.model_validate(args), mentions._actor(user)
    body = data.body.strip()
    if not body:
        raise ValueError("Note body must not be blank.")
    db = get_db()
    with db.atomic():
        note = _load_note(data.note_id)
        _require_record(note["record_type"], note["record_id"], actor)
        if note["author_id"] != actor["user_id"]:
            raise PermissionError("Only the author can edit a note.")
        db.sql(f'UPDATE "{TABLE}" SET body = ?, updated_at = ? WHERE name = ?', [body, now(), note["name"]])
        notified = mentions.sync_note_mentions(note["record_type"], note["record_id"], note["name"], body, actor)
    return {"note_id": note["name"], "notified_count": notified,
            "view_url": view_url(note["record_type"], note["record_id"], note["name"])}


def delete_record_note(args, user):
    data, actor = DeleteNote.model_validate(args), mentions._actor(user)
    db = get_db()
    with db.atomic():
        note = _load_note(data.note_id)
        _require_record(note["record_type"], note["record_id"], actor)
        if note["author_id"] != actor["user_id"] and actor["role"] != "admin":
            raise PermissionError("Only the author or an admin can delete a note.")
        db.sql('DELETE FROM "Mention Notification" WHERE doctype = ? AND note_id = ?',
               [note["record_type"], note["name"]])
        db.sql(f'DELETE FROM "{TABLE}" WHERE name = ?', [note["name"]])
    return {"success": True, "note_id": note["name"]}


def delete_record_notes_for(record_type, record_id):
    """Drop a hard-deleted record's notes and their inbox entries."""
    db = get_db()
    ids = [row["name"] for row in db.sql(
        f'SELECT name FROM "{TABLE}" WHERE record_type = ? AND record_id = ?', [record_type, record_id])]
    for start in range(0, len(ids), 250):
        chunk = ids[start:start + 250]
        db.sql('DELETE FROM "Mention Notification" WHERE doctype = ? AND note_id IN ('
               + ','.join('?' for _ in chunk) + ')', [record_type, *chunk])
    db.sql(f'DELETE FROM "{TABLE}" WHERE record_type = ? AND record_id = ?', [record_type, record_id])


_NOT_PRINTED = ("Internal notes are visible to signed-in colleagues only; they are never printed and "
                "never change the record. Not the printed `remarks` (Notes / Terms) field.")


def initialize_record_notes():
    mentions.register_mention_source_fallback(_source, record_types)
    for name, handler, schema, role, description in (
        ("list_record_notes", list_record_notes, ListNotes, "viewer",
         "List the internal team notes on one document or master record, newest first. record_type is the "
         "document or master type slug (e.g. sales-invoice, customer) and record_id its name. "
         + _NOT_PRINTED),
        ("update_record_note", update_record_note, EditNote, "manager",
         "Replace the text of an internal note you wrote (note_id from list_record_notes). @mentions are "
         "re-read from the new text: added colleagues are notified, removed ones lose the notification."),
        ("delete_record_note", delete_record_note, DeleteNote, "manager",
         "Delete an internal note. Authors can delete their own notes; admins can delete any. Also removes "
         "its mention notifications."),
    ):
        services.register_action(name, handler, description=description,
                                 parameters=schema.model_json_schema(), minimum_role=role, pass_user=True)
