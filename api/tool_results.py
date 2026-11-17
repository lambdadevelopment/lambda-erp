"""Keep what a tool hands to an assistant within a size a model can read.

The built-in chat and the assistants connected through MCP receive a tool's
result as JSON text. A result of megabytes (every customer with every
column) does not fit a model's context at all, and a result of a few hundred
kilobytes crowds out the conversation. Each list tool pages its own rows;
this module is the net below them: whatever a tool returns is brought under
`max_chars()` before it is serialised, and says that it was cut and how to
get the rest.
"""
import json
import os

DEFAULT_MAX_CHARS = 100_000
# Rows of a master search returned when the caller names no limit.
DEFAULT_PAGE = 50
MAX_PAGE = 500
# Lines of one child table of a document, and rows of a list report.
DEFAULT_ROWS = 200
MAX_ROWS = 2000


# Keys a handler uses to hand pictures or file content to the model.
PASSED_THROUGH = ("_images", "_multimodal_content")


def max_chars() -> int:
    """Largest JSON text of one tool result, in characters (about four per
    token). LAMBDA_ERP_TOOL_RESULT_MAX_CHARS overrides the default of 100000."""
    try:
        value = int(os.environ.get("LAMBDA_ERP_TOOL_RESULT_MAX_CHARS") or DEFAULT_MAX_CHARS)
    except ValueError:
        value = DEFAULT_MAX_CHARS
    return max(2_000, value)


def _size(value) -> int:
    return len(json.dumps(value, default=str))


def _fit(rows, budget):
    """The longest prefix of `rows` whose JSON stays within `budget` characters."""
    low, high = 0, len(rows)
    while low < high:
        middle = (low + high + 1) // 2
        if _size(rows[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


def page_window(args, default=DEFAULT_ROWS, maximum=MAX_ROWS):
    """(limit, offset) from a tool's arguments, or an error message."""
    limit, offset = args.get("limit", default), args.get("offset", 0)
    if limit in (None, ""):
        limit = default
    if offset in (None, ""):
        offset = 0
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        return None, None, f"limit must be an integer between 1 and {maximum}"
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return None, None, "offset must be a non-negative integer"
    return limit, offset, None


def bound_tool_result(tool, result, limit=None):
    """`result` as it may be handed to a model: unchanged when it fits, else
    with its largest list cut to what fits and a note saying so. A result
    that is too large without any list to cut becomes an error that names
    its size."""
    budget = limit or max_chars()
    if isinstance(result, dict) and any(key in result for key in PASSED_THROUGH):
        # Images and file content shown to the model take another route and
        # are not part of the JSON text.
        aside = {key: result[key] for key in PASSED_THROUGH if key in result}
        rest = bound_tool_result(tool, {k: v for k, v in result.items() if k not in aside}, limit)
        return {**rest, **aside} if isinstance(rest, dict) else rest
    try:
        size = _size(result)
    except (TypeError, ValueError):
        return result
    if size <= budget:
        return result

    note = ("The result was cut to fit ({total} {what} in all, the first {kept} are included; the full result was "
            "{size} characters, the limit is {budget}). Narrow the request with filters or a more specific query, "
            "ask for fewer columns where the tool has result_fields, or page with limit and offset.")

    if isinstance(result, list):
        kept = _fit(result, budget - 600)
        return {"rows": result[:kept], "returned": kept, "total": len(result), "truncated": True,
                "note": note.format(total=len(result), what="rows", kept=kept, size=size, budget=budget)}

    if isinstance(result, dict):
        lists = {key: value for key, value in result.items() if isinstance(value, list) and len(value) > 1}
        if lists:
            key = max(lists, key=lambda name: _size(lists[name]))
            rest = _size({name: value for name, value in result.items() if name != key})
            if rest + 800 < budget:
                kept = _fit(lists[key], budget - rest - 800)
                cut = dict(result)
                cut[key] = lists[key][:kept]
                cut["_truncated"] = {
                    "field": key, "returned": kept, "total": len(lists[key]),
                    "note": note.format(total=len(lists[key]), what=f"entries of '{key}'", kept=kept, size=size, budget=budget)}
                return cut

    return {"error": (f"The result of {tool} is too large to return ({size} characters, the limit is {budget}). "
                      "Narrow the request with filters or a more specific query, or ask for fewer columns.")}
