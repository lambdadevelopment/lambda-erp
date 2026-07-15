"""Reporting endpoints: Trial Balance, General Ledger, Stock Balance, P&L, Balance Sheet, Aging."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from lambda_erp.database import get_db
from lambda_erp.utils import flt, nowdate
from lambda_erp.controllers.currency import get_exchange_rate
from api.auth import require_role

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_role("viewer"))])


def _scale_amounts(obj, rate):
    """Recursively multiply every numeric amount by `rate` (booleans/strings
    left alone). Safe for the financial statements, where every number is money."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return flt(obj * rate, 2)
    if isinstance(obj, dict):
        return {k: _scale_amounts(v, rate) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scale_amounts(v, rate) for v in obj]
    return obj


def _present(db, report, company, presentation_currency, rate_date):
    """Re-express a base-currency report in a presentation currency for display
    only (a convenience translation at a single closing rate). The stored ledger
    is never changed. A trial balance still balances since every line scales by
    the same rate."""
    base_ccy = (db.get_value("Company", company, "default_currency") if company else None) or "USD"
    if not presentation_currency or presentation_currency == base_ccy:
        report["base_currency"] = base_ccy
        report["presentation_currency"] = base_ccy
        report["presentation_rate"] = 1.0
        return report
    rate = get_exchange_rate(base_ccy, presentation_currency, rate_date or nowdate())
    presented = _scale_amounts(report, rate)
    presented["base_currency"] = base_ccy
    presented["presentation_currency"] = presentation_currency
    presented["presentation_rate"] = flt(rate, 6)
    return presented


def _trial_balance(db, company=None, from_date=None, to_date=None):
    filters = {"is_group": 0}
    if company:
        filters["company"] = company
    accounts = db.get_all(
        "Account", filters=filters,
        fields=["name", "account_name", "root_type", "report_type"],
        order_by="root_type, name",
    )

    date_clause = ""
    params = []
    if from_date:
        date_clause += " AND posting_date >= ?"
        params.append(from_date)
    if to_date:
        date_clause += " AND posting_date <= ?"
        params.append(to_date)
    if company:
        date_clause += " AND company = ?"
        params.append(company)

    gl_data = db.sql(
        f"""SELECT account,
                   COALESCE(SUM(debit), 0) as total_debit,
                   COALESCE(SUM(credit), 0) as total_credit
            FROM "GL Entry"
            WHERE is_cancelled = 0 {date_clause}
            GROUP BY account""",
        params,
    )
    gl_map = {row["account"]: row for row in gl_data}

    rows = []
    total_debit = 0
    total_credit = 0
    for acc in accounts:
        gl = gl_map.get(acc["name"])
        if not gl:
            continue
        debit = flt(gl["total_debit"], 2)
        credit = flt(gl["total_credit"], 2)
        if not debit and not credit:
            continue
        balance = flt(debit - credit, 2)
        rows.append({
            "account": acc["name"],
            "account_name": acc["account_name"],
            "root_type": acc["root_type"],
            "report_type": acc["report_type"],
            "debit": debit,
            "credit": credit,
            "balance": balance,
        })
        total_debit += debit
        total_credit += credit

    return {
        "rows": rows,
        "total_debit": flt(total_debit, 2),
        "total_credit": flt(total_credit, 2),
        "difference": flt(total_debit - total_credit, 2),
    }


@router.get("/trial-balance")
def trial_balance(
    company: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    presentation_currency: str | None = None,
):
    db = get_db()
    return _present(db, _trial_balance(db, company, from_date, to_date),
                    company, presentation_currency, to_date)


def _chart_of_accounts(db, company=None, from_date=None, to_date=None,
                       include_disabled=False):
    """Chart of accounts (groups and zero-balance ones included) with period
    balances — the tree the GUI renders. Disabled accounts are hidden by default
    (``include_disabled=True`` to show them); balances still roll up over every
    descendant (incl. disabled), and a disabled group with an enabled descendant
    is kept so no active account is orphaned.

    Balance semantics follow standard presentation:
      - Balance Sheet accounts: CLOSING balance as of ``to_date`` (all postings
        up to and including it; ``from_date`` is ignored for them).
      - P&L accounts: movement WITHIN [from_date, to_date].
    Values are natural-signed per root_type (Asset/Expense: debit − credit;
    Liability/Equity/Income: credit − debit) so revenue reads positive.
    Group accounts roll up every descendant's balance (plus any postings of
    their own, though entries normally land on leaves).
    """
    acc_filters = {}
    if company:
        acc_filters["company"] = company
    accounts = db.get_all(
        "Account", filters=acc_filters,
        fields=["name", "account_name", "parent_account", "root_type",
                "report_type", "account_type", "is_group", "disabled"],
        order_by="name",
    )

    def _movement(where_extra, params_extra):
        clause = "is_cancelled = 0" + where_extra
        params = list(params_extra)
        if company:
            clause += " AND company = ?"
            params.append(company)
        found = db.sql(
            f"""SELECT account,
                       COALESCE(SUM(debit), 0) AS d,
                       COALESCE(SUM(credit), 0) AS c
                FROM "GL Entry" WHERE {clause} GROUP BY account""",
            params,
        )
        return {r["account"]: (flt(r["d"], 2), flt(r["c"], 2)) for r in found}

    bs_where, bs_params = "", []
    if to_date:
        bs_where, bs_params = " AND posting_date <= ?", [to_date]
    bs_map = _movement(bs_where, bs_params)

    if from_date or to_date:
        pl_where, pl_params = "", []
        if from_date:
            pl_where += " AND posting_date >= ?"
            pl_params.append(from_date)
        if to_date:
            pl_where += " AND posting_date <= ?"
            pl_params.append(to_date)
        pl_map = _movement(pl_where, pl_params)
    else:
        pl_map = bs_map

    rows = []
    by_name = {}
    for acc in accounts:
        is_pl = acc.get("report_type") == "Profit and Loss"
        d, c = (pl_map if is_pl else bs_map).get(acc["name"], (0.0, 0.0))
        if acc.get("root_type") in ("Liability", "Equity", "Income"):
            natural = c - d
        else:
            natural = d - c
        row = {
            "name": acc["name"],
            "account_name": acc.get("account_name"),
            "parent_account": acc.get("parent_account") or None,
            "root_type": acc.get("root_type"),
            "account_type": acc.get("account_type"),
            "is_group": bool(acc.get("is_group")),
            "disabled": bool(acc.get("disabled")),
            "balance": flt(natural, 2),
            "has_entries": bool(d or c),
        }
        rows.append(row)
        by_name[row["name"]] = row

    # Roll every account's own balance up its ancestor chain so group rows
    # show subtree totals.
    subtree_extra = {r["name"]: 0.0 for r in rows}
    for row in rows:
        parent = row["parent_account"]
        while parent and parent in by_name:
            subtree_extra[parent] += row["balance"]
            parent = by_name[parent]["parent_account"]
    for row in rows:
        if row["is_group"]:
            row["balance"] = flt(row["balance"] + subtree_extra[row["name"]], 2)
            row["has_entries"] = row["has_entries"] or bool(subtree_extra[row["name"]])

    # Hide disabled accounts by default, but keep any disabled ancestor of an
    # enabled account so the enabled one isn't orphaned in the rendered tree.
    if not include_disabled:
        keep = set()
        for row in rows:
            if not row["disabled"]:
                keep.add(row["name"])
                parent = row["parent_account"]
                while parent and parent in by_name and parent not in keep:
                    keep.add(parent)
                    parent = by_name[parent]["parent_account"]
        rows = [r for r in rows if r["name"] in keep]

    return {"accounts": rows, "from_date": from_date, "to_date": to_date}


@router.get("/chart-of-accounts")
def chart_of_accounts(
    company: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    presentation_currency: str | None = None,
    include_disabled: bool = False,
):
    db = get_db()
    return _present(
        db,
        _chart_of_accounts(db, company, from_date, to_date,
                           include_disabled=include_disabled),
        company, presentation_currency, to_date)


def _general_ledger(db, filters=None):
    filters = filters or {}
    where = ["is_cancelled = 0"]
    params = []

    if filters.get("account"):
        where.append("account = ?")
        params.append(filters["account"])
    if filters.get("party"):
        where.append("party = ?")
        params.append(filters["party"])
    if filters.get("voucher_type"):
        where.append("voucher_type = ?")
        params.append(filters["voucher_type"])
    if filters.get("from_date"):
        where.append("posting_date >= ?")
        params.append(filters["from_date"])
    if filters.get("to_date"):
        where.append("posting_date <= ?")
        params.append(filters["to_date"])
    if filters.get("company"):
        where.append("company = ?")
        params.append(filters["company"])

    limit = int(filters.get("limit", 200))
    offset = int(filters.get("offset", 0))
    where_str = " AND ".join(where)

    # Total count for pagination controls.
    total = db.sql(
        f'SELECT COUNT(*) AS n FROM "GL Entry" WHERE {where_str}',
        params,
    )[0]["n"]

    # Running balance must still be correct on page N: sum the net of every
    # row that precedes this page's first row, then accumulate within the
    # page. Sub-select avoids pulling the whole ledger into memory.
    opening_balance = 0
    if offset > 0:
        opening_rows = db.sql(
            f"""SELECT COALESCE(SUM(debit - credit), 0) AS opening
                FROM (
                    SELECT debit, credit FROM "GL Entry"
                    WHERE {where_str}
                    ORDER BY posting_date, name
                    LIMIT ?
                )""",
            params + [offset],
        )
        opening_balance = flt(opening_rows[0]["opening"]) if opening_rows else 0

    rows = db.sql(
        f"""SELECT posting_date, account, party_type, party,
                   debit, credit, voucher_type, voucher_no, remarks
            FROM "GL Entry"
            WHERE {where_str}
            ORDER BY posting_date, name
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )

    balance = opening_balance
    result = []
    for row in rows:
        balance += flt(row["debit"]) - flt(row["credit"])
        entry = dict(row)
        entry["balance"] = flt(balance, 2)
        result.append(entry)

    return {
        "rows": result,
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "opening_balance": flt(opening_balance, 2),
    }


@router.get("/general-ledger")
def general_ledger(
    account: str | None = None,
    party: str | None = None,
    voucher_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    company: str | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
):
    filters = {}
    if account: filters["account"] = account
    if party: filters["party"] = party
    if voucher_type: filters["voucher_type"] = voucher_type
    if from_date: filters["from_date"] = from_date
    if to_date: filters["to_date"] = to_date
    if company: filters["company"] = company
    filters["limit"] = limit
    filters["offset"] = offset
    return _general_ledger(get_db(), filters)


def _stock_balance(db, item_code=None, warehouse=None):
    filters = {}
    if item_code:
        filters["item_code"] = item_code
    if warehouse:
        filters["warehouse"] = warehouse

    bins = db.get_all(
        "Bin",
        filters=filters if filters else None,
        fields=["item_code", "warehouse", "actual_qty", "ordered_qty",
                "reserved_qty", "valuation_rate", "stock_value"],
    )

    result = []
    for b in bins:
        item_name = db.get_value("Item", b["item_code"], "item_name") or b["item_code"]
        entry = dict(b)
        entry["item_name"] = item_name
        result.append(entry)

    return {"rows": result}


@router.get("/stock-balance")
def stock_balance(
    item_code: str | None = None,
    warehouse: str | None = None,
):
    return _stock_balance(get_db(), item_code, warehouse)


def _dashboard_summary(db, company=None):
    """Compute dashboard summary metrics."""

    company_filter = ""
    params = []
    if company:
        company_filter = " AND company = ?"
        params.append(company)

    # Amounts are summed in base currency: each invoice's document-currency
    # figure is scaled by its own (historical) conversion_rate, so foreign
    # invoices aren't added in as if they were base-currency amounts.

    # Total revenue (submitted sales invoices)
    revenue = db.sql(
        f'SELECT COALESCE(SUM(grand_total * COALESCE(conversion_rate, 1)), 0) as total '
        f'FROM "Sales Invoice" WHERE docstatus = 1{company_filter}',
        params,
    )

    # Outstanding receivable
    receivable = db.sql(
        f'SELECT COALESCE(SUM(outstanding_amount * COALESCE(conversion_rate, 1)), 0) as total '
        f'FROM "Sales Invoice" WHERE docstatus = 1 AND outstanding_amount > 0{company_filter}',
        params,
    )

    # Outstanding payable
    payable = db.sql(
        f'SELECT COALESCE(SUM(outstanding_amount * COALESCE(conversion_rate, 1)), 0) as total '
        f'FROM "Purchase Invoice" WHERE docstatus = 1 AND outstanding_amount > 0{company_filter}',
        params,
    )

    # Total stock value
    stock_value = db.sql(
        'SELECT COALESCE(SUM(stock_value), 0) as total FROM "Bin"', [],
    )

    # Recent documents (last 10 across key types)
    recent = []
    for doctype in ["Sales Invoice", "Purchase Invoice", "Payment Entry", "Sales Order", "Quotation"]:
        docs = db.get_all(
            doctype,
            fields=["name", "status", "docstatus", "creation"],
            order_by="creation DESC",
            limit=3,
        )
        for d in docs:
            entry = dict(d)
            entry["doctype"] = doctype
            recent.append(entry)

    recent.sort(key=lambda x: x.get("creation", ""), reverse=True)

    return {
        "total_revenue": flt(revenue[0]["total"], 2) if revenue else 0,
        "outstanding_receivable": flt(receivable[0]["total"], 2) if receivable else 0,
        "outstanding_payable": flt(payable[0]["total"], 2) if payable else 0,
        "total_stock_value": flt(stock_value[0]["total"], 2) if stock_value else 0,
        "recent_documents": recent[:10],
    }


@router.get("/dashboard-summary")
def dashboard_summary(company: str | None = None):
    return _dashboard_summary(get_db(), company)


# --- Profit & Loss ---

def _profit_and_loss(db, company=None, from_date=None, to_date=None):
    """Income and Expense accounts grouped by root_type."""
    filters = {"is_group": 0}
    if company:
        filters["company"] = company

    accounts = db.get_all(
        "Account", filters=filters,
        fields=["name", "account_name", "root_type", "report_type"],
        order_by="root_type, name",
    )

    date_clause = ""
    params = []
    if from_date:
        date_clause += " AND posting_date >= ?"
        params.append(from_date)
    if to_date:
        date_clause += " AND posting_date <= ?"
        params.append(to_date)
    if company:
        date_clause += " AND company = ?"
        params.append(company)

    gl_data = db.sql(
        f"""SELECT account,
                   COALESCE(SUM(debit), 0) as total_debit,
                   COALESCE(SUM(credit), 0) as total_credit
            FROM "GL Entry"
            WHERE is_cancelled = 0 {date_clause}
            GROUP BY account""",
        params,
    )
    gl_map = {row["account"]: row for row in gl_data}

    income_rows = []
    expense_rows = []
    total_income = 0
    total_expense = 0

    for acc in accounts:
        if acc["root_type"] not in ("Income", "Expense"):
            continue
        gl = gl_map.get(acc["name"])
        if not gl:
            continue
        debit = flt(gl["total_debit"], 2)
        credit = flt(gl["total_credit"], 2)
        if not debit and not credit:
            continue
        # Income: credit balance (positive = earned). Expense: debit balance (positive = spent).
        balance = flt(credit - debit, 2) if acc["root_type"] == "Income" else flt(debit - credit, 2)
        row = {
            "account": acc["name"],
            "account_name": acc["account_name"],
            "root_type": acc["root_type"],
            "amount": abs(balance),
        }
        if acc["root_type"] == "Income":
            income_rows.append(row)
            total_income += abs(balance)
        else:
            expense_rows.append(row)
            total_expense += abs(balance)

    return {
        "income": income_rows,
        "expense": expense_rows,
        "total_income": flt(total_income, 2),
        "total_expense": flt(total_expense, 2),
        "net_profit": flt(total_income - total_expense, 2),
    }


@router.get("/profit-and-loss")
def profit_and_loss(
    company: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    presentation_currency: str | None = None,
):
    db = get_db()
    return _present(db, _profit_and_loss(db, company, from_date, to_date),
                    company, presentation_currency, to_date)


# --- Balance Sheet ---

def _balance_sheet(db, company=None, as_of_date=None):
    """Asset, Liability, and Equity accounts as of a date."""
    filters = {"is_group": 0}
    if company:
        filters["company"] = company

    accounts = db.get_all(
        "Account", filters=filters,
        fields=["name", "account_name", "root_type", "report_type"],
        order_by="root_type, name",
    )

    date_clause = ""
    params = []
    if as_of_date:
        date_clause += " AND posting_date <= ?"
        params.append(as_of_date)
    if company:
        date_clause += " AND company = ?"
        params.append(company)

    gl_data = db.sql(
        f"""SELECT account,
                   COALESCE(SUM(debit), 0) as total_debit,
                   COALESCE(SUM(credit), 0) as total_credit
            FROM "GL Entry"
            WHERE is_cancelled = 0 {date_clause}
            GROUP BY account""",
        params,
    )
    gl_map = {row["account"]: row for row in gl_data}

    asset_rows = []
    liability_rows = []
    equity_rows = []
    total_asset = 0
    total_liability = 0
    total_equity = 0

    for acc in accounts:
        if acc["root_type"] not in ("Asset", "Liability", "Equity"):
            continue
        gl = gl_map.get(acc["name"])
        if not gl:
            continue
        debit = flt(gl["total_debit"], 2)
        credit = flt(gl["total_credit"], 2)
        if not debit and not credit:
            continue
        # Asset: debit balance. Liability/Equity: credit balance.
        if acc["root_type"] == "Asset":
            balance = flt(debit - credit, 2)
        else:
            balance = flt(credit - debit, 2)
        row = {
            "account": acc["name"],
            "account_name": acc["account_name"],
            "root_type": acc["root_type"],
            "balance": balance,
        }
        if acc["root_type"] == "Asset":
            asset_rows.append(row)
            total_asset += balance
        elif acc["root_type"] == "Liability":
            liability_rows.append(row)
            total_liability += balance
        else:
            equity_rows.append(row)
            total_equity += balance

    # Add retained earnings (net P&L) to equity
    pl = _profit_and_loss(db, company, to_date=as_of_date)
    net_profit = flt(pl["net_profit"], 2)
    if net_profit:
        equity_rows.append({
            "account": "Retained Earnings (Current Period)",
            "account_name": "Retained Earnings (Current Period)",
            "root_type": "Equity",
            "balance": net_profit,
        })
        total_equity += net_profit

    return {
        "assets": asset_rows,
        "liabilities": liability_rows,
        "equity": equity_rows,
        "total_assets": flt(total_asset, 2),
        "total_liabilities": flt(total_liability, 2),
        "total_equity": flt(total_equity, 2),
        "total_liabilities_and_equity": flt(total_liability + total_equity, 2),
    }


@router.get("/balance-sheet")
def balance_sheet(
    company: str | None = None,
    as_of_date: str | None = None,
    presentation_currency: str | None = None,
):
    db = get_db()
    return _present(db, _balance_sheet(db, company, as_of_date),
                    company, presentation_currency, as_of_date)


# --- Accounts Receivable Aging ---

def _ar_aging(db, company=None, as_of_date=None):
    """Outstanding Sales Invoices bucketed by age, as of a given date.

    Rebuilds the outstanding balance historically rather than using the
    current `outstanding_amount` column, so setting as_of_date to a past
    date actually "rewinds" the ledger:

      outstanding_at_date = grand_total
                            - Payment Entries allocated ≤ as_of_date
                            - Credit Notes (return SIs) posted ≤ as_of_date
    """
    if not as_of_date:
        as_of_date = date.today().isoformat()

    where = [
        "si.docstatus = 1",
        "COALESCE(si.is_return, 0) = 0",
        "si.posting_date <= ?",
    ]
    params: list = [as_of_date]
    if company:
        where.append("si.company = ?")
        params.append(company)

    # Per-invoice outstanding_at_date is computed with two correlated
    # sub-queries: sum allocations and sum credit-note reductions, both
    # filtered to documents that existed on as_of_date.
    invoices = db.sql(
        f"""SELECT si.name, si.customer, si.posting_date, si.due_date,
                   si.grand_total, COALESCE(si.conversion_rate, 1) AS conversion_rate,
                   si.grand_total
                     - COALESCE((
                         SELECT SUM(per.allocated_amount)
                         FROM "Payment Entry Reference" per
                         JOIN "Payment Entry" pe ON pe.name = per.parent
                         WHERE pe.docstatus = 1
                           AND pe.posting_date <= ?
                           AND per.reference_doctype = 'Sales Invoice'
                           AND per.reference_name = si.name
                       ), 0)
                     - COALESCE((
                         SELECT SUM(ABS(sir.grand_total))
                         FROM "Sales Invoice" sir
                         WHERE sir.docstatus = 1
                           AND sir.is_return = 1
                           AND sir.return_against = si.name
                           AND sir.posting_date <= ?
                       ), 0) AS outstanding_at_date
            FROM "Sales Invoice" si
            WHERE {' AND '.join(where)}
            ORDER BY si.due_date""",
        [as_of_date, as_of_date] + params,
    )

    rows = []
    totals = {"outstanding": 0, "current": 0, "b1_30": 0, "b31_60": 0, "b61_90": 0, "b90_plus": 0}

    for inv in invoices:
        # outstanding_at_date is in the invoice's document currency; scale by its
        # own historical rate so the report aggregates in base currency.
        outstanding = flt(flt(inv["outstanding_at_date"], 2) * flt(inv["conversion_rate"] or 1), 2)
        if outstanding <= 0:
            continue

        due = inv["due_date"] or inv["posting_date"]
        days_overdue = (date.fromisoformat(as_of_date) - date.fromisoformat(due)).days
        if days_overdue < 0:
            days_overdue = 0

        bucket = {"current": 0, "b1_30": 0, "b31_60": 0, "b61_90": 0, "b90_plus": 0}
        if days_overdue == 0:
            bucket["current"] = outstanding
        elif days_overdue <= 30:
            bucket["b1_30"] = outstanding
        elif days_overdue <= 60:
            bucket["b31_60"] = outstanding
        elif days_overdue <= 90:
            bucket["b61_90"] = outstanding
        else:
            bucket["b90_plus"] = outstanding

        customer_name = db.get_value("Customer", inv["customer"], "customer_name") or inv["customer"]
        rows.append({
            "invoice": inv["name"],
            "customer": inv["customer"],
            "customer_name": customer_name,
            "posting_date": inv["posting_date"],
            "due_date": due,
            "days_overdue": max(days_overdue, 0),
            "outstanding": outstanding,
            **bucket,
        })

        totals["outstanding"] += outstanding
        for k in bucket:
            totals[k] += bucket[k]

    for k in totals:
        totals[k] = flt(totals[k], 2)

    return {"rows": rows, "totals": totals, "as_of_date": as_of_date}


@router.get("/ar-aging")
def ar_aging(
    company: str | None = None,
    as_of_date: str | None = None,
):
    return _ar_aging(get_db(), company, as_of_date)


# --- Accounts Payable Aging ---

def _ap_aging(db, company=None, as_of_date=None):
    """Outstanding Purchase Invoices bucketed by age, as of a given date.

    Mirror image of _ar_aging — rebuilds outstanding historically so
    past-date queries reflect what was open on that date.
    """
    if not as_of_date:
        as_of_date = date.today().isoformat()

    where = [
        "pi.docstatus = 1",
        "COALESCE(pi.is_return, 0) = 0",
        "pi.posting_date <= ?",
    ]
    params: list = [as_of_date]
    if company:
        where.append("pi.company = ?")
        params.append(company)

    invoices = db.sql(
        f"""SELECT pi.name, pi.supplier, pi.posting_date, pi.due_date,
                   pi.grand_total, COALESCE(pi.conversion_rate, 1) AS conversion_rate,
                   pi.grand_total
                     - COALESCE((
                         SELECT SUM(per.allocated_amount)
                         FROM "Payment Entry Reference" per
                         JOIN "Payment Entry" pe ON pe.name = per.parent
                         WHERE pe.docstatus = 1
                           AND pe.posting_date <= ?
                           AND per.reference_doctype = 'Purchase Invoice'
                           AND per.reference_name = pi.name
                       ), 0)
                     - COALESCE((
                         SELECT SUM(ABS(pir.grand_total))
                         FROM "Purchase Invoice" pir
                         WHERE pir.docstatus = 1
                           AND pir.is_return = 1
                           AND pir.return_against = pi.name
                           AND pir.posting_date <= ?
                       ), 0) AS outstanding_at_date
            FROM "Purchase Invoice" pi
            WHERE {' AND '.join(where)}
            ORDER BY pi.due_date""",
        [as_of_date, as_of_date] + params,
    )

    rows = []
    totals = {"outstanding": 0, "current": 0, "b1_30": 0, "b31_60": 0, "b61_90": 0, "b90_plus": 0}

    for inv in invoices:
        # outstanding_at_date is in the invoice's document currency; scale by its
        # own historical rate so the report aggregates in base currency.
        outstanding = flt(flt(inv["outstanding_at_date"], 2) * flt(inv["conversion_rate"] or 1), 2)
        if outstanding <= 0:
            continue

        due = inv["due_date"] or inv["posting_date"]
        days_overdue = (date.fromisoformat(as_of_date) - date.fromisoformat(due)).days
        if days_overdue < 0:
            days_overdue = 0

        bucket = {"current": 0, "b1_30": 0, "b31_60": 0, "b61_90": 0, "b90_plus": 0}
        if days_overdue == 0:
            bucket["current"] = outstanding
        elif days_overdue <= 30:
            bucket["b1_30"] = outstanding
        elif days_overdue <= 60:
            bucket["b31_60"] = outstanding
        elif days_overdue <= 90:
            bucket["b61_90"] = outstanding
        else:
            bucket["b90_plus"] = outstanding

        supplier_name = db.get_value("Supplier", inv["supplier"], "supplier_name") or inv["supplier"]
        rows.append({
            "invoice": inv["name"],
            "supplier": inv["supplier"],
            "supplier_name": supplier_name,
            "posting_date": inv["posting_date"],
            "due_date": due,
            "days_overdue": max(days_overdue, 0),
            "outstanding": outstanding,
            **bucket,
        })

        totals["outstanding"] += outstanding
        for k in bucket:
            totals[k] += bucket[k]

    for k in totals:
        totals[k] = flt(totals[k], 2)

    return {"rows": rows, "totals": totals, "as_of_date": as_of_date}


@router.get("/ap-aging")
def ap_aging(
    company: str | None = None,
    as_of_date: str | None = None,
):
    return _ap_aging(get_db(), company, as_of_date)
