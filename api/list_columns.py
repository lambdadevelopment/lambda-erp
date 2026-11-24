"""Personal list columns of the document lists, readable and changeable from the chat.

The web app keeps each user's columns of a document list as the preference
`columns.<doctype slug>` (comma-separated, in display order) and the sort
order as `sort.<doctype slug>` (`column:asc|desc`). The column menu of a list
writes them; these actions let the assistant do the same for the signed-in
user, and only for them. Since 1.4.29.

Columns a list can show: `name`, the document's own columns the user may see,
the readable name of a linked record (`<link field>__title`, see
services.linked_title_fields) and, where the document type prints, the PDF link
`__pdf`. Unset, a list shows the app's default columns.
"""
from pydantic import BaseModel, ConfigDict, Field

from api import services
from api.menu_layout import _actor
from lambda_erp import permissions
from lambda_erp.database import get_db

PDF_COLUMN = "__pdf"
# Bookkeeping columns no list shows.
_NOT_SHOWN = {"docstatus", "discarded", "idx", "content_hash", "access_stamp"}
MAX_COLUMNS = 20


class GetColumns(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doctype: str = Field(min_length=1, max_length=100, description="Document type slug, e.g. sales-invoice")


class UpdateColumns(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doctype: str = Field(min_length=1, max_length=100, description="Document type slug, e.g. sales-invoice")
    columns: list[str] | None = Field(
        default=None, max_length=MAX_COLUMNS,
        description="All columns of the list in display order (replaces the current set). Use the names "
                    "from get_list_columns; `name` is always shown first if left out.")
    add: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS,
                           description="Columns to append to the current set.")
    remove: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS,
                              description="Columns to take out of the current set.")
    sort_by: str | None = Field(default=None, max_length=100,
                                description="Column to sort the list by (a stored column, not a looked-up name or the PDF link).")
    sort_order: str | None = Field(default=None, pattern="^(asc|desc)$")
    reset: bool = Field(default=False, description="Back to the app's default columns and sort order.")


def _doctype(slug):
    doctype = services.SLUG_TO_DOCTYPE.get(slug)
    if not doctype:
        raise ValueError(f"Unknown document type {slug!r}; use a slug such as sales-invoice.")
    permissions.require(doctype, "read")
    return doctype


def _catalog(slug, doctype):
    """{column: what it is} for the columns this user's list of `slug` can show."""
    db = get_db()
    hidden = permissions.hidden_fields(doctype)
    columns = sorted(db._get_table_columns(doctype) - hidden - _NOT_SHOWN)
    catalog = {"name": "the document's id"}
    catalog.update({column: "a column of the document" for column in columns if column != "name"})
    cls = services.DOCUMENT_CLASSES.get(doctype)
    for field in sorted(getattr(cls, "LINK_FIELDS", {}) or {}):
        title = f"{field}{services.TITLE_SUFFIX}"
        if field in catalog and services.linked_title_fields(doctype, [title]):
            catalog[title] = f"the name of the linked {cls.LINK_FIELDS[field]} (looked up, not sortable)"
    try:
        from api.pdf_profiles import pdf_metadata
        if (pdf_metadata(doctype) or {}).get("supported"):
            catalog[PDF_COLUMN] = "a link that opens the document's PDF (not sortable)"
    except Exception:  # noqa: BLE001 - no PDF column rather than no columns
        pass
    return catalog


def _read(user_name, key):
    rows = get_db().sql('SELECT value FROM "User Preference" WHERE user_name = ? AND key = ?', [user_name, key])
    return rows[0]["value"] if rows else ""


def _write(user_name, key, value):
    db = get_db()
    with db.atomic():
        if db.sql('SELECT 1 FROM "User Preference" WHERE user_name = ? AND key = ?', [user_name, key]):
            if value:
                db.sql('UPDATE "User Preference" SET value = ? WHERE user_name = ? AND key = ?', [value, user_name, key])
            else:
                db.sql('DELETE FROM "User Preference" WHERE user_name = ? AND key = ?', [user_name, key])
        elif value:
            db.sql('INSERT INTO "User Preference" (user_name, key, value) VALUES (?, ?, ?)', [user_name, key, value])


def _state(user_name, slug, catalog):
    saved = [column.strip() for column in _read(user_name, f"columns.{slug}").split(",") if column.strip()]
    sort = _read(user_name, f"sort.{slug}")
    by, _, order = sort.partition(":")
    return {
        "doctype": slug,
        "columns": saved or None,
        "columns_note": None if saved else "Not set: the list shows the app's default columns.",
        "sort_by": by or None,
        "sort_order": order or None,
        "available": catalog,
    }


def get_list_columns(args, user):
    data = GetColumns.model_validate(args)
    user_name, doctype = _actor(user), _doctype(data.doctype)
    return _state(user_name, data.doctype, _catalog(data.doctype, doctype))


def update_list_columns(args, user):
    data = UpdateColumns.model_validate(args)
    user_name, doctype = _actor(user), _doctype(data.doctype)
    catalog = _catalog(data.doctype, doctype)
    slug = data.doctype

    def check(names):
        unknown = [name for name in names if name not in catalog]
        if unknown:
            raise ValueError(f"Unknown column(s) for {slug}: {', '.join(unknown)}. "
                             "Call get_list_columns for the columns this list can show.")
        return names

    if data.reset:
        _write(user_name, f"columns.{slug}", "")
        _write(user_name, f"sort.{slug}", "")
    current = [c.strip() for c in _read(user_name, f"columns.{slug}").split(",") if c.strip()]
    if data.columns is not None:
        columns = check(list(dict.fromkeys(data.columns)))
    elif data.add or data.remove:
        if not current:
            raise ValueError(f"The {slug} list shows its default columns, which are set by the app. "
                             "Send the whole set with columns=[...] (see get_list_columns) instead of add/remove.")
        columns = [c for c in current if c not in set(check(data.remove))] + \
                  [c for c in check(data.add) if c not in current]
    else:
        columns = None
    if columns is not None:
        if not columns:
            raise ValueError("A list needs at least one column.")
        if "name" not in columns:
            columns = ["name", *columns]
        _write(user_name, f"columns.{slug}", ",".join(columns[:MAX_COLUMNS]))
    if data.sort_by is not None or data.sort_order is not None:
        by = data.sort_by or (_read(user_name, f"sort.{slug}").partition(":")[0] or None)
        if not by:
            raise ValueError("sort_order needs sort_by.")
        if by not in catalog or by == PDF_COLUMN or by.endswith(services.TITLE_SUFFIX):
            raise ValueError(f"{by!r} cannot be sorted by; use a stored column of the document.")
        _write(user_name, f"sort.{slug}", f"{by}:{data.sort_order or 'asc'}")
    result = _state(user_name, slug, catalog)
    result["note"] = "Saved for the signed-in user only. The list shows it on its next load."
    return result


def initialize_list_columns():
    services.register_action(
        "get_list_columns", get_list_columns, minimum_role="viewer", pass_user=True,
        parameters=GetColumns.model_json_schema(),
        description=(
            "Show the current user's own columns and sort order of a document list (e.g. doctype "
            "'sales-invoice') and the columns that list can show: the document's columns, the names of "
            "linked records (customer__title, item_code__title) and __pdf, a link to each document's "
            "PDF. Call this before update_list_columns."))
    services.register_action(
        "update_list_columns", update_list_columns, minimum_role="viewer", pass_user=True,
        parameters=UpdateColumns.model_json_schema(),
        description=(
            "Change which columns the current user's own list of a document type shows, their order, "
            "and the sort order (no one else's). columns replaces the whole set in display order; "
            "add / remove change the current set; reset=true restores the app's defaults. Use column "
            "names from get_list_columns: e.g. customer_name for the customer's name stored on a sales "
            "invoice, item_code__title for an item's name, __pdf for a PDF link. Only stored columns "
            "can be sorted by. It changes the display only, never data or permissions."))
