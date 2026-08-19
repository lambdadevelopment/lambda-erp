"""Master data CRUD: Customer, Supplier, Item, Warehouse, Account, Company."""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from lambda_erp.database import get_db
from lambda_erp.utils import _dict
# The per-type registries live in api.services so `register_master` can extend
# them; imported here (not moved) so existing `from api.routers.masters import
# MASTER_IDENTITY_ALIAS`-style imports keep working.
from api.services import (
    MASTER_TABLES, MASTER_NAME_PREFIXES, MASTER_IDENTITY_ALIAS,
    _search_clause, _where_from_filters, master_search_columns, count_query_cached,
)
from api.auth import require_role, require_non_public_manager

router = APIRouter(prefix="/masters", tags=["masters"])

_viewer = Depends(require_role("viewer"))
_manager = Depends(require_non_public_manager)
_admin = Depends(require_role("admin"))


def _echo_identity_alias(master_type: str, row):
    """Mirror the master's `name` back under its intuitive alias (item_code)."""
    alias = MASTER_IDENTITY_ALIAS.get(master_type)
    if alias and isinstance(row, dict) and row.get("name") is not None:
        row[alias] = row["name"]
    return row

DELETE_REFERENCE_CHECKS = {
    "company": [
        ('SELECT 1 FROM "Account" WHERE company = ? LIMIT 1', "account"),
        ('SELECT 1 FROM "Cost Center" WHERE company = ? LIMIT 1', "cost center"),
        ('SELECT 1 FROM "Warehouse" WHERE company = ? LIMIT 1', "warehouse"),
        ('SELECT 1 FROM "Fiscal Year" WHERE company = ? LIMIT 1', "fiscal year"),
        ('SELECT 1 FROM "Tax Template" WHERE company = ? LIMIT 1', "tax template"),
        ('SELECT 1 FROM "GL Entry" WHERE company = ? LIMIT 1', "GL entry"),
        ('SELECT 1 FROM "Quotation" WHERE company = ? LIMIT 1', "quotation"),
        ('SELECT 1 FROM "Sales Order" WHERE company = ? LIMIT 1', "sales order"),
        ('SELECT 1 FROM "Purchase Order" WHERE company = ? LIMIT 1', "purchase order"),
        ('SELECT 1 FROM "Sales Invoice" WHERE company = ? LIMIT 1', "sales invoice"),
        ('SELECT 1 FROM "Purchase Invoice" WHERE company = ? LIMIT 1', "purchase invoice"),
        ('SELECT 1 FROM "Payment Entry" WHERE company = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Journal Entry" WHERE company = ? LIMIT 1', "journal entry"),
        ('SELECT 1 FROM "Stock Entry" WHERE company = ? LIMIT 1', "stock entry"),
        ('SELECT 1 FROM "Delivery Note" WHERE company = ? LIMIT 1', "delivery note"),
        ('SELECT 1 FROM "Purchase Receipt" WHERE company = ? LIMIT 1', "purchase receipt"),
        ('SELECT 1 FROM "POS Invoice" WHERE company = ? LIMIT 1', "POS invoice"),
        ('SELECT 1 FROM "Budget" WHERE company = ? LIMIT 1', "budget"),
        ('SELECT 1 FROM "Subscription" WHERE company = ? LIMIT 1', "subscription"),
        ('SELECT 1 FROM "Bank Transaction" WHERE company = ? LIMIT 1', "bank transaction"),
    ],
    "customer": [
        ('SELECT 1 FROM "Quotation" WHERE customer = ? LIMIT 1', "quotation"),
        ('SELECT 1 FROM "Sales Order" WHERE customer = ? LIMIT 1', "sales order"),
        ('SELECT 1 FROM "Sales Invoice" WHERE customer = ? LIMIT 1', "sales invoice"),
        ('SELECT 1 FROM "Delivery Note" WHERE customer = ? LIMIT 1', "delivery note"),
        ('SELECT 1 FROM "POS Invoice" WHERE customer = ? LIMIT 1', "POS invoice"),
        ('SELECT 1 FROM "Payment Entry" WHERE party_type = \'Customer\' AND party = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Subscription" WHERE party_type = \'Customer\' AND party = ? LIMIT 1', "subscription"),
    ],
    "supplier": [
        ('SELECT 1 FROM "Purchase Order" WHERE supplier = ? LIMIT 1', "purchase order"),
        ('SELECT 1 FROM "Purchase Invoice" WHERE supplier = ? LIMIT 1', "purchase invoice"),
        ('SELECT 1 FROM "Purchase Receipt" WHERE supplier = ? LIMIT 1', "purchase receipt"),
        ('SELECT 1 FROM "Payment Entry" WHERE party_type = \'Supplier\' AND party = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Subscription" WHERE party_type = \'Supplier\' AND party = ? LIMIT 1', "subscription"),
    ],
    "item": [
        ('SELECT 1 FROM "Quotation Item" WHERE item_code = ? LIMIT 1', "quotation item"),
        ('SELECT 1 FROM "Sales Order Item" WHERE item_code = ? LIMIT 1', "sales order item"),
        ('SELECT 1 FROM "Purchase Order Item" WHERE item_code = ? LIMIT 1', "purchase order item"),
        ('SELECT 1 FROM "Delivery Note Item" WHERE item_code = ? LIMIT 1', "delivery note item"),
        ('SELECT 1 FROM "Purchase Receipt Item" WHERE item_code = ? LIMIT 1', "purchase receipt item"),
        ('SELECT 1 FROM "Sales Invoice Item" WHERE item_code = ? LIMIT 1', "sales invoice item"),
        ('SELECT 1 FROM "Purchase Invoice Item" WHERE item_code = ? LIMIT 1', "purchase invoice item"),
        ('SELECT 1 FROM "POS Invoice Item" WHERE item_code = ? LIMIT 1', "POS invoice item"),
        ('SELECT 1 FROM "Stock Entry Detail" WHERE item_code = ? LIMIT 1', "stock entry item"),
        ('SELECT 1 FROM "Stock Ledger Entry" WHERE item_code = ? LIMIT 1', "stock ledger entry"),
        ('SELECT 1 FROM "Bin" WHERE item_code = ? LIMIT 1', "bin"),
        ('SELECT 1 FROM "Pricing Rule" WHERE item_code = ? LIMIT 1', "pricing rule"),
        ('SELECT 1 FROM "Subscription Plan" WHERE item_code = ? LIMIT 1', "subscription plan"),
        ('SELECT 1 FROM "Asset" WHERE item_code = ? LIMIT 1', "asset"),
        ('SELECT 1 FROM "Reservation" WHERE item_code = ? LIMIT 1', "reservation"),
    ],
    "warehouse": [
        ('SELECT 1 FROM "Item" WHERE default_warehouse = ? LIMIT 1', "item"),
        ('SELECT 1 FROM "Warehouse" WHERE parent_warehouse = ? LIMIT 1', "child warehouse"),
        ('SELECT 1 FROM "Quotation Item" WHERE warehouse = ? LIMIT 1', "quotation item"),
        ('SELECT 1 FROM "Sales Order Item" WHERE warehouse = ? LIMIT 1', "sales order item"),
        ('SELECT 1 FROM "Purchase Order Item" WHERE warehouse = ? LIMIT 1', "purchase order item"),
        ('SELECT 1 FROM "Delivery Note Item" WHERE warehouse = ? LIMIT 1', "delivery note item"),
        ('SELECT 1 FROM "Purchase Receipt Item" WHERE warehouse = ? LIMIT 1', "purchase receipt item"),
        ('SELECT 1 FROM "Sales Invoice Item" WHERE warehouse = ? LIMIT 1', "sales invoice item"),
        ('SELECT 1 FROM "Purchase Invoice Item" WHERE warehouse = ? LIMIT 1', "purchase invoice item"),
        ('SELECT 1 FROM "POS Invoice Item" WHERE warehouse = ? LIMIT 1', "POS invoice item"),
        ('SELECT 1 FROM "Stock Entry" WHERE from_warehouse = ? OR to_warehouse = ? LIMIT 1', "stock entry"),
        ('SELECT 1 FROM "Stock Entry Detail" WHERE s_warehouse = ? OR t_warehouse = ? LIMIT 1', "stock entry item"),
        ('SELECT 1 FROM "Stock Ledger Entry" WHERE warehouse = ? LIMIT 1', "stock ledger entry"),
        ('SELECT 1 FROM "Bin" WHERE warehouse = ? LIMIT 1', "bin"),
        ('SELECT 1 FROM "Asset" WHERE warehouse = ? LIMIT 1', "asset"),
        ('SELECT 1 FROM "Reservation" WHERE warehouse = ? LIMIT 1', "reservation"),
    ],
    "account": [
        ('SELECT 1 FROM "Account" WHERE parent_account = ? LIMIT 1', "child account"),
        ('SELECT 1 FROM "Cost Center" WHERE parent_cost_center = ? LIMIT 1', "cost center"),  # defensive, should not match normally
        ('SELECT 1 FROM "GL Entry" WHERE account = ? LIMIT 1', "GL entry"),
        ('SELECT 1 FROM "Journal Entry Account" WHERE account = ? LIMIT 1', "journal entry account"),
        ('SELECT 1 FROM "Payment Entry" WHERE paid_from = ? OR paid_to = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Sales Taxes and Charges" WHERE account_head = ? LIMIT 1', "tax row"),
        ('SELECT 1 FROM "Tax Template Detail" WHERE account_head = ? LIMIT 1', "tax template detail"),
        ('SELECT 1 FROM "Company" WHERE round_off_account = ? OR default_receivable_account = ? OR default_payable_account = ? OR default_income_account = ? OR default_expense_account = ? OR stock_received_but_not_billed = ? OR stock_adjustment_account = ? OR accumulated_depreciation_account = ? OR depreciation_expense_account = ? LIMIT 1', "company"),
        ('SELECT 1 FROM "Warehouse" WHERE account = ? LIMIT 1', "warehouse"),
        ('SELECT 1 FROM "Pricing Rule" WHERE discount_account = ? LIMIT 1', "pricing rule"),
        ('SELECT 1 FROM "Budget" WHERE account = ? LIMIT 1', "budget"),
        ('SELECT 1 FROM "Bank Transaction" WHERE bank_account = ? LIMIT 1', "bank transaction"),
    ],
    "cost-center": [
        ('SELECT 1 FROM "Cost Center" WHERE parent_cost_center = ? LIMIT 1', "child cost center"),
        ('SELECT 1 FROM "GL Entry" WHERE cost_center = ? LIMIT 1', "GL entry"),
        ('SELECT 1 FROM "Journal Entry Account" WHERE cost_center = ? LIMIT 1', "journal entry account"),
        ('SELECT 1 FROM "Sales Invoice Item" WHERE cost_center = ? LIMIT 1', "sales invoice item"),
        ('SELECT 1 FROM "Purchase Invoice Item" WHERE cost_center = ? LIMIT 1', "purchase invoice item"),
        ('SELECT 1 FROM "POS Invoice Item" WHERE cost_center = ? LIMIT 1', "POS invoice item"),
        ('SELECT 1 FROM "Company" WHERE default_cost_center = ? OR round_off_cost_center = ? LIMIT 1', "company"),
        ('SELECT 1 FROM "Budget" WHERE cost_center = ? LIMIT 1', "budget"),
    ],
}


def _get_table(master_type: str):
    entry = MASTER_TABLES.get(master_type)
    if not entry:
        return None, None
    return entry  # (doctype, name_field)


def _with_active_filter(db, doctype: str, filters: dict | None = None) -> dict | None:
    effective = dict(filters or {})
    if "disabled" in db._get_table_columns(doctype) and "disabled" not in effective:
        effective["disabled"] = 0
    return effective or None


def _find_reference(master_type: str, name: str) -> str | None:
    db = get_db()
    for query, label in DELETE_REFERENCE_CHECKS.get(master_type, []):
        params = [name] if query.count("?") == 1 else [name] * query.count("?")
        try:
            if db.sql(query, params):
                return label
        except Exception:
            continue
    return None


def _generate_master_name(db, doctype: str, prefix: str) -> str:
    # Take the highest numeric suffix among strict "PREFIX-<digits>" names.
    # Names with a non-numeric tail (e.g. a custom "ITEM-COST-TEST") are ignored
    # rather than parsed — string-sorting them to the top used to reset the
    # counter to 001 and collide with an existing record.
    rows = db.sql(
        f'SELECT name FROM "{doctype}" WHERE name LIKE ?',
        [f"{prefix}-%"],
    )
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    max_number = 0
    for row in rows:
        match = pattern.match(row["name"] or "")
        if match:
            max_number = max(max_number, int(match.group(1)))

    # Skip any number already taken by a non-standard name so we never collide.
    number = max_number + 1
    while db.exists(doctype, f"{prefix}-{number:03d}"):
        number += 1
    return f"{prefix}-{number:03d}"


def _normalize_master_data(data: dict) -> dict:
    normalized = _dict(data)
    for key, value in list(normalized.items()):
        # Flag columns (disabled, is_group, …) are INTEGER, not boolean. The chat
        # may send a JSON bool; coerce true/false -> 1/0 so e.g. re-enabling an
        # account with {"disabled": false} actually persists (on Postgres a bool
        # into an INTEGER column errors, which then gets narrated as "done"). Check
        # bool BEFORE int — in Python bool is a subclass of int.
        if isinstance(value, bool):
            normalized[key] = int(value)
        elif isinstance(value, str) and value.strip() == "":
            normalized[key] = None
    return normalized


def create_master_record(master_type: str, data: dict) -> dict:
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")

    db = get_db()
    doc = _normalize_master_data(data)

    # Let callers set the code under its intuitive alias (item_code -> name).
    # An explicit `name` still wins; the alias key is consumed either way so it
    # isn't reported as an ignored field.
    alias = MASTER_IDENTITY_ALIAS.get(master_type)
    if alias:
        alias_val = doc.pop(alias, None)
        if alias_val and not doc.get("name"):
            doc["name"] = alias_val

    if not doc.get("name"):
        prefix = MASTER_NAME_PREFIXES.get(master_type)
        if prefix:
            doc["name"] = _generate_master_name(db, doctype, prefix)
        elif master_type == "company" and doc.get("company_name"):
            # A company's id is conventionally its name (mirrors /setup/company).
            doc["name"] = doc["company_name"]
        else:
            raise HTTPException(status_code=422, detail="Name is required")

    if db.exists(doctype, doc["name"]):
        raise HTTPException(status_code=409, detail=f"{doctype} {doc['name']} already exists")

    # Party masters inherit the company's base currency when none is specified.
    # Otherwise the Customer/Supplier default_currency column default ('USD')
    # wins, so a non-USD company (e.g. CHF) gets USD customers that then force
    # every sales/purchase document to USD (and fail with no USD->base rate).
    if master_type in ("customer", "supplier") and not doc.get("default_currency"):
        companies = db.get_all("Company", fields=["name", "default_currency"], limit=1)
        if companies:
            doc["default_currency"] = companies[0].get("default_currency") or "USD"

    db.insert(doctype, doc)
    row = db.get_all(doctype, filters={"name": doc["name"]}, fields=["*"])[0]
    return _echo_identity_alias(master_type, row)


def update_master_record(master_type: str, name: str, data: dict) -> dict:
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")

    db = get_db()
    normalized = _normalize_master_data(data)

    # The alias (item_code) is the identity, not a mutable column. Allow it as a
    # no-op when it matches the record being updated, but reject a rename — the
    # code is a primary key referenced across every transaction.
    alias = MASTER_IDENTITY_ALIAS.get(master_type)
    if alias and alias in normalized:
        alias_val = normalized.pop(alias)
        if alias_val and alias_val != name:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot change a {master_type}'s {alias} ('{name}' -> '{alias_val}') — "
                    f"it is the record's identity. Create a new {master_type} with the desired code instead."
                ),
            )

    update_fields = {k: v for k, v in normalized.items() if k != "name"}
    if update_fields:
        db.set_value(doctype, name, update_fields)

    rows = db.get_all(doctype, filters={"name": name}, fields=["*"])
    if not rows:
        raise HTTPException(status_code=404, detail=f"{doctype} {name} not found")
    return _echo_identity_alias(master_type, rows[0])


# Query params list_masters consumes itself — anything else is treated as an
# ad-hoc equality field filter (validated against the master's real columns).
_MASTER_LIST_RESERVED = {
    "limit", "offset", "include_disabled", "search", "search_fields",
}


@router.get("/{master_type}/filter-values")
def master_filter_values(
    master_type: str,
    field: str,
    _user: dict = _viewer,
):
    """Distinct non-empty values of one column — populates a master-list filter
    dropdown. Capped; the field is validated against real columns."""
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")
    db = get_db()
    if field not in db._get_table_columns(doctype):
        raise HTTPException(status_code=400, detail=f"Unknown field: {field}")
    rows = db.sql(
        f'SELECT DISTINCT "{field}" AS v FROM "{doctype}" '
        f'WHERE "{field}" IS NOT NULL AND "{field}" <> \'\' '
        f'ORDER BY "{field}" LIMIT 200'
    )
    return {"values": [r["v"] for r in rows]}


@router.get("/{master_type}")
def list_masters(
    master_type: str,
    request: Request,
    limit: int = Query(default=50, le=1000),
    offset: int = Query(default=0, ge=0),
    include_disabled: bool = False,
    search: str | None = None,
    search_fields: str | None = None,
    _user: dict = _viewer,
):
    doctype, _ = _get_table(master_type)
    if not doctype:
        return {"detail": f"Unknown master type: {master_type}"}
    db = get_db()
    columns = db._get_table_columns(doctype)

    where_parts: list = []
    params: list = []

    # Active (non-disabled) filter, unless the caller opts in to disabled rows.
    active = None if include_disabled else _with_active_filter(db, doctype)
    if active:
        wp, ps = _where_from_filters(active)
        where_parts += wp
        params += ps

    # Ad-hoc equality field filters: any remaining query param naming a real
    # column (e.g. /masters/customer?customer_group=Commercial). Unknown field
    # -> 400 (a filter on a missing column is a client bug, never interpolated).
    for key, value in request.query_params.items():
        if key in _MASTER_LIST_RESERVED:
            continue
        if key not in columns:
            raise HTTPException(status_code=400, detail=f"Unknown filter field: {key}")
        where_parts.append(f'"{key}" = ?')
        params.append(value)

    # Free-text search — defaults to the master's text columns when the caller
    # doesn't name search_fields (so a plain ?search=... just works).
    if search:
        sf = [f.strip() for f in (search_fields or "").split(",")
              if f.strip() and f.strip() in columns]
        if not sf:
            sf = master_search_columns(db, doctype)
        clause, sp = _search_clause(db, doctype, master_type, search, sf)
        if clause:
            where_parts.append(clause)
            params += sp

    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    total = count_query_cached(f'SELECT COUNT(*) as c FROM "{doctype}"{where}', params)

    # Deterministic order by the `name` PK (already indexed) — required for
    # OFFSET pagination and the /adjacent prev/next contract.
    query = f'SELECT * FROM "{doctype}"{where} ORDER BY name LIMIT {int(limit)}'
    if offset:
        query += f" OFFSET {int(offset)}"
    rows = db.sql(query, params)

    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{master_type}/search")
def search_masters(master_type: str, q: str = "", _user: dict = _viewer):
    doctype, name_field = _get_table(master_type)
    if not doctype:
        return []
    db = get_db()
    active_prefix = 'disabled = 0 AND ' if "disabled" in db._get_table_columns(doctype) else ""
    if not q:
        return db.get_all(doctype, filters=_with_active_filter(db, doctype), fields=["name", name_field], limit=10)

    # Case-INSENSITIVE: on Postgres plain LIKE is case-sensitive, so "plus medica"
    # would miss "Plus Medica AG". LOWER(...) LIKE LOWER(...) is portable (SQLite
    # + Postgres) and matches how the document search (_search_clause) works.
    rows = db.sql(
        f'SELECT name, "{name_field}" FROM "{doctype}" '
        f'WHERE {active_prefix}(LOWER(name) LIKE LOWER(?) OR LOWER("{name_field}") LIKE LOWER(?)) LIMIT 10',
        [f"%{q}%", f"%{q}%"],
    )
    return rows


@router.get("/{master_type}/{name}/adjacent")
def adjacent_master(master_type: str, name: str, include_disabled: bool = True,
                    _user: dict = _viewer):
    """The records immediately before/after `name` in the list's order (name
    ASC) — {"prev": name|None, "next": name|None}. Keyset queries on the
    primary key. include_disabled defaults True to step 1:1 with the master
    list, which shows disabled records too."""
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")
    db = get_db()
    active = ("" if include_disabled or "disabled" not in db._get_table_columns(doctype)
              else "disabled = 0 AND ")

    def neighbor(cmp: str, direction: str):
        rows = db.sql(
            f'SELECT name FROM "{doctype}" WHERE {active}name {cmp} ? '
            f"ORDER BY name {direction} LIMIT 1", [name])
        return rows[0]["name"] if rows else None

    return {"prev": neighbor("<", "DESC"), "next": neighbor(">", "ASC")}


@router.get("/{master_type}/{name}")
def get_master(master_type: str, name: str, _user: dict = _viewer):
    doctype, _ = _get_table(master_type)
    if not doctype:
        return {"detail": f"Unknown master type: {master_type}"}
    db = get_db()
    rows = db.get_all(doctype, filters={"name": name}, fields=["*"])
    if not rows:
        return {"detail": f"{doctype} {name} not found"}
    return rows[0]


@router.post("/{master_type}")
def create_master(master_type: str, data: dict, _user: dict = _manager):
    return create_master_record(master_type, data)


@router.put("/{master_type}/{name}")
def update_master(master_type: str, name: str, data: dict, _user: dict = _manager):
    return update_master_record(master_type, name, data)


def delete_master_record(master_type: str, name: str) -> dict:
    """Delete a master record with reference protection — the ONLY sanctioned
    delete path (the tables have no FK constraints, so every caller — the REST
    route below AND the chat agent's delete_master tool — must go through here).

    Unreferenced record → hard delete. Referenced record → auto-disabled instead
    (when the doctype has a `disabled` column) with the blocking reference named,
    else a 409. Raising HTTPException keeps route behavior; the chat handler
    catches it and surfaces `detail` as the tool error.
    """
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=422, detail=f"Unknown master type: {master_type}")
    db = get_db()
    if not db.exists(doctype, name):
        raise HTTPException(status_code=404, detail=f"{doctype} {name} not found")

    reference = _find_reference(master_type, name)
    if reference:
        columns = db._get_table_columns(doctype)
        if "disabled" in columns:
            db.set_value(doctype, name, {"disabled": 1})
            return {"ok": True, "status": "disabled", "reason": f"Referenced by {reference}"}
        raise HTTPException(status_code=409, detail=f"Cannot delete {doctype} {name}: referenced by {reference}")

    db.delete(doctype, name=name)
    return {"ok": True, "status": "deleted"}


@router.delete("/{master_type}/{name}")
def delete_master(master_type: str, name: str, _user: dict = _admin):
    return delete_master_record(master_type, name)


@router.get("/account/tree")
def account_tree(company: str | None = None, include_disabled: bool = False,
                 _user: dict = _viewer):
    """Return Chart of Accounts as a nested tree.

    Disabled accounts are hidden by default (they already drop out of account
    pickers, and a disabled account is one the user retired). Pass
    ``include_disabled=true`` to show them. A disabled *group* is still shown if
    it has any enabled descendant, so an active account is never hidden behind a
    disabled parent. Each node carries a ``disabled`` bool for the UI.
    """
    db = get_db()
    filters = {}
    if company:
        filters["company"] = company
    accounts = db.get_all(
        "Account",
        filters=filters if filters else None,
        fields=["name", "account_name", "parent_account", "root_type",
                "report_type", "account_type", "is_group", "disabled"],
    )

    by_parent = {}
    for acc in accounts:
        parent = acc.get("parent_account") or "__root__"
        by_parent.setdefault(parent, []).append(acc)

    def _build(parent_name):
        children = by_parent.get(parent_name, [])
        result = []
        for acc in children:
            node = dict(acc)
            node["disabled"] = bool(acc.get("disabled"))
            node["children"] = _build(acc["name"])
            # Prune a disabled node only when nothing enabled survives beneath it,
            # so an enabled child is never dropped along with its disabled parent.
            if not include_disabled and node["disabled"] and not node["children"]:
                continue
            result.append(node)
        return result

    return _build("__root__") or _build(None)
