"""Accounting period-end actions (currently: foreign-currency revaluation)."""

from fastapi import APIRouter, Depends
from lambda_erp.database import get_db
from lambda_erp.accounting.revaluation import run_period_revaluation
from lambda_erp.utils import nowdate
from api.auth import require_role

router = APIRouter(prefix="/accounting", tags=["accounting"])


def _resolve_company(db, company):
    if company:
        return company
    companies = db.get_all("Company", fields=["name"], limit=1)
    return companies[0]["name"] if companies else None


@router.get("/currencies")
def list_currencies(company: str | None = None, _user: dict = Depends(require_role("viewer"))):
    """Currencies the books can transact in, for the document currency dropdown.

    Returns the company base currency, every currency that has a Currency
    Exchange rate on file, and any currency set as a default on a Company /
    Customer / Supplier. Base currency first, then the rest alphabetically. We
    only offer convertible currencies so a new document can't be denominated in
    one the ledger has no rate path to base for.
    """
    db = get_db()
    company = _resolve_company(db, company)
    base = (db.get_value("Company", company, "default_currency") if company else None) or "USD"

    found = {base}
    for row in db.sql(
        'SELECT from_currency AS c FROM "Currency Exchange" '
        'UNION SELECT to_currency AS c FROM "Currency Exchange"'
    ):
        if row.get("c"):
            found.add(row["c"])
    for table in ("Company", "Customer", "Supplier"):
        for row in db.sql(f'SELECT DISTINCT default_currency AS c FROM "{table}"'):
            if row.get("c"):
                found.add(row["c"])

    others = sorted(found - {base})
    return {"base_currency": base, "currencies": [base, *others]}


@router.post("/revaluation")
def period_revaluation(data: dict, _user: dict = Depends(require_role("manager"))):
    """Preview or post period-end FX revaluation of open foreign balances.

    Body: {company?, date?, post?}. With post=false (default) it's a dry run —
    returns the per-balance breakdown without touching the ledger. With
    post=true it posts the revaluation + a next-day auto-reversal.
    """
    db = get_db()
    company = _resolve_company(db, data.get("company"))
    if not company:
        return {"detail": "No company found; create one first."}
    return run_period_revaluation(
        company, data.get("date") or nowdate(), post=bool(data.get("post", False))
    )
