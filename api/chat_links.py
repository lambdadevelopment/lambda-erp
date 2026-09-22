"""Canonical record links on chat/MCP tool results, never persisted fields."""

from functools import wraps
from urllib.parse import quote, urlencode

from api import services


def record_view_url(record_type: str, record: dict, *, master: bool = False) -> str | None:
    name = record.get("name")
    if not name:
        return None
    if master:
        if record_type not in services.MASTER_TABLES or record_type == "cost-center":
            return None
        if record_type == "account":
            return "/reports/general-ledger?" + urlencode({"account": name})
        return f"/masters/{quote(record_type, safe='')}/{quote(str(name), safe='')}"

    slug = services.DOCTYPE_TO_SLUG.get(record_type, record_type)
    if slug not in services.SLUG_TO_DOCTYPE:
        return None
    page = services.chat_doctype_page_info(slug)
    if page:
        if page["kind"] == "none":
            return None
        if page["kind"] == "via":
            name = record.get(page["link_field"])
            if not name:
                # Projected list/batch results may omit the parent. The model
                # can get_document to obtain it; do not guess a destination.
                return None
            slug = page["parent_slug"]
    return f"/app/{quote(slug, safe='')}/{quote(str(name), safe='')}"


def with_record_view_urls(type_arg: str, *, master: bool = False):
    """Enrich records and list/batch envelopes without changing service data."""
    def decorate(handler):
        @wraps(handler)
        def wrapped(args):
            result = handler(args)

            def enrich(row):
                if not isinstance(row, dict) or "error" in row or row.get("ok") is False:
                    return row
                return {**row, "view_url": record_view_url(args.get(type_arg), row, master=master)}

            if isinstance(result, list):
                return [enrich(row) for row in result]
            if isinstance(result, dict) and "error" not in result:
                if "name" not in result:
                    for key in ("rows", "results"):
                        if isinstance(result.get(key), list):
                            return {**result, key: [enrich(row) for row in result[key]]}
                return enrich(result)
            return result
        return wrapped
    return decorate
