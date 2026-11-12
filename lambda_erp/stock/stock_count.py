"""Stock Count: compare what is there with what the system says, and correct
the difference with a reason.

    prepare   a draft for one warehouse lists what the system expects per
              item, batch and storage location (system_qty)
    count     the counter enters counted_qty per line and saves the draft;
              lines for stock the system does not know can be added
    post      submitting books the differences into the stock ledger, with
              the count as the voucher and its reason on record

Counting and posting are separate on purpose. Saving a draft changes no
stock, so someone who may create and update a Stock Count but not submit it
can hand in a count without being able to correct stock.

A count corrects quantities from now on. It does not rewrite earlier
documents: where the goods went is not something a count can know.

The document carries quantities only, no cost: differences are valued at the
item's current moving-average cost in the warehouse.

See docs/stock-counts.md.
"""
from lambda_erp.accounting.general_ledger import make_gl_entries, make_reverse_gl_entries
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document
from lambda_erp.stock.stock_ledger import (
    get_stock_details, make_cancel_sl_entries, make_sl_entries, reverse_stock_sles, stock_account_for,
)
from lambda_erp.utils import _dict, flt, nowdate

EPSILON = 1e-9


def _key(row):
    return (row.get("item_code"), str(row.get("batch_no") or ""), str(row.get("location") or ""))


def _system_quantities(warehouse):
    return {(row["item_code"], row["batch_no"] or "", row["location"] or ""): flt(row["actual_qty"])
            for row in get_stock_details(warehouse=warehouse)}


class StockCount(Document):
    DOCTYPE = "Stock Count"
    SUBMITTABLE = True
    CHILD_TABLES = {"items": ("Stock Count Item", None)}
    PREFIX = "SCNT"
    REQUIRED_FIELDS = ("warehouse", "items")
    CHILD_REQUIREMENTS = {"items": {"required": ["item_code"]}}
    CONDITIONAL_REQUIREMENTS = (
        "One count covers one warehouse. Use the prepare_stock_count action to create the draft with a line per item, "
        "batch and storage location and the quantity the system expects (system_qty).",
        "Enter counted_qty on the lines that were counted and save; leave it empty on lines that were not counted "
        "(they are not changed). Add a line for stock the system does not know (system_qty is filled in). A new batch "
        "on such a line needs expiry_date when the item has expiry dates.",
        "Saving a draft changes no stock. Submitting books counted_qty - system_qty per line into the stock ledger "
        "and needs a reason. It is refused when stock on a counted line has moved since the count was prepared: "
        "refresh the count (prepare_stock_count with stock_count) and check those lines again.",
        "A count does not change earlier documents. Do not use it to record where goods are believed to have gone.",
    )
    LINK_FIELDS = {"warehouse": "Warehouse", "company": "Company"}
    CHILD_LINK_FIELDS = {"items": {"item_code": "Item"}}

    def validate(self):
        db = get_db()
        warehouse = db.get_value("Warehouse", self.warehouse, ["company", "is_group"]) if self.warehouse else None
        if not warehouse or flt(warehouse.get("is_group")):
            raise ValidationError("Stock Count: Warehouse must be a warehouse that holds stock")
        self._data["company"] = warehouse.get("company")
        if not self.posting_date:
            self.posting_date = nowdate()
        if not self.get("items"):
            raise ValidationError("At least one item is required")
        system = _system_quantities(self.warehouse)
        seen = set()
        counted = differences = 0
        for idx, row in enumerate(self.get("items"), 1):
            if not row.get("item_code"):
                raise ValidationError(f"Stock Count row {idx}: Item Code is required")
            item = db.get_value("Item", row["item_code"], ["item_name", "is_stock_item"])
            if item and not flt(item.get("is_stock_item")):
                raise ValidationError(f"Stock Count row {idx}: {row['item_code']} is not a stock item")
            for field in ("batch_no", "location"):
                row[field] = str(row.get(field) or "").strip() or None
            key = _key(row)
            if key in seen:
                raise ValidationError(
                    f"Stock Count row {idx}: {row['item_code']}"
                    + (f" batch {row['batch_no']}" if row["batch_no"] else "")
                    + (f" at {row['location']}" if row["location"] else "") + " is listed twice")
            seen.add(key)
            row["warehouse"] = self.warehouse
            if not row.get("item_name") and item:
                row["item_name"] = item.get("item_name")
            if row.get("system_qty") in (None, ""):
                row["system_qty"] = system.get(key, 0.0)  # a line added by hand
            row["system_qty"] = flt(row["system_qty"])
            if row.get("counted_qty") in (None, ""):
                row["counted_qty"] = None
                row["difference_qty"] = None
                continue
            try:
                quantity = float(row["counted_qty"])
            except (TypeError, ValueError):
                raise ValidationError(f"Stock Count row {idx}: Counted Qty must be a number")
            if quantity < 0 or quantity != quantity or quantity == float("inf"):
                raise ValidationError(f"Stock Count row {idx}: Counted Qty cannot be negative")
            row["counted_qty"] = quantity
            row["difference_qty"] = quantity - row["system_qty"]
            counted += 1
            differences += 1 if abs(row["difference_qty"]) > EPSILON else 0
        self._data["counted_lines"] = counted
        self._data["difference_lines"] = differences

    def before_submit(self):
        if not str(self._data.get("reason") or "").strip():
            raise ValidationError("Stock Count: a reason is required to post a count")
        if not any(row.get("counted_qty") is not None for row in self.get("items")):
            raise ValidationError("Stock Count: no line has a counted quantity")
        system = _system_quantities(self.warehouse)
        moved = []
        for idx, row in enumerate(self.get("items"), 1):
            if row.get("counted_qty") is None:
                continue
            now_there = system.get(_key(row), 0.0)
            if abs(now_there - flt(row.get("system_qty"))) > EPSILON:
                moved.append(f"row {idx} ({row['item_code']}"
                             + (f", batch {row['batch_no']}" if row.get("batch_no") else "")
                             + f": {flt(row.get('system_qty')):g} when prepared, {now_there:g} now)")
        if moved:
            raise ValidationError(
                "Stock has moved since this count was prepared: " + "; ".join(moved)
                + ". Refresh the count and check these lines again before posting")

    def on_submit(self):
        entries = []
        for row in self.get("items"):
            difference = flt(row.get("difference_qty")) if row.get("counted_qty") is not None else 0
            if abs(difference) <= EPSILON:
                continue
            entries.append(_dict(
                item_code=row["item_code"], warehouse=self.warehouse, actual_qty=difference,
                # Valued at what the item costs here now; a count knows quantities, not prices.
                incoming_rate=0, outgoing_rate=0,
                batch_no=row.get("batch_no"), location=row.get("location"), expiry_date=row.get("expiry_date"),
                # For a serial-tracked item: the numbers found in addition, or missing.
                serial_no=row.get("serial_no"),
                allow_unassigned_serial=difference < 0 and not row.get("serial_no"),
                # Stock without a batch / location (from before tracking) can be counted away, never added.
                allow_unassigned_batch=difference < 0 and not row.get("batch_no"),
                allow_unassigned_location=difference < 0 and not row.get("location"),
                voucher_type=self.DOCTYPE, voucher_no=self.name, voucher_detail_no=row.get("name"),
                posting_date=self.posting_date, company=self.company))
        # Take out before putting in, so a correction between two batches or
        # locations of one item never needs more than is there.
        entries.sort(key=lambda entry: entry["actual_qty"] > 0)
        if entries:
            make_sl_entries(entries)
        gl_entries = self._get_gl_entries()
        if gl_entries:
            make_gl_entries(gl_entries)
        self._data["status"] = "Posted"
        self._persist(commit=False)

    def on_cancel(self):
        rows = get_db().get_all("Stock Ledger Entry", filters={
            "voucher_type": self.DOCTYPE, "voucher_no": self.name, "is_cancelled": 0}, fields=["*"])
        reversals = reverse_stock_sles(rows)
        for row in reversals:
            # Reverse at the value that was posted, whatever the cost is now.
            row["incoming_rate_is_explicit"] = True
            row["outgoing_rate_is_explicit"] = True
        # Put back before taking out again.
        reversals.sort(key=lambda entry: entry["actual_qty"] < 0)
        if reversals:
            make_cancel_sl_entries(reversals)
        make_reverse_gl_entries(voucher_type=self.DOCTYPE, voucher_no=self.name)

    def _get_gl_entries(self):
        """Stock account against the stock adjustment account, from the
        values the stock ledger posted."""
        db = get_db()
        rows = db.sql('SELECT COALESCE(SUM(stock_value_difference), 0) AS value FROM "Stock Ledger Entry" '
                      'WHERE voucher_type = ? AND voucher_no = ? AND is_cancelled = 0', [self.DOCTYPE, self.name])
        value = flt(rows[0]["value"], 2) if rows else 0
        if not value:
            return []
        account = stock_account_for(self.company, self.warehouse)
        contra = db.get_value("Company", self.company, "stock_adjustment_account")
        if not account or not contra:
            raise ValidationError("A stock account and the company's stock adjustment account are required to post a count")
        cost_center = db.get_value("Company", self.company, "default_cost_center")
        common = dict(voucher_type=self.DOCTYPE, voucher_no=self.name, posting_date=self.posting_date,
                      company=self.company, cost_center=cost_center,
                      remarks=f"Stock count {self.name}: {self._data.get('reason')}")

        def leg(name, amount):
            return _dict(account=name, debit=max(0, amount), credit=max(0, -amount),
                         debit_in_account_currency=max(0, amount), credit_in_account_currency=max(0, -amount), **common)

        return [leg(account, value), leg(contra, -value)]


def prepare_stock_count(warehouse, *, location=None, item_code=None, item_prefix=None, include_empty=False,
                        stock_count=None):
    """A draft Stock Count for a warehouse with a line per item, batch and
    storage location and the quantity the system expects, optionally limited
    to one storage location, one item or item codes starting with a prefix.

    With `stock_count`, refreshes that draft instead: system quantities are
    brought up to date, counted quantities are kept, and stock the system
    now knows but the draft did not list is added.
    """
    db = get_db()

    def wanted(row):
        if location is not None and (row["location"] or "") != (location or ""):
            return False
        if item_code and row["item_code"] != item_code:
            return False
        if item_prefix and not str(row["item_code"]).startswith(str(item_prefix)):
            return False
        return include_empty or abs(flt(row["actual_qty"])) > EPSILON

    if stock_count:
        doc = StockCount.load(stock_count)
        if doc.docstatus != 0 or doc.get("discarded"):
            raise ValidationError("Only a draft count can be refreshed")
        system = _system_quantities(doc.warehouse)
        listed = set()
        for row in doc.get("items"):
            row["system_qty"] = system.get(_key(row), 0.0)
            listed.add(_key(row))
        scoped = bool(location is not None or item_code or item_prefix)
        for row in get_stock_details(warehouse=doc.warehouse):
            key = (row["item_code"], row["batch_no"] or "", row["location"] or "")
            if key not in listed and abs(flt(row["actual_qty"])) > EPSILON and (not scoped or wanted(row)):
                doc.append("items", _dict(item_code=row["item_code"], batch_no=row["batch_no"] or None,
                                          location=row["location"] or None, system_qty=flt(row["actual_qty"])))
        return doc

    if not db.exists("Warehouse", warehouse):
        raise ValidationError(f"Warehouse {warehouse} not found")
    rows = [row for row in get_stock_details(warehouse=warehouse) if wanted(row)]
    if not rows:
        raise ValidationError(f"There is no stock to count in warehouse {warehouse} for this selection")
    doc = StockCount(warehouse=warehouse, posting_date=nowdate())
    for row in sorted(rows, key=lambda r: (r["location"] or "", r["item_code"], r["batch_no"] or "")):
        expiry = db.get_value("Batch", {"item_code": row["item_code"], "batch_no": row["batch_no"]}, "expiry_date") \
            if row["batch_no"] else None
        doc.append("items", _dict(item_code=row["item_code"], batch_no=row["batch_no"] or None,
                                  location=row["location"] or None, expiry_date=expiry,
                                  system_qty=flt(row["actual_qty"])))
    return doc
