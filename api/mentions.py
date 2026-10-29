"""Personal mentions resolved from visible note text, with atomic delivery.

No email/push delivery, hidden recipients, or caller-selectable inbox owner.
Plugins own record access and note storage; this module owns notifications.
"""
import hashlib
import json
import re
import uuid
from contextvars import ContextVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from api import services
from lambda_erp.database import get_db
from lambda_erp.utils import now

SOURCES = {}
_FALLBACKS = []  # (resolve(doctype) -> source | None, list_types() -> [doctype]) for unregistered types
REAL_ROLES = {"viewer", "manager", "admin"}
_WRITING_MENTION_NOTE = ContextVar("writing_mention_note", default=False)
_MENTION_START = re.compile(r"(?<![\w@./+\-])@(?=\S)")


def writing_mention_note():
    """Dedicated note action will deliver after its source has saved the note."""
    return _WRITING_MENTION_NOTE.get()


def _mention_users():
    return get_db().sql('SELECT name, full_name, email, role, enabled FROM "User" '
                        "WHERE enabled = 1 AND role IN ('viewer','manager','admin')")


def _aliases(users):
    aliases = {}
    for user in users:
        for value in (user.get("full_name"), user.get("email")):
            if value and value.strip():
                aliases.setdefault(value.strip().lower(), {})[user["name"]] = user
    return aliases


def resolve_note_mentions(body):
    """Resolve visible @Full Name / @email tokens, never hidden recipient IDs."""
    aliases = _aliases(_mention_users())
    patterns = [(re.compile(re.escape(alias) + r"(?![\w@\-])", re.IGNORECASE), users)
                for alias, users in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True)]
    recipients, end = {}, 0
    for marker in _MENTION_START.finditer(body):
        if marker.start() < end:
            continue  # The second @ inside an explicit @email address.
        for pattern, users in patterns:
            match = pattern.match(body, marker.end())
            if match:
                if len(users) != 1:
                    raise ValueError("An @mention matches multiple colleagues. Use the person's @email or select them from the suggestions. No note was saved.")
                recipients.update(users)
                end = match.end()
                break
        # Unknown handles are ordinary text, just like unmarked emails.
    if len(recipients) > 20:
        raise ValueError("A note can mention at most 20 colleagues. No note was saved.")
    return recipients


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


def register_mention_source_fallback(resolve, list_types):
    """Resolve sources for record types without an explicit registration
    (api.record_notes uses this for every document and master type)."""
    if all(entry[0] is not resolve for entry in _FALLBACKS):
        _FALLBACKS.append((resolve, list_types))


def find_source(doctype):
    source = SOURCES.get(doctype)
    for resolve, _ in _FALLBACKS:
        if source:
            break
        source = resolve(doctype)
    return source


def supported_sources():
    sources = {key: value["label"] for key, value in SOURCES.items()}
    for resolve, list_types in _FALLBACKS:
        for doctype in list_types():
            if doctype not in sources and (source := resolve(doctype)):
                sources[doctype] = source["label"]
    return [{"doctype": key, "label": label} for key, label in sources.items()]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Search(Input):
    query: str = Field(default="", max_length=100)


class Note(Input):
    doctype: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=8, max_length=100)


class Inbox(Input):
    view: Literal["open", "unread", "done", "all"] = "open"
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class State(Input):
    notification_id: str = Field(min_length=1, max_length=100)
    state: Literal["read", "unread", "done", "reopen"]


def _actor(user):
    if (user or {}).get('_shared_connection'):
        raise PermissionError("Mentions require a personal ERP connection. The shared team key identifies its owner, not the current user.")
    from api.auth import refresh_auth_principal
    actor = refresh_auth_principal(user)
    if not actor or actor.get("role") not in REAL_ROLES:
        raise PermissionError("Sign in with a personal ERP account to use mentions.")
    return {**actor, "user_id": actor.get("user_id") or actor["name"]}


def _identity(actor):
    return {"user_id": actor.get("user_id") or actor["name"],
            "full_name": actor.get("full_name"), "email": actor.get("email")}


def _source(doctype):
    source = find_source(doctype)
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
    aliases = _aliases(_mention_users())
    for row in rows:
        full_name = (row.get("full_name") or "").strip()
        token = full_name if full_name and len(aliases.get(full_name.lower(), {})) == 1 else row["email"]
        row["mention_text"] = "@" + token
    return {"users": rows, "acting_user": _identity(actor),
            "sources": supported_sources()}


def add_record_note(args, user):
    data, actor = Note.model_validate(args), _actor(user)
    if actor["role"] not in {"manager", "admin"}:
        raise PermissionError("Manager access is required to add notes.")
    if not data.body.strip():
        raise ValueError("Note body must not be blank.")
    source, db = _source(data.doctype), get_db()
    actor_id = _identity(actor)["user_id"]
    payload = data.model_dump(exclude={"request_id"})
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
            token = _WRITING_MENTION_NOTE.set(True)
            try:
                note_id = source["create_note"](data.record_id, data.body.strip(), actor)
            finally:
                _WRITING_MENTION_NOTE.reset(token)
            db.sql('UPDATE "Mention Request" SET note_id = ? WHERE id = ?', [note_id, request_pk])
            notified_count = sync_note_mentions(data.doctype, data.record_id, note_id, data.body, actor)
        note = source["resolve_note"](note_id, actor)
        if not note:
            raise HTTPException(404, "Note is no longer available")
    return {"note_id": note_id, "record_id": data.record_id, "view_url": note["view_url"],
            "notified_count": notified_count, "replayed": repeated, "acting_user": _identity(actor)}


def sync_note_mentions(doctype, record_id, note_id, body, user):
    """Shared delivery for dedicated actions and generic document save hooks.

    Call inside the note's save transaction. Existing recipients keep their
    read/done state; new recipients receive one notification; removing a
    visible mention removes its notification. No caller-selected recipient IDs.
    """
    db, source = get_db(), _source(doctype)
    recipients = resolve_note_mentions(body)
    with db.atomic():
        existing = db.sql('SELECT recipient_id FROM "Mention Notification" WHERE doctype = ? AND note_id = ?',
                          [doctype, note_id])
        if not recipients and not existing:
            return 0  # Ordinary text needs no personal notification identity.
        actor = _actor(user)
        if actor['role'] not in {'manager', 'admin'}:
            raise PermissionError('Manager access is required to write mention notes.')
        if not source['can_access'](record_id, actor):
            raise HTTPException(404, 'Record is unavailable')
        for recipient in recipients.values():
            if not source['can_access'](record_id, recipient):
                raise ValueError('A mentioned colleague cannot access this record. No note was saved.')
        previous = {row['recipient_id'] for row in existing}
        for recipient_id in previous - recipients.keys():
            db.sql('DELETE FROM "Mention Notification" WHERE doctype = ? AND note_id = ? AND recipient_id = ?',
                   [doctype, note_id, recipient_id])
        for recipient_id in recipients.keys() - previous:
            db.insert('Mention Notification', {
                'id': uuid.uuid4().hex, 'recipient_id': recipient_id, 'actor_id': _identity(actor)['user_id'],
                'doctype': doctype, 'note_id': note_id, 'created_at': now(),
            })
    return len(recipients)


def _visible(row, actor, notes=None, authors=None):
    source = find_source(row["doctype"])
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
        source = find_source(doctype)
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
         "Find enabled ERP colleagues for visible @mentions by name/email. Returns mention_text: insert that exact text in the note body to tag the person. Also returns canonical user IDs, acting_user and supported record types. Do not guess a person when ambiguous."),
        ("add_record_note", add_record_note, Note, "manager",
         "Save an internal team note on a record and notify people visibly @mentioned in its body, including yourself. doctype is the record type slug: any document or master type (e.g. sales-invoice, purchase-order, customer, supplier, item) or a plugin type listed in search_mention_users sources; record_id is the record's name. Internal notes are never printed and do not change the record; customer-facing text belongs in the document's printed `remarks` (Notes / Terms) field instead. A note needs no mention. To tag someone, first use search_mention_users, then include each recipient's exact mention_text in body, e.g. 'salü vom johnny @Jon Torus'. Recipients are determined only from body; there is no recipient-ID argument. Unknown @handles and ordinary emails remain text. Ambiguous names require @email. CRM lead notes written through the generic document tools use the same mention processing. Use a new request_id (UUID) per intended note; reuse it unchanged on retries. No email is sent. Authorship is the authenticated ERP account."),
        ("list_my_mentions", list_my_mentions, Inbox, "viewer",
         "List the authenticated ERP user's mentions, with record/note links. open means not done; unread is separate. Use this for 'my mentions', never guess or pass another user. Returns acting_user, unread_count and pagination."),
        ("set_mention_state", set_mention_state, State, "viewer",
         "Mark one of the authenticated user's mentions read, unread, done, or reopen it. Done only clears this personal notification; it does not complete or modify the business record."),
    ):
        services.register_action(name, handler, description=description,
                                 parameters=schema.model_json_schema(), minimum_role=role, pass_user=True)
