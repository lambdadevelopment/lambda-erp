"""Personal mentions with explicit recipients, atomic delivery and plugin sources.

No email/push delivery, inferred recipients, or caller-selectable inbox owner.
Plugins own record access and note storage; this module owns notifications.
"""
import hashlib
import json
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from api import services
from lambda_erp.database import get_db
from lambda_erp.utils import now

SOURCES = {}
REAL_ROLES = {"viewer", "manager", "admin"}


def register_mention_source(doctype, *, label, can_access, create_note, resolve_note, resolve_notes=None):
    """Register a record type's internal notes.

    can_access(record_id, principal) must check live record visibility.
    create_note(record_id, body, principal) returns a saved note ID; it must
    participate in db.atomic() without committing independently.
    resolve_note(note_id, principal) returns {record_id,title,body,view_url},
    or None for hidden/deleted notes. Check parent AND note visibility here.
    Optional resolve_notes(ids, principal) batches the same checks for inboxes.
    """
    SOURCES[doctype] = dict(label=label, can_access=can_access,
                           create_note=create_note, resolve_note=resolve_note, resolve_notes=resolve_notes)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Search(Input):
    query: str = Field(default="", max_length=100)


class Note(Input):
    doctype: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    mention_user_ids: list[str] = Field(default_factory=list, max_length=20)
    request_id: str = Field(min_length=8, max_length=100)


class Inbox(Input):
    view: Literal["open", "unread", "done", "all"] = "open"
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class State(Input):
    notification_id: str = Field(min_length=1, max_length=100)
    state: Literal["read", "unread", "done", "reopen"]


def _actor(user):
    from api.auth import refresh_auth_principal
    actor = refresh_auth_principal(user)
    if not actor or actor.get("role") not in REAL_ROLES:
        raise PermissionError("Sign in with a personal ERP account to use mentions.")
    return {**actor, "user_id": actor.get("user_id") or actor["name"]}


def _identity(actor):
    return {"user_id": actor.get("user_id") or actor["name"],
            "full_name": actor.get("full_name"), "email": actor.get("email")}


def _source(doctype):
    source = SOURCES.get(doctype)
    if not source:
        raise ValueError("This record type does not support mention notes.")
    return source


def search_mention_users(args, user):
    data, actor = Search.model_validate(args), _actor(user)
    q = data.query.strip().lower().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    rows = get_db().sql(
        'SELECT name, full_name, email FROM "User" WHERE enabled = 1 '
        "AND role IN ('viewer','manager','admin') "
        "AND (LOWER(COALESCE(full_name,'')) LIKE ? ESCAPE '!' OR LOWER(email) LIKE ? ESCAPE '!') "
        'ORDER BY full_name, name LIMIT 20', [f"%{q}%", f"%{q}%"])
    return {"users": rows, "acting_user": _identity(actor),
            "sources": [{"doctype": key, "label": value["label"]} for key, value in SOURCES.items()]}


def add_record_note(args, user):
    data, actor = Note.model_validate(args), _actor(user)
    if actor["role"] not in {"manager", "admin"}:
        raise PermissionError("Manager access is required to add notes.")
    if not data.body.strip():
        raise ValueError("Note body must not be blank.")
    source, db = _source(data.doctype), get_db()
    actor_id = _identity(actor)["user_id"]
    recipients = sorted(set(data.mention_user_ids))
    payload = {**data.model_dump(exclude={"request_id"}), "mention_user_ids": recipients}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    request_pk = uuid.uuid4().hex
    with db.atomic():
        if not source["can_access"](data.record_id, actor):
            raise HTTPException(404, "Record is unavailable")
        # The unique constraint also serializes concurrent retries on Postgres.
        db.sql('INSERT INTO "Mention Request" (id, actor_id, request_id, payload_hash, doctype, record_id) '
               'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (actor_id, request_id) DO NOTHING',
               [request_pk, actor_id, data.request_id, digest, data.doctype, data.record_id])
        saved = db.sql('SELECT * FROM "Mention Request" WHERE actor_id = ? AND request_id = ?',
                       [actor_id, data.request_id])[0]
        if saved["payload_hash"] != digest:
            raise ValueError("request_id was already used for a different note; use a new request_id.")
        repeated = saved["id"] != request_pk
        if repeated:
            note_id = saved["note_id"]
            notified_count = db.sql('SELECT COUNT(*) AS n FROM "Mention Notification" WHERE doctype = ? AND note_id = ?',
                                    [data.doctype, note_id])[0]["n"]
        else:
            targets = []
            for recipient_id in recipients:
                recipient = db.get_value("User", recipient_id, ["name", "full_name", "email", "role", "enabled"])
                if (not recipient or not recipient.get("enabled") or recipient.get("role") not in REAL_ROLES
                        or not source["can_access"](data.record_id, dict(recipient))):
                    raise ValueError("A mentioned colleague cannot access this record. No note was saved.")
                if recipient_id != actor_id:
                    targets.append(recipient_id)
            note_id = source["create_note"](data.record_id, data.body.strip(), actor)
            db.sql('UPDATE "Mention Request" SET note_id = ? WHERE id = ?', [note_id, request_pk])
            for recipient_id in targets:
                db.insert("Mention Notification", {
                    "id": uuid.uuid4().hex, "recipient_id": recipient_id, "actor_id": actor_id,
                    "doctype": data.doctype, "note_id": note_id, "created_at": now(),
                })
            notified_count = len(targets)
        note = source["resolve_note"](note_id, actor)
        if not note:
            raise HTTPException(404, "Note is no longer available")
    return {"note_id": note_id, "record_id": data.record_id, "view_url": note["view_url"],
            "notified_count": notified_count, "replayed": repeated, "acting_user": _identity(actor)}


def _visible(row, actor, notes=None, authors=None):
    source = SOURCES.get(row["doctype"])
    note = (notes.get((row["doctype"], row["note_id"])) if notes is not None else
            source["resolve_note"](row["note_id"], actor) if source else None)
    if not note:
        return None
    author = (authors.get(row["actor_id"]) if authors is not None else
              get_db().get_value("User", row["actor_id"], ["full_name", "email"]))
    return {"id": row["id"], "doctype": row["doctype"], "note_id": row["note_id"],
            "record_id": note["record_id"], "title": note["title"], "excerpt": note["body"][:240],
            "view_url": note["view_url"], "actor_name": (author or {}).get("full_name") or (author or {}).get("email") or row["actor_id"],
            "created_at": row["created_at"], "read_at": row["read_at"], "done_at": row["done_at"]}


def list_my_mentions(args, user):
    data, actor = Inbox.model_validate(args), _actor(user)
    rows = get_db().sql('SELECT * FROM "Mention Notification" WHERE recipient_id = ? ORDER BY created_at DESC, id DESC',
                        [_identity(actor)["user_id"]])
    # Re-check live access before exposing either excerpts or counts. Batch
    # source and author lookups so polling a populated inbox avoids N+1 queries.
    notes, authors = {}, {}
    for doctype in {row["doctype"] for row in rows}:
        source = SOURCES.get(doctype)
        if not source:
            continue
        ids = list({row["note_id"] for row in rows if row["doctype"] == doctype})
        for start in range(0, len(ids), 250):
            chunk = ids[start:start + 250]
            resolved = (source["resolve_notes"](chunk, actor) if source.get("resolve_notes") else
                        {note_id: source["resolve_note"](note_id, actor) for note_id in chunk})
            notes.update({(doctype, key): value for key, value in resolved.items()})
    author_ids = list({row["actor_id"] for row in rows})
    for start in range(0, len(author_ids), 250):
        chunk = author_ids[start:start + 250]
        authors.update({row["name"]: row for row in get_db().sql(
            'SELECT name, full_name, email FROM "User" WHERE name IN (' + ','.join('?' for _ in chunk) + ')', chunk)})
    visible = [item for row in rows if (item := _visible(row, actor, notes, authors))]
    unread = sum(not r["read_at"] and not r["done_at"] for r in visible)
    filtered = [r for r in visible if data.view == "all" or
                (data.view == "done" and r["done_at"]) or
                (data.view == "open" and not r["done_at"]) or
                (data.view == "unread" and not r["done_at"] and not r["read_at"])]
    return {"rows": filtered[data.offset:data.offset + data.limit], "total": len(filtered),
            "unread_count": unread, "has_more": data.offset + data.limit < len(filtered),
            "acting_user": _identity(actor)}


def set_mention_state(args, user):
    data, actor = State.model_validate(args), _actor(user)
    db, owner = get_db(), _identity(actor)["user_id"]
    with db.atomic():
        rows = db.sql('SELECT * FROM "Mention Notification" WHERE id = ? AND recipient_id = ?',
                      [data.notification_id, owner])
        if not rows or not _visible(rows[0], actor):
            raise HTTPException(404, "Mention is unavailable")
        assignments = {"read": 'read_at = COALESCE(read_at, ?)',
                       "unread": 'read_at = NULL, done_at = NULL',
                       "done": 'done_at = COALESCE(done_at, ?), read_at = COALESCE(read_at, ?)',
                       "reopen": 'done_at = NULL'}
        stamps = [now()] * (2 if data.state == "done" else 1 if data.state == "read" else 0)
        db.sql('UPDATE "Mention Notification" SET ' + assignments[data.state] +
               ' WHERE id = ? AND recipient_id = ?', [*stamps, data.notification_id, owner])
    return {"success": True, "notification_id": data.notification_id, "state": data.state}


def initialize_mentions():
    db = get_db()
    with db.atomic():
        db.sql('CREATE TABLE IF NOT EXISTS "Mention Request" ('
               'id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, request_id TEXT NOT NULL, '
               'payload_hash TEXT NOT NULL, doctype TEXT NOT NULL, record_id TEXT NOT NULL, note_id TEXT, '
               'UNIQUE(actor_id, request_id))')
        db.sql('CREATE TABLE IF NOT EXISTS "Mention Notification" ('
               'id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, actor_id TEXT NOT NULL, '
               'doctype TEXT NOT NULL, note_id TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT, done_at TEXT, '
               'UNIQUE(recipient_id, doctype, note_id))')
        db.sql('CREATE INDEX IF NOT EXISTS mention_inbox ON "Mention Notification" (recipient_id, created_at)')
    for name, handler, schema, role, description in (
        ("search_mention_users", search_mention_users, Search, "viewer",
         "Find enabled ERP colleagues for explicit @mentions by name/email. Returns canonical user IDs, acting_user and supported record types. Do not guess a person when ambiguous."),
        ("add_record_note", add_record_note, Note, "manager",
         "Save an internal note on a supported record and notify explicitly selected colleagues. Resolve mention_user_ids using search_mention_users. Use a new request_id (UUID) per intended note; reuse it unchanged on retries. No email is sent. Authorship is the authenticated ERP account. Self-mentions do not notify."),
        ("list_my_mentions", list_my_mentions, Inbox, "viewer",
         "List the authenticated ERP user's mentions, with record/note links. open means not done; unread is separate. Use this for 'my mentions', never guess or pass another user. Returns acting_user, unread_count and pagination."),
        ("set_mention_state", set_mention_state, State, "viewer",
         "Mark one of the authenticated user's mentions read, unread, done, or reopen it. Done only clears this personal notification; it does not complete or modify the business record."),
    ):
        services.register_action(name, handler, description=description,
                                 parameters=schema.model_json_schema(), minimum_role=role, pass_user=True)
