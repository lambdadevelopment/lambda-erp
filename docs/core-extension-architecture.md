# Core + extension architecture

Lambda ERP is distributed as an open core: customer deployments depend on the
published Python and npm packages and register their custom behavior at explicit
extension seams. Core fixes arrive through dependency updates instead of merges
into customer forks.

Backend extensions are loaded from the comma-separated
`LAMBDA_ERP_PLUGINS` environment variable. Each module exposes a
`register()` function. With the variable unset, the core runs unchanged.

## Building a customer deployment on top of the core

This repo is the **core product**. A customer deployment is a **separate
private repo that depends on this one** and overrides/extends it — it does NOT
fork or edit core files. Core fixes arrive via a version bump.

### Layout of a customer repo
```
acme-erp/
  pyproject.toml          # depends on lambda-erp==<version>
  acme/
    __init__.py
    plugin.py             # register() — wires overrides + hooks
    sales_invoice.py      # e.g. class AcmeSalesInvoice(SalesInvoice): ...
  config/                 # branding, enabled features, base currency, OAuth
  frontend/               # depends on core frontend; overrides via Vite alias
  deploy/                 # Dockerfile, Azure config, env/secrets
```

### Override core business logic (replace)
Subclass the core document class and register it:
```python
# acme/sales_invoice.py
from lambda_erp.accounting.sales_invoice import SalesInvoice
class AcmeSalesInvoice(SalesInvoice):
    def _get_gl_entries(self):
        gl = super()._get_gl_entries()
        # customer-specific posting
        return gl
```

### Add behavior (don't replace) — hooks
```python
from lambda_erp.hooks import register_hook
register_hook("Sales Invoice:after_submit", push_to_external_system)
```
Hook events: `<DocType>:{before,after}_{save,submit,cancel}`.

### Add a new master type — chat + REST discovery included
```python
from api.services import register_master

register_master(
    "gadget", "Gadget", "gadget_name", name_prefix="GAD",
    random_name=True,
    description="A deployment-specific inventory gadget.",
    fields=["gadget_name", "owner_ref"],
    reference_checks=[
        ('SELECT 1 FROM "Work Order" WHERE gadget = ? LIMIT 1', "work order"),
    ],
)
```
`register_master(slug, table, name_field, *, name_prefix=None, name_digits=3, random_name=False,
identity_alias=None, description=None, fields=None, reference_checks=None)` is
the master-side counterpart of `register_doctype`.
One call makes the type first-class on every master surface:

- **REST**: `/api/masters/gadget` CRUD with the standard role guards.
- **AI chat, from day one**: `search_masters`, `get_master_fields`,
  `create_master`, `update_master`, `delete_master` all accept the new type.
  The chat tool schemas and system prompt are built per request from the live
  registry (`api.chat.build_tools` / `build_system_prompt`), so plugin load
  order doesn't matter and no prompt editing is needed. Doctypes added via
  `register_doctype` are likewise folded into the document tools' enums.
- **No field declarations**: columns are introspected from the live table.
  Every text column is immediately searchable (minus audit noise and bulk
  free-text columns like `notes`, which are searched only when named in
  `fields`), with the same fuzzy-misspelling fallback core masters get.

`name_prefix` enables auto-generated ids (`GAD-001`) when a record is created
without an explicit `name`; `name_digits=4` produces `GAD-0001`. With
`random_name=True`, ids use an opaque
`GAD-<hex>` suffix instead; use this for high-volume reference tables where a
restart-time sequential suffix scan would be inappropriate. `identity_alias`
exposes the `name` PK under a
friendlier key, like Item's `item_code`. `description`/`fields` add deployment
guidance to the chat prompt. `reference_checks` gives plugin masters the same
safe-delete behavior as core masters: a referenced row is disabled when it has
a `disabled` column, otherwise deletion returns 409. Caveat: chat/REST master
writes are plain row CRUD (a registered Document class's `validate()` does not
run on this path).

The frontend has a parallel build-time `registerMaster(config)` registry for
generic master pages. It configures fields, lightweight list projection,
default/selectable columns, search fields, dropdown filters, linked columns,
and registered-action buttons without adding a hard-coded core master.

### Add a deliberate business action — REST + chat + MCP
```python
from api.services import register_action

register_action(
    "activate_gadget",
    lambda args: activate_gadget(args["name"]),
    description="Activate one Gadget. Idempotent.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    minimum_role="manager",
)
```
The action is callable at `POST /api/actions/activate_gadget`, appears as the
same function tool in ERP chat, and is listed/callable over MCP for permitted
roles. One handler and JSON Schema drive all three paths. Demo
`public_manager` access is denied by default. A frontend master action points at
the same name through `registerMaster({ actions: [...] })`.

### Give a plugin its own tables and migrations
```python
from api.services import register_table, register_migration

GADGET_TABLE = '''CREATE TABLE IF NOT EXISTS "Gadget" (
    name TEXT PRIMARY KEY, gadget_name TEXT, owner_ref TEXT,
    docstatus INTEGER DEFAULT 0, creation TEXT, modified TEXT
)'''

def _add_color(db):
    db.ensure_column("Gadget", "color", "TEXT")          # idempotent ALTER
    db.sql('UPDATE "Gadget" SET color = ? WHERE color IS NULL', ["unset"])

def register():
    register_table(GADGET_TABLE)
    register_migration("acme:0001_gadget_color", _add_color)
```
`register_table(ddl)` declares a table (write the SQLite-flavoured `CREATE TABLE
IF NOT EXISTS` subset the core uses; it's translated per backend). The core
creates it in the app lifespan via `apply_plugin_schema()` — after
`load_plugins()`, before any document is created — so `register_doctype` /
`register_master` can then attach to it. This replaces the
`db.conn.execute(db._ddl(...))` hack.

`register_migration(migration_id, fn)` runs `fn(db)` **exactly once per
database**, recorded in `_PluginMigrations` by id (namespace it,
`"acme:0001_…"`). Use it for `ALTER`/backfills on an existing table — the thing
`register_table`'s `IF NOT EXISTS` can't do. `db.ensure_column(table, col, type)`
(idempotent column-add) and `db.drop_column(table, col)` (idempotent drop) are
the helpers for use inside `fn`. Both are **lock-safe**: on Postgres the `ALTER`
is bounded by `lock_timeout` + retry, so a schema change during a rolling deploy
catches a gap in the live old revision's traffic instead of blocking the boot
(an unbounded `ALTER` on a hot table hangs the new revision's startup probe and
crash-loops it). Migrations run in registration order after tables exist; a
failing one is rolled back and left unrecorded so it retries next boot (it never
aborts startup). Must be idempotent and self-contained.

### Filter a document list by any field
`GET /api/documents/{slug}` accepts any query param that names a real column of
the doctype as an equality filter, plus `order_by` + `order` (`asc|desc`):
```
GET /api/documents/activity?lead_id=LEAD-3316&order_by=occurred_at&order=desc
```
An unknown field name (or `order_by`) is a **400**, never interpolated into SQL
— names are validated against the live columns (`api.services.document_columns`)
and values are parameterized. Reserved params (`status`, `party`, `from_date`,
`to_date`, `docstatus`, `limit`, `offset`, `order_by`, `order`,
`include_discarded`) keep their meaning. This is what lets a plugin doctype like
`Activity` be listed by its FK to a parent.

### Make a doctype chat-manageable
```python
from api.services import register_chat_doctype

register_chat_doctype("contact", description=(
    "A person at a Lead — the buying centre. Set buying_role."))
```
The chat's document tools (`create_document`/`update_document`/`list_documents`/
`get_document`) already accept **any** registered doctype and run its
`validate()`. `register_chat_doctype(slug, *, description, fields=None)` only
**teaches the model** what the type is: `build_system_prompt` gains a "Custom
record types" section with the description, the (optional) `fields` hint, and
the Document class's **`LINK_FIELDS`** relationships (read live — e.g. a
contact's `lead_id → Lead`), plus the rule to drive these with the document
tools and attach a child by setting its link field to the parent's `name`.

This is the validated, first-class way to expose a whole module (a CRM's lead /
contact / activity) to chat — no need to also `register_master` it (the master
path does plain-row writes that skip `validate()`).

### Register everything at startup
```python
# acme/plugin.py
from api.services import (
    register_doctype, register_master, register_converter,
    register_table, register_migration,
)
from lambda_erp.hooks import register_hook
from .sales_invoice import AcmeSalesInvoice

def register():
    register_doctype("Sales Invoice", AcmeSalesInvoice)
    register_master("gadget", "Gadget", "gadget_name", name_prefix="GAD")
    register_table(GADGET_TABLE)
    register_migration("acme:0001_gadget_color", _add_color)
    register_hook("Sales Invoice:after_submit", push_to_external_system)
```
Point the deployment at it: `LAMBDA_ERP_PLUGINS=acme` (comma-separated for
multiple). The core imports each module and calls `register()` on startup.

### Rules
- Don't fork or edit core files — override at a seam. If the thing you need to
  change isn't a seam yet, **add the seam to the core** (a PR upstream), then
  override from the customer repo.
- Keep branding / feature toggles / currency / auth config in `config`/env.
- Document conversions upgrade their result to the registered subclass.
  Use `register_converter` only when the conversion logic itself must change.
- Bump the core dependency version to pull fixes; never copy core code in.

## Frontend extensions

The `@lambda-development/erp-core` package exposes registries for doctypes,
masters, routes, navigation groups and whole components, plus runtime branding,
a configurable API base and a Tailwind preset. A customer frontend registers
its overrides before calling `bootstrap()`. See the corresponding example in
the repository README and the working `lambda-erp-example` deployment.

## Relevant code

- `api/services.py` — document, master, action and converter registries.
- `lambda_erp/hooks.py` and `lambda_erp/model.py` — lifecycle hooks.
- `api/main.py` — plugin loading and startup schema application.
- `frontend/src/index.ts` — public frontend extension surface.
- `tests/test_erp_validation.py` and the focused plugin tests — regression
  coverage for registries, hooks, startup loading and plugin schema.

## PDF profiles

Every new document registration must declare `pdf_profile=PDFProfile(...)` or
`pdf_profile=DisabledPDF(reason)`. Core doctype overrides inherit their profile.
See [PDF output contracts](pdf-output.md) for field allowlists, templates, errors
and the shared REST/chat/MCP generation contract.
