"""Access restrictions, applied where data leaves or enters the system.

A user has a role (admin / manager / viewer), which decides what they may do
at all. An *access profile* narrows that for one user: it hides classes of
sensitive data and can take away actions per document type. Users without a
profile are not affected by anything in this module.

Everything here reads the *current principal*: the authenticated caller of
the running request or tool call. REST binds it per request, chat and MCP run
each tool call under it. Code that runs without a principal (startup, tests
that call the business logic directly, scheduled jobs) is unrestricted.

See docs/access-profiles.md.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar

from lambda_erp.exceptions import PermissionDenied, ValidationError

# -- sensitivity classes ------------------------------------------------------

SENSITIVITY_CLASSES = {
    "purchase_price": "Purchase prices: rates, amounts and totals of purchasing documents, buying price lists and rules.",
    "cost": "Cost and stock value: valuation rates, stock value, stock entry rates.",
    "supplier_terms": "What is owed and paid to suppliers: payables, supplier payments.",
    "margin": "Financial statements and the general ledger, from which margins follow.",
}

# Hiding a class hides what it can be derived from: the stock value gives the
# purchase price away, the ledger gives the cost away.
IMPLIES = {
    "purchase_price": ("cost", "supplier_terms"),
    "cost": ("margin",),
}

ACTIONS = ("read", "create", "update", "submit", "cancel", "discard", "convert", "delete", "export")


def expand_classes(classes):
    """The classes plus everything they imply."""
    result, todo = set(), list(classes or ())
    while todo:
        cls = todo.pop()
        if cls not in result:
            result.add(cls)
            todo.extend(IMPLIES.get(cls, ()))
    return frozenset(result)


# -- what is sensitive --------------------------------------------------------

_PRICE = ("rate", "price_list_rate", "amount", "net_rate", "net_amount", "base_rate", "base_amount",
          "base_net_rate", "base_net_amount", "discount_amount", "discount_percentage", "discount_label")
_TOTALS = ("total", "net_total", "grand_total", "rounded_total", "base_total", "base_net_total",
           "base_grand_total", "total_taxes_and_charges", "discount_amount", "outstanding_amount", "paid_amount")
_TAX = ("tax_amount", "total", "base_tax_amount", "base_total")


def _purchasing():
    return {"": dict.fromkeys(_TOTALS, "purchase_price"),
            "items": dict.fromkeys(_PRICE, "purchase_price"),
            "taxes": dict.fromkeys(_TAX, "purchase_price")}


# doctype -> {"" (the document itself) | child key -> {field: class}}
SENSITIVE_FIELDS = {
    "Purchase Order": _purchasing(),
    "Purchase Receipt": _purchasing(),
    "Purchase Invoice": _purchasing(),
    "Stock Entry": {
        "": dict.fromkeys(("total_amount", "total_incoming_value", "total_outgoing_value", "value_difference"), "cost"),
        "items": dict.fromkeys(("basic_rate", "basic_amount", "amount", "valuation_rate"), "cost"),
    },
    "Asset": {"": {"purchase_value": "purchase_price"}},
}

# doctype -> (class, SQL condition a visible row satisfies). The rows that do
# not satisfy it do not exist for a principal the class is hidden from.
SENSITIVE_RECORDS = {
    "Price List": ("purchase_price", "COALESCE(buying, 0) = 0"),
    "Pricing Rule": ("purchase_price", "COALESCE(buying, 0) = 0"),
    "Item Price": ("purchase_price",
                   "(supplier IS NULL OR supplier = '') AND (price_list IS NULL OR price_list NOT IN "
                   "(SELECT name FROM \"Price List\" WHERE COALESCE(buying, 0) = 1))"),
    "Payment Entry": ("supplier_terms", "COALESCE(party_type, '') <> 'Supplier'"),
    "Subscription": ("purchase_price", "COALESCE(party_type, '') <> 'Supplier'"),
}

# doctype -> class: the whole document type is unreadable.
SENSITIVE_DOCTYPES = {
    "Journal Entry": "margin",
    "Budget": "margin",
    "Bank Transaction": "margin",
    "Bank Account": "margin",
}

# Reports, datasets and metrics. kind -> name -> class (the whole artifact is
# denied) and kind -> name -> {field: class} (those columns are left out).
SENSITIVE_ARTIFACTS = {
    "report": {
        "trial-balance": "margin", "chart-of-accounts": "margin", "general-ledger": "margin",
        "profit-and-loss": "margin", "balance-sheet": "margin", "ap-aging": "supplier_terms",
    },
    "dataset": {
        "purchase_invoices": "purchase_price", "purchase_invoice_lines": "purchase_price",
        "ap_open_items": "supplier_terms", "payments": "supplier_terms",
        "gl_entries": "margin", "bank_transactions": "margin",
    },
    "metric": {
        "purchases": "purchase_price", "payments_made": "supplier_terms",
        "outstanding_ap": "supplier_terms", "stock_value": "cost",
    },
}
SENSITIVE_ARTIFACT_FIELDS = {
    "report": {
        "stock-balance": {"valuation_rate": "cost", "stock_value": "cost"},
        "dashboard-summary": {"stock_value": "cost", "total_stock_value": "cost",
                              "outstanding_payable": "supplier_terms", "payable": "supplier_terms"},
    },
    "dataset": {
        "stock_balances": {"valuation_rate": "cost", "stock_value": "cost"},
        "stock_movements": dict.fromkeys(
            ("incoming_rate", "outgoing_rate", "valuation_rate", "stock_value", "stock_value_difference"), "cost"),
    },
}


def _check_class(cls):
    if cls not in SENSITIVITY_CLASSES:
        raise ValueError(f"Unknown sensitivity class: {cls}. Known: {', '.join(sorted(SENSITIVITY_CLASSES))}")


def register_sensitive_fields(doctype, fields, child=None):
    """Plugin seam: declare fields of a document type (or of one of its child
    tables, by its key) as sensitive. `fields` is {field: class}."""
    for cls in fields.values():
        _check_class(cls)
    SENSITIVE_FIELDS.setdefault(doctype, {}).setdefault(child or "", {}).update(fields)


def register_sensitive_records(doctype, cls, visible_sql):
    """Plugin seam: rows of `doctype` that do not satisfy `visible_sql` (a
    condition over the table's own columns) are hidden with class `cls`."""
    _check_class(cls)
    SENSITIVE_RECORDS[doctype] = (cls, visible_sql)


def register_sensitive_doctype(doctype, cls):
    """Plugin seam: the whole document type is hidden with class `cls`."""
    _check_class(cls)
    SENSITIVE_DOCTYPES[doctype] = cls


def register_sensitive_artifact(kind, name, cls=None, fields=None):
    """Plugin seam: a report, dataset or metric that is denied as a whole
    (`cls`) or has columns left out (`fields`: {column: class})."""
    if cls:
        _check_class(cls)
        SENSITIVE_ARTIFACTS.setdefault(kind, {})[name] = cls
    if fields:
        for value in fields.values():
            _check_class(value)
        SENSITIVE_ARTIFACT_FIELDS.setdefault(kind, {}).setdefault(name, {}).update(fields)


# -- the current principal ----------------------------------------------------

_ACTING = ContextVar("permissions_acting_principal", default=None)
_REQUEST = ContextVar("permissions_request_scope", default=None)


def open_request_scope():
    """Start a request: returns a token for close_request_scope. The holder is
    a mutable object on purpose, so the principal bound by an authentication
    dependency (which may run in another thread with a copy of the context) is
    seen by the endpoint."""
    return _REQUEST.set({})


def close_request_scope(token):
    _REQUEST.reset(token)


def bind_request_principal(principal):
    holder = _REQUEST.get()
    if holder is not None:
        holder["principal"] = principal


def bind_request_principal_if_unset(principal):
    holder = _REQUEST.get()
    if holder is not None and "principal" not in holder:
        holder["principal"] = principal


@contextmanager
def acting_as(principal):
    """Run a block as `principal` (tool dispatch, background work on behalf
    of a user, tests)."""
    token = _ACTING.set(principal)
    try:
        yield
    finally:
        _ACTING.reset(token)


def current_principal():
    acting = _ACTING.get()
    if acting is not None:
        return acting
    holder = _REQUEST.get()
    return holder.get("principal") if holder else None


def current_restrictions():
    """The restrictions of the current principal, or None when unrestricted."""
    principal = current_principal()
    return (principal or {}).get("restrictions") or None


def is_restricted():
    return current_restrictions() is not None


def hidden_classes():
    restrictions = current_restrictions()
    return restrictions["hidden"] if restrictions else frozenset()


# -- access profiles ----------------------------------------------------------

LOCKED_OUT = {"profile": None, "hidden": expand_classes(SENSITIVITY_CLASSES),
              "permissions": {"default": [], "doctypes": {}}}


def validate_profile(hidden_classes_, permissions):
    """Normalise and check a profile's settings; returns (classes, permissions)."""
    classes = sorted(set(hidden_classes_ or ()))
    for cls in classes:
        if cls not in SENSITIVITY_CLASSES:
            raise ValidationError(
                f"Unknown sensitivity class: {cls}. Use: {', '.join(sorted(SENSITIVITY_CLASSES))}")
    permissions = permissions or {}
    if not isinstance(permissions, dict) or set(permissions) - {"default", "doctypes"}:
        raise ValidationError('permissions must be an object with "default" and/or "doctypes"')
    default = permissions.get("default")
    doctypes = permissions.get("doctypes") or {}
    if not isinstance(doctypes, dict):
        raise ValidationError('permissions.doctypes must map a document or master type to its allowed actions')

    def actions(value, where):
        if not isinstance(value, list) or any(action not in ACTIONS for action in value):
            raise ValidationError(f"{where}: allowed actions are a list of {', '.join(ACTIONS)}")
        return sorted(set(value), key=ACTIONS.index)

    result = {"default": None if default is None else actions(default, "permissions.default"),
              "doctypes": {str(name): actions(value, f"permissions.doctypes.{name}")
                           for name, value in doctypes.items()}}
    return classes, result


def load_restrictions(profile_name):
    """Restrictions for a user's access profile. A profile that is missing or
    disabled locks the user out of everything it could have hidden, and of
    every action, instead of silently lifting the restriction."""
    if not profile_name:
        return None
    from lambda_erp.database import get_db

    row = get_db().get_value("Access Profile", profile_name, ["name", "hidden_classes", "permissions", "disabled"])
    if not row or row.get("disabled"):
        return {**LOCKED_OUT, "profile": profile_name}
    try:
        classes, permissions = validate_profile(json.loads(row.get("hidden_classes") or "[]"),
                                                json.loads(row.get("permissions") or "{}"))
    except (ValueError, ValidationError):
        return {**LOCKED_OUT, "profile": profile_name}
    return {"profile": profile_name, "hidden": expand_classes(classes), "permissions": permissions}


# -- checks -------------------------------------------------------------------

def _hidden(cls, restrictions):
    return bool(restrictions) and cls in restrictions["hidden"]


def can(doctype, action, restrictions=None):
    """May the current principal perform `action` on this document or master
    type? Only the profile is checked here; the role is checked where it
    always was. Unrestricted principals may do everything."""
    restrictions = restrictions if restrictions is not None else current_restrictions()
    if not restrictions:
        return True
    if _hidden(SENSITIVE_DOCTYPES.get(doctype), restrictions):
        return False
    permissions = restrictions["permissions"]
    allowed = permissions["doctypes"].get(doctype, permissions["default"])
    return allowed is None or action in allowed


def require(doctype, action):
    if not can(doctype, action):
        raise PermissionDenied(f"Your access profile does not allow you to {action} {doctype}")


def hidden_fields(doctype, child=None, restrictions=None):
    """Fields of the document type (or of its child table `child`) that the
    current principal may not see."""
    restrictions = restrictions if restrictions is not None else current_restrictions()
    if not restrictions:
        return frozenset()
    fields = SENSITIVE_FIELDS.get(doctype, {}).get(child or "", {})
    return frozenset(field for field, cls in fields.items() if cls in restrictions["hidden"])


def has_hidden_fields(doctype):
    restrictions = current_restrictions()
    if not restrictions:
        return False
    return any(cls in restrictions["hidden"]
               for fields in SENSITIVE_FIELDS.get(doctype, {}).values() for cls in fields.values())


def require_fields_visible(doctype, fields, purpose="use"):
    """Refuse a request that names a hidden field (to filter, sort, search or
    select by it): leaving it out silently would still let it be probed."""
    blocked = sorted(set(fields or ()) & hidden_fields(doctype))
    if blocked:
        raise PermissionDenied(
            f"Your access profile does not allow you to {purpose} {doctype} field(s): {', '.join(blocked)}")


def redact_document(doctype, record):
    """A copy of a document (dict, child tables as lists under their keys)
    without the fields hidden from the current principal."""
    restrictions = current_restrictions()
    if not restrictions or record is None:
        return record
    policy = SENSITIVE_FIELDS.get(doctype)
    if not policy:
        return record
    result = dict(record)
    for field in hidden_fields(doctype, restrictions=restrictions):
        result.pop(field, None)
    for child in policy:
        if not child or not isinstance(result.get(child), list):
            continue
        drop = hidden_fields(doctype, child, restrictions)
        if drop:
            result[child] = [{key: value for key, value in dict(row).items() if key not in drop}
                             for row in result[child]]
    return result


def record_conditions(doctype):
    """SQL conditions (over the doctype's own columns) that restrict a query
    to the rows the current principal may see. Empty when nothing is hidden."""
    restrictions = current_restrictions()
    rule = SENSITIVE_RECORDS.get(doctype)
    if restrictions and rule and rule[0] in restrictions["hidden"]:
        return [rule[1]]
    return []


def require_record_visible(doctype, name):
    """Refuse a single record that the record rules hide. The message is the
    same as for a record that does not exist."""
    conditions = record_conditions(doctype)
    if not conditions:
        return
    from lambda_erp.database import get_db

    rows = get_db().sql(f'SELECT name FROM "{doctype}" WHERE name = ? AND ({" AND ".join(conditions)})', [name])
    if not rows:
        raise PermissionDenied(f"Your access profile does not allow you to read {doctype} {name}")


def require_readable(doctype, name=None):
    require(doctype, "read")
    if name is not None:
        require_record_visible(doctype, name)


def require_writable(doctype, action):
    """Writes need the action, and are refused for document types with fields
    the principal cannot see: a document saved from a redacted copy would lose
    or overwrite what was hidden."""
    require(doctype, action)
    if action in ("create", "update") and has_hidden_fields(doctype):
        raise PermissionDenied(
            f"Your access profile hides fields of {doctype}, so you cannot {action} it")


def artifact_denied(kind, name):
    restrictions = current_restrictions()
    return _hidden(SENSITIVE_ARTIFACTS.get(kind, {}).get(name), restrictions)


def require_artifact(kind, name):
    if artifact_denied(kind, name):
        raise PermissionDenied(f"Your access profile does not allow the {kind} {name}")


def hidden_artifact_fields(kind, name):
    restrictions = current_restrictions()
    if not restrictions:
        return frozenset()
    fields = SENSITIVE_ARTIFACT_FIELDS.get(kind, {}).get(name, {})
    return frozenset(field for field, cls in fields.items() if cls in restrictions["hidden"])


def redact_rows(kind, name, rows):
    drop = hidden_artifact_fields(kind, name)
    if not drop:
        return rows
    return [{key: value for key, value in dict(row).items() if key not in drop} for row in rows]


def describe_restrictions(restrictions=None):
    """What a client needs to adapt its interface: no secrets in here."""
    restrictions = restrictions if restrictions is not None else current_restrictions()
    if not restrictions:
        return None
    return {
        "profile": restrictions["profile"],
        "hidden_classes": sorted(restrictions["hidden"]),
        "permissions": restrictions["permissions"],
        "hidden_doctypes": sorted(name for name, cls in SENSITIVE_DOCTYPES.items() if cls in restrictions["hidden"]),
        "denied_reports": sorted(name for name, cls in SENSITIVE_ARTIFACTS["report"].items()
                                 if cls in restrictions["hidden"]),
    }
