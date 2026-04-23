"""
Historical business simulator.

Walks business days between two dates (skipping weekends and country holidays)
and generates a realistic stream of documents:

  Quotation -> (80% Lost | 20% Sales Order) -> Delivery Note(s) -> Sales Invoice -> Payment Entry
  (Reorder threshold) -> Purchase Order -> Purchase Receipt -> Purchase Invoice -> Payment Entry

Flow timing is randomized within business-day windows, with monthly seasonality
and year-over-year growth applied to the quotation arrival rate. A seeded RNG
makes runs reproducible.

Partial flows:
  - 10% of sales orders split across two Delivery Notes
  - 15% of sales invoices paid in two Payment Entries
  -  5% of sales invoices remain outstanding at end of run
"""

import math
import random
from collections import defaultdict
from datetime import date, timedelta

import holidays as pyholidays

from lambda_erp.database import get_db
from lambda_erp.utils import _dict, flt, getdate, add_days
from lambda_erp.stock.stock_ledger import get_stock_balance


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MONTH_SEASONALITY = {
    1: 0.85, 2: 0.90, 3: 1.00,
    4: 1.05, 5: 1.10, 6: 0.85,
    7: 0.70, 8: 0.75, 9: 1.05,
    10: 1.15, 11: 1.20, 12: 1.25,
}

ANNUAL_GROWTH = 0.15
BASE_QUOTES_PER_DAY = 2.5

QUOTE_CONVERSION_RATE = 0.20
DAYS_QUOTE_TO_DECISION = (3, 15)
DAYS_SO_TO_DELIVERY = (2, 10)
DAYS_DN_TO_INVOICE = (0, 3)
DAYS_PARTIAL_DELIVERY_GAP = (3, 10)
DAYS_INVOICE_NET = 30
DAYS_PAYMENT_VARIANCE = (-5, 20)
DAYS_SECOND_PAYMENT_GAP = (5, 30)

DAYS_PO_TO_RECEIPT = (3, 10)
DAYS_PR_TO_INVOICE = (0, 2)
DAYS_PI_NET = 30
DAYS_PI_PAYMENT_VARIANCE = (0, 15)

PARTIAL_DELIVERY_PCT = 0.10
PARTIAL_PAYMENT_PCT = 0.15
OUTSTANDING_INVOICE_PCT = 0.05


# ---------------------------------------------------------------------------
# Master data (same universe as seed_demo, plus reorder parameters)
# ---------------------------------------------------------------------------

CUSTOMERS = [
    dict(name="CUST-001", customer_name="Riverside Manufacturing", customer_group="Commercial",
         email="orders@riverside-mfg.com", phone="+1-555-0101", address="42 River Road",
         city="Portland", country="US", tax_id="US-RM-78234"),
    dict(name="CUST-002", customer_name="Summit Logistics", customer_group="Commercial",
         email="procurement@summitlog.com", phone="+1-555-0102", address="880 Summit Ave",
         city="Denver", country="US", tax_id="US-SL-44521"),
    dict(name="CUST-003", customer_name="Crescent Healthcare", customer_group="Premium",
         email="supply@crescenthc.org", phone="+1-555-0103", address="15 Crescent Blvd",
         city="Austin", country="US", tax_id="US-CH-91037"),
    dict(name="CUST-004", customer_name="Horizon Energy Solutions", customer_group="Commercial",
         email="purchasing@horizonenergy.com", phone="+1-555-0104", address="3200 Energy Pkwy",
         city="Houston", country="US"),
    dict(name="CUST-005", customer_name="Lakeside Construction", customer_group="Commercial",
         email="info@lakesideconstruction.com", phone="+1-555-0105", address="77 Lakeview Dr",
         city="Chicago", country="US"),
    dict(name="CUST-006", customer_name="Pine Valley Schools", customer_group="Government",
         email="facilities@pinevalleysd.edu", phone="+1-555-0106", address="1 School Lane",
         city="Sacramento", country="US"),
    dict(name="CUST-007", customer_name="Redstone Automotive", customer_group="Premium",
         email="parts@redstoneauto.com", phone="+1-555-0107", address="500 Motor Way",
         city="Detroit", country="US"),
    dict(name="CUST-008", customer_name="Clearwater Foods", customer_group="Commercial",
         email="ops@clearwaterfoods.com", phone="+1-555-0108", address="22 Harbor St",
         city="Seattle", country="US"),
    dict(name="CUST-009", customer_name="Bridgeport Electronics", customer_group="Commercial",
         email="sourcing@bridgeportelec.com", phone="+1-555-0109", address="150 Circuit Ave",
         city="San Jose", country="US"),
    dict(name="CUST-010", customer_name="Granite Peak Mining", customer_group="Premium",
         email="supply@granitepeak.com", phone="+1-555-0110", address="9 Mine Rd",
         city="Salt Lake City", country="US"),
]

SUPPLIERS = [
    dict(name="SUPP-001", supplier_name="Atlas Supply Co",
         email="sales@atlassupply.com", phone="+1-555-0201", address="100 Industrial Blvd",
         city="Cleveland", country="US", tax_id="US-AS-55123"),
    dict(name="SUPP-002", supplier_name="Northern Materials",
         email="orders@northernmat.com", phone="+1-555-0202", address="45 Nordic Way",
         city="Minneapolis", country="US", tax_id="US-NM-62890"),
    dict(name="SUPP-003", supplier_name="Keystone Fasteners",
         email="wholesale@keystonefast.com", phone="+1-555-0203", address="88 Bolt St",
         city="Pittsburgh", country="US"),
    dict(name="SUPP-004", supplier_name="Delta Fluid Systems",
         email="sales@deltafluid.com", phone="+1-555-0204", address="200 Hydraulic Dr",
         city="Birmingham", country="US"),
    dict(name="SUPP-005", supplier_name="Ironclad Metals",
         email="trade@ironcladmetals.com", phone="+1-555-0205", address="12 Forge Lane",
         city="Gary", country="US"),
]

# (code, name, uom, sell_price, cost_factor, reorder_level, reorder_qty)
STOCK_ITEMS = [
    ("ITEM-001", "Bolt Pack M8", "Nos", 100, 0.60, 80, 400),
    ("ITEM-002", "Gasket Set K2", "Nos", 250, 0.65, 60, 250),
    ("ITEM-003", "Bearing Assembly Pro", "Nos", 500, 0.62, 40, 150),
    ("ITEM-004", "Steel Flange DN50", "Nos", 85, 0.58, 80, 350),
    ("ITEM-005", "Copper Tube 15mm", "Meter", 12, 0.55, 200, 1000),
    ("ITEM-006", "Hydraulic Hose 3/4in", "Meter", 35, 0.60, 120, 500),
    ("ITEM-007", "Air Filter Cartridge", "Nos", 45, 0.58, 100, 400),
    ("ITEM-008", "Weld Rod E7018 5kg", "Box", 60, 0.62, 60, 200),
    ("ITEM-009", "Safety Valve DN25", "Nos", 320, 0.64, 30, 120),
    ("ITEM-010", "O-Ring Kit Imperial", "Set", 28, 0.55, 100, 400),
    ("ITEM-011", "Stainless Sheet 2mm", "Sheet", 190, 0.63, 40, 150),
    ("ITEM-012", "Cable Tray 300mm", "Meter", 42, 0.60, 80, 300),
    ("ITEM-013", "Pressure Gauge 0-10bar", "Nos", 75, 0.58, 60, 200),
    ("ITEM-014", "Thermal Insulation Wrap", "Roll", 110, 0.62, 40, 150),
    ("ITEM-015", "Anchor Bolt Set M12", "Set", 55, 0.58, 80, 300),
]

# (code, name, uom, sell_price)
SERVICE_ITEMS = [
    ("SVC-001", "Engineering Consultation", "Hour", 150),
    ("SVC-002", "Field Setup Service", "Hour", 200),
    ("SVC-003", "Calibration Service", "Hour", 175),
    ("SVC-004", "Welding Inspection", "Hour", 120),
    ("SVC-005", "Project Management", "Hour", 250),
]


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class HistoricalSimulator:
    def __init__(self, company, start, end, seed=42, intensity=1.0, country="US", log=True):
        self.company = company
        self.abbr = company[:4].upper()
        self.warehouse = f"Main Warehouse - {self.abbr}"
        self.start = getdate(start)
        self.end = getdate(end)
        if self.end < self.start:
            raise ValueError("end_date must be >= start_date")

        self.base_year = self.start.year
        self.intensity = intensity
        self.rng = random.Random(seed)
        self.holidays = pyholidays.country_holidays(country)
        # Log progress to stdout by default — a fresh container boot runs
        # ~3 minutes of simulation and `docker compose up` users otherwise
        # see no sign of life. Flip off for test suites that want silence.
        self.log_enabled = bool(log)

        self.events: dict[date, list] = defaultdict(list)
        self.reorder_info: dict[str, dict] = {}
        self.stats: dict[str, int] = defaultdict(int)

        self._customer_names: list[str] = []
        self._stock_item_codes: list[str] = []
        self._service_item_codes: list[str] = []
        self._item_prices: dict[str, float] = {}

    def _log(self, msg: str) -> None:
        if self.log_enabled:
            print(f"[sim] {msg}", flush=True)

    # ----- public -----

    def run(self, simulate_activity: bool = True):
        """Seed master data, and optionally simulate transactional activity.

        With simulate_activity=False this behaves as a fast master-data seeder
        (customers, suppliers, items, warehouse only) — useful for new admins
        who want an empty ERP to start entering real data. With True (default)
        it also seeds opening stock and walks every business day in the range
        to produce ~3 years of simulated transactions.
        """
        import time

        t0 = time.monotonic()
        self._log(f"seeding masters for {self.company}")
        self._seed_masters()

        if not simulate_activity:
            self._log(f"masters seeded in {time.monotonic() - t0:.1f}s (activity skipped)")
            return dict(self.stats)

        self._log("seeding opening stock")
        self._seed_opening_stock()

        total_days = (self.end - self.start).days + 1
        self._log(f"simulating {total_days} days ({self.start} -> {self.end})")
        last_logged_month = (self.start.year, self.start.month - 1)
        phase_start = time.monotonic()

        d = self.start
        while d <= self.end:
            if self._is_business_day(d):
                self._run_scheduled_events(d)
                self._maybe_generate_quotations(d)
                self._check_reorder_points(d)

            # One progress line per calendar month of simulated time so a
            # `docker compose up` watcher sees steady progress over ~36
            # lines for a 3-year run.
            if (d.year, d.month) != last_logged_month:
                last_logged_month = (d.year, d.month)
                pct = int(100 * ((d - self.start).days + 1) / total_days)
                self._log(
                    f"  {d:%Y-%m}  [{pct:3d}%]  "
                    f"qtn={self.stats.get('quotations', 0)}  "
                    f"sinv={self.stats.get('sales_invoices', 0)}  "
                    f"pinv={self.stats.get('purchase_invoices', 0)}  "
                    f"pay={self.stats.get('payments', 0)}"
                )
            d += timedelta(days=1)

        self._log(
            f"done in {time.monotonic() - phase_start:.1f}s | "
            f"qtn={self.stats.get('quotations', 0)}  "
            f"sinv={self.stats.get('sales_invoices', 0)}  "
            f"pinv={self.stats.get('purchase_invoices', 0)}  "
            f"pay={self.stats.get('payments', 0)}"
        )
        return dict(self.stats)

    # ----- calendar helpers -----

    def _is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def _add_business_days(self, d: date, n: int) -> date:
        cur = d
        while n > 0:
            cur += timedelta(days=1)
            if self._is_business_day(cur):
                n -= 1
        return cur

    def _next_business_day(self, d: date) -> date:
        while not self._is_business_day(d):
            d += timedelta(days=1)
        return d

    # ----- event queue -----

    def _schedule(self, on_day: date, callback):
        if on_day > self.end:
            return
        on_day = self._next_business_day(on_day)
        if on_day > self.end:
            return
        self.events[on_day].append(callback)

    def _run_scheduled_events(self, day: date):
        events = self.events.pop(day, [])
        for evt in events:
            try:
                evt(day)
            except Exception as e:
                self.stats["event_errors"] += 1
                print(f"  [sim] {day} event failed: {type(e).__name__}: {e}")

    # ----- master data -----

    def _seed_masters(self):
        db = get_db()

        for c in CUSTOMERS:
            if not db.exists("Customer", c["name"]):
                db.insert("Customer", _dict(**c))

        for s in SUPPLIERS:
            if not db.exists("Supplier", s["name"]):
                db.insert("Supplier", _dict(**s))

        for code, item_name, uom, price, *_ in STOCK_ITEMS:
            if not db.exists("Item", code):
                db.insert("Item", _dict(
                    name=code, item_name=item_name, stock_uom=uom,
                    standard_rate=price, is_stock_item=1,
                ))

        for code, item_name, uom, price in SERVICE_ITEMS:
            if not db.exists("Item", code):
                db.insert("Item", _dict(
                    name=code, item_name=item_name, stock_uom=uom,
                    standard_rate=price, is_stock_item=0,
                ))

        if not db.exists("Warehouse", self.warehouse):
            db.insert("Warehouse", _dict(
                name=self.warehouse, warehouse_name="Main Warehouse",
                company=self.company,
            ))

        supplier_names = [s["name"] for s in SUPPLIERS]
        for code, _n, _u, price, cost_factor, reorder_level, reorder_qty in STOCK_ITEMS:
            self.reorder_info[code] = {
                "level": reorder_level,
                "qty": reorder_qty,
                "rate": flt(price * cost_factor, 2),
                "supplier": self.rng.choice(supplier_names),
                "open_po": 0,
            }

        self._customer_names = [c["name"] for c in CUSTOMERS]
        self._stock_item_codes = [i[0] for i in STOCK_ITEMS]
        self._service_item_codes = [i[0] for i in SERVICE_ITEMS]
        self._item_prices = {i[0]: i[3] for i in STOCK_ITEMS}
        self._item_prices.update({i[0]: i[3] for i in SERVICE_ITEMS})

    def _seed_opening_stock(self):
        from lambda_erp.stock.stock_entry import StockEntry

        items = []
        for code, info in self.reorder_info.items():
            # Seed ~2x reorder_level: comfortable buffer while still triggering
            # restocks within the first few months of simulated activity.
            qty = int(info["level"] * self.rng.uniform(2.0, 3.0))
            items.append(_dict(item_code=code, qty=qty, basic_rate=info["rate"]))

        se = StockEntry(
            stock_entry_type="Opening Stock",
            posting_date=self.start.isoformat(),
            company=self.company,
            to_warehouse=self.warehouse,
            items=items,
        )
        se.save()
        se.submit()
        self.stats["opening_stock_entries"] += 1

    # ----- quotation generation -----

    def _daily_intensity(self, day: date) -> float:
        month_mult = MONTH_SEASONALITY[day.month]
        year_offset = day.year - self.base_year
        growth = (1.0 + ANNUAL_GROWTH) ** year_offset
        return BASE_QUOTES_PER_DAY * self.intensity * month_mult * growth

    def _poisson(self, lam: float) -> int:
        """Knuth's algorithm, seeded via self.rng."""
        if lam <= 0:
            return 0
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while p > L:
            k += 1
            p *= self.rng.random()
        return k - 1

    def _maybe_generate_quotations(self, day: date):
        count = self._poisson(self._daily_intensity(day))
        for _ in range(count):
            self._create_quotation(day)

    def _create_quotation(self, day: date):
        from lambda_erp.selling.quotation import Quotation

        customer = self.rng.choice(self._customer_names)

        n_items = self.rng.randint(1, 4)
        picks: list[str] = []
        for _ in range(n_items):
            pool = self._stock_item_codes if self.rng.random() < 0.75 else self._service_item_codes
            picks.append(self.rng.choice(pool))
        picks = list(dict.fromkeys(picks))

        items = []
        for code in picks:
            base = self._item_prices[code]
            rate = flt(base * self.rng.uniform(0.9, 1.1), 2)
            qty = self.rng.randint(1, 20)
            items.append(_dict(item_code=code, qty=qty, rate=rate))

        q = Quotation(
            customer=customer,
            company=self.company,
            transaction_date=day.isoformat(),
            items=items,
            taxes=[_dict(
                charge_type="On Net Total",
                account_head=f"Tax Payable - {self.abbr}",
                description="Sales Tax 10%",
                rate=10, idx=1,
            )],
        )
        q.save()
        q.submit()
        self.stats["quotations"] += 1

        decide_day = self._add_business_days(day, self.rng.randint(*DAYS_QUOTE_TO_DECISION))
        qname = q.name
        if self.rng.random() < QUOTE_CONVERSION_RATE:
            self._schedule(decide_day, lambda d, n=qname: self._convert_quotation(d, n))
        else:
            self._schedule(decide_day, lambda d, n=qname: self._lose_quotation(d, n))

    def _lose_quotation(self, day: date, qname: str):
        db = get_db()
        db.set_value("Quotation", qname, "status", "Lost")
        db.conn.commit()
        self.stats["quotations_lost"] += 1

    def _convert_quotation(self, day: date, qname: str):
        from lambda_erp.selling.quotation import make_sales_order

        so = make_sales_order(qname)
        so.transaction_date = day.isoformat()
        delivery_date = self._add_business_days(day, self.rng.randint(*DAYS_SO_TO_DELIVERY))
        so.delivery_date = delivery_date.isoformat()

        for item in so.get("items"):
            if item.get("item_code") in self.reorder_info and not item.get("warehouse"):
                item["warehouse"] = self.warehouse

        so.save()
        so.submit()
        self.stats["sales_orders"] += 1

        soname = so.name
        first_of_two = self.rng.random() < PARTIAL_DELIVERY_PCT
        self._schedule(
            delivery_date,
            lambda d, n=soname, f=first_of_two: self._do_delivery(d, n, first_of_two=f),
        )

    def _do_delivery(self, day: date, soname: str, first_of_two: bool = False):
        from lambda_erp.stock.delivery_note import make_delivery_note

        dn = make_delivery_note(soname)
        if not dn.get("items"):
            return

        dn.posting_date = day.isoformat()
        for item in dn.get("items"):
            if not item.get("warehouse") and item.get("item_code") in self.reorder_info:
                item["warehouse"] = self.warehouse

        if first_of_two:
            for item in dn.get("items"):
                orig = flt(item["qty"])
                if orig > 1:
                    item["qty"] = max(1, int(orig / 2))

        # Availability check against current stock (skip items without warehouse — services)
        insufficient = False
        for item in list(dn.get("items")):
            wh = item.get("warehouse")
            if not wh:
                continue
            bal = get_stock_balance(item["item_code"], wh)
            avail = flt(bal.actual_qty) if bal else 0
            want = flt(item["qty"])
            if avail <= 0:
                dn.get("items").remove(item)
                insufficient = True
            elif avail < want:
                item["qty"] = avail
                insufficient = True

        if not dn.get("items"):
            # nothing to ship right now — retry in a few days
            retry_day = self._add_business_days(day, self.rng.randint(2, 5))
            self._schedule(
                retry_day,
                lambda d, n=soname, f=first_of_two: self._do_delivery(d, n, first_of_two=f),
            )
            self.stats["deliveries_deferred"] += 1
            return

        dn.save()
        dn.submit()
        self.stats["delivery_notes"] += 1

        if first_of_two:
            gap = self.rng.randint(*DAYS_PARTIAL_DELIVERY_GAP)
            second_day = self._add_business_days(day, gap)
            self._schedule(
                second_day,
                lambda d, n=soname: self._do_delivery(d, n, first_of_two=False),
            )
            return

        invoice_day = self._add_business_days(day, self.rng.randint(*DAYS_DN_TO_INVOICE))
        self._schedule(invoice_day, lambda d, n=soname: self._do_invoice(d, n))

    def _do_invoice(self, day: date, soname: str):
        from lambda_erp.selling.sales_order import make_sales_invoice

        inv = make_sales_invoice(soname)
        inv.posting_date = day.isoformat()
        inv.due_date = add_days(day, DAYS_INVOICE_NET).isoformat()
        inv.save()
        inv.submit()
        self.stats["sales_invoices"] += 1

        invname = inv.name
        grand_total = flt(inv.grand_total)

        r = self.rng.random()
        if r < OUTSTANDING_INVOICE_PCT:
            self.stats["invoices_left_outstanding"] += 1
            return

        due = getdate(inv.due_date)
        pay_day = due + timedelta(days=self.rng.randint(*DAYS_PAYMENT_VARIANCE))
        pay_day = self._next_business_day(pay_day)

        if r < OUTSTANDING_INVOICE_PCT + PARTIAL_PAYMENT_PCT:
            half = flt(grand_total / 2, 2)
            self._schedule(
                pay_day,
                lambda d, n=invname, a=half: self._do_payment(d, n, amount=a),
            )
            gap = self.rng.randint(*DAYS_SECOND_PAYMENT_GAP)
            second_day = self._add_business_days(pay_day, gap)
            self._schedule(
                second_day,
                lambda d, n=invname: self._do_payment(d, n, amount=None),
            )
        else:
            self._schedule(
                pay_day,
                lambda d, n=invname: self._do_payment(d, n, amount=None),
            )

    def _do_payment(self, day: date, invname: str, amount: float | None):
        from lambda_erp.accounting.payment_entry import PaymentEntry

        db = get_db()
        inv = db.get_value(
            "Sales Invoice", invname,
            ["customer", "grand_total", "outstanding_amount"],
        )
        if not inv:
            return
        outstanding = flt(inv.outstanding_amount)
        if outstanding <= 0:
            return

        amt = outstanding if amount is None else min(flt(amount, 2), outstanding)
        if amt <= 0:
            return

        pe = PaymentEntry(
            payment_type="Receive",
            posting_date=day.isoformat(),
            company=self.company,
            party_type="Customer",
            party=inv.customer,
            paid_from=f"Accounts Receivable - {self.abbr}",
            paid_to=f"Primary Bank - {self.abbr}",
            paid_amount=amt,
            received_amount=amt,
            references=[_dict(
                reference_doctype="Sales Invoice",
                reference_name=invname,
                total_amount=flt(inv.grand_total),
                outstanding_amount=outstanding,
                allocated_amount=amt,
            )],
        )
        pe.save()
        pe.submit()
        self.stats["sales_payments"] += 1

    # ----- purchasing (reorder-driven) -----

    def _check_reorder_points(self, day: date):
        for code, info in self.reorder_info.items():
            if info["open_po"] > 0:
                continue
            bal = get_stock_balance(code, self.warehouse)
            on_hand = flt(bal.actual_qty) if bal else 0
            if on_hand > info["level"]:
                continue
            self._create_po(day, code, info)

    def _create_po(self, day: date, code: str, info: dict):
        from lambda_erp.buying.purchase_order import PurchaseOrder

        po = PurchaseOrder(
            supplier=info["supplier"],
            company=self.company,
            transaction_date=day.isoformat(),
            items=[_dict(
                item_code=code,
                qty=info["qty"],
                rate=info["rate"],
                warehouse=self.warehouse,
            )],
        )
        po.save()
        po.submit()
        info["open_po"] += 1
        self.stats["purchase_orders"] += 1

        poname = po.name
        receipt_day = self._add_business_days(day, self.rng.randint(*DAYS_PO_TO_RECEIPT))
        self._schedule(
            receipt_day,
            lambda d, n=poname, c=code: self._do_purchase_receipt(d, n, c),
        )

    def _do_purchase_receipt(self, day: date, poname: str, code: str):
        from lambda_erp.stock.purchase_receipt import make_purchase_receipt

        pr = make_purchase_receipt(poname)
        if not pr.get("items"):
            return
        pr.posting_date = day.isoformat()
        pr.save()
        pr.submit()
        self.stats["purchase_receipts"] += 1

        pi_day = self._add_business_days(day, self.rng.randint(*DAYS_PR_TO_INVOICE))
        self._schedule(
            pi_day,
            lambda d, n=poname, c=code: self._do_purchase_invoice(d, n, c),
        )

    def _do_purchase_invoice(self, day: date, poname: str, code: str):
        from lambda_erp.buying.purchase_order import make_purchase_invoice

        pi = make_purchase_invoice(poname)
        pi.posting_date = day.isoformat()
        pi.due_date = add_days(day, DAYS_PI_NET).isoformat()
        pi.save()
        pi.submit()
        self.reorder_info[code]["open_po"] = max(0, self.reorder_info[code]["open_po"] - 1)
        self.stats["purchase_invoices"] += 1

        piname = pi.name
        pay_day = self._add_business_days(day, DAYS_PI_NET + self.rng.randint(*DAYS_PI_PAYMENT_VARIANCE))
        self._schedule(pay_day, lambda d, n=piname: self._do_purchase_payment(d, n))

    def _do_purchase_payment(self, day: date, piname: str):
        from lambda_erp.accounting.payment_entry import PaymentEntry

        db = get_db()
        pi = db.get_value(
            "Purchase Invoice", piname,
            ["supplier", "grand_total", "outstanding_amount"],
        )
        if not pi:
            return
        outstanding = flt(pi.outstanding_amount)
        if outstanding <= 0:
            return

        pe = PaymentEntry(
            payment_type="Pay",
            posting_date=day.isoformat(),
            company=self.company,
            party_type="Supplier",
            party=pi.supplier,
            paid_from=f"Primary Bank - {self.abbr}",
            paid_to=f"Accounts Payable - {self.abbr}",
            paid_amount=outstanding,
            received_amount=outstanding,
            references=[_dict(
                reference_doctype="Purchase Invoice",
                reference_name=piname,
                total_amount=flt(pi.grand_total),
                outstanding_amount=outstanding,
                allocated_amount=outstanding,
            )],
        )
        pe.save()
        pe.submit()
        self.stats["purchase_payments"] += 1
