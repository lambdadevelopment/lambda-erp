# Recent changes and CRM outreach

The core's document and master lists accept a shared `time_filter`; no activity
feed or event table is introduced. REST, browser chat, the Chat API and MCP use
the same validation and query implementation. Deployments inherit this when they
upgrade their core dependency. Existing installations do not change until then.

## Query contract

For records changed in the preceding 24 elapsed hours:

```json
{"field": "modified", "last_hours": 24}
```

Use `creation` for new records and `occurred_at` for CRM interactions. The field
must exist on that type and be a text-stored timestamp (`creation`, `modified`,
`*_at` or `*_datetime`). `get_document_fields` / `get_master_fields` expose eligible
fields in `time_filter_fields`. Business dates retain `from_date` / `to_date`.

The server calculates the current instant once and returns UTC bounds. A window
includes its lower bound and excludes its upper bound. For stable pagination and
queries across several types, reuse those bounds (changing only `field` as needed):

```json
{
  "field": "occurred_at",
  "since": "2026-09-14T17:51:00+00:00",
  "until": "2026-09-15T17:51:00+00:00"
}
```

Absolute bounds require an explicit timezone. Calendar days must be converted
from the user's timezone; they are different from elapsed 24-hour windows,
particularly across daylight-saving changes. Windows must be positive and no
longer than 366 days. Unknown fields, malformed timestamps, conflicting relative
and absolute bounds, and ambiguous combinations with legacy date filters return
an error. A failed filter must never be retried by silently removing it.

REST examples (URL-encode the JSON query parameter):

```text
GET /api/documents/activity?time_filter={"field":"occurred_at","last_hours":24}
GET /api/masters/customer?time_filter={"field":"modified","last_hours":24}
```

Chat/MCP tool arguments:

```json
{
  "doctype": "activity",
  "time_filter": {"field": "occurred_at", "last_hours": 24},
  "filters": {"type": ["in", ["call", "email", "note", "meeting"]]},
  "fields": ["name", "type", "lead_id", "subject", "body", "occurred_at"],
  "limit": 50
}
```

Master lookups use `search_masters` with `master_type`, `time_filter`, and
`result_fields` for projection (`fields` retains its historical search meaning).
Both generic filter builders now support `in` and `not in` with up to 500
non-null scalar values. Empty IN matches nothing; empty NOT IN imposes no
restriction. Otherwise SQL NULL semantics apply.

## Results and compatibility

REST lists return their usual `rows`, `total`, `limit`, `offset`, and `text_fields`,
with these additional properties:

- `has_more` and `next_offset` describe remaining matches, including when fields
  are projected. Time-filtered lists order by the parsed timestamp, then name,
  unless another order column is explicitly selected.
- `time_filter` contains the resolved absolute window, or null.
- `coverage.complete` and `coverage.unknown_time_rows` disclose otherwise matching
  records with missing/invalid timestamps. Those records cannot be assigned to
  this window; they are excluded from `rows` and `total`, with a `warnings` entry.

Chat and MCP preserve their historical array result unless `time_filter` is used
or `include_meta: true` is requested. In that case they return the same page
metadata. The master metadata mode defaults to 50 rows (max 500); legacy simple
master lookups preserve their previous behavior. Documents default to 20 in chat
and 50 in REST. Use explicit limits and projections for compact answers. Prev/next
REST navigation honors the same window and ordering.

`coverage.complete` describes timestamp quality among the filtered records,
**not** completeness of an ERP-wide audit. Pages are live queries, not an MVCC
snapshot across requests: concurrent updates/deletions may change later pages.

## Timestamp storage and migration

Migration 31 installs the PostgreSQL timestamp comparison function and adds
nullable `creation`/`modified` columns to Customer, Supplier, Item, Warehouse,
Account, Company and Cost Center. SQLite registers the equivalent deterministic
function per connection. Queries compare instants, so historical space-separated
UTC values and ISO strings with offsets are not sorted lexicographically.
Indexes cover parsed creation/modified/occurred_at for core and plugin tables.

Old masters retain unknown (NULL) timestamps; migration time is **not** their
creation time. New core master inserts (including bulk inserts) and updates via
`db.set_value` capture timestamps. REST/chat master writes manage them server-side.
Plugin master CRUD uses timestamps when its schema provides them; no schema is
invented for unknown plugin masters. Direct SQL/import paths must maintain their
own timestamps. Document lifecycle writes already maintain `modified`; dependent
updates through low-level paths do not all capture it. These lists therefore
cannot promise to include every indirect business change.

Document persistence validates/normalizes creation, modified and occurred_at.
A valid full timestamp with a space separator is normalized **before** plugin
validation, preventing the existing CRM validator from appending a second time.
Persist-time validation catches malformed values produced by hooks too.
Date-only CRM input may still be completed by its controller before persistence.
Historical naive full timestamps are interpreted as UTC, matching ERP storage.

## Existing malformed CRM values

No existing Activity data is silently rewritten. The maintenance tool defaults
to a read-only report, does not bootstrap the application, and runs no migrations:

```bash
python -m scripts.repair_activity_timestamps > activity-timestamp-review.json
```

Set `LAMBDA_ERP_DB` in the environment to the intended database. The report only
proposes repairs for the known `date first-clock T appended-clock+00:00` shape.
It preserves the **first** clock, assuming that original naive timestamp was UTC.
Review that assumption and each row; other invalid values require separate
investigation. Once reviewed, explicitly apply that file:

```bash
python -m scripts.repair_activity_timestamps --apply activity-timestamp-review.json
```

Apply is one atomic transaction with old-value checks. A stale/missing record
rolls the entire batch back. `creation`/`modified` and business data are preserved;
this repair must not look like new business activity. No live repair is part of
the core implementation. Derived CRM metrics are not recomputed by this utility;
review/recompute those separately if a repaired interaction affects them.

## Chat interpretation and limits

The system prompt teaches the model to use exact windows, check totals and data
quality, and cover relevant types rather than generalizing from creation alone.
Outreach notes can describe drafts or scheduled sends; those are distinct from
actually sent messages and replies. Leads enrich matching interactions; old lead
state is not evidence of activity within the window. Include disabled/discarded
records explicitly when the requested overview requires them.

`modified` only describes the current record's latest captured change. These
queries do not reconstruct earlier edits, authors, old/new field values or hard
deletions. The [activity-feed design](activity-events-plan.md) remains deferred
until such historical/audit requirements justify a separate event mechanism.
