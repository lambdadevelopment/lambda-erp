"""Shared, server-resolved time windows for REST, chat and MCP lists."""

import json
from datetime import datetime, timedelta, timezone

from lambda_erp.timestamps import parse_timestamp


def time_filter_fields(db, doctype):
    """ERP instant fields are stored as text on both supported databases."""
    return sorted(field for field in db._get_text_columns(doctype)
                  if field in {"creation", "modified"} or field.endswith(("_at", "_datetime")))


def resolve_time_filter(db, doctype, value, *, clock=None):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("time_filter must be an object")
    unknown = set(value) - {"field", "last_hours", "since", "until"}
    if unknown:
        raise ValueError(f"Unknown time_filter keys: {', '.join(sorted(unknown))}")
    field = value.get("field")
    if not isinstance(field, str) or field not in db._get_table_columns(doctype):
        raise ValueError(f"Unknown time_filter field for {doctype}: {field}")
    if field not in time_filter_fields(db, doctype):
        raise ValueError("time_filter requires a timestamp field; use from_date/to_date for date-only fields")
    if "last_hours" in value:
        if "since" in value or "until" in value:
            raise ValueError("Use last_hours OR since/until, not both")
        hours = value["last_hours"]
        if isinstance(hours, bool) or not isinstance(hours, (float, int)) or not 0 < hours <= 24 * 366:
            raise ValueError("last_hours must be a positive number, at most 8784")
        end = clock or datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)
    else:
        if "since" not in value or "until" not in value:
            raise ValueError("time_filter requires last_hours or both since and until")
        start = parse_timestamp(value["since"], require_timezone=True)
        end = parse_timestamp(value["until"], require_timezone=True)
    if start >= end:
        raise ValueError("time_filter since must precede until")
    if end - start > timedelta(days=366):
        raise ValueError("time_filter window must not exceed 366 days")
    return {"field": field, "since": start.isoformat(), "until": end.isoformat()}


def request_time_filter(request):
    values = request.query_params.getlist("time_filter")
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("Supply time_filter only once")
    try:
        value = json.loads(values[0])
        if not isinstance(value, dict):
            raise ValueError("time_filter must be a JSON object")
        return value
    except (ValueError, TypeError) as exc:
        raise ValueError("time_filter must be a JSON object") from exc


def time_predicate(window):
    field = window["field"]  # validated against the actual table before use
    expression = f'erp_timestamp_epoch("{field}")'
    return f"{expression} >= ? AND {expression} < ?", [
        parse_timestamp(window["since"]).timestamp(),
        parse_timestamp(window["until"]).timestamp(),
    ]


def time_quality(db, doctype, window, where, params):
    """Unknown times cannot be placed in/out of a window: disclose the count."""
    expression = f'erp_timestamp_epoch("{window["field"]}")'
    clauses = [*where, f"{expression} IS NULL"]
    rows = db.sql(f'SELECT COUNT(*) AS n FROM "{doctype}" WHERE ' + " AND ".join(f"({x})" for x in clauses), params)
    return int(rows[0]["n"])


def page_metadata(rows, total, limit, offset, window=None, unknown=0):
    has_more = offset + len(rows) < total
    return {
        "rows": rows, "total": total, "limit": limit, "offset": offset,
        "has_more": has_more, "next_offset": offset + len(rows) if has_more else None,
        "time_filter": window,
        "coverage": {"complete": unknown == 0, "unknown_time_rows": unknown},
        "warnings": ([f"{unknown} otherwise matching records have missing/invalid timestamps; their inclusion in this period is unknown."] if unknown else []),
    }


TIME_FILTER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Exact server-resolved time window. Use field=modified for changed records, creation for new records, occurred_at for CRM interactions. Use last_hours=24 for the preceding 24 elapsed hours, NOT yesterday at midnight. Alternatively supply timezone-aware since AND until; until is exclusive. Reuse the returned absolute window for further pages/types. Missing/invalid stored timestamps are disclosed in coverage.",
    "properties": {
        "field": {"type": "string"},
        "last_hours": {"type": "number", "exclusiveMinimum": 0, "maximum": 8784},
        "since": {"type": "string", "description": "ISO timestamp with timezone"},
        "until": {"type": "string", "description": "ISO timestamp with timezone (exclusive)"},
    },
    "required": ["field"],
}
