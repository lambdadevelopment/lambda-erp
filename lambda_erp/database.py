"""
Lightweight SQLite database layer replacing the framework's database abstraction.

the framework uses framework.db.sql(), framework.db.get_value(), framework.db.get_all(), etc.
backed by MariaDB. This module provides the same interface on top of SQLite,
so the ported business logic can call db.get_value(...) the same way.

Schema is created from Python table definitions instead of DocType JSON files.
"""

import datetime as _datetime
import os
import sqlite3
import threading
from contextlib import contextmanager
from lambda_erp.utils import _dict


_VALID_JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}


def _journal_mode() -> str:
    """SQLite journal mode for connections, from LAMBDA_ERP_SQLITE_JOURNAL_MODE.

    Defaults to WAL — best for a local-disk DB (concurrent readers alongside a
    writer). But WAL relies on a memory-mapped ``-shm`` file, so it does NOT work
    on a network filesystem (SMB/NFS Azure Files, NFS mounts): the
    ``PRAGMA journal_mode=WAL`` itself fails with "database is locked". A
    deployment whose DB lives on such a share must set
    ``LAMBDA_ERP_SQLITE_JOURNAL_MODE=DELETE`` — rollback-journal mode, which uses
    only the byte-range locks the share does support. A single-writer deployment
    (one replica, one worker) loses nothing by using DELETE. Unknown values fall
    back to WAL. The value is validated against an allowlist before being
    interpolated into the PRAGMA.
    """
    mode = os.environ.get("LAMBDA_ERP_SQLITE_JOURNAL_MODE", "WAL").strip().upper()
    return mode if mode in _VALID_JOURNAL_MODES else "WAL"


def _is_postgres_dsn(db_path) -> bool:
    """A db_path that looks like a Postgres connection URL selects Postgres;
    anything else (a file path or ':memory:') stays on SQLite. This is how
    LAMBDA_ERP_DB doubles as the backend switch — no separate env var."""
    return isinstance(db_path, str) and db_path.startswith(("postgresql://", "postgres://"))


# ---------------------------------------------------------------------------
# Postgres adapter
#
# The whole codebase was written against sqlite3: `?` placeholders,
# `conn.execute(sql, params) -> cursor`, and `sqlite3.Row` rows that support
# BOTH positional (row[0]) and mapping (row["x"], dict(row)) access. Rather
# than rewrite every call site, we make a psycopg connection quack like that:
#   * _PgConn translates `?`->`%s` and forwards execute/executemany/commit/...
#   * _PgRow mirrors sqlite3.Row's dual access so get_value/sql/migrations and
#     the external `db.conn.execute(...)` users keep working untouched.
# ---------------------------------------------------------------------------

class _PgRow:
    """Row that mimics sqlite3.Row: positional and by-name access, dict(row),
    and tuple(row) -> values."""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._cols.index(key)]

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, ValueError, IndexError):
            return default

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)  # tuple(row) -> values, like sqlite3.Row

    def __len__(self):
        return len(self._vals)


def _pg_row_factory(cursor):
    cols = [c.name for c in (cursor.description or [])]

    def make(values):
        return _PgRow(cols, values)

    return make


class _PgConn:
    """Adapts a psycopg (v3) connection to the sqlite3 connection API the code
    uses. autocommit is off so the existing `_in_transaction` + single commit()
    / rollback() flow gives the same atomicity as SQLite's implicit transaction."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        try:
            cur = self._raw.cursor()
            if params:
                # psycopg does %-substitution when params are given, so a literal
                # % must be doubled. No params -> psycopg leaves the SQL
                # untouched, so literal % is already safe and needs no escaping.
                cur.execute(sql.replace("%", "%%").replace("?", "%s"), list(params))
            else:
                cur.execute(sql.replace("?", "%s"))
            return cur
        except Exception:
            # A failed statement leaves the connection in an aborted transaction
            # (autocommit=False); every later query on this pooled/thread-local
            # connection would then fail with InFailedSqlTransaction. Roll back
            # so the connection stays usable, then re-raise the real error.
            self._safe_rollback()
            raise

    def executemany(self, sql, seq_params):
        try:
            cur = self._raw.cursor()
            cur.executemany(sql.replace("%", "%%").replace("?", "%s"),
                            [list(p) for p in seq_params])
            return cur
        except Exception:
            self._safe_rollback()
            raise

    def _safe_rollback(self):
        try:
            self._raw.rollback()
        except Exception:
            pass

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def cursor(self, *args, **kwargs):
        return self._raw.cursor(*args, **kwargs)


class _NullLock:
    """No-op context manager used when SQLite-level locking is sufficient."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Database:
    def __init__(self, db_path=":memory:"):
        # Concurrency model:
        # - File DBs: one sqlite3 connection per thread (thread-local), so the
        #   process can serve many WebSocket users without serializing every
        #   read behind a single Python lock. WAL allows concurrent readers
        #   plus one writer; busy_timeout makes the writer wait briefly
        #   instead of erroring with SQLITE_BUSY.
        # - :memory: DBs (tests): each fresh sqlite connection to ":memory:"
        #   is a separate empty database, so we keep one shared connection
        #   guarded by a Python lock.
        self.db_path = db_path
        self.dialect = "postgres" if _is_postgres_dsn(db_path) else "sqlite"
        # :memory: is a SQLite-only concept (Postgres is always a server).
        self._is_memory = (db_path == ":memory:")
        self._local = threading.local()
        self._col_cache = {}  # doctype -> set(columns); invalidated on ALTER
        self._text_col_cache = {}  # doctype -> set(text columns); invalidated on ALTER
        if self._is_memory:
            self._lock = threading.Lock()
            self._shared_conn = self._open_conn()
            self._shared_in_transaction = False
        else:
            self._lock = _NullLock()
            self._shared_conn = None
            # Open the init-thread connection now so _setup_schema can use it
            # via the self.conn property.
            self._open_conn()
        self._setup_schema()

    def _open_conn(self):
        """Open a new connection (per-backend) with the same per-conn settings.

        For file/Postgres DBs the connection is stored on thread-local state so
        the same thread reuses it; for :memory: it is returned and held as the
        shared connection. Returns the object the `conn` property hands out:
        the raw sqlite3 connection, or a _PgConn wrapper for Postgres.
        """
        conn = self._open_pg_conn() if self.dialect == "postgres" else self._open_sqlite_conn()
        if not self._is_memory:
            self._local.conn = conn
            self._local.in_transaction = False
        return conn

    def _open_sqlite_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA journal_mode={_journal_mode()}")
        conn.execute("PRAGMA foreign_keys=ON")
        # Wait up to 5s for the file lock instead of raising SQLITE_BUSY when
        # another thread is mid-write. Generous for our workload (LLM calls
        # dwarf any DB write).
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _open_pg_conn(self):
        # psycopg is only needed for the Postgres backend; import lazily so a
        # SQLite-only install (tests, local dev) doesn't require the driver.
        import psycopg

        # autocommit off: matches SQLite's implicit transaction so the existing
        # _in_transaction + commit()/rollback() atomicity (GL submit/cancel) is
        # preserved exactly.
        return _PgConn(psycopg.connect(self.db_path, autocommit=False,
                                       row_factory=_pg_row_factory))

    @property
    def conn(self):
        if self._is_memory:
            return self._shared_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_conn()
        return conn

    @property
    def _in_transaction(self):
        if self._is_memory:
            return self._shared_in_transaction
        return getattr(self._local, "in_transaction", False)

    @_in_transaction.setter
    def _in_transaction(self, value):
        if self._is_memory:
            self._shared_in_transaction = value
        else:
            self._local.in_transaction = value

    def _setup_schema(self):
        """Create all core ERP tables.

        In the framework/the reference implementation, tables are auto-generated from DocType JSON.
        Here we define them explicitly, matching the essential columns
        that the business logic depends on.
        """
        stmts = [
            # --- Master data ---
            """CREATE TABLE IF NOT EXISTS "Company" (
                name TEXT PRIMARY KEY,
                company_name TEXT,
                disabled INTEGER DEFAULT 0,
                default_currency TEXT DEFAULT 'USD',
                email TEXT,
                phone TEXT,
                address TEXT,
                city TEXT,
                zip_code TEXT,
                country TEXT,
                tax_id TEXT,
                iban TEXT,
                default_cost_center TEXT,
                round_off_account TEXT,
                round_off_cost_center TEXT,
                default_receivable_account TEXT,
                default_payable_account TEXT,
                default_income_account TEXT,
                default_expense_account TEXT,
                stock_received_but_not_billed TEXT,
                stock_in_hand_account TEXT,
                stock_adjustment_account TEXT,
                default_opening_balance_equity TEXT,
                accumulated_depreciation_account TEXT,
                depreciation_expense_account TEXT,
                default_freight_in_account TEXT,
                default_customs_account TEXT,
                default_exchange_gain_loss_account TEXT,
                default_unrealized_exchange_account TEXT,
                proposal_cover_template TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Account" (
                name TEXT PRIMARY KEY,
                account_name TEXT NOT NULL,
                parent_account TEXT,
                company TEXT,
                root_type TEXT,  -- Asset, Liability, Equity, Income, Expense
                report_type TEXT,  -- Balance Sheet, Profit and Loss
                account_type TEXT,  -- Receivable, Payable, Bank, Cash, Stock, etc.
                account_currency TEXT DEFAULT 'USD',
                is_group INTEGER DEFAULT 0,
                disabled INTEGER DEFAULT 0,
                lft INTEGER DEFAULT 0,
                rgt INTEGER DEFAULT 0,
                FOREIGN KEY (company) REFERENCES "Company"(name)
            )""",

            """CREATE TABLE IF NOT EXISTS "Cost Center" (
                name TEXT PRIMARY KEY,
                cost_center_name TEXT NOT NULL,
                company TEXT,
                parent_cost_center TEXT,
                is_group INTEGER DEFAULT 0,
                disabled INTEGER DEFAULT 0,
                FOREIGN KEY (company) REFERENCES "Company"(name)
            )""",

            """CREATE TABLE IF NOT EXISTS "Customer" (
                name TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                disabled INTEGER DEFAULT 0,
                customer_group TEXT,
                territory TEXT,
                default_currency TEXT DEFAULT 'USD',
                default_price_list TEXT,
                credit_limit REAL DEFAULT 0,
                email TEXT,
                phone TEXT,
                address TEXT,
                city TEXT,
                zip_code TEXT,
                country TEXT,
                tax_id TEXT,
                contact_person TEXT,
                contact_email TEXT,
                contact_phone TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Supplier" (
                name TEXT PRIMARY KEY,
                supplier_name TEXT NOT NULL,
                disabled INTEGER DEFAULT 0,
                supplier_group TEXT,
                default_currency TEXT DEFAULT 'USD',
                email TEXT,
                phone TEXT,
                address TEXT,
                city TEXT,
                zip_code TEXT,
                country TEXT,
                tax_id TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Item" (
                name TEXT PRIMARY KEY,
                item_name TEXT NOT NULL,
                disabled INTEGER DEFAULT 0,
                item_group TEXT,
                stock_uom TEXT DEFAULT 'Nos',
                is_stock_item INTEGER DEFAULT 1,
                is_fixed_asset INTEGER DEFAULT 0,
                valuation_method TEXT DEFAULT 'FIFO',
                default_warehouse TEXT,
                standard_rate REAL DEFAULT 0,
                description TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Warehouse" (
                name TEXT PRIMARY KEY,
                warehouse_name TEXT NOT NULL,
                company TEXT,
                parent_warehouse TEXT,
                is_group INTEGER DEFAULT 0,
                disabled INTEGER DEFAULT 0,
                account TEXT,
                address TEXT,
                city TEXT,
                zip_code TEXT,
                country TEXT,
                FOREIGN KEY (company) REFERENCES "Company"(name)
            )""",

            """CREATE TABLE IF NOT EXISTS "Fiscal Year" (
                name TEXT PRIMARY KEY,
                year_start_date TEXT,
                year_end_date TEXT,
                company TEXT
            )""",

            # --- Tax templates ---
            """CREATE TABLE IF NOT EXISTS "Tax Template" (
                name TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                tax_type TEXT  -- 'Sales' or 'Purchase'
            )""",

            """CREATE TABLE IF NOT EXISTS "Tax Template Detail" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                charge_type TEXT DEFAULT 'On Net Total',
                account_head TEXT,
                rate REAL DEFAULT 0,
                description TEXT,
                idx INTEGER DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Tax Template"(name)
            )""",

            # --- GL Entry (the heart of accounting) ---
            """CREATE TABLE IF NOT EXISTS "GL Entry" (
                name TEXT PRIMARY KEY,
                posting_date TEXT NOT NULL,
                account TEXT NOT NULL,
                party_type TEXT,
                party TEXT,
                cost_center TEXT,
                debit REAL DEFAULT 0,
                credit REAL DEFAULT 0,
                debit_in_account_currency REAL DEFAULT 0,
                credit_in_account_currency REAL DEFAULT 0,
                account_currency TEXT DEFAULT 'USD',
                voucher_type TEXT,
                voucher_no TEXT,
                against_voucher_type TEXT,
                against_voucher TEXT,
                remarks TEXT,
                is_opening TEXT DEFAULT 'No',
                is_cancelled INTEGER DEFAULT 0,
                company TEXT,
                fiscal_year TEXT,
                creation TEXT,
                modified TEXT
            )""",

            # --- Stock Ledger Entry (the heart of inventory) ---
            """CREATE TABLE IF NOT EXISTS "Stock Ledger Entry" (
                name TEXT PRIMARY KEY,
                posting_date TEXT NOT NULL,
                posting_time TEXT DEFAULT '00:00:00',
                item_code TEXT NOT NULL,
                warehouse TEXT NOT NULL,
                actual_qty REAL DEFAULT 0,
                qty_after_transaction REAL DEFAULT 0,
                incoming_rate REAL DEFAULT 0,
                outgoing_rate REAL DEFAULT 0,
                valuation_rate REAL DEFAULT 0,
                stock_value REAL DEFAULT 0,
                stock_value_difference REAL DEFAULT 0,
                voucher_type TEXT,
                voucher_no TEXT,
                voucher_detail_no TEXT,
                batch_no TEXT,
                serial_no TEXT,
                company TEXT,
                is_cancelled INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            # --- Bin (current stock summary per item+warehouse) ---
            """CREATE TABLE IF NOT EXISTS "Bin" (
                name TEXT PRIMARY KEY,
                item_code TEXT NOT NULL,
                warehouse TEXT NOT NULL,
                actual_qty REAL DEFAULT 0,
                ordered_qty REAL DEFAULT 0,
                reserved_qty REAL DEFAULT 0,
                projected_qty REAL DEFAULT 0,
                valuation_rate REAL DEFAULT 0,
                stock_value REAL DEFAULT 0,
                UNIQUE(item_code, warehouse)
            )""",

            # --- Quotation (offer / proposal) ---
            """CREATE TABLE IF NOT EXISTS "Quotation" (
                name TEXT PRIMARY KEY,
                customer TEXT,
                customer_name TEXT,
                transaction_date TEXT,
                valid_till TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                apply_discount_on TEXT DEFAULT 'Grand Total',
                remarks TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Quotation Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                frequency TEXT DEFAULT 'One-time',
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                warehouse TEXT,
                FOREIGN KEY (parent) REFERENCES "Quotation"(name)
            )""",

            # --- Proposal (Sammelofferte) ---
            # A print-only assembly of several independent Quotations into one
            # branded PDF. No docstatus lifecycle that posts anything: it never
            # touches the quotations it references. `discarded` lets a saved
            # proposal be soft-deleted like other documents.
            """CREATE TABLE IF NOT EXISTS "Proposal" (
                name TEXT PRIMARY KEY,
                title TEXT,
                customer TEXT,
                customer_name TEXT,
                company TEXT,
                proposal_date TEXT,
                partner_name TEXT,
                partner_email TEXT,
                cover_letter TEXT,
                appendix_filename TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT,
                FOREIGN KEY (customer) REFERENCES "Customer"(name)
            )""",

            # Each row references one independent Quotation, rendered as a
            # lettered position (A, B, C…) in the combined PDF. position_title /
            # position_blurb are per-proposal copy; is_recommended draws the badge.
            """CREATE TABLE IF NOT EXISTS "Proposal Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                quotation TEXT,
                position_title TEXT,
                position_blurb TEXT,
                is_recommended INTEGER DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Proposal"(name)
            )""",

            # The optional uploaded appendix PDF (e.g. a static price overview),
            # stored as a blob so it survives container restarts (the app disk is
            # ephemeral). 1:1 with a Proposal; kept out of the Proposal row so the
            # generic document CRUD/list never has to serialise bytes.
            """CREATE TABLE IF NOT EXISTS "Proposal Appendix" (
                parent TEXT PRIMARY KEY,
                filename TEXT,
                data BLOB,
                uploaded TEXT,
                FOREIGN KEY (parent) REFERENCES "Proposal"(name)
            )""",

            # --- Sales Order ---
            """CREATE TABLE IF NOT EXISTS "Sales Order" (
                name TEXT PRIMARY KEY,
                customer TEXT,
                customer_name TEXT,
                transaction_date TEXT,
                delivery_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                apply_discount_on TEXT DEFAULT 'Grand Total',
                per_delivered REAL DEFAULT 0,
                per_billed REAL DEFAULT 0,
                remarks TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Sales Order Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                delivered_qty REAL DEFAULT 0,
                billed_qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                warehouse TEXT,
                quotation_item TEXT,
                FOREIGN KEY (parent) REFERENCES "Sales Order"(name)
            )""",

            # --- Purchase Order ---
            """CREATE TABLE IF NOT EXISTS "Purchase Order" (
                name TEXT PRIMARY KEY,
                supplier TEXT,
                supplier_name TEXT,
                transaction_date TEXT,
                schedule_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                apply_discount_on TEXT DEFAULT 'Grand Total',
                per_received REAL DEFAULT 0,
                per_billed REAL DEFAULT 0,
                remarks TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Purchase Order Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                received_qty REAL DEFAULT 0,
                billed_qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                warehouse TEXT,
                FOREIGN KEY (parent) REFERENCES "Purchase Order"(name)
            )""",

            # --- Sales Invoice ---
            """CREATE TABLE IF NOT EXISTS "Sales Invoice" (
                name TEXT PRIMARY KEY,
                customer TEXT,
                customer_name TEXT,
                posting_date TEXT,
                due_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                debit_to TEXT,  -- receivable account
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                rounded_total REAL DEFAULT 0,
                outstanding_amount REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                apply_discount_on TEXT DEFAULT 'Grand Total',
                is_return INTEGER DEFAULT 0,
                return_against TEXT,
                update_stock INTEGER DEFAULT 0,
                is_pos INTEGER DEFAULT 0,
                paid_amount REAL DEFAULT 0,
                against_income_account TEXT,
                sales_order TEXT,
                per_billed REAL DEFAULT 0,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Sales Invoice Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                income_account TEXT,
                cost_center TEXT,
                warehouse TEXT,
                sales_order TEXT,
                sales_order_item TEXT,
                FOREIGN KEY (parent) REFERENCES "Sales Invoice"(name)
            )""",

            # --- Purchase Invoice ---
            """CREATE TABLE IF NOT EXISTS "Purchase Invoice" (
                name TEXT PRIMARY KEY,
                supplier TEXT,
                supplier_name TEXT,
                posting_date TEXT,
                due_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                credit_to TEXT,  -- payable account
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                rounded_total REAL DEFAULT 0,
                outstanding_amount REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                apply_discount_on TEXT DEFAULT 'Grand Total',
                is_return INTEGER DEFAULT 0,
                return_against TEXT,
                update_stock INTEGER DEFAULT 0,
                against_expense_account TEXT,
                purchase_order TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Purchase Invoice Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                expense_account TEXT,
                cost_center TEXT,
                warehouse TEXT,
                purchase_order TEXT,
                purchase_order_item TEXT,
                FOREIGN KEY (parent) REFERENCES "Purchase Invoice"(name)
            )""",

            # --- Payment Entry ---
            """CREATE TABLE IF NOT EXISTS "Payment Entry" (
                name TEXT PRIMARY KEY,
                payment_type TEXT,  -- Receive, Pay, Internal Transfer
                posting_date TEXT,
                company TEXT,
                party_type TEXT,  -- Customer, Supplier
                party TEXT,
                party_name TEXT,
                paid_from TEXT,  -- account
                paid_to TEXT,    -- account
                paid_amount REAL DEFAULT 0,
                received_amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                reference_no TEXT,
                reference_date TEXT,
                cost_center TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Payment Entry Reference" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                reference_doctype TEXT,
                reference_name TEXT,
                total_amount REAL DEFAULT 0,
                outstanding_amount REAL DEFAULT 0,
                allocated_amount REAL DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Payment Entry"(name)
            )""",

            # --- Journal Entry ---
            """CREATE TABLE IF NOT EXISTS "Journal Entry" (
                name TEXT PRIMARY KEY,
                posting_date TEXT,
                company TEXT,
                voucher_type TEXT DEFAULT 'Journal Entry',
                total_debit REAL DEFAULT 0,
                total_credit REAL DEFAULT 0,
                remark TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Journal Entry Account" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                account TEXT NOT NULL,
                party_type TEXT,
                party TEXT,
                cost_center TEXT,
                debit_in_account_currency REAL DEFAULT 0,
                credit_in_account_currency REAL DEFAULT 0,
                debit REAL DEFAULT 0,
                credit REAL DEFAULT 0,
                reference_type TEXT,
                reference_name TEXT,
                FOREIGN KEY (parent) REFERENCES "Journal Entry"(name)
            )""",

            # --- Stock Entry (material movement) ---
            """CREATE TABLE IF NOT EXISTS "Stock Entry" (
                name TEXT PRIMARY KEY,
                stock_entry_type TEXT,  -- Material Receipt, Material Issue, Material Transfer
                posting_date TEXT,
                posting_time TEXT DEFAULT '00:00:00',
                company TEXT,
                from_warehouse TEXT,
                to_warehouse TEXT,
                total_incoming_value REAL DEFAULT 0,
                total_outgoing_value REAL DEFAULT 0,
                value_difference REAL DEFAULT 0,
                total_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Stock Entry Detail" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                s_warehouse TEXT,  -- source
                t_warehouse TEXT,  -- target
                basic_rate REAL DEFAULT 0,
                basic_amount REAL DEFAULT 0,
                valuation_rate REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Stock Entry"(name)
            )""",

            # --- Sales/Purchase Taxes and Charges (child table of transactions) ---
            """CREATE TABLE IF NOT EXISTS "Sales Taxes and Charges" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                parenttype TEXT,
                idx INTEGER DEFAULT 0,
                charge_type TEXT DEFAULT 'On Net Total',
                account_head TEXT,
                description TEXT,
                rate REAL DEFAULT 0,
                tax_amount REAL DEFAULT 0,
                total REAL DEFAULT 0,
                base_tax_amount REAL DEFAULT 0,
                base_total REAL DEFAULT 0,
                included_in_print_rate INTEGER DEFAULT 0,
                add_deduct_tax TEXT DEFAULT 'Add',
                row_id INTEGER
            )""",

            # --- Delivery Note ---
            """CREATE TABLE IF NOT EXISTS "Delivery Note" (
                name TEXT PRIMARY KEY,
                customer TEXT,
                customer_name TEXT,
                posting_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                per_billed REAL DEFAULT 0,
                is_return INTEGER DEFAULT 0,
                return_against TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Delivery Note Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                warehouse TEXT,
                against_sales_order TEXT,
                so_detail TEXT,
                FOREIGN KEY (parent) REFERENCES "Delivery Note"(name)
            )""",

            # --- Purchase Receipt ---
            """CREATE TABLE IF NOT EXISTS "Purchase Receipt" (
                name TEXT PRIMARY KEY,
                supplier TEXT,
                supplier_name TEXT,
                posting_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                per_billed REAL DEFAULT 0,
                is_return INTEGER DEFAULT 0,
                return_against TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Purchase Receipt Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                warehouse TEXT,
                against_purchase_order TEXT,
                po_detail TEXT,
                FOREIGN KEY (parent) REFERENCES "Purchase Receipt"(name)
            )""",

            # --- POS Invoice ---
            """CREATE TABLE IF NOT EXISTS "POS Invoice" (
                name TEXT PRIMARY KEY,
                customer TEXT,
                customer_name TEXT,
                posting_date TEXT,
                company TEXT,
                currency TEXT DEFAULT 'USD',
                conversion_rate REAL DEFAULT 1.0,
                debit_to TEXT,
                total_qty REAL DEFAULT 0,
                total REAL DEFAULT 0,
                net_total REAL DEFAULT 0,
                base_net_total REAL DEFAULT 0,
                base_grand_total REAL DEFAULT 0,
                grand_total REAL DEFAULT 0,
                rounded_total REAL DEFAULT 0,
                total_taxes_and_charges REAL DEFAULT 0,
                paid_amount REAL DEFAULT 0,
                outstanding_amount REAL DEFAULT 0,
                change_amount REAL DEFAULT 0,
                update_stock INTEGER DEFAULT 1,
                is_return INTEGER DEFAULT 0,
                return_against TEXT,
                status TEXT DEFAULT 'Draft',
                docstatus INTEGER DEFAULT 0,
                discarded INTEGER DEFAULT 0,
                remarks TEXT,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "POS Invoice Item" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                description TEXT,
                qty REAL DEFAULT 0,
                uom TEXT DEFAULT 'Nos',
                rate REAL DEFAULT 0,
                price_list_rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                net_rate REAL DEFAULT 0,
                net_amount REAL DEFAULT 0,
                base_rate REAL DEFAULT 0,
                base_amount REAL DEFAULT 0,
                base_net_rate REAL DEFAULT 0,
                base_net_amount REAL DEFAULT 0,
                income_account TEXT,
                cost_center TEXT,
                warehouse TEXT,
                FOREIGN KEY (parent) REFERENCES "POS Invoice"(name)
            )""",

            """CREATE TABLE IF NOT EXISTS "POS Invoice Payment" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                mode_of_payment TEXT,
                account TEXT,
                amount REAL DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "POS Invoice"(name)
            )""",

            # --- Pricing Rule ---
            """CREATE TABLE IF NOT EXISTS "Pricing Rule" (
                name TEXT PRIMARY KEY,
                title TEXT,
                item_code TEXT,
                selling INTEGER DEFAULT 0,
                buying INTEGER DEFAULT 0,
                rate_or_discount TEXT DEFAULT 'Discount Percentage',
                rate REAL DEFAULT 0,
                discount_percentage REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                min_qty REAL DEFAULT 0,
                valid_from TEXT,
                valid_upto TEXT,
                priority INTEGER DEFAULT 0,
                company TEXT,
                enabled INTEGER DEFAULT 1,
                status TEXT DEFAULT 'Active',
                docstatus INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            # --- Budget ---
            """CREATE TABLE IF NOT EXISTS "Budget" (
                name TEXT PRIMARY KEY,
                budget_against TEXT DEFAULT 'Cost Center',
                cost_center TEXT,
                account TEXT,
                fiscal_year TEXT,
                company TEXT,
                budget_amount REAL DEFAULT 0,
                action_if_exceeded TEXT DEFAULT 'Warn',
                status TEXT DEFAULT 'Active',
                docstatus INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Monthly Distribution" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                month TEXT,
                percentage REAL DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Budget"(name)
            )""",

            # --- Subscription ---
            """CREATE TABLE IF NOT EXISTS "Subscription" (
                name TEXT PRIMARY KEY,
                party_type TEXT,
                party TEXT,
                company TEXT,
                start_date TEXT,
                end_date TEXT,
                billing_interval TEXT DEFAULT 'Monthly',
                current_invoice_start TEXT,
                current_invoice_end TEXT,
                status TEXT DEFAULT 'Active',
                docstatus INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Subscription Plan" (
                name TEXT PRIMARY KEY,
                parent TEXT,
                idx INTEGER DEFAULT 0,
                item_code TEXT,
                item_name TEXT,
                qty REAL DEFAULT 1,
                rate REAL DEFAULT 0,
                FOREIGN KEY (parent) REFERENCES "Subscription"(name)
            )""",

            # --- Bank Transaction ---
            """CREATE TABLE IF NOT EXISTS "Bank Transaction" (
                name TEXT PRIMARY KEY,
                bank_account TEXT,
                posting_date TEXT,
                deposit REAL DEFAULT 0,
                withdrawal REAL DEFAULT 0,
                description TEXT,
                reference_number TEXT,
                allocated_amount REAL DEFAULT 0,
                unallocated_amount REAL DEFAULT 0,
                reference_doctype TEXT,
                reference_name TEXT,
                status TEXT DEFAULT 'Unreconciled',
                docstatus INTEGER DEFAULT 0,
                creation TEXT,
                modified TEXT
            )""",

            # --- Chat Sessions ---
            """CREATE TABLE IF NOT EXISTS "Chat Session" (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT 'New Chat',
                user_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""",

            # --- Chat Messages (conversation persistence) ---
            """CREATE TABLE IF NOT EXISTS "Chat Message" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT NOT NULL,
                message_type TEXT DEFAULT 'chat',
                content TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES "Chat Session"(id)
            )""",

            # --- Chat Attachments (PDFs + images uploaded via chat) ---
            """CREATE TABLE IF NOT EXISTS "Chat Attachment" (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES "Chat Session"(id)
            )""",

            # --- Report Drafts (chat-authored analytics reports) ---
            """CREATE TABLE IF NOT EXISTS "Report Draft" (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                definition_json TEXT NOT NULL,
                created_by TEXT,
                source_chat_session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""",

            # --- Authentication ---
            """CREATE TABLE IF NOT EXISTS "User" (
                name TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                enabled INTEGER DEFAULT 1,
                creation TEXT,
                modified TEXT
            )""",

            """CREATE TABLE IF NOT EXISTS "Invite" (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_by TEXT,
                used INTEGER DEFAULT 0,
                creation TEXT,
                FOREIGN KEY (created_by) REFERENCES "User"(name)
            )""",

            # Third-party (Google / Apple) sign-in identities. One User may hold
            # several (password + Google + Apple); a provider account maps to at
            # most one User via the UNIQUE(provider, subject). `subject` is the
            # provider's stable per-user id (OIDC `sub`), so it survives the user
            # changing their provider-side email. Password stays on User (an
            # OAuth-only user just carries a sentinel, non-matchable hash).
            """CREATE TABLE IF NOT EXISTS "User OAuth Identity" (
                name TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                subject TEXT NOT NULL,
                email TEXT,
                creation TEXT,
                UNIQUE(provider, subject),
                FOREIGN KEY (user_name) REFERENCES "User"(name)
            )""",

            """CREATE TABLE IF NOT EXISTS "Settings" (
                key TEXT PRIMARY KEY,
                value TEXT
            )""",

            # Bearer API keys for the programmatic chat API (off by default via
            # the `chat_api_enabled` Settings flag). Tokens are stored hashed
            # (sha256); `key_prefix` is a display-only fragment. `role` bounds the
            # agent's tool access; `session_owner` is the user_id the key's chat
            # sessions are owned under (isolated from human users). See
            # docs/chat-api-plan.md.
            # Per-user API keys (v2): a key BELONGS to a User and can never act
            # above its owner — `role` is only a CAP; the effective role is
            # min(cap, owner's current role) resolved live at auth time, so a
            # demotion or disable applies to existing keys immediately.
            """CREATE TABLE IF NOT EXISTS "Api Key" (
                id TEXT PRIMARY KEY,
                name TEXT,
                user TEXT NOT NULL,
                key_hash TEXT UNIQUE,
                key_prefix TEXT,
                role TEXT DEFAULT 'manager',
                session_owner TEXT,
                created_at TEXT,
                last_used_at TEXT,
                revoked INTEGER DEFAULT 0
            )""",

            # Exchange rates for multi-currency. A lookup carries forward the
            # most recent rate on/before a transaction's date; the rate is then
            # snapshotted onto the document, so editing this table never changes
            # already-posted books. exchange_rate = units of to_currency per 1
            # unit of from_currency (e.g. EUR->USD 1.10 means 1 EUR = 1.10 USD).
            """CREATE TABLE IF NOT EXISTS "Currency Exchange" (
                name TEXT PRIMARY KEY,
                date TEXT,
                from_currency TEXT,
                to_currency TEXT,
                exchange_rate REAL,
                creation TEXT,
                modified TEXT
            )""",
        ]

        for stmt in stmts:
            self.conn.execute(self._ddl(stmt))
        self.conn.commit()
        self._migrate()

    def _ddl(self, stmt: str) -> str:
        """Translate SQLite DDL to the active dialect. No-op for SQLite.

        For Postgres: SQLite's `REAL` is 8-byte (double); Postgres `REAL` is
        only 4-byte, so map to DOUBLE PRECISION to keep float arithmetic
        identical (the validation suite's balances depend on it). And
        `INTEGER PRIMARY KEY AUTOINCREMENT` becomes BIGSERIAL.
        """
        if self.dialect != "postgres":
            return stmt
        import re
        stmt = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
                      "BIGSERIAL PRIMARY KEY", stmt, flags=re.IGNORECASE)
        stmt = re.sub(r"\bAUTOINCREMENT\b", "", stmt, flags=re.IGNORECASE)
        stmt = re.sub(r"\bREAL\b", "DOUBLE PRECISION", stmt, flags=re.IGNORECASE)
        # SQLite's BLOB is Postgres' BYTEA (used for the Proposal appendix PDF).
        stmt = re.sub(r"\bBLOB\b", "BYTEA", stmt, flags=re.IGNORECASE)
        return stmt

    # -----------------------------------------------------------------
    # Migrations
    #
    # Each entry is (version, name, callable). The runner applies each
    # migration exactly once, records it in _SchemaMigrations, and commits.
    # Versions are monotonically increasing integers — do not renumber or
    # remove existing migrations. To add a new one, append the next version.
    # Migrations must be idempotent: they run against both fresh databases
    # (after CREATE TABLE) and existing databases that may already have the
    # new columns from earlier ad-hoc ALTERs, so guard with PRAGMA checks.
    # -----------------------------------------------------------------

    MIGRATIONS = None  # populated just below __init_subclass__ — see end of class

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if column not in self._get_table_columns(table):
            self.conn.execute(self._ddl(f'ALTER TABLE "{table}" ADD COLUMN {column} {definition}'))
            self._col_cache.pop(table, None)
            self._text_col_cache.pop(table, None)

    def _migrate(self):
        """Run each pending migration in order, tracking applied versions."""
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS "_SchemaMigrations" (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
        self.conn.commit()

        applied = {
            row[0]
            for row in self.conn.execute('SELECT version FROM "_SchemaMigrations"').fetchall()
        }

        for version, name, fn in self.MIGRATIONS:
            if version in applied:
                continue
            try:
                fn(self)
                self.conn.execute(
                    'INSERT INTO "_SchemaMigrations" (version, name, applied_at) VALUES (?, ?, ?)',
                    [version, name, _datetime.datetime.utcnow().isoformat(timespec="seconds")],
                )
                self.conn.commit()
            except Exception:
                # Keep the DB usable even if one migration fails (e.g. the
                # column already exists from a prior ad-hoc ALTER on a
                # long-lived database). The CREATE TABLE at startup already
                # covers the happy path; migrations are only needed for drift.
                self.conn.rollback()

    # --- Core query interface (mirrors framework.db) ---

    def sql(self, query, values=None, as_dict=True):
        """Execute raw SQL. Mirrors framework.db.sql().

        For file DBs the lock is a no-op: each thread has its own connection,
        and SQLite's own file-level locking + WAL is enough. For :memory:
        the Python lock prevents concurrent execute() calls on the single
        shared connection (which otherwise raises "bad parameter or other
        API misuse").
        """
        with self._lock:
            cursor = self.conn.execute(query, values or [])
            # Only fetch when the statement produced a result set. SQLite's
            # fetchall() after an INSERT/UPDATE/DELETE harmlessly returns [];
            # psycopg raises ("the last operation didn't produce records"). A
            # NULL cursor.description means no result set on both drivers.
            rows = cursor.fetchall() if cursor.description is not None else []
        if as_dict:
            return [_dict(dict(row)) for row in rows]
        return [tuple(row) for row in rows]

    def get_value(self, doctype, name, fieldname=None, filters=None):
        """Get a single field value. Mirrors framework.db.get_value().

        Usage:
            db.get_value("Customer", "CUST-001", "customer_name")
            db.get_value("Customer", {"customer_group": "Retail"}, "name")
        """
        if fieldname is None:
            fieldname = "name"

        fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
        field_str = self._select_fields(doctype, fields)

        if isinstance(name, dict) or filters:
            filt = name if isinstance(name, dict) else (filters or {})
            where_parts = []
            params = []
            for k, v in filt.items():
                where_parts.append(f'"{k}" = ?')
                params.append(v)
            where = " AND ".join(where_parts) if where_parts else "1=1"
            query = f'SELECT {field_str} FROM "{doctype}" WHERE {where} LIMIT 1'
            rows = self.sql(query, params)
        else:
            query = f'SELECT {field_str} FROM "{doctype}" WHERE name = ? LIMIT 1'
            rows = self.sql(query, [name])

        if not rows:
            return None

        if isinstance(fieldname, (list, tuple)):
            return rows[0]
        return rows[0].get(fieldname)

    def get_all(self, doctype, filters=None, fields=None, order_by=None, limit=None):
        """Get all matching records. Mirrors framework.db.get_all()."""
        if fields is None:
            fields = ["name"]
        if isinstance(fields, str):
            fields = [fields]

        if fields == ["*"]:
            field_str = "*"
        else:
            field_str = self._select_fields(doctype, fields)
        query = f'SELECT {field_str} FROM "{doctype}"'

        params = []
        if filters:
            where_parts = []
            for k, v in filters.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    op, val = v
                    where_parts.append(f'"{k}" {op} ?')
                    params.append(val)
                else:
                    where_parts.append(f'"{k}" = ?')
                    params.append(v)
            query += " WHERE " + " AND ".join(where_parts)

        if order_by:
            query += f" ORDER BY {order_by}"
        if limit:
            query += f" LIMIT {limit}"

        return self.sql(query, params)

    def set_value(self, doctype, name, fieldname, value=None):
        """Set a single field value. Mirrors framework.db.set_value()."""
        if isinstance(fieldname, dict):
            sets = ", ".join(f'"{k}" = ?' for k in fieldname)
            params = list(fieldname.values()) + [name]
        else:
            sets = f'"{fieldname}" = ?'
            params = [value, name]

        with self._lock:
            self.conn.execute(f'UPDATE "{doctype}" SET {sets} WHERE name = ?', params)
            if not self._in_transaction:
                self.conn.commit()

    def exists(self, doctype, name=None, filters=None):
        """Check if a record exists."""
        if name and not filters:
            rows = self.sql(f'SELECT name FROM "{doctype}" WHERE name = ?', [name])
        elif filters:
            where_parts = []
            params = []
            for k, v in filters.items():
                where_parts.append(f'"{k}" = ?')
                params.append(v)
            where = " AND ".join(where_parts)
            rows = self.sql(f'SELECT name FROM "{doctype}" WHERE {where} LIMIT 1', params)
        else:
            return False
        return bool(rows)

    def _get_table_columns(self, doctype):
        """Get column names for a table (cached; invalidated on ALTER)."""
        cached = self._col_cache.get(doctype)
        if cached is not None:
            return cached
        if self.dialect == "postgres":
            rows = self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [doctype],
            ).fetchall()
            cols = {row[0] for row in rows}
        else:
            cursor = self.conn.execute(f'PRAGMA table_info("{doctype}")')
            cols = {row[1] for row in cursor.fetchall()}
        self._col_cache[doctype] = cols
        return cols

    def _get_text_columns(self, doctype):
        """Text/character columns of a table — the ones worth matching a
        free-text search against. Discovered from the live schema so new
        columns become searchable automatically. Cached; invalidated on ALTER."""
        cached = self._text_col_cache.get(doctype)
        if cached is not None:
            return cached
        if self.dialect == "postgres":
            rows = self.conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ?",
                [doctype],
            ).fetchall()
            cols = {
                row[0]
                for row in rows
                if "char" in (row[1] or "").lower() or (row[1] or "").lower() in ("text", "citext")
            }
        else:
            cursor = self.conn.execute(f'PRAGMA table_info("{doctype}")')
            cols = {
                row[1]
                for row in cursor.fetchall()
                if any(t in (row[2] or "").upper() for t in ("CHAR", "CLOB", "TEXT"))
            }
        self._text_col_cache[doctype] = cols
        return cols

    def _select_fields(self, doctype, fields):
        """Build the SELECT column list, selecting only columns that exist and
        padding the rest as NULL. SQLite silently returns an unknown quoted
        identifier as a string literal; Postgres errors. Padding missing fields
        as NULL gives portable behaviour (None for absent columns) and supports
        the cross-doctype field lists the codebase relies on."""
        valid = self._get_table_columns(doctype)
        return ", ".join(f'"{f}"' if f in valid else f'NULL AS "{f}"' for f in fields)

    def insert(self, doctype, doc):
        """Insert a record from a dict. Ignores fields not in the table schema."""
        valid_columns = self._get_table_columns(doctype)
        fields = [f for f in doc.keys() if f in valid_columns]
        if not fields:
            return
        placeholders = ", ".join(["?"] * len(fields))
        field_str = ", ".join(f'"{f}"' for f in fields)
        values = [doc[f] for f in fields]
        with self._lock:
            self.conn.execute(
                f'INSERT INTO "{doctype}" ({field_str}) VALUES ({placeholders})', values
            )
            if not self._in_transaction:
                self.conn.commit()

    def insert_many(self, doctype, docs):
        """Insert multiple records."""
        if not docs:
            return
        fields = list(docs[0].keys())
        placeholders = ", ".join(["?"] * len(fields))
        field_str = ", ".join(f'"{f}"' for f in fields)
        values = [[doc.get(f) for f in fields] for doc in docs]
        self.conn.executemany(
            f'INSERT INTO "{doctype}" ({field_str}) VALUES ({placeholders})', values
        )
        if not self._in_transaction:
            self.conn.commit()

    def delete(self, doctype, name=None, filters=None):
        """Delete a record."""
        with self._lock:
            if name:
                self.conn.execute(f'DELETE FROM "{doctype}" WHERE name = ?', [name])
            elif filters:
                where_parts = []
                params = []
                for k, v in filters.items():
                    where_parts.append(f'"{k}" = ?')
                    params.append(v)
                where = " AND ".join(where_parts)
                self.conn.execute(f'DELETE FROM "{doctype}" WHERE {where}', params)
            if not self._in_transaction:
                self.conn.commit()

    def commit(self):
        with self._lock:
            self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


# Module-level singleton, initialized by setup()
# -----------------------------------------------------------------
# Schema migrations
#
# Each tuple: (version: int, name: str, callable(db: Database) -> None).
# Numbers are monotonic — never renumber or remove applied migrations.
# Add new migrations by appending with the next integer.
# -----------------------------------------------------------------

def _m001_chat_message_session_id(db: "Database") -> None:
    db._add_column_if_missing("Chat Message", "session_id", "TEXT")


def _m002_chat_session_user_id(db: "Database") -> None:
    db._add_column_if_missing("Chat Session", "user_id", "TEXT")


def _m003_transactional_remarks(db: "Database") -> None:
    for table in ("Quotation", "Sales Order", "Purchase Order"):
        db._add_column_if_missing(table, "remarks", "TEXT")


def _m004_master_disabled_flag(db: "Database") -> None:
    for table in ("Company", "Cost Center", "Customer", "Supplier", "Item", "Warehouse"):
        db._add_column_if_missing(table, "disabled", "INTEGER DEFAULT 0")


def _m005_company_stock_in_hand_account(db: "Database") -> None:
    # The field existed in code (purchase_receipt.py / delivery_note.py) but
    # not in the schema. Without it, GL entries were silently skipped because
    # SQLite's quoted-identifier quirk returns None instead of erroring.
    db._add_column_if_missing("Company", "stock_in_hand_account", "TEXT")


def _m006_company_opening_balance_equity(db: "Database") -> None:
    # Added so opening-stock (and in future other opening-balance) flows have
    # a dedicated contra instead of distorting Stock Adjustment or SRBNB.
    db._add_column_if_missing("Company", "default_opening_balance_equity", "TEXT")


def _m007_pos_invoice_return_fields(db: "Database") -> None:
    # POS Invoice had no return flow; add the fields that mirror Sales
    # Invoice so make_pos_return can record is_return/return_against.
    db._add_column_if_missing("POS Invoice", "is_return", "INTEGER DEFAULT 0")
    db._add_column_if_missing("POS Invoice", "return_against", "TEXT")


def _m008_report_draft_source_chat_session_id(db: "Database") -> None:
    db._add_column_if_missing("Report Draft", "source_chat_session_id", "TEXT")


def _m009_company_charge_accounts(db: "Database") -> None:
    """Add the two standard charge accounts (Freight In, Customs & Duties)
    to every existing company and wire them onto the Company defaults.

    For fresh companies going forward this is handled in
    `setup_chart_of_accounts`. This migration backfills on-disk DBs that
    predate the charge-account work so the LLM / UI has somewhere to
    route supplier-invoice freight and customs charges.
    """
    db._add_column_if_missing("Company", "default_freight_in_account", "TEXT")
    db._add_column_if_missing("Company", "default_customs_account", "TEXT")

    companies = db.conn.execute('SELECT name FROM "Company"').fetchall()
    for row in companies:
        company = row[0]
        abbr = company[:4].upper()
        op_ex = f"Operating Expenses - {abbr}"
        op_ex_exists = db.conn.execute(
            'SELECT 1 FROM "Account" WHERE name = ?', [op_ex]
        ).fetchone()
        if not op_ex_exists:
            # Odd shape — skip silently rather than crash the migration.
            continue

        for label, acct_type in (("Freight In", "Chargeable"),
                                 ("Customs & Duties", "Chargeable")):
            acct_name = f"{label} - {abbr}"
            exists = db.conn.execute(
                'SELECT 1 FROM "Account" WHERE name = ?', [acct_name]
            ).fetchone()
            if exists:
                continue
            db.conn.execute(
                'INSERT INTO "Account" (name, account_name, parent_account, '
                'company, root_type, report_type, account_type, '
                'account_currency, is_group) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)',
                [acct_name, label, op_ex, company, "Expense",
                 "Profit and Loss", acct_type,
                 db.conn.execute(
                     'SELECT default_currency FROM "Company" WHERE name = ?',
                     [company],
                 ).fetchone()[0] or "USD"],
            )

        db.conn.execute(
            'UPDATE "Company" SET '
            'default_freight_in_account = COALESCE(default_freight_in_account, ?), '
            'default_customs_account = COALESCE(default_customs_account, ?) '
            'WHERE name = ?',
            [f"Freight In - {abbr}", f"Customs & Duties - {abbr}", company],
        )


def _m010_master_zip_code(db: "Database") -> None:
    # Postal code is free text — values like "8400", "ZH 8400" and "59123" are
    # all valid — so it's TEXT, never numeric.
    for table in ("Company", "Customer", "Supplier", "Warehouse"):
        db._add_column_if_missing(table, "zip_code", "TEXT")
    # Warehouse never had an address block in the schema even though the form
    # exposes an Address field; add it so warehouse address/city/country/zip
    # actually persist (set_value would otherwise error on the missing columns).
    for col in ("address", "city", "country"):
        db._add_column_if_missing("Warehouse", col, "TEXT")


def _m011_payment_entry_currency(db: "Database") -> None:
    # Payment Entry gains a currency + conversion_rate so it can settle a
    # foreign-currency invoice and post realized FX gain/loss.
    db._add_column_if_missing("Payment Entry", "currency", "TEXT DEFAULT 'USD'")
    db._add_column_if_missing("Payment Entry", "conversion_rate", "REAL DEFAULT 1.0")


def _m012_exchange_gain_loss_account(db: "Database") -> None:
    """Add the Exchange Gain/Loss account to every existing company and wire it
    onto Company.default_exchange_gain_loss_account. Mirrors the charge-account
    backfill (_m009); fresh companies get it via setup_chart_of_accounts.
    """
    db._add_column_if_missing("Company", "default_exchange_gain_loss_account", "TEXT")

    companies = db.conn.execute('SELECT name FROM "Company"').fetchall()
    for row in companies:
        company = row[0]
        abbr = company[:4].upper()
        op_ex = f"Operating Expenses - {abbr}"
        if not db.conn.execute('SELECT 1 FROM "Account" WHERE name = ?', [op_ex]).fetchone():
            continue

        acct_name = f"Exchange Gain/Loss - {abbr}"
        if not db.conn.execute('SELECT 1 FROM "Account" WHERE name = ?', [acct_name]).fetchone():
            currency = db.conn.execute(
                'SELECT default_currency FROM "Company" WHERE name = ?', [company]
            ).fetchone()[0] or "USD"
            db.conn.execute(
                'INSERT INTO "Account" (name, account_name, parent_account, company, '
                'root_type, report_type, account_type, account_currency, is_group) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)',
                [acct_name, "Exchange Gain/Loss", op_ex, company, "Expense",
                 "Profit and Loss", "", currency],
            )

        db.conn.execute(
            'UPDATE "Company" SET default_exchange_gain_loss_account = '
            'COALESCE(default_exchange_gain_loss_account, ?) WHERE name = ?',
            [acct_name, company],
        )


def _m013_unrealized_exchange_account(db: "Database") -> None:
    """Add the Unrealized Exchange Gain/Loss account to every existing company
    and wire Company.default_unrealized_exchange_account. Mirrors _m012."""
    db._add_column_if_missing("Company", "default_unrealized_exchange_account", "TEXT")

    companies = db.conn.execute('SELECT name FROM "Company"').fetchall()
    for row in companies:
        company = row[0]
        abbr = company[:4].upper()
        op_ex = f"Operating Expenses - {abbr}"
        if not db.conn.execute('SELECT 1 FROM "Account" WHERE name = ?', [op_ex]).fetchone():
            continue

        acct_name = f"Unrealized Exchange Gain/Loss - {abbr}"
        if not db.conn.execute('SELECT 1 FROM "Account" WHERE name = ?', [acct_name]).fetchone():
            currency = db.conn.execute(
                'SELECT default_currency FROM "Company" WHERE name = ?', [company]
            ).fetchone()[0] or "USD"
            db.conn.execute(
                'INSERT INTO "Account" (name, account_name, parent_account, company, '
                'root_type, report_type, account_type, account_currency, is_group) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)',
                [acct_name, "Unrealized Exchange Gain/Loss", op_ex, company, "Expense",
                 "Profit and Loss", "", currency],
            )

        db.conn.execute(
            'UPDATE "Company" SET default_unrealized_exchange_account = '
            'COALESCE(default_unrealized_exchange_account, ?) WHERE name = ?',
            [acct_name, company],
        )


def _m014_company_iban(db: "Database") -> None:
    """Add Company.iban (bank account for payment instructions / QR-bills)."""
    db._add_column_if_missing("Company", "iban", "TEXT")


# Submittable transactional doctypes — those with a Draft/Submitted/Cancelled
# lifecycle. `discarded` is the soft-delete flag for voided drafts.
_SUBMITTABLE_DOCTYPES = (
    "Quotation", "Sales Order", "Sales Invoice", "Delivery Note",
    "Purchase Order", "Purchase Invoice", "Purchase Receipt",
    "Payment Entry", "Journal Entry", "Stock Entry", "POS Invoice",
)


def _m015_document_discarded(db: "Database") -> None:
    """Add the `discarded` soft-delete flag to every submittable doctype.

    Voiding an unwanted DRAFT sets discarded=1 (status 'Discarded') instead of
    hard-deleting it — the row is kept for the audit trail and hidden from
    default lists. Cancel (for submitted docs) is unaffected."""
    for table in _SUBMITTABLE_DOCTYPES:
        db._add_column_if_missing(table, "discarded", "INTEGER DEFAULT 0")


def _m016_customer_contact_person(db: "Database") -> None:
    """Add a named contact person (Ansprechperson) to the Customer master:
    a free-text name plus their own email and phone, kept separate from the
    company-level email/phone already on the record."""
    db._add_column_if_missing("Customer", "contact_person", "TEXT")
    db._add_column_if_missing("Customer", "contact_email", "TEXT")
    db._add_column_if_missing("Customer", "contact_phone", "TEXT")


def _m017_proposal_cover_template(db: "Database") -> None:
    """Add Company.proposal_cover_template — the default cover-letter text a new
    Proposal (Sammelofferte) pre-fills, so account managers don't retype the
    salutation/intro every time. May contain {placeholders} resolved at build.

    The Proposal / Proposal Item / Proposal Appendix tables themselves are
    created by _setup_schema (CREATE TABLE IF NOT EXISTS runs every startup);
    only this column on the pre-existing Company table needs a migration."""
    db._add_column_if_missing("Company", "proposal_cover_template", "TEXT")


def _m018_quotation_item_frequency(db: "Database") -> None:
    """Add Quotation Item.frequency — the billing cadence of an offer line
    (One-time / Monthly / Quarterly / Half-Yearly / Yearly, matching the
    Subscription billing intervals). One-time and recurring lines are totalled
    separately on the offer; existing rows are NULL, treated as One-time."""
    db._add_column_if_missing("Quotation Item", "frequency", "TEXT")


def _m019_api_keys_per_user(db: "Database") -> None:
    """API keys v2 — per-user credentials (clean break, deliberate).

    v1 keys were org-global objects minted by admins with a free-standing role
    and a synthetic identity; anyone holding the token WAS the key. v2 binds
    every key to a User (self-service creation, effective role capped by the
    owner's live role). Existing v1 rows are DROPPED, not migrated — decided
    2026-07-14: no legacy rules; holders simply re-issue their keys.
    """
    if "user" in db._get_table_columns("Api Key"):
        return  # fresh install — table already has the v2 shape
    db.conn.execute('DROP TABLE "Api Key"')
    db.conn.execute(
        """CREATE TABLE "Api Key" (
            id TEXT PRIMARY KEY,
            name TEXT,
            user TEXT NOT NULL,
            key_hash TEXT UNIQUE,
            key_prefix TEXT,
            role TEXT DEFAULT 'manager',
            session_owner TEXT,
            created_at TEXT,
            last_used_at TEXT,
            revoked INTEGER DEFAULT 0
        )"""
    )
    db._col_cache.pop("Api Key", None)
    db._text_col_cache.pop("Api Key", None)


Database.MIGRATIONS = [
    (1, "chat_message_session_id", _m001_chat_message_session_id),
    (2, "chat_session_user_id", _m002_chat_session_user_id),
    (3, "transactional_remarks", _m003_transactional_remarks),
    (4, "master_disabled_flag", _m004_master_disabled_flag),
    (5, "company_stock_in_hand_account", _m005_company_stock_in_hand_account),
    (6, "company_opening_balance_equity", _m006_company_opening_balance_equity),
    (7, "pos_invoice_return_fields", _m007_pos_invoice_return_fields),
    (8, "report_draft_source_chat_session_id", _m008_report_draft_source_chat_session_id),
    (9, "company_charge_accounts", _m009_company_charge_accounts),
    (10, "master_zip_code", _m010_master_zip_code),
    (11, "payment_entry_currency", _m011_payment_entry_currency),
    (12, "exchange_gain_loss_account", _m012_exchange_gain_loss_account),
    (13, "unrealized_exchange_account", _m013_unrealized_exchange_account),
    (14, "company_iban", _m014_company_iban),
    (15, "document_discarded", _m015_document_discarded),
    (16, "customer_contact_person", _m016_customer_contact_person),
    (17, "proposal_cover_template", _m017_proposal_cover_template),
    (18, "quotation_item_frequency", _m018_quotation_item_frequency),
    (19, "api_keys_per_user", _m019_api_keys_per_user),
]


_db = None


def get_db():
    global _db
    if _db is None:
        _db = Database()
    return _db


def setup(db_path=":memory:"):
    """Initialize the database. Call once at startup."""
    global _db
    _db = Database(db_path)
    return _db
