"""Number ranges of the document types: read them, and (admins) set the next number.

Each document type numbers its records "PREFIX-<n>" from one shared counter
(Database.next_in_series), e.g. SINV-0006. An admin can move a range up, for
example so invoices continue at 320, or back down as far as the highest number
already used, never onto a taken number. A record saved with a hand-set number
ahead of its range moves the range past it (BaseDocument._follow_hand_set_name).
Since 1.4.30.
"""
from pydantic import BaseModel, ConfigDict, Field

from api import services
from lambda_erp import permissions
from lambda_erp.database import get_db

MAX_NUMBER = 999_999_999


class GetSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doctype: str | None = Field(default=None, max_length=100,
                                description="Document type slug, e.g. sales-invoice. Omit for every type you may read.")


class SetNext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doctype: str = Field(min_length=1, max_length=100, description="Document type slug, e.g. sales-invoice")
    next_number: int = Field(ge=1, le=MAX_NUMBER,
                             description="The number the next new document of this type gets, e.g. 321 for SINV-0321.")


def _format(prefix: str, number: int) -> str:
    return f"{prefix}-{number:04d}"


def _types(slug: str | None):
    if slug:
        doctype = services.SLUG_TO_DOCTYPE.get(slug)
        if not doctype or doctype not in services.DOCUMENT_CLASSES:
            raise ValueError(f"Unknown document type {slug!r}; use a slug such as sales-invoice.")
        permissions.require(doctype, "read")
        return [(slug, doctype)]
    out = []
    for doctype in services.DOCUMENT_CLASSES:
        slug = services.DOCTYPE_TO_SLUG.get(doctype)
        if slug and permissions.can(doctype, "read"):
            out.append((slug, doctype))
    return sorted(out, key=lambda pair: pair[1])


def _row(slug: str, doctype: str) -> dict | None:
    prefix = getattr(services.DOCUMENT_CLASSES[doctype], "PREFIX", None)
    if not prefix:
        return None
    state = get_db().series_state(prefix)
    next_number = state["current"] + 1
    return {
        "doctype": slug,
        "doctype_name": doctype,
        "prefix": prefix,
        "last_number": state["current"],
        "next_number": next_number,
        "next_name": _format(prefix, next_number),
        "highest_used": state["highest_used"],
        "lowest_allowed_next": state["highest_used"] + 1,
    }


def get_number_series(args, user=None):
    data = GetSeries.model_validate(args or {})
    rows = [row for row in (_row(slug, doctype) for slug, doctype in _types(data.doctype)) if row]
    return {"series": rows,
            "note": "next_name is what the next new document of the type gets. Numbers already used are skipped."}


def set_next_number(args, user=None):
    data = SetNext.model_validate(args)
    (slug, doctype), = _types(data.doctype)
    prefix = getattr(services.DOCUMENT_CLASSES[doctype], "PREFIX", None)
    if not prefix:
        raise ValueError(f"{doctype} has no number range.")
    get_db().set_series_next(prefix, data.next_number)
    row = _row(slug, doctype)
    shared = [other for other, cls in services.DOCUMENT_CLASSES.items()
              if other != doctype and getattr(cls, "PREFIX", None) == prefix]
    row["note"] = (f"The next new {doctype} gets {row['next_name']}. Existing documents keep their numbers."
                   + (f" The range is shared with {', '.join(shared)}." if shared else ""))
    return row


def initialize_number_series():
    services.register_action(
        "get_number_series", get_number_series, minimum_role="viewer", pass_user=True,
        parameters=GetSeries.model_json_schema(),
        description=(
            "Show the number ranges of document types: the prefix, the last number handed out and the "
            "number the next new document gets (next_name, e.g. SINV-0006). Use it to answer which number "
            "comes next instead of guessing from a list of documents."))
    services.register_action(
        "set_next_number", set_next_number, minimum_role="admin", pass_user=True,
        parameters=SetNext.model_json_schema(),
        description=(
            "Admins only: set the number the next new document of a type gets, e.g. sales-invoice 321 so "
            "invoices continue at SINV-0321. It cannot be at or below a number already used. Existing "
            "documents keep their numbers. Use this when someone wants numbers to start higher, instead "
            "of giving single documents hand-picked numbers."))
