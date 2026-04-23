"""Company setup and demo data seeding."""

import hashlib
import random

from fastapi import APIRouter, Depends
from lambda_erp.database import get_db
from lambda_erp.utils import _dict, flt, nowdate
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
from api.auth import require_role

# Sample US corporate addresses (streets, cities, states) used to auto-fill
# the company profile when the user doesn't supply one.
_DEMO_ADDRESSES = [
    ("1200 Lakeshore Dr", "Chicago", "IL", "60611", "US"),
    ("88 Market Street", "San Francisco", "CA", "94105", "US"),
    ("450 Park Avenue", "New York", "NY", "10022", "US"),
    ("222 Congress Ave", "Austin", "TX", "78701", "US"),
    ("3700 Peachtree Rd", "Atlanta", "GA", "30326", "US"),
    ("900 Biscayne Blvd", "Miami", "FL", "33132", "US"),
    ("1 Pike Place", "Seattle", "WA", "98101", "US"),
    ("555 California St", "San Francisco", "CA", "94104", "US"),
    ("100 Federal Street", "Boston", "MA", "02110", "US"),
    ("1100 Louisiana St", "Houston", "TX", "77002", "US"),
]


def _random_address_for(company_name: str) -> dict:
    """Deterministic pseudo-random address derived from the company name."""
    digest = hashlib.md5(company_name.encode()).hexdigest()
    rng = random.Random(int(digest[:8], 16))
    street, city, _state, _zip, country = rng.choice(_DEMO_ADDRESSES)
    tax_id = f"US-{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}"
    phone = f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
    local = "".join(c for c in company_name.lower() if c.isalnum())[:20] or "contact"
    email = f"hello@{local}.com"
    return {
        "email": email,
        "phone": phone,
        "address": street,
        "city": city,
        "country": country,
        "tax_id": tax_id,
    }

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status")
def setup_status():
    """Check if any company exists (for first-run detection)."""
    db = get_db()
    companies = db.get_all(
        "Company",
        fields=[
            "name", "company_name", "default_currency",
            "email", "phone", "address", "city", "country", "tax_id",
        ],
    )
    return {
        "setup_complete": len(companies) > 0,
        "companies": [dict(c) for c in companies],
    }


@router.post("/company")
def create_company(data: dict, _user: dict = Depends(require_role("admin"))):
    """Create a new company with Chart of Accounts and Cost Center."""
    db = get_db()
    name = data.get("name")
    currency = data.get("currency", "USD")

    if not name:
        return {"detail": "Company name is required"}

    if db.exists("Company", name):
        return {"detail": f"Company {name} already exists"}

    # Auto-fill contact info with a deterministic random address if the caller
    # didn't supply one. This keeps the PDFs looking complete for demo users.
    auto = _random_address_for(name)
    db.insert("Company", _dict(
        name=name,
        company_name=name,
        default_currency=currency,
        email=data.get("email") or auto["email"],
        phone=data.get("phone") or auto["phone"],
        address=data.get("address") or auto["address"],
        city=data.get("city") or auto["city"],
        country=data.get("country") or auto["country"],
        tax_id=data.get("tax_id") or auto["tax_id"],
    ))

    setup_chart_of_accounts(name, currency)
    cost_center = setup_cost_center(name)

    return {
        "ok": True,
        "company": name,
        "cost_center": cost_center,
        "currency": currency,
    }


@router.post("/seed-demo")
def seed_demo(_user: dict = Depends(require_role("admin"))):
    """Seed demo master data (customers, suppliers, items, warehouse).

    Now a thin wrapper around HistoricalSimulator with simulate_activity=False,
    so both this endpoint and /seed-history share one source of truth for the
    demo customer/supplier/item catalog.
    """
    from lambda_erp.simulation import HistoricalSimulator

    db = get_db()
    companies = db.get_all("Company", fields=["name"])
    if not companies:
        return {"detail": "Create a company first via POST /api/setup/company"}

    company = companies[0]["name"]
    sim = HistoricalSimulator(
        company=company,
        start=nowdate(),
        end=nowdate(),
    )
    sim.run(simulate_activity=False)

    return {"ok": True, "company": company}


@router.post("/seed-history")
def seed_history(data: dict, _user: dict = Depends(require_role("admin"))):
    """Seed ~3 years of simulated business activity.

    Walks business days (skipping weekends + US federal holidays) and generates
    quotations, sales orders, deliveries, invoices, payments, and the
    reorder-driven purchasing that keeps stock available. Seasonality and YoY
    growth are baked in; the RNG seed makes runs reproducible.

    Expects: {
        "start_date": "2023-04-20",  # optional, default: 3 years ago
        "end_date":   "2026-04-20",  # optional, default: today
        "seed":       42,            # optional, default: 42
        "intensity":  1.0            # optional, multiplier on quote volume
    }
    """
    from lambda_erp.simulation import HistoricalSimulator

    db = get_db()
    companies = db.get_all("Company", fields=["name"])
    if not companies:
        return {"detail": "Create a company first via POST /api/setup/company"}

    company = companies[0]["name"]

    today = nowdate()
    start = data.get("start_date")
    end = data.get("end_date", today)
    if not start:
        from datetime import date, timedelta
        start = (date.fromisoformat(today) - timedelta(days=365 * 3)).isoformat()

    # Skip the simulation itself when a world already exists so re-running
    # this endpoint is safe (and useful for backfilling the chat-demo settings
    # on an already-seeded DB). The `ensure_demo_chat_records` call below
    # still runs and fills any gaps.
    existing_qtn = db.sql('SELECT COUNT(*) as cnt FROM "Quotation"')[0]["cnt"]
    if existing_qtn == 0:
        sim = HistoricalSimulator(
            company=company,
            start=start,
            end=end,
            seed=int(data.get("seed", 42)),
            intensity=float(data.get("intensity", 1.0)),
        )
        stats = sim.run()
    else:
        stats = {"skipped": "quotations already exist"}

    # Also set up the docs + Settings that the scripted chat replay uses. This
    # way the admin-triggered history seed produces the same demo-ready state
    # as the auto-boot path, so enabling public_manager locally "just works".
    from api.bootstrap import ensure_demo_chat_records
    ensure_demo_chat_records(company)

    return {
        "ok": True,
        "company": company,
        "start_date": start,
        "end_date": end,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Opening Balances
# ---------------------------------------------------------------------------


@router.post("/opening-balances/accounts")
def import_account_balances(data: dict, _user: dict = Depends(require_role("admin"))):
    """Create a Journal Entry for opening account balances.

    Expects: {
        "company": "...",
        "posting_date": "2026-01-01",
        "entries": [
            {"account": "Accounts Receivable - LAMB", "debit": 5000, "credit": 0},
            {"account": "Primary Bank - LAMB", "debit": 10000, "credit": 0},
            {"account": "Accounts Payable - LAMB", "debit": 0, "credit": 3000},
            ...
        ]
    }
    The difference is automatically balanced against Opening Balance Equity.
    """
    from lambda_erp.accounting.journal_entry import JournalEntry

    company = data.get("company")
    if not company:
        return {"detail": "Company is required"}

    posting_date = data.get("posting_date", nowdate())
    entries = data.get("entries", [])
    if not entries:
        return {"detail": "At least one account entry is required"}

    db = get_db()
    abbr = company[:4].upper()
    equity_account = f"Opening Balance Equity - {abbr}"

    accounts = []
    total_debit = 0
    total_credit = 0

    for e in entries:
        debit = flt(e.get("debit", 0), 2)
        credit = flt(e.get("credit", 0), 2)
        if not debit and not credit:
            continue
        accounts.append(_dict(
            account=e["account"],
            debit=debit,
            credit=credit,
            party_type=e.get("party_type", ""),
            party=e.get("party", ""),
        ))
        total_debit += debit
        total_credit += credit

    # Balance against Opening Balance Equity
    diff = flt(total_debit - total_credit, 2)
    if diff > 0:
        accounts.append(_dict(account=equity_account, debit=0, credit=diff))
    elif diff < 0:
        accounts.append(_dict(account=equity_account, debit=abs(diff), credit=0))

    je = JournalEntry(
        posting_date=posting_date,
        company=company,
        remark=f"Opening balances as of {posting_date}",
        accounts=accounts,
    )
    je.save()
    je.submit()

    return {"ok": True, "journal_entry": je.name, "total_debit": total_debit, "total_credit": total_credit}


@router.post("/opening-balances/stock")
def import_stock_balances(data: dict, _user: dict = Depends(require_role("admin"))):
    """Create a Stock Entry (Opening Stock) for opening inventory.

    Expects: {
        "company": "...",
        "posting_date": "2026-01-01",
        "warehouse": "Main Warehouse - LAMB",
        "items": [
            {"item_code": "ITEM-001", "qty": 100, "rate": 60},
            {"item_code": "ITEM-002", "qty": 50, "rate": 180},
            ...
        ]
    }
    """
    from lambda_erp.stock.stock_entry import StockEntry

    company = data.get("company")
    if not company:
        return {"detail": "Company is required"}

    posting_date = data.get("posting_date", nowdate())
    warehouse = data.get("warehouse")
    items = data.get("items", [])
    if not items:
        return {"detail": "At least one item is required"}
    if not warehouse:
        return {"detail": "Warehouse is required"}

    se_items = []
    for item in items:
        qty = flt(item.get("qty", 0))
        rate = flt(item.get("rate", 0))
        if qty <= 0:
            continue
        se_items.append(_dict(
            item_code=item["item_code"],
            qty=qty,
            basic_rate=rate,
            t_warehouse=warehouse,
        ))

    se = StockEntry(
        stock_entry_type="Opening Stock",
        posting_date=posting_date,
        company=company,
        to_warehouse=warehouse,
        items=se_items,
    )
    se.save()
    se.submit()

    return {"ok": True, "stock_entry": se.name, "items_count": len(se_items)}


@router.post("/opening-balances/invoices")
def import_outstanding_invoices(data: dict, _user: dict = Depends(require_role("admin"))):
    """Create submitted invoices for outstanding AR/AP balances.

    Expects: {
        "company": "...",
        "invoices": [
            {"type": "sales", "party": "CUST-001", "amount": 5000, "due_date": "2026-02-15", "remarks": "INV-OLD-001"},
            {"type": "purchase", "party": "SUPP-001", "amount": 3000, "due_date": "2026-03-01"},
            ...
        ]
    }
    """
    from lambda_erp.accounting.sales_invoice import SalesInvoice
    from lambda_erp.accounting.purchase_invoice import PurchaseInvoice

    company = data.get("company")
    if not company:
        return {"detail": "Company is required"}

    invoices = data.get("invoices", [])
    if not invoices:
        return {"detail": "At least one invoice is required"}

    db = get_db()
    results = []

    for inv in invoices:
        inv_type = inv.get("type", "sales")
        party = inv.get("party")
        amount = flt(inv.get("amount", 0), 2)
        due_date = inv.get("due_date")
        posting_date = inv.get("posting_date", nowdate())
        remarks = inv.get("remarks", f"Opening balance — {party}")

        if not party or amount <= 0:
            continue

        if inv_type == "sales":
            doc = SalesInvoice(
                customer=party,
                company=company,
                posting_date=posting_date,
                due_date=due_date,
                remarks=remarks,
                items=[_dict(item_code="OPENING", item_name="Opening Balance", qty=1, rate=amount)],
            )
        else:
            doc = PurchaseInvoice(
                supplier=party,
                company=company,
                posting_date=posting_date,
                due_date=due_date,
                remarks=remarks,
                items=[_dict(item_code="OPENING", item_name="Opening Balance", qty=1, rate=amount)],
            )

        doc.save()
        doc.submit()
        results.append({"name": doc.name, "type": inv_type, "party": party, "amount": amount})

    return {"ok": True, "invoices_created": len(results), "invoices": results}
