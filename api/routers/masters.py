"""Master data CRUD: Customer, Supplier, Item, Warehouse, Account, Company."""

from fastapi import APIRouter, Depends, HTTPException, Query
from lambda_erp.database import get_db
from lambda_erp.utils import _dict
from api.services import MASTER_TABLES
from api.auth import require_role, require_non_public_manager

router = APIRouter(prefix="/masters", tags=["masters"])

_viewer = Depends(require_role("viewer"))
_manager = Depends(require_non_public_manager)
_admin = Depends(require_role("admin"))

MASTER_NAME_PREFIXES = {
    "customer": "CUST",
    "supplier": "SUPP",
    "item": "ITEM",
    "warehouse": "WH",
}

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
        ('SELECT 1 FROM "Payment Entry" WHERE party_type = "Customer" AND party = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Subscription" WHERE party_type = "Customer" AND party = ? LIMIT 1', "subscription"),
    ],
    "supplier": [
        ('SELECT 1 FROM "Purchase Order" WHERE supplier = ? LIMIT 1', "purchase order"),
        ('SELECT 1 FROM "Purchase Invoice" WHERE supplier = ? LIMIT 1', "purchase invoice"),
        ('SELECT 1 FROM "Purchase Receipt" WHERE supplier = ? LIMIT 1', "purchase receipt"),
        ('SELECT 1 FROM "Payment Entry" WHERE party_type = "Supplier" AND party = ? LIMIT 1', "payment entry"),
        ('SELECT 1 FROM "Subscription" WHERE party_type = "Supplier" AND party = ? LIMIT 1', "subscription"),
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
    rows = db.sql(
        f'SELECT name FROM "{doctype}" WHERE name LIKE ? ORDER BY name DESC LIMIT 1',
        [f"{prefix}-%"],
    )
    if not rows:
        return f"{prefix}-001"

    last_name = rows[0]["name"] or ""
    try:
        next_number = int(last_name.split("-")[-1]) + 1
    except (ValueError, IndexError):
        next_number = 1
    return f"{prefix}-{next_number:03d}"


def _normalize_master_data(data: dict) -> dict:
    normalized = _dict(data)
    for key, value in list(normalized.items()):
        if isinstance(value, str) and value.strip() == "":
            normalized[key] = None
    return normalized


def create_master_record(master_type: str, data: dict) -> dict:
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")

    db = get_db()
    doc = _normalize_master_data(data)
    if not doc.get("name"):
        prefix = MASTER_NAME_PREFIXES.get(master_type)
        if not prefix:
            raise HTTPException(status_code=422, detail="Name is required")
        doc["name"] = _generate_master_name(db, doctype, prefix)

    if db.exists(doctype, doc["name"]):
        raise HTTPException(status_code=409, detail=f"{doctype} {doc['name']} already exists")

    db.insert(doctype, doc)
    return db.get_all(doctype, filters={"name": doc["name"]}, fields=["*"])[0]


def update_master_record(master_type: str, name: str, data: dict) -> dict:
    doctype, _ = _get_table(master_type)
    if not doctype:
        raise HTTPException(status_code=404, detail=f"Unknown master type: {master_type}")

    db = get_db()
    normalized = _normalize_master_data(data)
    update_fields = {k: v for k, v in normalized.items() if k != "name"}
    if update_fields:
        db.set_value(doctype, name, update_fields)

    rows = db.get_all(doctype, filters={"name": name}, fields=["*"])
    if not rows:
        raise HTTPException(status_code=404, detail=f"{doctype} {name} not found")
    return rows[0]


@router.get("/{master_type}")
def list_masters(
    master_type: str,
    limit: int = Query(default=50, le=1000),
    offset: int = Query(default=0, ge=0),
    include_disabled: bool = False,
    _user: dict = _viewer,
):
    doctype, _ = _get_table(master_type)
    if not doctype:
        return {"detail": f"Unknown master type: {master_type}"}
    db = get_db()
    filters = None if include_disabled else _with_active_filter(db, doctype)

    # Build WHERE clause and params
    where_parts = []
    params = []
    if filters:
        for k, v in filters.items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                op, val = v
                where_parts.append(f'"{k}" {op} ?')
                params.append(val)
            else:
                where_parts.append(f'"{k}" = ?')
                params.append(v)

    count_query = f'SELECT COUNT(*) as c FROM "{doctype}"'
    if where_parts:
        count_query += " WHERE " + " AND ".join(where_parts)
    total_rows = db.sql(count_query, params)
    total = int(total_rows[0]["c"]) if total_rows else 0

    query = f'SELECT * FROM "{doctype}"'
    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)
    query += f" LIMIT {int(limit)}"
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

    rows = db.sql(
        f'SELECT name, "{name_field}" FROM "{doctype}" '
        f'WHERE {active_prefix}(name LIKE ? OR "{name_field}" LIKE ?) LIMIT 10',
        [f"%{q}%", f"%{q}%"],
    )
    return rows


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


@router.delete("/{master_type}/{name}")
def delete_master(master_type: str, name: str, _user: dict = _admin):
    doctype, _ = _get_table(master_type)
    if not doctype:
        return {"detail": f"Unknown master type: {master_type}"}
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


@router.get("/account/tree")
def account_tree(company: str | None = None, _user: dict = _viewer):
    """Return Chart of Accounts as a nested tree."""
    db = get_db()
    filters = {}
    if company:
        filters["company"] = company
    accounts = db.get_all(
        "Account",
        filters=filters if filters else None,
        fields=["name", "account_name", "parent_account", "root_type",
                "report_type", "account_type", "is_group"],
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
            node["children"] = _build(acc["name"])
            result.append(node)
        return result

    return _build("__root__") or _build(None)
