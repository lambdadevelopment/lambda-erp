"""
Base Document class replacing the framework's framework.model.document.Document.

In the reference implementation, every DocType instance is a Document with lifecycle hooks
(validate, before_submit, on_submit, on_cancel, etc.), child table support,
and automatic DB persistence. This module provides the same pattern.
"""

from lambda_erp.utils import _dict, flt, new_name, now
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError, DocumentStatusError
from lambda_erp.hooks import run_hooks


# Document status constants (mirrors the framework's docstatus)
DRAFT = 0
SUBMITTED = 1
CANCELLED = 2


class Document:
    """Base class for all ERP documents.

    Mirrors the framework's Document class with:
    - Attribute-style field access
    - Child table support (items, taxes, etc.)
    - Lifecycle hooks (validate, on_submit, on_cancel)
    - Automatic DB persistence
    - Status tracking via docstatus
    """

    DOCTYPE = None  # Override in subclasses, e.g. "Sales Invoice"
    SUBMITTABLE = False  # Explicit opt-in to Draft/Submitted/Cancelled.
    INPUT_FIELDS = set()  # Explicit transient input fields consumed by plugins.
    CHILD_INPUT_FIELDS = {}
    REQUIRED_FIELDS = ()
    CONDITIONAL_REQUIREMENTS = ()
    CHILD_REQUIREMENTS = {}
    SERVER_MANAGED_FIELDS = ()
    SERVER_MANAGED_DEFAULTS = {}

    CHILD_TABLES = {}  # {"items": ("Sales Invoice Item", SalesInvoiceItem), ...}
    PREFIX = "DOC"  # For auto-naming

    # Master-reference validation — checked on every save(). Each entry is
    # (field_name, master_doctype) at the parent level, or nested per child
    # table key. A typo in e.g. supplier="SUPP-XX5" is caught here rather
    # than silently creating an invoice against a phantom supplier.
    LINK_FIELDS: dict = {}          # {field_name: master_doctype}
    CHILD_LINK_FIELDS: dict = {}    # {child_key: {field_name: master_doctype}}

    # Dynamic-link fields — target doctype is determined at runtime by
    # reading a sibling "type" field. Example: Payment Entry's `party` field
    # points at either Customer or Supplier depending on `party_type`.
    # Shape: {field_name: (type_field, {type_value: master_doctype, ...})}
    DYNAMIC_LINK_FIELDS: dict = {}
    CHILD_DYNAMIC_LINK_FIELDS: dict = {}  # same shape, nested by child table key

    # Account-type direction constraints — check root_type / account_type on
    # linked Account fields so a Sales Invoice can't accidentally post its
    # Income to a random Expense account (GL still balances, P&L is junk).
    # Shape: {field_name: {"root_type": str | list, "account_type": str | list}}
    ACCOUNT_TYPE_CONSTRAINTS: dict = {}
    CHILD_ACCOUNT_TYPE_CONSTRAINTS: dict = {}

    def __init__(self, data=None, **kwargs):
        self._data = _dict(data or {})
        self._data.update(kwargs)
        self._children = {}  # field_name -> list of child dicts
        self._persisted = False
        self._loaded_modified = None

        if not self._data.get("name"):
            self._data["name"] = new_name(self.PREFIX)
        if not self._data.get("docstatus"):
            self._data["docstatus"] = DRAFT
        if not self._data.get("creation"):
            self._data["creation"] = now()

        # Initialize child tables
        for field_name, (child_doctype, child_cls) in self.CHILD_TABLES.items():
            children = self._data.pop(field_name, []) or []
            self._children[field_name] = []
            for i, child in enumerate(children):
                if isinstance(child, dict):
                    child = _dict(child)
                child["parent"] = self._data["name"]
                child["idx"] = child.get("idx", i + 1)
                if not child.get("name"):
                    child["name"] = new_name(f"{self.PREFIX}-ITEM")
                self._children[field_name].append(child)

    # --- Attribute access (mirrors the framework's Document) ---

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        # Check child tables first
        if key in self.__dict__.get("_children", {}):
            return self._children[key]
        data = self.__dict__.get("_data", {})
        return data.get(key)

    def __setattr__(self, key, value):
        if key.startswith("_") or key in ("DOCTYPE", "CHILD_TABLES", "PREFIX"):
            super().__setattr__(key, value)
        else:
            self._data[key] = value

    def __getitem__(self, key):
        if key in self._children:
            return self._children[key]
        return self._data[key]

    def __setitem__(self, key, value):
        if key in self._children:
            self._children[key] = value
        else:
            self._data[key] = value

    def __contains__(self, key):
        return key in self._data or key in self._children

    def get(self, key, default=None):
        if key in self._children:
            return self._children[key]
        return self._data.get(key, default)

    def set(self, key, value):
        if key in self._children:
            self._children[key] = value
        else:
            self._data[key] = value

    def update(self, d):
        for key, value in d.items():
            self.set(key, value)

    def as_dict(self):
        d = _dict(self._data.copy())
        for field_name, children in self._children.items():
            d[field_name] = [_dict(c) if isinstance(c, dict) else c for c in children]
        return d

    @property
    def docstatus(self):
        return self._data.get("docstatus", DRAFT)

    # --- Child table helpers ---

    def append(self, table_name, row=None):
        """Add a row to a child table. Mirrors doc.append('items', {...})."""
        if table_name not in self._children:
            self._children[table_name] = []

        if row is None:
            row = {}
        if isinstance(row, dict):
            row = _dict(row)

        row["parent"] = self._data["name"]
        row["idx"] = len(self._children[table_name]) + 1
        if not row.get("name"):
            row["name"] = new_name(f"{self.PREFIX}-ITEM")

        self._children[table_name].append(row)
        return row

    # --- Lifecycle hooks (override in subclasses) ---

    def validate(self):
        """Called before save/submit. Override to add validation logic."""
        pass

    def before_save(self):
        pass

    def before_submit(self):
        pass

    def on_submit(self):
        """Called after submit. Override to post GL entries, update stock, etc."""
        pass

    def on_cancel(self):
        """Called on cancellation. Override to reverse GL entries, etc."""
        pass

    def _validate_links(self):
        """Check every declared LINK_FIELDS / CHILD_LINK_FIELDS reference
        actually exists in its master table. Runs after the subclass's own
        validate() so that default values populated by e.g. _set_missing_accounts
        are already in place."""
        db = get_db()

        def _label(field: str) -> str:
            return field.replace("_", " ").strip().title()

        def _check(master: str, value, where: str, field: str):
            if not value:
                return
            if not db.exists(master, value):
                raise ValidationError(
                    f"{self.DOCTYPE}: {where}{_label(field)} '{value}' does not "
                    f"exist in {master}"
                )
            if master == 'Customer':
                from lambda_erp.validation import validate_customer_eligibility
                validate_customer_eligibility(self, value)
            if self.get('company') and master in {'Warehouse', 'Account', 'Cost Center'}:
                if db.get_value(master, value, 'company') != self.company:
                    raise ValidationError(f'{self.DOCTYPE}: {where}{_label(field)} must belong to Company {self.company}')

        for field, master in self.LINK_FIELDS.items():
            _check(master, self.get(field), "", field)

        for child_key, fields in self.CHILD_LINK_FIELDS.items():
            for idx, row in enumerate(self.get(child_key) or [], start=1):
                row_get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, None))
                for field, master in fields.items():
                    _check(master, row_get(field), f"row {idx} ", field)

        # Dynamic-link fields: resolve the target master from a sibling
        # type field. Empty values are allowed, but once a dynamic-link value
        # is present its discriminator must resolve to a known master.
        for field, (type_field, type_map) in self.DYNAMIC_LINK_FIELDS.items():
            value = self.get(field)
            if not value:
                continue
            type_value = self.get(type_field)
            master = type_map.get(type_value)
            if not master:
                raise ValidationError(
                    f"{self.DOCTYPE}: {_label(type_field)} '{type_value}' is not valid for "
                    f"{_label(field)}"
                )
            _check(master, value, "", field)

        for child_key, fields in self.CHILD_DYNAMIC_LINK_FIELDS.items():
            for idx, row in enumerate(self.get(child_key) or [], start=1):
                row_get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, None))
                for field, (type_field, type_map) in fields.items():
                    value = row_get(field)
                    if not value:
                        continue
                    type_value = row_get(type_field)
                    master = type_map.get(type_value)
                    if not master:
                        raise ValidationError(
                            f"{self.DOCTYPE}: row {idx} {_label(type_field)} '{type_value}' "
                            f"is not valid for {_label(field)}"
                        )
                    _check(master, value, f"row {idx} ", field)

        # Account-type direction constraints. Runs after the existence checks
        # above, so we know the account row exists. If a constraint fails the
        # underlying GL would still balance, but reports (P&L especially)
        # would be nonsense — e.g. revenue posted to Administrative Expenses.
        def _check_account(account: str, constraint: dict, where: str, field: str):
            info = db.get_value("Account", account, ["root_type", "account_type"])
            if not info:
                return  # link check already caught nonexistent account
            for key, allowed in constraint.items():
                actual = info.get(key)
                allowed_set = {allowed} if isinstance(allowed, str) else set(allowed)
                if actual not in allowed_set:
                    expected = " or ".join(sorted(allowed_set))
                    raise ValidationError(
                        f"{self.DOCTYPE}: {where}{_label(field)} '{account}' has "
                        f"{key}={actual!r}, expected {expected}"
                    )

        for field, constraint in self.ACCOUNT_TYPE_CONSTRAINTS.items():
            value = self.get(field)
            if value:
                _check_account(value, constraint, "", field)

        for child_key, fields in self.CHILD_ACCOUNT_TYPE_CONSTRAINTS.items():
            for idx, row in enumerate(self.get(child_key) or [], start=1):
                row_get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, None))
                for field, constraint in fields.items():
                    value = row_get(field)
                    if value:
                        _check_account(value, constraint, f"row {idx} ", field)

    # --- Persistence ---

    def _check_write_state(self, expected):
        """Check persisted identity/status under the transaction's row lock."""
        db = get_db()
        suffix = ' FOR UPDATE' if db.dialect == 'postgres' else ''
        rows = db.sql(f'SELECT * FROM "{self.DOCTYPE}" WHERE name = ?{suffix}', [self.name])
        current = rows[0] if rows else None
        if current and not self._persisted:
            raise DocumentStatusError(f"{self.DOCTYPE} {self.name} already exists; load it to update a draft, or use a new name")
        if self._persisted and not current:
            raise DocumentStatusError(f"{self.DOCTYPE} {self.name} no longer exists; reload before writing")
        if current:
            if current.get('docstatus', DRAFT) != expected or current.get('discarded'):
                raise DocumentStatusError(f"Cannot write {self.DOCTYPE} {self.name}: stored document is submitted, cancelled or discarded; reload it")
            if current.get('modified') != self._loaded_modified:
                raise DocumentStatusError(f"{self.DOCTYPE} {self.name} changed since it was loaded; reload before writing")

    def save(self):
        """Create a new draft or update a loaded draft, atomically with hooks."""
        if self._data.get('discarded'):
            raise DocumentStatusError('Use discard() to discard a document; saving cannot bypass its dependency checks')
        if self.docstatus != DRAFT:
            raise DocumentStatusError(f"Cannot save {self.DOCTYPE} {self.name}: submitted docs are immutable; cancel and create a new one to amend")
        db = get_db()
        persisted = self._persisted, self._loaded_modified
        try:
            with db.atomic():
                self._check_write_state(DRAFT)
                self._validate_server_managed_fields()
                from lambda_erp.assets.lifecycle import lock_rental_references
                lock_rental_references(self)
                self._data["modified"] = now()
                from lambda_erp.controllers.item_prices import normalize_item_prices
                normalize_item_prices(self)
                self.validate()
                normalize_item_prices(self)
                from lambda_erp.validation import validate_document_requirements
                validate_document_requirements(self)
                self._validate_links()
                self.before_save()
                run_hooks(f"{self.DOCTYPE}:before_save", self)
                self._persist()
                run_hooks(f"{self.DOCTYPE}:after_save", self)
        except Exception:
            self._persisted, self._loaded_modified = persisted
            raise
        return self

    def _validate_server_managed_fields(self):
        """Allow round trips, but only the owning workflow can change progress."""
        if not self.SERVER_MANAGED_FIELDS:
            return
        stored = get_db().get_value(self.DOCTYPE, self.name, list(self.SERVER_MANAGED_FIELDS)) if self._persisted else {}
        for field in self.SERVER_MANAGED_FIELDS:
            current = self.get(field)
            if not self._persisted and current in (None, ''):
                continue
            previous = (stored or {}).get(field) if self._persisted else self.SERVER_MANAGED_DEFAULTS.get(field)
            if current != previous and not (current in (None, '') and previous in (None, '')):
                raise ValidationError(f'{self.DOCTYPE}: {field} is server-managed and cannot be supplied or changed; use the document workflow')

    def submit(self):
        """Validate and post once; quantities and ledgers share one transaction."""
        self._require_submittable()
        if self.docstatus != DRAFT:
            raise DocumentStatusError(f"Cannot submit {self.DOCTYPE} {self.name}: docstatus is {self.docstatus}")
        if self._data.get("discarded"):
            raise DocumentStatusError(f"Cannot submit {self.DOCTYPE} {self.name}: it was discarded.")
        db = get_db()
        snapshot = (_dict(self._data), {key: [_dict(row) for row in rows] for key, rows in self._children.items()}, self._persisted, self._loaded_modified)
        try:
            with db.atomic():
                self._check_write_state(DRAFT)
                from lambda_erp.workflow import lock_workflow_references
                self._validate_server_managed_fields()
                lock_workflow_references(self)
                self._data["modified"] = now()
                from lambda_erp.controllers.item_prices import normalize_item_prices
                normalize_item_prices(self)
                self.validate()
                normalize_item_prices(self)
                from lambda_erp.validation import validate_document_requirements
                validate_document_requirements(self)
                self._validate_links()
                self.before_submit()
                self._data["docstatus"] = SUBMITTED
                self._data["status"] = "Submitted"
                run_hooks(f"{self.DOCTYPE}:before_submit", self)
                self._persist(commit=False)
                self.on_submit()
                from lambda_erp.workflow import refresh_order_progress
                refresh_order_progress(self)
        except Exception:
            self._data, self._children, self._persisted, self._loaded_modified = snapshot
            raise
        run_hooks(f"{self.DOCTYPE}:after_submit", self)
        return self

    def cancel(self):
        """Reverse a submitted document once, including its planning effects."""
        self._require_submittable()
        if self.docstatus != SUBMITTED:
            raise DocumentStatusError(f"Cannot cancel {self.DOCTYPE} {self.name}: docstatus is {self.docstatus}")
        db = get_db()
        snapshot = (_dict(self._data), {key: [_dict(row) for row in rows] for key, rows in self._children.items()}, self._persisted, self._loaded_modified)
        try:
            with db.atomic():
                self._check_write_state(SUBMITTED)
                from lambda_erp.workflow import lock_workflow_references, validate_cancellation, refresh_order_progress
                lock_workflow_references(self)
                from lambda_erp.assets.lifecycle import validate_voucher_release
                validate_voucher_release(self)
                validate_cancellation(self)
                self._data["docstatus"] = CANCELLED
                self._data["status"] = "Cancelled"
                self._data["modified"] = now()
                run_hooks(f"{self.DOCTYPE}:before_cancel", self)
                self._persist(commit=False)
                self.on_cancel()
                refresh_order_progress(self)
        except Exception:
            self._data, self._children, self._persisted, self._loaded_modified = snapshot
            raise
        run_hooks(f"{self.DOCTYPE}:after_cancel", self)
        return self

    def discard(self):
        """Void a draft without deleting its audit trail."""
        if 'discarded' not in get_db()._get_table_columns(self.DOCTYPE):
            raise DocumentStatusError(f'{self.DOCTYPE} does not support discard; use its documented status workflow')
        if self.docstatus != DRAFT:
            raise DocumentStatusError(f"Cannot discard {self.DOCTYPE} {self.name}: only drafts can be discarded; a submitted document must be cancelled")
        with get_db().atomic():
            self._check_write_state(DRAFT)
            from lambda_erp.assets.lifecycle import lock_rental_references, validate_voucher_release, validate_asset_change
            lock_rental_references(self)
            validate_voucher_release(self)
            if self.DOCTYPE == 'Asset':
                validate_asset_change(self, discarding=True)
            self._data["discarded"] = 1
            self._data["status"] = "Discarded"
            self._data["modified"] = now()
            self._persist()
        return self

    def _require_submittable(self):
        if not self.SUBMITTABLE:
            raise DocumentStatusError(f'{self.DOCTYPE} does not support submit/cancel; use its documented status workflow')

    def _persist(self, commit=True):
        """Save document and child tables to database."""
        db = get_db()
        doctype = self.DOCTYPE

        # Build a clean dict with only the parent-level fields
        parent_data = {}
        for key, value in self._data.items():
            if key not in self._children:
                parent_data[key] = value

        # Insert new identities; only loaded/persisted instances may update.
        valid_columns = db._get_table_columns(doctype)
        filtered_data = {k: v for k, v in parent_data.items() if k in valid_columns}

        if self._persisted:
            sets = ", ".join(f'"{k}" = ?' for k in filtered_data if k != "name")
            params = [v for k, v in filtered_data.items() if k != "name"]
            params.append(filtered_data["name"])
            db.conn.execute(f'UPDATE "{doctype}" SET {sets} WHERE name = ?', params)
        else:
            db.insert(doctype, filtered_data)

        # Persist child tables
        for field_name, (child_doctype, child_cls) in self.CHILD_TABLES.items():
            # Delete existing children and re-insert
            db.delete(child_doctype, filters={"parent": self._data["name"]})
            for child in self._children.get(field_name, []):
                child_data = dict(child) if isinstance(child, dict) else child
                db.insert(child_doctype, child_data)

        if commit and not db._in_transaction:
            db.commit()
        self._persisted = True
        self._loaded_modified = self._data.get("modified")

    def reload(self):
        """Reload from database."""
        db = get_db()
        rows = db.get_all(self.DOCTYPE, filters={"name": self.name}, fields=["*"])
        if rows:
            self._data = rows[0]
            self._persisted = True
            self._loaded_modified = self._data.get("modified")
            for field_name, (child_doctype, child_cls) in self.CHILD_TABLES.items():
                children = db.get_all(
                    child_doctype,
                    filters={"parent": self.name},
                    fields=["*"],
                    order_by="idx",
                )
                self._children[field_name] = children

    @classmethod
    def load(cls, name):
        """Load a document from the database by name."""
        db = get_db()
        rows = db.get_all(cls.DOCTYPE, filters={"name": name}, fields=["*"])
        if not rows:
            raise ValidationError(f"{cls.DOCTYPE} {name} not found")

        doc = cls(rows[0])
        doc._persisted = True
        doc._loaded_modified = doc._data.get("modified")
        for field_name, (child_doctype, child_cls) in cls.CHILD_TABLES.items():
            children = db.get_all(
                child_doctype,
                filters={"parent": name},
                fields=["*"],
                order_by="idx",
            )
            doc._children[field_name] = children
        return doc

    def __repr__(self):
        return f"<{self.DOCTYPE}: {self.name}>"

    # --- Utility methods used by controllers ---

    def precision(self, fieldname):
        """Return precision for a field. Default 2 for currency fields."""
        # Simplified - the reference implementation pulls this from DocType metadata
        return 2

    def round_floats_in(self, doc=None, do_not_round_fields=None):
        """Round float values in the document."""
        if doc is None:
            doc = self._data
        if isinstance(doc, dict):
            target = doc
        else:
            target = doc._data if hasattr(doc, "_data") else doc

        for key, value in list(target.items()):
            if isinstance(value, float) and (
                not do_not_round_fields or key not in do_not_round_fields
            ):
                target[key] = flt(value, 2)
