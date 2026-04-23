"""Analytics and client-runtime reporting endpoints.

This module now exposes two layers:

1. The original preset analytics API used by the current /reports/analytics page.
2. A semantic dataset registry + bounded data-fetch endpoint for client-side
   programmable reports. The browser can request approved datasets, then run an
   arbitrary JS transform locally in a worker without direct database access.
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_role
from lambda_erp.database import get_db
from lambda_erp.utils import flt

router = APIRouter(prefix="/reports", tags=["analytics"])

_viewer = Depends(require_role("viewer"))


# ---------------------------------------------------------------------------
# Legacy preset analytics
# ---------------------------------------------------------------------------

_TIME_BUCKETS = {
    "month": "strftime('%Y-%m', {date_col})",
    "quarter": "strftime('%Y-Q', {date_col}) || ((CAST(strftime('%m', {date_col}) AS INTEGER) + 2) / 3)",
    "year": "strftime('%Y', {date_col})",
}


def _bucket_expr(group_by: str, date_col: str) -> str:
    tmpl = _TIME_BUCKETS.get(group_by)
    if tmpl:
        return tmpl.format(date_col=date_col)
    return group_by


def _date_filter(filters, date_col, params):
    clauses = []
    if filters.get("from_date"):
        clauses.append(f"{date_col} >= ?")
        params.append(filters["from_date"])
    if filters.get("to_date"):
        clauses.append(f"{date_col} <= ?")
        params.append(filters["to_date"])
    return clauses


def _company_filter(filters, table_alias, params):
    if filters.get("company"):
        params.append(filters["company"])
        return [f"{table_alias}.company = ?"]
    return []


def _sales_metric(db, group_by, filters, *, is_return):
    where = ["si.docstatus = 1", f"COALESCE(si.is_return, 0) = {1 if is_return else 0}"]
    params = []
    where += _date_filter(filters, "si.posting_date", params)
    where += _company_filter(filters, "si", params)

    if group_by == "item":
        sql = f"""
            SELECT sii.item_code AS bucket, SUM(sii.net_amount) AS value
            FROM "Sales Invoice Item" sii
            JOIN "Sales Invoice" si ON si.name = sii.parent
            WHERE {' AND '.join(where)}
            GROUP BY bucket ORDER BY value DESC LIMIT 30
        """
    else:
        bucket = _bucket_expr(group_by, "si.posting_date") if group_by in _TIME_BUCKETS else "si.customer"
        order = "bucket" if group_by in _TIME_BUCKETS else "value DESC"
        sql = f"""
            SELECT {bucket} AS bucket, SUM(si.grand_total) AS value
            FROM "Sales Invoice" si
            WHERE {' AND '.join(where)}
            GROUP BY bucket ORDER BY {order}
            {'LIMIT 30' if group_by == 'customer' else ''}
        """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


def _purchases(db, group_by, filters):
    where = ["pi.docstatus = 1", "COALESCE(pi.is_return, 0) = 0"]
    params = []
    where += _date_filter(filters, "pi.posting_date", params)
    where += _company_filter(filters, "pi", params)

    if group_by == "item":
        sql = f"""
            SELECT pii.item_code AS bucket, SUM(pii.net_amount) AS value
            FROM "Purchase Invoice Item" pii
            JOIN "Purchase Invoice" pi ON pi.name = pii.parent
            WHERE {' AND '.join(where)}
            GROUP BY bucket ORDER BY value DESC LIMIT 30
        """
    else:
        bucket = _bucket_expr(group_by, "pi.posting_date") if group_by in _TIME_BUCKETS else "pi.supplier"
        order = "bucket" if group_by in _TIME_BUCKETS else "value DESC"
        sql = f"""
            SELECT {bucket} AS bucket, SUM(pi.grand_total) AS value
            FROM "Purchase Invoice" pi
            WHERE {' AND '.join(where)}
            GROUP BY bucket ORDER BY {order}
            {'LIMIT 30' if group_by == 'supplier' else ''}
        """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


def _payments(db, group_by, filters, *, payment_type, party_type):
    where = ["pe.docstatus = 1", "pe.payment_type = ?", "pe.party_type = ?"]
    params = [payment_type, party_type]
    where += _date_filter(filters, "pe.posting_date", params)
    where += _company_filter(filters, "pe", params)

    bucket = _bucket_expr(group_by, "pe.posting_date") if group_by in _TIME_BUCKETS else "pe.party"
    order = "bucket" if group_by in _TIME_BUCKETS else "value DESC"
    sql = f"""
        SELECT {bucket} AS bucket, SUM(pe.paid_amount) AS value
        FROM "Payment Entry" pe
        WHERE {' AND '.join(where)}
        GROUP BY bucket ORDER BY {order}
        {'LIMIT 30' if group_by in ('customer', 'supplier') else ''}
    """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


def _outstanding_ar(db, _group_by, filters):
    where = ["docstatus = 1", "COALESCE(is_return, 0) = 0", "outstanding_amount > 0"]
    params = []
    if filters.get("company"):
        where.append("company = ?")
        params.append(filters["company"])
    sql = f"""
        SELECT customer AS bucket, SUM(outstanding_amount) AS value
        FROM "Sales Invoice"
        WHERE {' AND '.join(where)}
        GROUP BY customer ORDER BY value DESC LIMIT 30
    """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


def _outstanding_ap(db, _group_by, filters):
    where = ["docstatus = 1", "COALESCE(is_return, 0) = 0", "outstanding_amount > 0"]
    params = []
    if filters.get("company"):
        where.append("company = ?")
        params.append(filters["company"])
    sql = f"""
        SELECT supplier AS bucket, SUM(outstanding_amount) AS value
        FROM "Purchase Invoice"
        WHERE {' AND '.join(where)}
        GROUP BY supplier ORDER BY value DESC LIMIT 30
    """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


def _stock_value(db, group_by, filters):
    where = []
    params: list[Any] = []
    if filters.get("company"):
        where.append("w.company = ?")
        params.append(filters["company"])
    if group_by == "warehouse":
        sql = f"""
            SELECT b.warehouse AS bucket, SUM(b.stock_value) AS value
            FROM "Bin" b
            LEFT JOIN "Warehouse" w ON w.name = b.warehouse
            {'WHERE ' + ' AND '.join(where) if where else ''}
            GROUP BY b.warehouse ORDER BY value DESC
        """
    else:
        sql = f"""
            SELECT b.item_code AS bucket, SUM(b.stock_value) AS value
            FROM "Bin" b
            LEFT JOIN "Warehouse" w ON w.name = b.warehouse
            {'WHERE ' + ' AND '.join(where) if where else ''}
            GROUP BY b.item_code ORDER BY value DESC LIMIT 30
        """
    rows = db.sql(sql, params)
    return [{"label": r["bucket"] or "—", "value": flt(r["value"], 2)} for r in rows]


METRICS: dict[str, dict[str, Any]] = {
    "sales_revenue": {
        "label": "Sales Revenue",
        "group_by": ["month", "quarter", "year", "customer", "item"],
        "time_based": True,
        "handler": lambda db, g, f: _sales_metric(db, g, f, is_return=False),
    },
    "sales_returns": {
        "label": "Sales Returns",
        "group_by": ["month", "quarter", "year", "customer"],
        "time_based": True,
        "handler": lambda db, g, f: _sales_metric(db, g, f, is_return=True),
    },
    "purchases": {
        "label": "Purchases",
        "group_by": ["month", "quarter", "year", "supplier", "item"],
        "time_based": True,
        "handler": _purchases,
    },
    "payments_received": {
        "label": "Payments Received",
        "group_by": ["month", "quarter", "year", "customer"],
        "time_based": True,
        "handler": lambda db, g, f: _payments(db, g, f, payment_type="Receive", party_type="Customer"),
    },
    "payments_made": {
        "label": "Payments Made",
        "group_by": ["month", "quarter", "year", "supplier"],
        "time_based": True,
        "handler": lambda db, g, f: _payments(db, g, f, payment_type="Pay", party_type="Supplier"),
    },
    "outstanding_ar": {
        "label": "Outstanding AR",
        "group_by": ["customer"],
        "time_based": False,
        "handler": _outstanding_ar,
    },
    "outstanding_ap": {
        "label": "Outstanding AP",
        "group_by": ["supplier"],
        "time_based": False,
        "handler": _outstanding_ap,
    },
    "stock_value": {
        "label": "Stock Value",
        "group_by": ["warehouse", "item"],
        "time_based": False,
        "handler": _stock_value,
    },
}


def _chart_type(group_by: str) -> str:
    return "line" if group_by in _TIME_BUCKETS else "bar"


@router.get("/analytics/metrics")
def list_metrics(_user: dict = _viewer):
    return {
        "metrics": [
            {
                "metric": key,
                "label": m["label"],
                "group_by": m["group_by"],
                "time_based": m["time_based"],
            }
            for key, m in METRICS.items()
        ],
    }


@router.get("/analytics")
def analytics(
    metric: str = Query(..., description="Metric key (see /analytics/metrics)"),
    group_by: str = Query(..., description="Dimension to group by"),
    from_date: str | None = None,
    to_date: str | None = None,
    company: str | None = None,
    _user: dict = _viewer,
):
    meta = METRICS.get(metric)
    if not meta:
        raise HTTPException(400, f"Unknown metric '{metric}'. Try /analytics/metrics.")
    if group_by not in meta["group_by"]:
        raise HTTPException(
            400,
            f"Metric '{metric}' doesn't support group_by='{group_by}'. "
            f"Allowed: {', '.join(meta['group_by'])}",
        )

    filters = {"from_date": from_date, "to_date": to_date, "company": company}
    rows = meta["handler"](get_db(), group_by, filters)
    return {
        "metric": metric,
        "metric_label": meta["label"],
        "group_by": group_by,
        "chart_type": _chart_type(group_by),
        "time_based": meta["time_based"],
        "from_date": from_date,
        "to_date": to_date,
        "company": company,
        "rows": rows,
        "total": flt(sum(flt(r["value"]) for r in rows), 2),
    }


# ---------------------------------------------------------------------------
# Client-side report runtime
# ---------------------------------------------------------------------------


class RuntimeDataRequest(BaseModel):
    name: str | None = None
    dataset: str
    fields: list[str] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = None


class RuntimeFetchPayload(BaseModel):
    requests: list[RuntimeDataRequest]


class ReportDraftPayload(BaseModel):
    title: str
    description: str | None = None
    data_requests: list[RuntimeDataRequest]
    transform_js: str


class ReportDraftUpdatePayload(BaseModel):
    title: str | None = None
    description: str | None = None
    data_requests: list[RuntimeDataRequest] | None = None
    transform_js: str | None = None


SEMANTIC_DATASETS: dict[str, dict[str, Any]] = {
    "sales_invoices": {
        "label": "Sales Invoices",
        "description": "Submitted sales invoices, including returns and outstanding amounts.",
        "sql_from": 'FROM "Sales Invoice" si',
        "fields": {
            "name": "string",
            "docstatus": "number",
            "posting_date": "date",
            "company": "string",
            "customer": "string",
            "customer_name": "string",
            "net_total": "number",
            "grand_total": "number",
            "outstanding_amount": "number",
            "is_return": "boolean",
        },
        "field_sql": {
            "name": "si.name",
            "docstatus": "si.docstatus",
            "posting_date": "si.posting_date",
            "company": "si.company",
            "customer": "si.customer",
            "customer_name": "si.customer_name",
            "net_total": "si.net_total",
            "grand_total": "si.grand_total",
            "outstanding_amount": "si.outstanding_amount",
            "is_return": "COALESCE(si.is_return, 0)",
        },
        "default_where": ["si.docstatus = 1"],
        "filter_fields": {"company", "customer", "posting_date", "is_return", "name"},
        "default_limit": 500,
        "max_limit": 5000,
        "default_order_by": "si.posting_date DESC, si.name DESC",
    },
    "sales_invoice_lines": {
        "label": "Sales Invoice Lines",
        "description": "Submitted sales invoice lines joined to their invoice header.",
        "sql_from": 'FROM "Sales Invoice Item" sii JOIN "Sales Invoice" si ON si.name = sii.parent',
        "fields": {
            "invoice": "string",
            "docstatus": "number",
            "posting_date": "date",
            "company": "string",
            "customer": "string",
            "item_code": "string",
            "warehouse": "string",
            "qty": "number",
            "net_amount": "number",
            "income_account": "string",
            "cost_center": "string",
            "is_return": "boolean",
        },
        "field_sql": {
            "invoice": "si.name",
            "docstatus": "si.docstatus",
            "posting_date": "si.posting_date",
            "company": "si.company",
            "customer": "si.customer",
            "item_code": "sii.item_code",
            "warehouse": "sii.warehouse",
            "qty": "sii.qty",
            "net_amount": "sii.net_amount",
            "income_account": "sii.income_account",
            "cost_center": "sii.cost_center",
            "is_return": "COALESCE(si.is_return, 0)",
        },
        "default_where": ["si.docstatus = 1"],
        "filter_fields": {"company", "customer", "posting_date", "item_code", "warehouse", "is_return"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "si.posting_date DESC, si.name DESC, sii.idx ASC",
    },
    "purchase_invoices": {
        "label": "Purchase Invoices",
        "description": "Submitted purchase invoices, including returns and outstanding amounts.",
        "sql_from": 'FROM "Purchase Invoice" pi',
        "fields": {
            "name": "string",
            "docstatus": "number",
            "posting_date": "date",
            "company": "string",
            "supplier": "string",
            "supplier_name": "string",
            "net_total": "number",
            "grand_total": "number",
            "outstanding_amount": "number",
            "is_return": "boolean",
        },
        "field_sql": {
            "name": "pi.name",
            "docstatus": "pi.docstatus",
            "posting_date": "pi.posting_date",
            "company": "pi.company",
            "supplier": "pi.supplier",
            "supplier_name": "pi.supplier_name",
            "net_total": "pi.net_total",
            "grand_total": "pi.grand_total",
            "outstanding_amount": "pi.outstanding_amount",
            "is_return": "COALESCE(pi.is_return, 0)",
        },
        "default_where": ["pi.docstatus = 1"],
        "filter_fields": {"company", "supplier", "posting_date", "is_return", "name"},
        "default_limit": 500,
        "max_limit": 5000,
        "default_order_by": "pi.posting_date DESC, pi.name DESC",
    },
    "purchase_invoice_lines": {
        "label": "Purchase Invoice Lines",
        "description": "Submitted purchase invoice lines joined to their invoice header.",
        "sql_from": 'FROM "Purchase Invoice Item" pii JOIN "Purchase Invoice" pi ON pi.name = pii.parent',
        "fields": {
            "invoice": "string",
            "docstatus": "number",
            "posting_date": "date",
            "company": "string",
            "supplier": "string",
            "item_code": "string",
            "warehouse": "string",
            "qty": "number",
            "net_amount": "number",
            "expense_account": "string",
            "cost_center": "string",
            "is_return": "boolean",
        },
        "field_sql": {
            "invoice": "pi.name",
            "docstatus": "pi.docstatus",
            "posting_date": "pi.posting_date",
            "company": "pi.company",
            "supplier": "pi.supplier",
            "item_code": "pii.item_code",
            "warehouse": "pii.warehouse",
            "qty": "pii.qty",
            "net_amount": "pii.net_amount",
            "expense_account": "pii.expense_account",
            "cost_center": "pii.cost_center",
            "is_return": "COALESCE(pi.is_return, 0)",
        },
        "default_where": ["pi.docstatus = 1"],
        "filter_fields": {"company", "supplier", "posting_date", "item_code", "warehouse", "is_return"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "pi.posting_date DESC, pi.name DESC, pii.idx ASC",
    },
    "payments": {
        "label": "Payments",
        "description": "Submitted payment entries with party and bank/control account context.",
        "sql_from": 'FROM "Payment Entry" pe',
        "fields": {
            "name": "string",
            "docstatus": "number",
            "posting_date": "date",
            "company": "string",
            "payment_type": "string",
            "party_type": "string",
            "party": "string",
            "paid_amount": "number",
            "received_amount": "number",
            "paid_from": "string",
            "paid_to": "string",
        },
        "field_sql": {
            "name": "pe.name",
            "docstatus": "pe.docstatus",
            "posting_date": "pe.posting_date",
            "company": "pe.company",
            "payment_type": "pe.payment_type",
            "party_type": "pe.party_type",
            "party": "pe.party",
            "paid_amount": "pe.paid_amount",
            "received_amount": "pe.received_amount",
            "paid_from": "pe.paid_from",
            "paid_to": "pe.paid_to",
        },
        "default_where": ["pe.docstatus = 1"],
        "filter_fields": {"company", "posting_date", "payment_type", "party_type", "party"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "pe.posting_date DESC, pe.name DESC",
    },
    "ar_open_items": {
        "label": "AR Open Items",
        "description": "Open submitted non-return sales invoices with positive outstanding amounts.",
        "sql_from": 'FROM "Sales Invoice" si',
        "fields": {
            "invoice": "string",
            "posting_date": "date",
            "company": "string",
            "customer": "string",
            "customer_name": "string",
            "grand_total": "number",
            "outstanding_amount": "number",
        },
        "field_sql": {
            "invoice": "si.name",
            "posting_date": "si.posting_date",
            "company": "si.company",
            "customer": "si.customer",
            "customer_name": "si.customer_name",
            "grand_total": "si.grand_total",
            "outstanding_amount": "si.outstanding_amount",
        },
        "default_where": [
            "si.docstatus = 1",
            "COALESCE(si.is_return, 0) = 0",
            "si.outstanding_amount > 0",
        ],
        "filter_fields": {"company", "customer", "posting_date"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "si.outstanding_amount DESC, si.posting_date DESC",
    },
    "ap_open_items": {
        "label": "AP Open Items",
        "description": "Open submitted non-return purchase invoices with positive outstanding amounts.",
        "sql_from": 'FROM "Purchase Invoice" pi',
        "fields": {
            "invoice": "string",
            "posting_date": "date",
            "company": "string",
            "supplier": "string",
            "supplier_name": "string",
            "grand_total": "number",
            "outstanding_amount": "number",
        },
        "field_sql": {
            "invoice": "pi.name",
            "posting_date": "pi.posting_date",
            "company": "pi.company",
            "supplier": "pi.supplier",
            "supplier_name": "pi.supplier_name",
            "grand_total": "pi.grand_total",
            "outstanding_amount": "pi.outstanding_amount",
        },
        "default_where": [
            "pi.docstatus = 1",
            "COALESCE(pi.is_return, 0) = 0",
            "pi.outstanding_amount > 0",
        ],
        "filter_fields": {"company", "supplier", "posting_date"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "pi.outstanding_amount DESC, pi.posting_date DESC",
    },
    "stock_balances": {
        "label": "Stock Balances",
        "description": "Current stock positions from Bin joined to Warehouse for company-aware filtering.",
        "sql_from": 'FROM "Bin" b LEFT JOIN "Warehouse" w ON w.name = b.warehouse',
        "fields": {
            "item_code": "string",
            "warehouse": "string",
            "company": "string",
            "actual_qty": "number",
            "valuation_rate": "number",
            "stock_value": "number",
        },
        "field_sql": {
            "item_code": "b.item_code",
            "warehouse": "b.warehouse",
            "company": "w.company",
            "actual_qty": "b.actual_qty",
            "valuation_rate": "b.valuation_rate",
            "stock_value": "b.stock_value",
        },
        "default_where": [],
        "filter_fields": {"company", "warehouse", "item_code"},
        "default_limit": 1000,
        "max_limit": 10000,
        "default_order_by": "b.stock_value DESC, b.item_code ASC",
    },
}


def _build_dataset_filter_clauses(spec: dict[str, Any], filters: dict[str, Any], params: list[Any]) -> list[str]:
    clauses = list(spec.get("default_where", []))
    for field, value in (filters or {}).items():
        if field not in spec["filter_fields"]:
            raise HTTPException(400, f"Dataset '{spec['label']}' does not allow filtering on '{field}'")
        column = spec["field_sql"][field]
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            if "from" in value and value["from"] not in (None, ""):
                clauses.append(f"{column} >= ?")
                params.append(value["from"])
            if "to" in value and value["to"] not in (None, ""):
                clauses.append(f"{column} <= ?")
                params.append(value["to"])
            continue
        if isinstance(value, list):
            if not value:
                continue
            placeholders = ", ".join("?" for _ in value)
            clauses.append(f"{column} IN ({placeholders})")
            params.extend(value)
            continue
        clauses.append(f"{column} = ?")
        params.append(value)
    return clauses


def _fetch_semantic_dataset(request: RuntimeDataRequest) -> dict[str, Any]:
    db = get_db()
    spec = SEMANTIC_DATASETS.get(request.dataset)
    if not spec:
        raise HTTPException(400, f"Unknown semantic dataset '{request.dataset}'")

    requested_fields = request.fields or list(spec["fields"].keys())
    invalid = [field for field in requested_fields if field not in spec["fields"]]
    if invalid:
        raise HTTPException(
            400,
            f"Dataset '{request.dataset}' does not expose fields: {', '.join(invalid)}",
        )

    limit = request.limit or spec["default_limit"]
    limit = max(1, min(limit, spec["max_limit"]))

    params: list[Any] = []
    where = _build_dataset_filter_clauses(spec, request.filters, params)
    select = ", ".join(f"{spec['field_sql'][field]} AS \"{field}\"" for field in requested_fields)
    sql = f"""
        SELECT {select}
        {spec['sql_from']}
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY {spec['default_order_by']}
        LIMIT {limit}
    """
    rows = [dict(row) for row in db.sql(sql, params)]
    return {
        "name": request.name or request.dataset,
        "dataset": request.dataset,
        "rows": rows,
        "fields": {field: spec["fields"][field] for field in requested_fields},
        "row_count": len(rows),
        "truncated": len(rows) >= limit,
        "limit": limit,
    }


_AGG_OPS = {"sum", "count", "avg", "min", "max"}


def aggregate_semantic_dataset(
    dataset: str,
    group_by: list[str] | None,
    measures: dict[str, list[str]],
    filters: dict[str, Any] | None = None,
    order_by: list[dict[str, str]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run a whitelisted GROUP BY aggregation over a semantic dataset.

    `measures` is shaped like `{alias: [op, field]}` where `op` is one of
    sum / count / avg / min / max. For `count`, field can be omitted or
    set to "*".
    """
    spec = SEMANTIC_DATASETS.get(dataset)
    if not spec:
        raise HTTPException(400, f"Unknown semantic dataset '{dataset}'")

    group_by = group_by or []
    invalid_groups = [f for f in group_by if f not in spec["fields"]]
    if invalid_groups:
        raise HTTPException(
            400,
            f"Dataset '{dataset}' does not expose fields: {', '.join(invalid_groups)}",
        )

    if not measures:
        raise HTTPException(400, "At least one measure is required.")

    select_parts: list[str] = []
    for field in group_by:
        select_parts.append(f"{spec['field_sql'][field]} AS \"{field}\"")

    for alias, definition in measures.items():
        if not isinstance(definition, (list, tuple)) or len(definition) < 1:
            raise HTTPException(400, f"Measure '{alias}' must be [op] or [op, field].")
        op = str(definition[0]).lower()
        if op not in _AGG_OPS:
            raise HTTPException(
                400,
                f"Measure '{alias}' uses unsupported op '{op}'. Allowed: {sorted(_AGG_OPS)}",
            )
        field = definition[1] if len(definition) > 1 else "*"
        if op == "count" and field in (None, "*"):
            select_parts.append(f"COUNT(*) AS \"{alias}\"")
            continue
        if field not in spec["fields"]:
            raise HTTPException(
                400,
                f"Measure '{alias}' references unknown field '{field}' for dataset '{dataset}'.",
            )
        select_parts.append(f"{op.upper()}({spec['field_sql'][field]}) AS \"{alias}\"")

    params: list[Any] = []
    where = _build_dataset_filter_clauses(spec, filters or {}, params)

    order_clauses: list[str] = []
    for entry in order_by or []:
        field = entry.get("field")
        direction = (entry.get("direction") or "asc").lower()
        if direction not in ("asc", "desc"):
            raise HTTPException(400, f"Invalid order direction '{direction}'.")
        if field in measures:
            order_clauses.append(f'"{field}" {direction}')
        elif field in group_by:
            order_clauses.append(f'"{field}" {direction}')
        else:
            raise HTTPException(
                400,
                f"order_by field '{field}' must be a group_by field or a measure alias.",
            )

    resolved_limit = limit if limit is not None else spec["default_limit"]
    resolved_limit = max(1, min(resolved_limit, spec["max_limit"]))

    sql = f"""
        SELECT {', '.join(select_parts)}
        {spec['sql_from']}
        {'WHERE ' + ' AND '.join(where) if where else ''}
        {'GROUP BY ' + ', '.join(spec['field_sql'][f] for f in group_by) if group_by else ''}
        {'ORDER BY ' + ', '.join(order_clauses) if order_clauses else ''}
        LIMIT {resolved_limit}
    """
    rows = [dict(row) for row in get_db().sql(sql, params)]
    return {
        "dataset": dataset,
        "group_by": group_by,
        "measures": list(measures.keys()),
        "row_count": len(rows),
        "rows": rows,
        "truncated": len(rows) >= resolved_limit,
    }


@router.get("/runtime/datasets")
def list_semantic_datasets(_user: dict = _viewer):
    return {
        "datasets": [
            {
                "dataset": dataset,
                "label": spec["label"],
                "description": spec["description"],
                "fields": spec["fields"],
                "filter_fields": sorted(spec["filter_fields"]),
                "default_limit": spec["default_limit"],
                "max_limit": spec["max_limit"],
            }
            for dataset, spec in SEMANTIC_DATASETS.items()
        ]
    }


@router.post("/runtime/data")
def fetch_runtime_data(payload: RuntimeFetchPayload, _user: dict = _viewer):
    return {
        "datasets": [_fetch_semantic_dataset(request) for request in payload.requests],
    }


def create_report_draft_record(
    payload: dict[str, Any],
    user: dict | None = None,
    source_chat_session_id: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    draft_id = f"RPT-{str(uuid.uuid4())[:8].upper()}"
    definition = {
        "title": payload["title"],
        "description": payload.get("description"),
        "data_requests": payload.get("data_requests", []),
        "transform_js": payload.get("transform_js", ""),
    }
    db.sql(
        'INSERT INTO "Report Draft" (id, title, description, definition_json, created_by, source_chat_session_id) VALUES (?, ?, ?, ?, ?, ?)',
        [
            draft_id,
            payload["title"],
            payload.get("description"),
            json.dumps(definition),
            (user or {}).get("name"),
            source_chat_session_id,
        ],
    )
    db.conn.commit()
    return {
        "id": draft_id,
        "title": payload["title"],
        "description": payload.get("description"),
        "definition": definition,
        "url": f"/reports/analytics?report_id={draft_id}",
        "created_by": (user or {}).get("name"),
        "source_chat_session_id": source_chat_session_id,
    }


def get_report_draft_record(report_id: str, user: dict | None = None) -> dict[str, Any] | None:
    db = get_db()
    rows = db.sql(
        '''
        SELECT id, title, description, definition_json, created_by, source_chat_session_id, created_at, updated_at
        FROM "Report Draft"
        WHERE id = ?
        LIMIT 1
        ''',
        [report_id],
    )
    if not rows:
        return None
    row = dict(rows[0])
    if user and row.get("created_by") and row.get("created_by") != user.get("name") and user.get("role") != "admin":
        raise HTTPException(403, "You do not have access to this report draft.")
    definition = json.loads(row["definition_json"])
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description"),
        "definition": definition,
        "created_by": row.get("created_by"),
        "source_chat_session_id": row.get("source_chat_session_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "url": f"/reports/analytics?report_id={row['id']}",
    }


def update_report_draft_record(report_id: str, payload: dict[str, Any], user: dict | None = None) -> dict[str, Any] | None:
    existing = get_report_draft_record(report_id, user)
    if not existing:
        return None

    definition = dict(existing["definition"])
    title = payload.get("title", existing["title"])
    description = payload.get("description", existing.get("description"))
    if payload.get("title") is not None:
        definition["title"] = payload["title"]
    if payload.get("description") is not None:
        definition["description"] = payload["description"]
    if payload.get("data_requests") is not None:
        definition["data_requests"] = payload["data_requests"]
    if payload.get("transform_js") is not None:
        definition["transform_js"] = payload["transform_js"]

    db = get_db()
    db.sql(
        '''
        UPDATE "Report Draft"
        SET title = ?, description = ?, definition_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        ''',
        [title, description, json.dumps(definition), report_id],
    )
    db.conn.commit()
    return get_report_draft_record(report_id, user)


@router.post("/runtime/drafts")
def create_report_draft(payload: ReportDraftPayload, user: dict = _viewer):
    return create_report_draft_record(payload.model_dump(), user)


@router.get("/runtime/drafts/{report_id}")
def get_report_draft(report_id: str, user: dict = _viewer):
    row = get_report_draft_record(report_id, user)
    if not row:
        raise HTTPException(404, f"Report draft '{report_id}' not found")
    return row


@router.put("/runtime/drafts/{report_id}")
def update_report_draft(report_id: str, payload: ReportDraftUpdatePayload, user: dict = _viewer):
    row = update_report_draft_record(report_id, payload.model_dump(exclude_none=True), user)
    if not row:
        raise HTTPException(404, f"Report draft '{report_id}' not found")
    return row


@router.get("/runtime/drafts")
def list_report_drafts(user: dict = _viewer):
    db = get_db()
    if user.get("role") == "admin":
        rows = db.sql(
            '''
            SELECT id, title, description, created_by, source_chat_session_id, created_at, updated_at
            FROM "Report Draft"
            ORDER BY updated_at DESC
            '''
        )
    else:
        # Non-admins see their own drafts, plus ownerless drafts — those
        # are system/demo assets (e.g. the seeded "Top 7 Customers by
        # Revenue" draft bootstrap creates for the demo). The access check
        # in get_report_draft_record already treats null-owned drafts as
        # open, so showing them in the list is consistent.
        rows = db.sql(
            '''
            SELECT id, title, description, created_by, source_chat_session_id, created_at, updated_at
            FROM "Report Draft"
            WHERE created_by = ? OR created_by IS NULL
            ORDER BY updated_at DESC
            ''',
            [user.get("name")],
        )
    return {
        "drafts": [
            {
                "id": row["id"],
                "title": row["title"],
                "description": row.get("description"),
                "created_by": row.get("created_by"),
                "source_chat_session_id": row.get("source_chat_session_id"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "url": f"/reports/analytics?report_id={row['id']}",
            }
            for row in (dict(r) for r in rows)
        ]
    }


@router.delete("/runtime/drafts/{report_id}")
def delete_report_draft(report_id: str, user: dict = _viewer):
    db = get_db()
    rows = db.sql(
        'SELECT created_by FROM "Report Draft" WHERE id = ? LIMIT 1',
        [report_id],
    )
    if not rows:
        raise HTTPException(404, f"Report draft '{report_id}' not found")
    owner = dict(rows[0]).get("created_by")
    if owner and owner != user.get("name") and user.get("role") != "admin":
        raise HTTPException(403, "You do not have access to this report draft.")
    db.sql('DELETE FROM "Report Draft" WHERE id = ?', [report_id])
    db.conn.commit()
    return {"deleted": report_id}
