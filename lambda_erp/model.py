"""
Base Document class replacing the framework's framework.model.document.Document.

In the reference implementation, every DocType instance is a Document with lifecycle hooks
(validate, before_submit, on_submit, on_cancel, etc.), child table support,
and automatic DB persistence. This module provides the same pattern.
"""

import copy
from lambda_erp.utils import _dict, flt, new_name, now
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError, DocumentStatusError


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

    def save(self):
        """Validate and save to database.

        Only drafts can be saved. Submitted documents are immutable by design
        — re-running validate() on a submitted doc would recompute totals and
        reset outstanding_amount, silently decoupling the subledger (aging,
        outstanding) from the already-posted GL. Post-submit mutations that
        are genuinely needed (outstanding_amount, billed_qty, modified) go
        through db.set_value directly rather than round-tripping save().
        """
        if self.docstatus != DRAFT:
            raise DocumentStatusError(
                f"Cannot save {self.DOCTYPE} {self.name}: document is "
                f"{'submitted' if self.docstatus == SUBMITTED else 'cancelled'}. "
                f"Submitted docs are immutable; cancel and create a new one to amend."
            )
        self._data["modified"] = now()
        self.validate()
        self._validate_links()
        self.before_save()
        self._persist()
        return self

    def submit(self):
        """Submit the document (docstatus 0 -> 1).

        In the reference implementation, submitting a document is what actually posts GL entries,
        creates stock ledger entries, etc. Draft documents have no effect
        on the ledgers.

        The entire operation (docstatus change + on_submit hooks like GL/stock
        posting) is wrapped in a transaction. If on_submit() fails, the
        docstatus change is rolled back.
        """
        if self.docstatus != DRAFT:
            raise DocumentStatusError(
                f"Cannot submit {self.DOCTYPE} {self.name}: docstatus is {self.docstatus}"
            )

        db = get_db()
        db._in_transaction = True
        self._data["modified"] = now()
        self.validate()
        self.before_submit()
        self._data["docstatus"] = SUBMITTED
        self._data["status"] = "Submitted"
        try:
            self._persist(commit=False)
            self.on_submit()
            db.commit()
        except Exception:
            db.conn.rollback()
            # Restore in-memory state
            self._data["docstatus"] = DRAFT
            self._data["status"] = "Draft"
            raise
        finally:
            db._in_transaction = False
        return self

    def cancel(self):
        """Cancel a submitted document (docstatus 1 -> 2).

        Wrapped in a transaction — if on_cancel() fails (e.g. reversing
        GL entries), the docstatus change is rolled back.
        """
        if self.docstatus != SUBMITTED:
            raise DocumentStatusError(
                f"Cannot cancel {self.DOCTYPE} {self.name}: docstatus is {self.docstatus}"
            )

        db = get_db()
        db._in_transaction = True
        self._data["docstatus"] = CANCELLED
        self._data["status"] = "Cancelled"
        self._data["modified"] = now()
        try:
            self._persist(commit=False)
            self.on_cancel()
            db.commit()
        except Exception:
            db.conn.rollback()
            self._data["docstatus"] = SUBMITTED
            self._data["status"] = "Submitted"
            raise
        finally:
            db._in_transaction = False
        return self

    def _persist(self, commit=True):
        """Save document and child tables to database."""
        db = get_db()
        doctype = self.DOCTYPE

        # Build a clean dict with only the parent-level fields
        parent_data = {}
        for key, value in self._data.items():
            if key not in self._children:
                parent_data[key] = value

        # Upsert parent (only persist columns that exist in the table)
        valid_columns = db._get_table_columns(doctype)
        filtered_data = {k: v for k, v in parent_data.items() if k in valid_columns}

        if db.exists(doctype, self._data["name"]):
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

        if commit:
            db.commit()

    def reload(self):
        """Reload from database."""
        db = get_db()
        rows = db.get_all(self.DOCTYPE, filters={"name": self.name}, fields=["*"])
        if rows:
            self._data = rows[0]
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
