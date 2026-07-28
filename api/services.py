"""Bridge between FastAPI request data and lambda_erp Document classes."""

from lambda_erp.utils import _dict, now
from lambda_erp.database import get_db

from lambda_erp.selling.quotation import (
    Quotation, make_sales_order,
    make_sales_invoice_from_quotation, make_delivery_note_from_quotation,
)
from lambda_erp.selling.proposal import Proposal
from lambda_erp.selling.sales_order import SalesOrder, make_sales_invoice
from lambda_erp.buying.purchase_order import PurchaseOrder, make_purchase_invoice
from lambda_erp.accounting.sales_invoice import SalesInvoice, make_sales_return
from lambda_erp.accounting.purchase_invoice import PurchaseInvoice, make_purchase_return
from lambda_erp.accounting.payment_entry import PaymentEntry
from lambda_erp.accounting.journal_entry import JournalEntry
from lambda_erp.stock.stock_entry import StockEntry
from lambda_erp.stock.delivery_note import DeliveryNote, make_delivery_note, make_delivery_return
from lambda_erp.stock.purchase_receipt import PurchaseReceipt, make_purchase_receipt, make_purchase_receipt_return
from lambda_erp.accounting.pos_invoice import POSInvoice
from lambda_erp.controllers.pricing_rule import PricingRule
from lambda_erp.accounting.budget import Budget
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.accounting.bank_transaction import BankTransaction


# --- Doctype registries ---

DOCUMENT_CLASSES = {
    "Quotation": Quotation,
    "Proposal": Proposal,
    "Sales Order": SalesOrder,
    "Sales Invoice": SalesInvoice,
    "Purchase Order": PurchaseOrder,
    "Purchase Invoice": PurchaseInvoice,
    "Payment Entry": PaymentEntry,
    "Journal Entry": JournalEntry,
    "Stock Entry": StockEntry,
    "Delivery Note": DeliveryNote,
    "Purchase Receipt": PurchaseReceipt,
    "POS Invoice": POSInvoice,
    "Pricing Rule": PricingRule,
    "Budget": Budget,
    "Subscription": Subscription,
    "Bank Transaction": BankTransaction,
}

CONVERTERS = {
    ("Quotation", "Sales Order"): make_sales_order,
    ("Quotation", "Sales Invoice"): make_sales_invoice_from_quotation,
    ("Quotation", "Delivery Note"): make_delivery_note_from_quotation,
    ("Sales Order", "Sales Invoice"): make_sales_invoice,
    ("Sales Order", "Delivery Note"): make_delivery_note,
    ("Purchase Order", "Purchase Invoice"): make_purchase_invoice,
    ("Purchase Order", "Purchase Receipt"): make_purchase_receipt,
    # Returns (same-to-same conversion creates a return document)
    ("Sales Invoice", "Sales Invoice"): make_sales_return,
    ("Purchase Invoice", "Purchase Invoice"): make_purchase_return,
    ("Delivery Note", "Delivery Note"): make_delivery_return,
    ("Purchase Receipt", "Purchase Receipt"): make_purchase_receipt_return,
}

MASTER_TABLES = {
    "customer": ("Customer", "customer_name"),
    "supplier": ("Supplier", "supplier_name"),
    "item": ("Item", "item_name"),
    "warehouse": ("Warehouse", "warehouse_name"),
    "account": ("Account", "account_name"),
    "company": ("Company", "company_name"),
    "cost-center": ("Cost Center", "cost_center_name"),
}

# Auto-naming prefixes: masters created without an explicit `name` get a
# generated PREFIX-NNN id (see masters.create_master_record). Types without an
# entry require an explicit name (or derive it, like company).
MASTER_NAME_PREFIXES = {
    "customer": "CUST",
    "supplier": "SUPP",
    "item": "ITEM",
    "warehouse": "WH",
}

# An Item's code is stored in the primary-key column `name`, but every
# transaction line and report references it as `item_code`. Accept that
# intuitive alias on the master API so callers (LLM tools, REST clients) can
# set and read the code under the name they already use everywhere else,
# instead of silently falling back to ITEM-NNN.
MASTER_IDENTITY_ALIAS = {
    "item": "item_code",
}

# Slug <-> doctype name mapping
SLUG_TO_DOCTYPE = {}
DOCTYPE_TO_SLUG = {}
for dt in DOCUMENT_CLASSES:
    slug = dt.lower().replace(" ", "-")
    SLUG_TO_DOCTYPE[slug] = dt
    DOCTYPE_TO_SLUG[dt] = slug


def register_doctype(doctype: str, cls, slug: str | None = None) -> None:
    """Register (or override) the class used for a doctype.

    Extension point for customer deployments (see
    docs/core-extension-architecture.md): a plugin subclasses a core document
    class and registers it here at startup, so every loader path
    (create/load/update/submit/cancel_document) resolves the subclass.
    `get_document_class` reads `DOCUMENT_CLASSES` live, so no other change is
    needed.
    """
    DOCUMENT_CLASSES[doctype] = cls
    slug = slug or doctype.lower().replace(" ", "-")
    SLUG_TO_DOCTYPE[slug] = doctype
    DOCTYPE_TO_SLUG[doctype] = slug


# --- Plugin schema seam ---
#
# Core tables are created by database.setup(); plugin tables have no home there
# (setup() runs before plugins load). These two registries let a plugin declare
# its own tables and one-time migrations from register(); the core applies them
# via apply_plugin_schema() in the app lifespan, right after load_plugins() and
# before any document is created. See docs/core-extension-architecture.md.

_PLUGIN_TABLES: list[str] = []
_PLUGIN_MIGRATIONS: list = []  # list[tuple[str, Callable[[DB], None]]]


def register_table(ddl: str) -> None:
    """Register a CREATE TABLE (use IF NOT EXISTS) for a plugin table. The DDL is
    the SQLite-flavoured subset the core's own DDL uses; apply_plugin_schema()
    translates it per backend via db._ddl(). Idempotent — safe on every boot."""
    _PLUGIN_TABLES.append(ddl)


def register_migration(migration_id: str, fn) -> None:
    """Register a one-shot migration run exactly once per database, recorded in
    _PluginMigrations by `migration_id` (namespace it, e.g. "internal:0001_x").
    `fn(db)` receives the DB; use db.ensure_column(...) for idempotent ALTERs
    and plain db.sql(...) for backfills. Runs after registered tables exist, in
    registration order. Must be idempotent and self-contained (single logical
    unit — it commits on success)."""
    _PLUGIN_MIGRATIONS.append((migration_id, fn))


def apply_plugin_schema() -> None:
    """Create registered plugin tables, then run pending plugin migrations once
    each. Call from the app lifespan after load_plugins(). Table creation always
    runs (IF NOT EXISTS); each migration runs only if its id isn't already in
    _PluginMigrations. A failing migration is rolled back and left unrecorded so
    it retries next boot; it does not abort startup (mirrors the core migrator)."""
    db = get_db()
    for ddl in _PLUGIN_TABLES:
        db.conn.execute(db._ddl(ddl))
    db.conn.commit()

    if not _PLUGIN_MIGRATIONS:
        return

    db.conn.execute(
        'CREATE TABLE IF NOT EXISTS "_PluginMigrations" '
        "(migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    db.conn.commit()
    applied = {r["migration_id"] for r in db.sql('SELECT migration_id FROM "_PluginMigrations"')}

    for migration_id, fn in _PLUGIN_MIGRATIONS:
        if migration_id in applied:
            continue
        try:
            fn(db)
            db.sql(
                'INSERT INTO "_PluginMigrations" (migration_id, applied_at) VALUES (?, ?)',
                [migration_id, now()],
            )
            db.conn.commit()
            print(f"[plugin-migration] applied {migration_id}", flush=True)
        except Exception as e:
            db.conn.rollback()
            print(f"[plugin-migration] FAILED {migration_id}: {e!r} — will retry next boot", flush=True)


def document_columns(doctype_slug: str) -> set:
    """The real column names of a registered doctype's table, or empty set if the
    slug is unknown. Used to validate ad-hoc list filters against actual columns
    (see the documents router) so an unknown field is a 400, never SQL."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        return set()
    return get_db()._get_table_columns(doctype)


def register_master(slug: str, table: str, name_field: str, *,
                    name_prefix: str | None = None,
                    identity_alias: str | None = None) -> None:
    """Register (or override) a master type — the master-side counterpart of
    `register_doctype`.

    A plugin calls this at startup and the master becomes a first-class type
    everywhere `MASTER_TABLES` is consulted: the REST CRUD surface
    (`/api/masters/{slug}`) and the chat tools (search_masters,
    get_master_fields, create/update/delete_master — their schemas and the
    system prompt are built from the live registry per request). Fields are
    never declared: they're introspected from the table at call time, so every
    text column of `table` is immediately searchable.

    `name_field` is the human display column (e.g. "company_name").
    `name_prefix` enables auto-generated ids (prefix "LEAD" -> LEAD-001) when a
    record is created without an explicit `name`. `identity_alias` lets callers
    address the `name` PK under a friendlier key, like item's `item_code`.
    """
    MASTER_TABLES[slug] = (table, name_field)
    if name_prefix:
        MASTER_NAME_PREFIXES[slug] = name_prefix
    if identity_alias:
        MASTER_IDENTITY_ALIAS[slug] = identity_alias


# Chat guidance for registered doctypes: slug -> {"description", "fields"}. The
# document tools (create/update/list/get_document) already accept any registered
# doctype and run its validate(); this registry only teaches the AI chat WHAT a
# custom doctype is and HOW it links, so it uses the validated document path
# instead of, say, scribbling on a parent's fields. Relationships are read
# automatically from the Document class's LINK_FIELDS — not declared here.
CHAT_DOCTYPES: dict = {}


def register_chat_doctype(slug: str, *, description: str, fields: list | None = None,
                          page: str | None = "self") -> None:
    """Give the AI chat a description (and optional key-field hints) for a doctype
    already registered via `register_doctype`, so build_system_prompt can tell the
    model what it is and how it links (LINK_FIELDS are surfaced automatically).
    No new tools — the document tools already cover every registered doctype.

    `page` tells the chat how a record of this doctype is *opened*, so it emits
    correct view links (and the frontend can auto-register redirects):
      "self" (default) — its own page at `/app/{slug}/{name}`;
      "<link_field>"   — no page of its own; it's shown inside the parent that
                         `link_field` (a LINK_FIELD) references, so it opens at
                         `/app/{parent-slug}/{value of link_field}` (e.g. a
                         contact opens via its lead: page="lead_id");
      None             — no page and no view link at all.
    """
    CHAT_DOCTYPES[slug] = {"description": description, "fields": fields or [], "page": page}


def chat_doctype_page_info(slug: str) -> dict | None:
    """Resolve a chat-doctype's `page` config into a link rule, for the system
    prompt and the /api/chat-doctypes read. Returns one of:
      {"kind": "self", "slug": slug}
      {"kind": "via", "link_field": f, "parent_slug": p}   # opens via a parent
      {"kind": "none"}
    or None if the slug isn't a registered chat-doctype."""
    meta = CHAT_DOCTYPES.get(slug)
    if not meta:
        return None
    page = meta.get("page", "self")
    if page == "self":
        return {"kind": "self", "slug": slug}
    if page is None:
        return {"kind": "none"}
    # page names a LINK_FIELD -> resolve the parent doctype -> its slug.
    doctype = SLUG_TO_DOCTYPE.get(slug)
    cls = DOCUMENT_CLASSES.get(doctype) if doctype else None
    links = getattr(cls, "LINK_FIELDS", None) or {}
    parent_slug = DOCTYPE_TO_SLUG.get(links.get(page)) if links.get(page) else None
    if not parent_slug:
        return {"kind": "self", "slug": slug}  # misconfigured -> safe fallback
    return {"kind": "via", "link_field": page, "parent_slug": parent_slug}


def get_document_class(doctype_slug: str):
    """Get document class from URL slug."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        return None, None
    return doctype, DOCUMENT_CLASSES[doctype]


def create_document(doctype_slug: str, data: dict) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls(data)
    doc.save()
    return doc.as_dict()


def load_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    return doc.as_dict()


def update_document(doctype_slug: str, name: str, data: dict) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    # Update parent fields
    for key, value in data.items():
        if key not in ("name", "docstatus", "creation") and key not in doc.CHILD_TABLES:
            doc._data[key] = value
    # Update child tables if provided
    for table_name in doc.CHILD_TABLES:
        if table_name in data:
            doc._children[table_name] = []
            for row in data[table_name]:
                doc.append(table_name, _dict(row))
    doc.save()
    return doc.as_dict()


def submit_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    doc.submit()
    return doc.as_dict()


def cancel_document(doctype_slug: str, name: str) -> dict:
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    doc.cancel()
    return doc.as_dict()


def discard_document(doctype_slug: str, name: str) -> dict:
    """Void an unwanted draft (soft delete — keeps the row, hides it from lists)."""
    doctype, cls = get_document_class(doctype_slug)
    if not cls:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    doc = cls.load(name)
    doc.discard()
    return doc.as_dict()


def register_converter(source_doctype: str, target_doctype: str, fn) -> None:
    """Register (or override) the converter for a (source, target) pair.

    Extension point for customer deployments that need different conversion
    logic (see docs/core-extension-architecture.md). To merely have the
    converted document use an overridden class, you don't need this — register
    the class with `register_doctype` and `convert_document` upgrades the
    produced instance automatically.
    """
    CONVERTERS[(source_doctype, target_doctype)] = fn


def convert_document(doctype_slug: str, name: str, target_doctype: str) -> dict:
    source_doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not source_doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    converter = CONVERTERS.get((source_doctype, target_doctype))
    if not converter:
        raise ValueError(f"Cannot convert {source_doctype} to {target_doctype}")

    new_doc = converter(name)
    # Honor a registered class override for the produced doctype. The core
    # converter builds a base-class instance; if a plugin registered a subclass
    # for the target, upgrade the instance in place so save()/on_submit() use
    # the override. Safe because overrides are subclasses (same layout, no
    # __slots__); guarded so a non-subclass registration is ignored.
    override = DOCUMENT_CLASSES.get(target_doctype)
    if override is not None and type(new_doc) is not override and issubclass(override, type(new_doc)):
        new_doc.__class__ = override

    new_doc.save()
    return new_doc.as_dict()


DATE_FIELDS = {
    "Quotation": "transaction_date",
    "Sales Order": "transaction_date",
    "Purchase Order": "transaction_date",
    "Sales Invoice": "posting_date",
    "Purchase Invoice": "posting_date",
    "Payment Entry": "posting_date",
    "Journal Entry": "posting_date",
    "Stock Entry": "posting_date",
    "Delivery Note": "posting_date",
    "Purchase Receipt": "posting_date",
    "POS Invoice": "posting_date",
    "Bank Transaction": "posting_date",
}


def _exclude_discarded(db, doctype: str, db_filters: dict, include_discarded: bool) -> None:
    """Hide voided drafts (discarded=1) unless explicitly requested. Guarded on
    the column existing so non-submittable doctypes are unaffected."""
    if include_discarded:
        return
    if "discarded" in db._get_table_columns(doctype) and "discarded" not in db_filters:
        db_filters["discarded"] = 0


def _order_clause(order_by: str = None, order: str = "desc") -> str:
    """ORDER BY clause for list queries. Defaults to newest-created first.
    `order_by`, when given, must already be a validated column name (the
    documents router checks it against document_columns) — it is quoted here."""
    if not order_by:
        return "creation DESC"
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    return f'"{order_by}" {direction}'


def list_documents(doctype_slug: str, filters: dict = None, limit: int = 50, offset: int = 0,
                   include_discarded: bool = False, order_by: str = None, order: str = "desc") -> list:
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    db = get_db()
    db_filters = {}
    from_date = None
    to_date = None
    if filters:
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key == "from_date":
                from_date = value
            elif key == "to_date":
                to_date = value
            else:
                db_filters[key] = value

    _exclude_discarded(db, doctype, db_filters, include_discarded)

    # Date range filtering via the doctype's primary date field
    date_field = DATE_FIELDS.get(doctype)
    if date_field:
        if from_date:
            db_filters[date_field] = (">=", from_date)
        if to_date:
            # If we already set from_date, we need a second condition on the same field
            if from_date:
                # Use raw SQL fallback below
                pass
            else:
                db_filters[date_field] = ("<=", to_date)

    # If both from and to are set, the dict can only hold one constraint per key
    # Fall through to a raw SQL query for that case
    if date_field and from_date and to_date:
        return _list_with_date_range(
            db, doctype, doctype_slug, db_filters, date_field, from_date, to_date, limit, offset
        )

    order_clause = _order_clause(order_by, order)

    # get_all doesn't support offset, so use raw SQL when needed
    if offset:
        return _list_with_offset(db, doctype, doctype_slug, db_filters, limit, offset, order_clause)

    rows = db.get_all(
        doctype,
        filters=db_filters if db_filters else None,
        fields=["*"],
        order_by=order_clause,
        limit=limit,
    )

    return _attach_children(db, doctype_slug, rows)


def count_documents(doctype_slug: str, filters: dict = None, include_discarded: bool = False) -> int:
    """Count documents matching the filters (ignores limit/offset)."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")

    db = get_db()
    db_filters = {}
    from_date = None
    to_date = None
    if filters:
        for key, value in filters.items():
            if value is None or value == "":
                continue
            if key == "from_date":
                from_date = value
            elif key == "to_date":
                to_date = value
            else:
                db_filters[key] = value

    _exclude_discarded(db, doctype, db_filters, include_discarded)

    date_field = DATE_FIELDS.get(doctype)
    where_parts = []
    params = []
    if date_field and from_date:
        where_parts.append(f'"{date_field}" >= ?')
        params.append(from_date)
    if date_field and to_date:
        where_parts.append(f'"{date_field}" <= ?')
        params.append(to_date)
    for k, v in db_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)

    query = f'SELECT COUNT(*) as c FROM "{doctype}"'
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    rows = db.sql(query, params)
    return int(rows[0]["c"]) if rows else 0


def _filter_where(db, doctype: str, filters: dict, include_discarded: bool):
    """Build (where_parts, params) for a doctype from a list-style `filters` dict
    (equality filters + from_date/to_date on the doctype's date field + discard
    exclusion). Same semantics as list_documents/count_documents so prev/next
    matches exactly what the list shows."""
    db_filters, from_date, to_date = {}, None, None
    for key, value in (filters or {}).items():
        if value is None or value == "":
            continue
        if key == "from_date":
            from_date = value
        elif key == "to_date":
            to_date = value
        else:
            db_filters[key] = value
    _exclude_discarded(db, doctype, db_filters, include_discarded)

    date_field = DATE_FIELDS.get(doctype)
    where_parts, params = [], []
    if date_field and from_date:
        where_parts.append(f'"{date_field}" >= ?'); params.append(from_date)
    if date_field and to_date:
        where_parts.append(f'"{date_field}" <= ?'); params.append(to_date)
    for k, v in db_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?'); params.append(val)
        else:
            where_parts.append(f'"{k}" = ?'); params.append(v)
    return where_parts, params


def adjacent_documents(doctype_slug: str, name: str, filters: dict = None,
                       include_discarded: bool = False, order_by: str = None,
                       order: str = "desc") -> dict:
    """The records immediately before/after `name` in the same order+filters the
    list uses — {"prev": name|None, "next": name|None}. `prev` is one step *up*
    the list (toward the top), `next` one step *down*. Keyset queries (indexed,
    not a full scan). Order defaults to creation DESC (the list default), with
    `name` as a stable tie-break so rows sharing an order value still step 1:1."""
    doctype = SLUG_TO_DOCTYPE.get(doctype_slug)
    if not doctype:
        raise ValueError(f"Unknown document type: {doctype_slug}")
    db = get_db()
    cols = db._get_table_columns(doctype)
    oc = order_by if (order_by and order_by in cols) else "creation"
    desc = str(order).lower() != "asc"

    cur = db.sql(f'SELECT "{oc}" AS oc FROM "{doctype}" WHERE name = ?', [name])
    if not cur:
        return {"prev": None, "next": None}
    cur_oc = cur[0]["oc"]

    base_where, base_params = _filter_where(db, doctype, filters or {}, include_discarded)

    def neighbor(direction):
        # DESC list: next = tuple < current, prev = tuple > current (ASC flips).
        going_less = (direction == "next") == desc
        cmp = "<" if going_less else ">"
        dir_sql = "DESC" if going_less else "ASC"
        keyset = f'(("{oc}" {cmp} ?) OR ("{oc}" = ? AND name {cmp} ?))'
        query = (f'SELECT name FROM "{doctype}" WHERE '
                 + " AND ".join(base_where + [keyset])
                 + f' ORDER BY "{oc}" {dir_sql}, name {dir_sql} LIMIT 1')
        rows = db.sql(query, base_params + [cur_oc, cur_oc, name])
        return rows[0]["name"] if rows else None

    return {"prev": neighbor("prev"), "next": neighbor("next")}


def _list_with_offset(db, doctype, doctype_slug, db_filters, limit, offset, order_clause="creation DESC"):
    where_parts = []
    params = []
    for k, v in db_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)
    query = f'SELECT * FROM "{doctype}"'
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    query += f" ORDER BY {order_clause}"
    if limit:
        query += f" LIMIT {int(limit)}"
    if offset:
        query += f" OFFSET {int(offset)}"
    rows = db.sql(query, params)
    return _attach_children(db, doctype_slug, rows)


def _attach_children(db, doctype_slug: str, rows: list) -> list:
    _, cls = get_document_class(doctype_slug)
    result = []
    for row in rows:
        doc_dict = dict(row)
        if cls and cls.CHILD_TABLES:
            for table_name, (child_doctype, _) in cls.CHILD_TABLES.items():
                children = db.get_all(
                    child_doctype,
                    filters={"parent": row["name"]},
                    fields=["*"],
                    order_by="idx",
                )
                doc_dict[table_name] = [dict(c) for c in children]
        result.append(doc_dict)
    return result


def _list_with_date_range(db, doctype, doctype_slug, extra_filters, date_field, from_date, to_date, limit, offset=0):
    """List documents when both from_date and to_date are set."""
    where_parts = [f'"{date_field}" >= ?', f'"{date_field}" <= ?']
    params = [from_date, to_date]
    for k, v in extra_filters.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            op, val = v
            where_parts.append(f'"{k}" {op} ?')
            params.append(val)
        else:
            where_parts.append(f'"{k}" = ?')
            params.append(v)
    query = (
        f'SELECT * FROM "{doctype}" WHERE ' + " AND ".join(where_parts)
        + " ORDER BY creation DESC"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    if offset:
        query += f" OFFSET {int(offset)}"
    rows = db.sql(query, params)
    return _attach_children(db, doctype_slug, rows)
