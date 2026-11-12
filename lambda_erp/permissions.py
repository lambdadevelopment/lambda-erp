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


# -- record scope -------------------------------------------------------------
#
# A user can be restricted to assigned records of a dimension (customers,
# warehouses). Which records carry a dimension is declared here per document
# or master type; reports, datasets and metrics declare it too, and one that
# does not is refused for a user restricted in that dimension.

SCOPE_DIMENSIONS = {"customer": "Customer", "warehouse": "Warehouse"}

# doctype -> dimension -> [rule]; all rules must hold. A rule is a dict:
#   {"self": True}                       the record is the assigned one (the master itself)
#   {"column": c}                        the document's column names an assigned value
#   {"column": c, "optional": True}      ... or is empty
#   {"column": c, "when": (field, v)}    ... only checked where field = v
#   {"child": key, "table": t, "columns": (c, ...)}   no line names a value that is not assigned
#   {"deny": True}                       no record of this type is visible
#   {..., "consignment": True}           (customer dimension) the column or line columns name
#                                        warehouses; none may be the consignment warehouse of a
#                                        customer that is not assigned
RECORD_SCOPES = {}

# kind -> name -> {dimension: None | field | SQL template}: how a report,
# dataset or metric is limited. None: it has nothing to do with the
# dimension. Missing: refused for a user restricted in that dimension.
SCOPED_ARTIFACTS = {"report": {}, "dataset": {}, "metric": {}}


# A warehouse that is the consignment warehouse of a customer who is not assigned.
_FOREIGN_WAREHOUSES = ('SELECT name FROM "Warehouse" WHERE COALESCE(consignment_customer, \'\') <> \'\' '
                       "AND consignment_customer NOT IN ({values})")
FOREIGN_CONSIGNMENT = "COALESCE({column}, '') NOT IN (" + _FOREIGN_WAREHOUSES.replace("{values}", "{{values}}") + ")"


def _check_dimension(dimension):
    if dimension not in SCOPE_DIMENSIONS:
        raise ValueError(f"Unknown scope dimension: {dimension}. Known: {', '.join(sorted(SCOPE_DIMENSIONS))}")


def register_scope_dimension(dimension, master_doctype):
    """Plugin seam: a further dimension users can be restricted by; records of
    `master_doctype` are the values (the record itself is visible only when
    assigned)."""
    SCOPE_DIMENSIONS[dimension] = master_doctype
    RECORD_SCOPES.setdefault(master_doctype, {}).setdefault(dimension, []).append({"self": True})


def register_record_scope(doctype, dimension, column=None, *, optional=False, when=None,
                          child=None, child_table=None, child_columns=None, deny=False, consignment=False,
                          unrelated=False):
    """Plugin seam: declare how records of a document or master type belong to
    a scope dimension.

        register_record_scope("Service Visit", "customer", "customer")
        register_record_scope("Service Visit", "warehouse", child="parts",
                              child_table="Service Visit Part", child_columns=("warehouse",))

    `optional` also shows records where the column is empty; `when=(field,
    value)` applies the rule only to records with that field value;
    `deny=True` hides the whole type from users restricted in the dimension.
    `consignment=True` (customer dimension) says the columns name warehouses:
    records touching the consignment warehouse of a customer that is not
    assigned are hidden. `unrelated=True` declares that the type has nothing
    to do with the dimension.
    """
    _check_dimension(dimension)
    if unrelated:
        # Declared, with nothing to check: a plugin type has to say so (see can()).
        RECORD_SCOPES.setdefault(doctype, {}).setdefault(dimension, [])
        return
    if deny:
        rule = {"deny": True}
    elif child:
        if not child_table or not child_columns:
            raise ValueError("a child rule needs child_table and child_columns")
        rule = {"child": child, "table": child_table, "columns": tuple(child_columns), "when": when,
                "consignment": bool(consignment)}
    elif column:
        rule = {"column": column, "optional": bool(optional), "when": when, "consignment": bool(consignment)}
    else:
        raise ValueError("register_record_scope needs a column, a child table or deny=True")
    RECORD_SCOPES.setdefault(doctype, {}).setdefault(dimension, []).append(rule)


def register_scoped_artifact(kind, name, **dimensions):
    """Plugin seam: declare how a report, dataset or metric respects record
    scope, per dimension: None when it has nothing to do with it; for a
    dataset the SQL expression of the field, or a condition with `{values}`;
    for a report the row column the caller filters with `scope_rows`."""
    for dimension in dimensions:
        _check_dimension(dimension)
    SCOPED_ARTIFACTS.setdefault(kind, {}).setdefault(name, {}).update(dimensions)


def _core_record_scopes():
    for dimension, master in SCOPE_DIMENSIONS.items():
        RECORD_SCOPES.setdefault(master, {}).setdefault(dimension, []).append({"self": True})
    for doctype in ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice", "Proposal",
                    "Consignment Withdrawal", "Stock Reservation"):
        register_record_scope(doctype, "customer", "customer")
    for doctype in ("Item Price", "Pricing Rule", "Serial No"):
        register_record_scope(doctype, "customer", "customer", optional=True)
    for doctype in ("Payment Entry", "Subscription", "Reservation"):
        register_record_scope(doctype, "customer", "party", when=("party_type", "Customer"))
    register_record_scope("Journal Entry", "customer", child="accounts", child_table="Journal Entry Account",
                          child_columns=("party",), when=("party_type", "Customer"))
    register_record_scope("Bank Transaction", "customer", deny=True)  # counterparties
    register_record_scope("Warehouse", "customer", "consignment_customer", optional=True)
    # Stock lying at another customer is that customer's business too.
    for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Stock Count"):
        register_record_scope(doctype, "customer", child="items", child_table=f"{doctype} Item",
                              child_columns=("warehouse",), consignment=True)
    register_record_scope("Stock Entry", "customer", child="items", child_table="Stock Entry Detail",
                          child_columns=("s_warehouse", "t_warehouse"), consignment=True)
    for doctype in ("Stock Count", "Storage Location", "Serial No", "Asset"):
        register_record_scope(doctype, "customer", "warehouse", consignment=True)

    for doctype in ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice", "POS Invoice",
                    "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Stock Count"):
        register_record_scope(doctype, "warehouse", child="items", child_table=f"{doctype} Item",
                              child_columns=("warehouse",))
    register_record_scope("Stock Entry", "warehouse", child="items", child_table="Stock Entry Detail",
                          child_columns=("s_warehouse", "t_warehouse"))
    for column in ("from_warehouse", "to_warehouse"):
        register_record_scope("Stock Entry", "warehouse", column, optional=True)
    register_record_scope("Stock Count", "warehouse", "warehouse")
    register_record_scope("Consignment Withdrawal", "warehouse", "warehouse")
    register_record_scope("Consignment Withdrawal", "warehouse", "replenish_from_warehouse", optional=True)
    register_record_scope("Stock Reservation", "warehouse", "warehouse")
    register_record_scope("Storage Location", "warehouse", "warehouse")
    for doctype in ("Serial No", "Reservation", "Asset"):
        register_record_scope(doctype, "warehouse", "warehouse", optional=True)

    # Stock is per warehouse; receivables per customer. Everything else that
    # adds up over customers or warehouses is not declared, hence refused.
    stock = {"customer": "warehouse@consignment", "warehouse": "warehouse"}
    for report in ("stock-balance", "stock-availability", "stock-detail", "batch-stock"):
        register_scoped_artifact("report", report, **stock)
    register_scoped_artifact("report", "sales-backlog", customer="customer", warehouse="warehouse")
    register_scoped_artifact("report", "purchase-backlog", customer="warehouse@consignment", warehouse="warehouse")
    register_scoped_artifact("report", "ar-aging", customer="customer", warehouse=None)
    register_scoped_artifact("report", "ap-aging", customer=None, warehouse=None)
    register_scoped_artifact("report", "batch-changes", customer=None, warehouse=None)

    def lines(table, alias):
        return (f'NOT EXISTS (SELECT 1 FROM "{table}" scope_line WHERE scope_line.parent = {alias}.name '
                "AND COALESCE(scope_line.warehouse, '') <> '' AND scope_line.warehouse NOT IN ({values}))")

    sales, purchase = lines("Sales Invoice Item", "si"), lines("Purchase Invoice Item", "pi")
    for dataset in ("sales_invoices", "sales_invoice_lines", "ar_open_items"):
        register_scoped_artifact("dataset", dataset, customer="si.customer", warehouse=sales)
    for dataset in ("purchase_invoices", "purchase_invoice_lines", "ap_open_items"):
        register_scoped_artifact("dataset", dataset, customer=None, warehouse=purchase)
    register_scoped_artifact("dataset", "payments", warehouse=None,
                             customer="(COALESCE(pe.party_type, '') <> 'Customer' OR pe.party IN ({values}))")
    register_scoped_artifact("dataset", "stock_balances", customer=FOREIGN_CONSIGNMENT.format(column="b.warehouse"),
                             warehouse="b.warehouse")
    register_scoped_artifact("dataset", "stock_movements", customer=FOREIGN_CONSIGNMENT.format(column="sle.warehouse"),
                             warehouse="sle.warehouse")
    for metric in ("purchases", "payments_made", "outstanding_ap"):
        register_scoped_artifact("metric", metric, customer=None, warehouse=None)
    for metric in ("payments_received", "outstanding_ar"):
        register_scoped_artifact("metric", metric, warehouse=None)


_core_record_scopes()


# -- plugin types -------------------------------------------------------------
#
# The core knows what its own document and master types contain. It cannot
# know that of a type a plugin adds, so a restricted user does not get one
# until someone has said what is in it: the plugin (register_sensitive_*,
# register_plain_doctype, register_record_scope) or the profile, by naming it.

PLUGIN_TYPES = set()
PLAIN_TYPES = set()
_LOADING_PLUGINS = [False]


@contextmanager
def loading_plugins():
    """Document and master types registered inside this block are plugin types."""
    _LOADING_PLUGINS[0] = True
    try:
        yield
    finally:
        _LOADING_PLUGINS[0] = False


def note_registered_type(doctype, already_known=False):
    """Called by the registries for every document or master type."""
    if _LOADING_PLUGINS[0] and not already_known:
        PLUGIN_TYPES.add(doctype)


def register_plain_doctype(doctype):
    """Plugin seam: this document or master type contains nothing of the
    sensitivity classes (no purchase prices, cost, supplier terms, margins)."""
    PLAIN_TYPES.add(doctype)


def _plugin_type_undeclared(doctype, restrictions):
    """Why a plugin type is closed to these restrictions, or None."""
    if doctype not in PLUGIN_TYPES:
        return None
    for dimension in restrictions.get("scope") or ():
        if dimension not in RECORD_SCOPES.get(doctype, {}):
            return f"it has not declared how it relates to assigned {SCOPE_DIMENSIONS[dimension]} records"
    if restrictions.get("profile") is None:
        return None
    permissions = restrictions["permissions"]
    if doctype in permissions["doctypes"] or permissions["default"] is not None:
        return None
    if doctype in PLAIN_TYPES or doctype in SENSITIVE_FIELDS or doctype in SENSITIVE_RECORDS \
            or doctype in SENSITIVE_DOCTYPES:
        return None
    return "it has not declared what it contains and the access profile does not name it"


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
              "permissions": {"default": [], "doctypes": {}}, "scope": None}


def _locked_out(profile_name=None):
    return {**LOCKED_OUT, "profile": profile_name, "scope": {dimension: frozenset() for dimension in SCOPE_DIMENSIONS}}


def validate_record_scope(scope):
    """Normalise and check a user's record scope: {dimension: [record, ...]}.
    A dimension that is left out is not restricted; an empty list means no
    record of it."""
    if scope in (None, "", {}):
        return {}
    if not isinstance(scope, dict):
        raise ValidationError("record_scope must be an object like {\"customer\": [\"CUST-001\"]}")
    result = {}
    for dimension, values in scope.items():
        if dimension not in SCOPE_DIMENSIONS:
            raise ValidationError(
                f"Unknown scope dimension: {dimension}. Use: {', '.join(sorted(SCOPE_DIMENSIONS))}")
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValidationError(f"record_scope.{dimension} must be a list of record names")
        result[dimension] = sorted({value.strip() for value in values})
    return result


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


def load_restrictions(profile_name, record_scope=None):
    """Restrictions for a user's access profile and record scope (the stored
    JSON text or a dict). A profile that is missing or disabled, or a scope
    that cannot be read, locks the user out of everything instead of silently
    lifting the restriction."""
    try:
        scope = validate_record_scope(json.loads(record_scope) if isinstance(record_scope, str) and record_scope.strip()
                                      else (record_scope if isinstance(record_scope, dict) else None))
    except (ValueError, ValidationError):
        return _locked_out(profile_name)
    scope = {dimension: frozenset(values) for dimension, values in scope.items()} or None
    if not profile_name:
        if not scope:
            return None
        return {"profile": None, "hidden": frozenset(), "permissions": {"default": None, "doctypes": {}},
                "scope": scope}
    from lambda_erp.database import get_db

    row = get_db().get_value("Access Profile", profile_name, ["name", "hidden_classes", "permissions", "disabled"])
    if not row or row.get("disabled"):
        return _locked_out(profile_name)
    try:
        classes, permissions = validate_profile(json.loads(row.get("hidden_classes") or "[]"),
                                                json.loads(row.get("permissions") or "{}"))
    except (ValueError, ValidationError):
        return _locked_out(profile_name)
    return {"profile": profile_name, "hidden": expand_classes(classes), "permissions": permissions, "scope": scope}


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
    if _plugin_type_undeclared(doctype, restrictions):
        return False
    for dimension in restrictions.get("scope") or ():
        # A type that cannot be limited to assigned records is closed as a whole.
        if any(rule.get("deny") for rule in RECORD_SCOPES.get(doctype, {}).get(dimension, ())):
            return False
    permissions = restrictions["permissions"]
    allowed = permissions["doctypes"].get(doctype, permissions["default"])
    return allowed is None or action in allowed


def require(doctype, action):
    if not can(doctype, action):
        restrictions = current_restrictions()
        undeclared = _plugin_type_undeclared(doctype, restrictions) if restrictions else None
        if undeclared:
            raise PermissionDenied(
                f"Your access is restricted and {doctype} is an extension type: {undeclared}")
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


def current_scope():
    """{dimension: frozenset of assigned records} for the dimensions the
    current principal is restricted in; empty when there is no record scope."""
    restrictions = current_restrictions()
    return (restrictions or {}).get("scope") or {}


def scope_values(dimension):
    """The records of a dimension the current principal is restricted to, or
    None when they are not restricted in it."""
    return current_scope().get(dimension)


def in_scope(dimension, value, optional=False):
    values = scope_values(dimension)
    if values is None:
        return True
    if value in (None, ""):
        return optional
    return value in values


def require_in_scope(dimension, value, optional=False):
    if not in_scope(dimension, value, optional):
        label = SCOPE_DIMENSIONS.get(dimension, dimension)
        raise PermissionDenied(
            f"Your access is limited to assigned {label} records; {value or 'an empty value'} is not one of them")


def require_warehouse_in_scope(warehouse, optional=False):
    """A warehouse named in a request: assigned (when restricted by
    warehouse) and not the consignment warehouse of a customer who is not
    assigned (when restricted by customer)."""
    require_in_scope("warehouse", warehouse, optional)
    if warehouse and warehouse in foreign_consignment_warehouses():
        raise PermissionDenied(
            f"Your access is limited to assigned Customer records; warehouse {warehouse} belongs to another one")


def require_no_record_scope(what):
    """For functions that cannot be limited to assigned records."""
    scope = current_scope()
    if scope:
        labels = " and ".join(SCOPE_DIMENSIONS[dimension] for dimension in scope)
        raise PermissionDenied(f"Your access is limited to assigned {labels} records, and {what} cannot be limited to them")


def _sql_values(values):
    """A SQL list of quoted names. An empty scope becomes (''): no record has
    an empty name, so IN is false and NOT IN is true for every real value."""
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in sorted(values)) or "''"


def scope_sql(dimension, expression, optional=False):
    """A SQL condition limiting `expression` to the assigned records of the
    dimension, or None when the current principal is not restricted in it."""
    values = scope_values(dimension)
    if values is None:
        return None
    condition = f"{expression} IN ({_sql_values(values)})"
    return f"(COALESCE({expression}, '') = '' OR {condition})" if optional else condition


def _scope_rule_sql(doctype, rule, values):
    listed = _sql_values(values)
    when = rule.get("when")
    if rule.get("deny"):
        return "1 = 0"
    if rule.get("self"):
        return f'"{doctype}".name IN ({listed})'
    foreign = _FOREIGN_WAREHOUSES.format(values=listed)
    if rule.get("child"):
        guard = f" AND COALESCE(scope_row.\"{when[0]}\", '') = '{when[1]}'" if when else ""
        outside = (f"IN ({foreign})" if rule.get("consignment") else f"NOT IN ({listed})")
        return " AND ".join(
            f'NOT EXISTS (SELECT 1 FROM "{rule["table"]}" scope_row WHERE scope_row.parent = "{doctype}".name '
            f"AND COALESCE(scope_row.\"{column}\", '') <> '' AND scope_row.\"{column}\" {outside}{guard})"
            for column in rule["columns"])
    column = f'"{doctype}"."{rule["column"]}"'
    if rule.get("consignment"):
        return f"COALESCE({column}, '') NOT IN ({foreign})"
    condition = f"{column} IN ({listed})"
    if rule.get("optional"):
        condition = f"(COALESCE({column}, '') = '' OR {condition})"
    if when:
        condition = f"(COALESCE(\"{doctype}\".\"{when[0]}\", '') <> '{when[1]}' OR {condition})"
    return condition


def scope_conditions(doctype):
    """SQL conditions that limit `doctype` (queried without a table alias) to
    the records inside the current principal's record scope."""
    scope = current_scope()
    rules = RECORD_SCOPES.get(doctype) or {}
    return [_scope_rule_sql(doctype, rule, scope[dimension])
            for dimension in scope for rule in rules.get(dimension, ())]


def foreign_consignment_warehouses(values=None):
    """Consignment warehouses of customers the principal is not assigned to
    (empty when they are not restricted by customer)."""
    values = scope_values("customer") if values is None else values
    if values is None:
        return frozenset()
    from lambda_erp.database import get_db

    return frozenset(row["name"] for row in get_db().sql(_FOREIGN_WAREHOUSES.format(values=_sql_values(values))))


def _scope_rule_violation(rule, values, record):
    when = rule.get("when")
    if rule.get("deny"):
        return "this type of record"
    if rule.get("self"):
        return None if record.get("name") in values else (record.get("name") or "a new record")
    foreign = foreign_consignment_warehouses(values) if rule.get("consignment") else None

    def outside(value):
        if value in (None, ""):
            return False
        return value in foreign if foreign is not None else value not in values

    if rule.get("child"):
        for row in record.get(rule["child"]) or ():
            if when and (row.get(when[0]) or "") != when[1]:
                continue
            for column in rule["columns"]:
                if outside(row.get(column)):
                    return row.get(column)
        return None
    if when and (record.get(when[0]) or "") != when[1]:
        return None
    value = record.get(rule["column"])
    if foreign is not None:
        return value if outside(value) else None
    if value in (None, ""):
        return None if rule.get("optional") else "an empty value"
    return None if value in values else value


def require_record_in_scope(doctype, record):
    """Refuse writing a record (dict, child tables as lists under their keys)
    that lies outside the current principal's record scope: what they could
    not read afterwards, they cannot create or change."""
    scope = current_scope()
    rules = RECORD_SCOPES.get(doctype) or {}
    for dimension in scope:
        for rule in rules.get(dimension, ()):
            outside = _scope_rule_violation(rule, scope[dimension], record)
            if outside:
                raise PermissionDenied(
                    f"Your access is limited to assigned {SCOPE_DIMENSIONS[dimension]} records; "
                    f"{doctype} with {outside} is outside of them")


def record_conditions(doctype):
    """SQL conditions (over the doctype's own columns, the table queried
    without an alias) that restrict a query to the rows the current principal
    may see. Empty when nothing is hidden."""
    restrictions = current_restrictions()
    rule = SENSITIVE_RECORDS.get(doctype)
    conditions = [rule[1]] if restrictions and rule and rule[0] in restrictions["hidden"] else []
    return conditions + scope_conditions(doctype)


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


def require_writable(doctype, action, keeps_hidden_fields=False):
    """Writes need the action. For a document type with fields the principal
    cannot see they are refused, since a record saved from a redacted copy
    would lose or overwrite what was hidden, unless the caller says it keeps
    those fields itself (`keeps_hidden_fields`: the document services do, with
    clean_hidden_input / restore_hidden_values / require_hidden_complete, and
    so does code that builds the document on the server)."""
    require(doctype, action)
    if action in ("create", "update") and not keeps_hidden_fields and has_hidden_fields(doctype):
        raise PermissionDenied(
            f"Your access profile hides fields of {doctype}, so you cannot {action} it")


# -- writing a document whose prices or values are hidden ---------------------
#
# A user who cannot see purchase prices can still receive goods; one who
# cannot see cost can still move stock. They never supply a hidden value: the
# server keeps what is stored and derives the rest (from the order, the price
# list, the valuation). Where a hidden value is needed and nobody with access
# has provided one, the write is refused instead of posting at zero.

# doctype -> [(child key, field, applies(doc, row) or None, derived(doc, row) or None)]
HIDDEN_WRITE_REQUIRED = {}


def register_hidden_write_requirement(doctype, child, field, applies=None, derived=None):
    """Plugin seam: a new line of `child` written by a user who cannot see
    `field` must end up with a non-zero value in it (derived by the server),
    or the write is refused. `applies(doc, row)` limits the rule;
    `derived(doc, row)` can demand more than non-zero."""
    HIDDEN_WRITE_REQUIRED.setdefault(doctype, []).append((child, field, applies, derived))


# A purchase line nobody priced falls back to the item's standard rate, which
# someone who sees the line would notice and correct. Someone who cannot see
# it would not: for them the price has to come from a buying price.
for _doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
    register_hidden_write_requirement(
        _doctype, "items", "rate",
        derived=lambda doc, row: row.get("price_source") not in (None, "", "None", "Standard Rate"))
# Stock that comes in from nowhere needs a value; a transfer takes the value
# it has at the source, an assignment has none.
register_hidden_write_requirement(
    "Stock Entry", "items", "basic_rate",
    lambda doc, row: not row.get("s_warehouse")
    and doc.get("stock_entry_type") not in ("Batch Assignment", "Serial Assignment"))


def _blank(value):
    if value in (None, ""):
        return True
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def hidden_write_plan(doctype):
    """{"" | child key: hidden fields} for a document type the current
    principal may write but not fully see; None when nothing is hidden."""
    restrictions = current_restrictions()
    if not restrictions:
        return None
    plan = {child: hidden_fields(doctype, child, restrictions) for child in SENSITIVE_FIELDS.get(doctype, {})}
    plan = {child: fields for child, fields in plan.items() if fields}
    return plan or None


def clean_hidden_input(doctype, data):
    """The input without the fields hidden from the current principal. Blank
    values for them (a form sends its empty inputs) are dropped; an actual
    value is refused: they cannot set what they cannot see."""
    plan = hidden_write_plan(doctype)
    if not plan or not isinstance(data, dict):
        return data
    sent = []

    def strip(record, fields, where):
        result = {}
        for key, value in record.items():
            if key in fields:
                if not _blank(value):
                    sent.append(f"{where}{key}")
                continue
            result[key] = value
        return result

    cleaned = strip(data, plan.get("", frozenset()), "")
    for child, fields in plan.items():
        if child and isinstance(cleaned.get(child), list):
            cleaned[child] = [strip(dict(row), fields, f"{child}.") if isinstance(row, dict) else row
                              for row in cleaned[child]]
    if sent:
        raise PermissionDenied(
            f"Your access profile hides {', '.join(sorted(set(sent)))} of {doctype}: you cannot set them. "
            f"Leave them out; the stored or derived values are kept")
    return cleaned


def restore_hidden_values(doctype, stored_rows, new_rows, child):
    """Give the lines a restricted user sent back their hidden values from the
    stored lines: the same line (by name, or by position with the same item)
    keeps them. Returns the positions of lines that are new or now name
    another item, which the server must price itself."""
    fields = (hidden_write_plan(doctype) or {}).get(child)
    if not fields:
        return set()
    by_name = {row.get("name"): row for row in stored_rows if row.get("name")}
    fresh = set()
    for position, row in enumerate(new_rows):
        stored = by_name.get(row.get("name")) if row.get("name") else None
        if stored is None and position < len(stored_rows) and not row.get("name"):
            stored = stored_rows[position]
        if stored is None or (stored.get("item_code") or None) != (row.get("item_code") or None):
            fresh.add(position)
            continue
        for field in fields:
            if field in stored:
                row[field] = stored[field]
    return fresh


def require_hidden_complete(doctype, doc, fresh=None):
    """After the server has validated a document written by a restricted
    user: a new line that needs a hidden value must have got one. `fresh` is
    {child key: positions of new lines}; None means every line is new."""
    plan = hidden_write_plan(doctype)
    if not plan:
        return
    for child, field, applies, derived in HIDDEN_WRITE_REQUIRED.get(doctype, ()):
        if field not in plan.get(child, ()):
            continue
        for position, row in enumerate(doc.get(child) or ()):
            if fresh is not None and position not in fresh.get(child, ()):
                continue
            if applies and not applies(doc, row):
                continue
            if _blank(row.get(field)) or (derived and not derived(doc, row)):
                raise PermissionDenied(
                    f"{doctype} row {position + 1} ({row.get('item_code') or 'no item'}): there is no value on record "
                    f"for a field your access profile hides, and you cannot enter one. Create the document from its "
                    f"order, or ask someone who can see these values")


def _scope_undeclared(kind, name):
    """Dimensions the principal is restricted in that this report, dataset or
    metric says nothing about: it cannot be limited, so it is refused."""
    declared = SCOPED_ARTIFACTS.get(kind, {}).get(name, {})
    return [dimension for dimension in current_scope() if dimension not in declared]


def artifact_denied(kind, name):
    restrictions = current_restrictions()
    return _hidden(SENSITIVE_ARTIFACTS.get(kind, {}).get(name), restrictions) or bool(_scope_undeclared(kind, name))


def require_artifact(kind, name):
    if artifact_denied(kind, name):
        undeclared = _scope_undeclared(kind, name)
        if undeclared:
            labels = " and ".join(SCOPE_DIMENSIONS[dimension] for dimension in undeclared)
            raise PermissionDenied(
                f"Your access is limited to assigned {labels} records, and the {kind} {name} cannot be limited to them")
        raise PermissionDenied(f"Your access profile does not allow the {kind} {name}")


def artifact_scope_sql(kind, name):
    """SQL conditions that limit a declared dataset to the current
    principal's record scope (see register_scoped_artifact)."""
    declared = SCOPED_ARTIFACTS.get(kind, {}).get(name, {})
    conditions = []
    for dimension, values in current_scope().items():
        spec = declared.get(dimension)
        if spec is None:
            continue
        listed = _sql_values(values)
        conditions.append(spec.format(values=listed) if "{values}" in spec else f"{spec} IN ({listed})")
    return conditions


def scope_rows(kind, name, rows):
    """The rows of a declared report that lie inside the current principal's
    record scope, by the row column declared per dimension. A row without a
    value in that column is left out. `column@consignment` (customer
    dimension) drops rows in consignment warehouses of other customers."""
    declared = SCOPED_ARTIFACTS.get(kind, {}).get(name, {})
    scope = current_scope()
    for dimension in scope:
        spec = declared.get(dimension)
        if not spec:
            continue
        if spec.endswith("@consignment"):
            column, foreign = spec.split("@")[0], foreign_consignment_warehouses(scope[dimension])
            rows = [row for row in rows if row.get(column) not in foreign]
        else:
            rows = [row for row in rows if row.get(spec) in scope[dimension]]
    return rows


def hidden_artifact_fields(kind, name):
    restrictions = current_restrictions()
    if not restrictions:
        return frozenset()
    fields = SENSITIVE_ARTIFACT_FIELDS.get(kind, {}).get(name, {})
    return frozenset(field for field, cls in fields.items() if cls in restrictions["hidden"])


def redact_rows(kind, name, rows):
    rows = scope_rows(kind, name, rows)
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
        "record_scope": {dimension: sorted(values) for dimension, values in (restrictions.get("scope") or {}).items()},
    }


# -- stored content -----------------------------------------------------------
#
# A chat conversation or a report draft holds what its author was shown. It is
# stamped with the restrictions it was written under, and a restricted reader
# gets it back only if they may see at least as much now. Content without a
# stamp was written without restrictions (or before stamps existed).

def access_stamp(restrictions=None):
    """The current principal's restrictions as stored text; None when
    unrestricted."""
    restrictions = restrictions if restrictions is not None else current_restrictions()
    if not restrictions:
        return None
    return json.dumps({
        "hidden": sorted(restrictions["hidden"]),
        "permissions": restrictions["permissions"],
        "scope": {dimension: sorted(values) for dimension, values in (restrictions.get("scope") or {}).items()},
    }, sort_keys=True)


def _stamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return {"hidden": sorted(SENSITIVITY_CLASSES), "permissions": {"default": [], "doctypes": {}},
                "scope": {dimension: [] for dimension in SCOPE_DIMENSIONS}}
    return parsed


def _reads(stamp, doctype=None):
    rules = stamp.get("permissions") or {}
    allowed = (rules.get("doctypes") or {}).get(doctype, rules.get("default")) if doctype else rules.get("default")
    return allowed is None or "read" in allowed


def stamp_within(inner, outer):
    """Could everything visible under `inner` also be seen under `outer`?
    Both are stamps (text or parsed); None is "unrestricted"."""
    inner, outer = _stamp(inner), _stamp(outer)
    if outer is None:
        return True
    if inner is None:
        return False
    if not set(outer.get("hidden") or ()) <= set(inner.get("hidden") or ()):
        return False
    inner_scope = inner.get("scope") or {}
    for dimension, values in (outer.get("scope") or {}).items():
        if dimension not in inner_scope or not set(inner_scope[dimension]) <= set(values):
            return False
    named = set((inner.get("permissions") or {}).get("doctypes") or {}) | \
        set((outer.get("permissions") or {}).get("doctypes") or {})
    return all(_reads(outer, doctype) or not _reads(inner, doctype) for doctype in [None, *named])


def stored_content_readable(stamp, restrictions=None, use_current=True):
    """May content stamped with `stamp` be given to these restrictions (the
    current principal's by default)?"""
    if restrictions is None and use_current:
        restrictions = current_restrictions()
    return stamp_within(stamp, access_stamp(restrictions) if restrictions else None)


def widen_stamp(stamp, restrictions=None, use_current=True):
    """The stamp for content that now also holds what these restrictions
    allow: the wider of the two, or None (unrestricted) when neither
    contains the other."""
    if restrictions is None and use_current:
        restrictions = current_restrictions()
    now = access_stamp(restrictions) if restrictions else None
    if stamp_within(stamp, now):
        return now
    if stamp_within(now, stamp):
        return stamp
    return None


def restrictions_of_user(user_name):
    """The restrictions of a user by name (for work that is not running as a
    bound principal); None when unrestricted or unknown."""
    from lambda_erp.database import get_db

    row = get_db().get_value("User", user_name, ["access_profile", "record_scope"]) if user_name else None
    return load_restrictions(row.get("access_profile"), row.get("record_scope")) if row else None

