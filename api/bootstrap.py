"""Idempotent first-run bootstrap for a public demo container.

Called from the FastAPI lifespan when LAMBDA_ERP_AUTO_DEMO=1. On first boot this
provisions a company, runs the 3-year historical simulator so the UI has real
transactional data to display, creates the public_manager demo account, and
pre-creates every concrete artifact the scripted chat replay narrates
(quotation, purchase order, custom analytics draft, a Redstone sales invoice)
so the links the demo shows resolve to real records.

Safe to run on every container start — each step checks existence before
doing work.

Simulation is pinned to 2023-04-20 → 2026-04-20 with seed=42, so the demo
world is identical across deploys and the chat script can reference it.
"""

import json

from lambda_erp.database import get_db
from lambda_erp.utils import _dict


DEMO_COMPANY = "Lambda Demo Corp"
DEMO_SIM_START = "2023-04-20"
DEMO_SIM_END = "2026-04-20"
DEMO_SIM_SEED = 42
DEMO_CHAT_DATE = "2026-04-22"   # "today" inside the demo narrative

# Settings keys used by load_demo_script() for placeholder substitution.
SETTING_DEMO_QUOTATION = "demo_chat_quotation"
SETTING_DEMO_PURCHASE_ORDER = "demo_chat_purchase_order"
SETTING_DEMO_COMPANY = "demo_chat_company"
# Top 3 customer ranking (from simulator data; deterministic under seed 42).
SETTING_DEMO_TOP1_ID = "demo_chat_top1_id"
SETTING_DEMO_TOP1_NAME = "demo_chat_top1_name"
SETTING_DEMO_TOP1_REVENUE = "demo_chat_top1_revenue"
SETTING_DEMO_TOP1_INVOICES = "demo_chat_top1_invoices"
SETTING_DEMO_TOP2_ID = "demo_chat_top2_id"
SETTING_DEMO_TOP2_NAME = "demo_chat_top2_name"
SETTING_DEMO_TOP2_REVENUE = "demo_chat_top2_revenue"
SETTING_DEMO_TOP3_ID = "demo_chat_top3_id"
SETTING_DEMO_TOP3_NAME = "demo_chat_top3_name"
SETTING_DEMO_TOP3_REVENUE = "demo_chat_top3_revenue"
# Last invoice for each of those top 3 (rendered as markdown line lists).
SETTING_DEMO_TOP1_LAST_INV = "demo_chat_top1_last_inv"
SETTING_DEMO_TOP1_LAST_INV_DATE = "demo_chat_top1_last_inv_date"
SETTING_DEMO_TOP1_LAST_INV_ITEMS = "demo_chat_top1_last_inv_items"
SETTING_DEMO_TOP2_LAST_INV = "demo_chat_top2_last_inv"
SETTING_DEMO_TOP2_LAST_INV_DATE = "demo_chat_top2_last_inv_date"
SETTING_DEMO_TOP2_LAST_INV_ITEMS = "demo_chat_top2_last_inv_items"
SETTING_DEMO_TOP3_LAST_INV = "demo_chat_top3_last_inv"
SETTING_DEMO_TOP3_LAST_INV_DATE = "demo_chat_top3_last_inv_date"
SETTING_DEMO_TOP3_LAST_INV_ITEMS = "demo_chat_top3_last_inv_items"
# Custom analytics report draft + follow-up Redstone sales invoice.
SETTING_DEMO_TOP7_REPORT_ID = "demo_chat_top7_report_id"
SETTING_DEMO_REDSTONE_SINV = "demo_chat_redstone_sinv"
SETTING_DEMO_REDSTONE_SINV_DATE = "demo_chat_redstone_sinv_date"
SETTING_DEMO_REDSTONE_DUE_DATE = "demo_chat_redstone_due_date"


def bootstrap_demo() -> None:
    """Idempotent demo bootstrap. Logs each phase to stdout so a
    `docker compose up` user sees steady progress during the ~3-minute
    first-boot simulation run.

    By default this seeds a company + 3 years of simulated history and then
    hands off to the normal register-your-first-admin login flow. Set
    LAMBDA_ERP_ENABLE_PUBLIC_DEMO=1 to additionally create the shared
    public_manager account and the chat-replay artefacts — that's the mode
    used for the hosted public demo at lambda.dev/erp."""
    import os
    import time

    db = get_db()
    t0 = time.monotonic()
    public_demo = os.environ.get("LAMBDA_ERP_ENABLE_PUBLIC_DEMO") == "1"

    # 1. Company (only if no company yet)
    companies = db.get_all("Company", fields=["name"])
    if not companies:
        print(f"[bootstrap] creating company: {DEMO_COMPANY}", flush=True)
        from api.routers.setup import create_company

        create_company(
            {"name": DEMO_COMPANY, "currency": "USD"},
            _user=None,
        )
    else:
        print(f"[bootstrap] company already exists: {companies[0]['name']}", flush=True)

    company = db.get_all("Company", fields=["name"])[0]["name"]

    # 2. Historical simulation — skip if any quotations already exist, so
    #    re-boots don't regenerate and duplicate.
    qtn_count = db.sql('SELECT COUNT(*) as cnt FROM "Quotation"')[0]["cnt"]
    if qtn_count == 0:
        print(
            f"[bootstrap] running historical simulation "
            f"({DEMO_SIM_START} -> {DEMO_SIM_END}, seed={DEMO_SIM_SEED})",
            flush=True,
        )
        from lambda_erp.simulation import HistoricalSimulator

        sim = HistoricalSimulator(
            company=company,
            start=DEMO_SIM_START,
            end=DEMO_SIM_END,
            seed=DEMO_SIM_SEED,
        )
        sim.run(simulate_activity=True)
    else:
        print(f"[bootstrap] simulation data present ({qtn_count} quotations), skipping", flush=True)

    # 3. Public-demo extras — opt-in. Without this, the container hands
    #    off to the normal login page where the first registered user
    #    becomes admin.
    if public_demo:
        print("[bootstrap] LAMBDA_ERP_ENABLE_PUBLIC_DEMO=1 — creating public_manager + chat-replay records", flush=True)
        from api.auth import create_public_manager

        create_public_manager(user=None)
        ensure_demo_chat_records(company)
    else:
        print("[bootstrap] public demo disabled — first visitor registers as admin via the login page", flush=True)

    elapsed = time.monotonic() - t0
    # Recommend 127.0.0.1 — on WSL2 + Docker Desktop, browsers can stall
    # for minutes on WebSocket upgrades to `localhost` even though HTTP
    # works fine to the same address. 127.0.0.1 has never been observed
    # to misbehave on any combination tested.
    banner_url = "http://127.0.0.1:8000"
    bar = "=" * 62
    print(f"[bootstrap] complete in {elapsed:.1f}s", flush=True)
    print(bar, flush=True)
    print(f"  Lambda ERP is READY — open {banner_url} in your browser", flush=True)
    if public_demo:
        print(f"  Click 'Enter Live Demo' on the login page to start.", flush=True)
    else:
        print(f"  First user to register on the login page becomes the admin.", flush=True)
    print(f"  (requests sent before this line were queued at the TCP layer)", flush=True)
    print(bar, flush=True)


def ensure_demo_chat_records(company: str) -> None:
    """Create every concrete artifact the scripted chat replay points at and
    record their identifiers in Settings so load_demo_script can substitute
    them into the template. Idempotent."""
    db = get_db()

    # --- quotation + purchase order (the opening demo references) ---------
    existing_qtn = _get_setting(db, SETTING_DEMO_QUOTATION)
    existing_po = _get_setting(db, SETTING_DEMO_PURCHASE_ORDER)
    if not (
        existing_qtn
        and existing_po
        and db.exists("Quotation", existing_qtn)
        and db.exists("Purchase Order", existing_po)
    ):
        from lambda_erp.selling.quotation import Quotation
        from lambda_erp.buying.purchase_order import PurchaseOrder

        qtn = Quotation(
            customer="CUST-001",
            company=company,
            transaction_date="2026-04-17",
            items=[
                _dict(item_code="ITEM-001", qty=10, rate=125),
                _dict(item_code="SVC-001", qty=1, rate=150),
            ],
        )
        qtn.save()
        qtn.submit()

        po = PurchaseOrder(
            supplier="SUPP-005",
            company=company,
            transaction_date="2026-04-17",
            items=[
                _dict(item_code="ITEM-001", qty=20, rate=100),
            ],
        )
        po.save()
        po.submit()

        _set_setting(db, SETTING_DEMO_QUOTATION, qtn.name)
        _set_setting(db, SETTING_DEMO_PURCHASE_ORDER, po.name)
        _set_setting(db, SETTING_DEMO_COMPANY, company)

    # --- top customer ranking + last invoices ----------------------------
    _ensure_top_customer_snapshots(db)

    # --- custom analytics draft (Top 7 Customers by Revenue) -------------
    _ensure_top7_report_draft(db)

    # --- Redstone "8 more hours of project management" sales invoice -----
    _ensure_redstone_project_management_sinv(db, company)


def _ensure_top_customer_snapshots(db) -> None:
    """Compute the top-3 revenue customers from the simulator output and
    snapshot the last-invoice line items for each. Written to Settings so
    the scripted reply is accurate and doesn't depend on the LLM."""
    if _get_setting(db, SETTING_DEMO_TOP1_ID):
        return  # already snapshotted

    top_rows = db.sql(
        """
        SELECT customer,
               customer_name,
               SUM(net_total) AS revenue,
               COUNT(*) AS invoice_count
        FROM "Sales Invoice"
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0
        GROUP BY customer, customer_name
        ORDER BY revenue DESC
        LIMIT 3
        """
    )
    if len(top_rows) < 3:
        return

    slots = [
        (SETTING_DEMO_TOP1_ID, SETTING_DEMO_TOP1_NAME, SETTING_DEMO_TOP1_REVENUE,
         SETTING_DEMO_TOP1_INVOICES, SETTING_DEMO_TOP1_LAST_INV,
         SETTING_DEMO_TOP1_LAST_INV_DATE, SETTING_DEMO_TOP1_LAST_INV_ITEMS),
        (SETTING_DEMO_TOP2_ID, SETTING_DEMO_TOP2_NAME, SETTING_DEMO_TOP2_REVENUE,
         None, SETTING_DEMO_TOP2_LAST_INV,
         SETTING_DEMO_TOP2_LAST_INV_DATE, SETTING_DEMO_TOP2_LAST_INV_ITEMS),
        (SETTING_DEMO_TOP3_ID, SETTING_DEMO_TOP3_NAME, SETTING_DEMO_TOP3_REVENUE,
         None, SETTING_DEMO_TOP3_LAST_INV,
         SETTING_DEMO_TOP3_LAST_INV_DATE, SETTING_DEMO_TOP3_LAST_INV_ITEMS),
    ]
    for row, slot in zip(top_rows, slots):
        id_key, name_key, rev_key, inv_key, last_inv_key, last_inv_date_key, items_key = slot
        _set_setting(db, id_key, row["customer"])
        _set_setting(db, name_key, row["customer_name"] or row["customer"])
        _set_setting(db, rev_key, _format_money(row["revenue"]))
        if inv_key:
            _set_setting(db, inv_key, str(int(row["invoice_count"])))

        last_inv = db.sql(
            """
            SELECT name, posting_date
            FROM "Sales Invoice"
            WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND customer = ?
            ORDER BY posting_date DESC, name DESC
            LIMIT 1
            """,
            [row["customer"]],
        )
        if not last_inv:
            continue
        inv = last_inv[0]
        _set_setting(db, last_inv_key, inv["name"])
        _set_setting(db, last_inv_date_key, str(inv["posting_date"]))

        item_rows = db.sql(
            """
            SELECT item_name, item_code, qty, uom
            FROM "Sales Invoice Item"
            WHERE parent = ?
            ORDER BY idx
            """,
            [inv["name"]],
        )
        items_md = "\n".join(
            f"   - **{r['item_name'] or r['item_code']}** — {_format_qty(r['qty'])} {r['uom'] or ''}".rstrip()
            for r in item_rows
        )
        _set_setting(db, items_key, items_md)


def _ensure_top7_report_draft(db) -> None:
    """Create the 'Top 7 Customers by Revenue' analytics draft the chat
    replay links to, so clicking the bar-chart link opens a real report."""
    if _get_setting(db, SETTING_DEMO_TOP7_REPORT_ID):
        return

    from api.routers.analytics import create_report_draft_record

    transform_js = (
        "const grouped = helpers.group(sales, ['customer', 'customer_name'], {\n"
        "  revenue: ['sum', 'net_total'],\n"
        "});\n"
        "const sorted = helpers.sortBy(grouped, 'revenue', 'desc');\n"
        "const top = helpers.topN(sorted, 7);\n"
        "const rows = top.map(function(r) { return {\n"
        "  customer: r.customer,\n"
        "  customer_name: r.customer_name || r.customer,\n"
        "  revenue: r.revenue,\n"
        "}; });\n"
        "return {\n"
        "  title: 'Top 7 Customers by Revenue',\n"
        "  kpis: [\n"
        "    { label: 'Total Revenue (Top 7)', value: helpers.sum(rows, 'revenue'), format: 'currency' },\n"
        "    { label: 'Customers Shown', value: rows.length, format: 'number' },\n"
        "  ],\n"
        "  tables: [\n"
        "    {\n"
        "      title: 'Top 7 Customers by Revenue',\n"
        "      columns: [\n"
        "        { key: 'customer_name', label: 'Customer', type: 'string' },\n"
        "        { key: 'revenue', label: 'Revenue', type: 'currency' },\n"
        "      ],\n"
        "      rows: rows,\n"
        "    },\n"
        "  ],\n"
        "  charts: [\n"
        "    {\n"
        "      title: 'Top 7 Customers by Revenue',\n"
        "      type: 'bar',\n"
        "      x: 'customer_name',\n"
        "      y: 'revenue',\n"
        "      dataTable: 'Top 7 Customers by Revenue',\n"
        "    },\n"
        "  ],\n"
        "};"
    )

    payload = {
        "title": "Top 7 Customers by Revenue",
        "description": "Top 7 customers ranked by total submitted sales invoice revenue (returns excluded).",
        "data_requests": [
            {
                "name": "sales",
                "dataset": "sales_invoices",
                "fields": ["customer", "customer_name", "net_total", "is_return"],
                "filters": {"is_return": 0},
            }
        ],
        "transform_js": transform_js,
    }
    # Own the draft as the public_manager so the demo user can see it in
    # the Custom Analytics sidebar and open it without hitting the 403
    # "you do not have access" check. The user's `name` is generated at
    # random by create_public_manager, so we look it up rather than
    # hardcoding.
    pm_user = _public_manager_user(db)
    draft = create_report_draft_record(payload, user=pm_user)
    _set_setting(db, SETTING_DEMO_TOP7_REPORT_ID, draft["id"])


def _ensure_redstone_project_management_sinv(db, company: str) -> None:
    """Pre-create the 8-hour Project Management sales invoice for the #2
    customer so the demo's "submit it" / "yup" step lands on a real,
    already-submitted invoice.

    We look up the customer dynamically (whoever is top-2) so the narrative
    stays self-consistent even if simulator output shifts slightly.
    """
    existing_sinv = _get_setting(db, SETTING_DEMO_REDSTONE_SINV)
    if existing_sinv and db.exists("Sales Invoice", existing_sinv):
        return

    top2_customer = _get_setting(db, SETTING_DEMO_TOP2_ID)
    if not top2_customer:
        return

    from lambda_erp.accounting.sales_invoice import SalesInvoice
    from datetime import date, timedelta

    posting = date.fromisoformat(DEMO_CHAT_DATE)
    due = posting + timedelta(days=30)

    sinv = SalesInvoice(
        customer=top2_customer,
        company=company,
        posting_date=DEMO_CHAT_DATE,
        due_date=due.isoformat(),
        items=[_dict(item_code="SVC-005", qty=8, rate=250)],
    )
    sinv.save()
    sinv.submit()

    _set_setting(db, SETTING_DEMO_REDSTONE_SINV, sinv.name)
    _set_setting(db, SETTING_DEMO_REDSTONE_SINV_DATE, DEMO_CHAT_DATE)
    _set_setting(db, SETTING_DEMO_REDSTONE_DUE_DATE, due.isoformat())


def _format_money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "0.00"


def _format_qty(value) -> str:
    try:
        f = float(value)
        return str(int(f)) if f == int(f) else f"{f:g}"
    except Exception:
        return str(value)


def _public_manager_user(db) -> dict | None:
    rows = db.sql(
        'SELECT name, role FROM "User" WHERE role = "public_manager" LIMIT 1'
    )
    if not rows:
        return None
    return {"name": rows[0]["name"], "role": rows[0]["role"]}


def _get_setting(db, key: str) -> str:
    rows = db.sql('SELECT value FROM "Settings" WHERE key = ?', [key])
    return rows[0]["value"] if rows else ""


def _set_setting(db, key: str, value: str) -> None:
    existing = db.sql('SELECT key FROM "Settings" WHERE key = ?', [key])
    if existing:
        db.sql('UPDATE "Settings" SET value = ? WHERE key = ?', [str(value), key])
    else:
        db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', [key, str(value)])
    db.conn.commit()
