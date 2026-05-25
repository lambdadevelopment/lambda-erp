"""Period-end revaluation of open foreign-currency monetary balances.

Open foreign receivables, payables, and bank/cash balances are carried at the
historical rate(s) they were booked at. At period end their base value has
drifted from today's rate; `run_period_revaluation` restates them to the
closing rate and books the difference as **unrealized** FX gain/loss.

Principles (see docs/multicurrency-phase-4c.md):
- Never edit posted entries — revaluation posts new GL, and an auto-reversal
  dated the next day backs it out so it doesn't double-count once the balance
  settles (and realized FX is recognized then).
- Only the base value moves; the account-currency balance is unchanged, so the
  *_in_account_currency amounts on revaluation entries are 0.
"""

from lambda_erp.utils import _dict, flt, nowdate, add_days, new_name
from lambda_erp.database import get_db
from lambda_erp.controllers.currency import get_exchange_rate
from lambda_erp.accounting.general_ledger import make_gl_entries, get_account_balances
from lambda_erp.exceptions import ValidationError

# A delta smaller than this (in base currency) isn't worth a GL line.
_EPSILON = 0.005


def collect_revaluation_lines(company, date):
    """Return the per-balance revaluation breakdown (no posting).

    Each line: account, currency, kind, is_asset, foreign, current_base,
    closing_base, unrealized (= closing_base - current_base).
    """
    db = get_db()
    base_ccy = db.get_value("Company", company, "default_currency") or "USD"
    ar_account = db.get_value("Company", company, "default_receivable_account")
    ap_account = db.get_value("Company", company, "default_payable_account")

    lines = []

    def _aggregate_invoices(doctypes):
        by_ccy = {}
        for doctype in doctypes:
            rows = db.sql(
                f'SELECT currency, conversion_rate, outstanding_amount FROM "{doctype}" '
                f'WHERE company = ? AND docstatus = 1 AND outstanding_amount != 0 '
                f'AND currency IS NOT NULL AND currency != ?',
                [company, base_ccy],
            )
            for r in rows:
                agg = by_ccy.setdefault(r["currency"], [0.0, 0.0])
                out = flt(r["outstanding_amount"])
                agg[0] += out
                agg[1] += out * (flt(r["conversion_rate"]) or 1.0)
        return by_ccy

    def _add_party_lines(by_ccy, account, kind, is_asset):
        for ccy, (out_sum, current_base) in by_ccy.items():
            closing = get_exchange_rate(ccy, base_ccy, date)
            closing_base = flt(out_sum * closing, 2)
            lines.append(_dict(
                account=account, currency=ccy, kind=kind, is_asset=is_asset,
                foreign=flt(out_sum, 2), current_base=flt(current_base, 2),
                closing_base=closing_base, unrealized=flt(closing_base - flt(current_base, 2), 2),
            ))

    _add_party_lines(_aggregate_invoices(["Sales Invoice", "POS Invoice"]),
                     ar_account, "receivable", True)
    _add_party_lines(_aggregate_invoices(["Purchase Invoice"]),
                     ap_account, "payable", False)

    # Foreign bank / cash accounts: the whole balance is open.
    accts = db.sql(
        'SELECT name, account_currency FROM "Account" '
        'WHERE company = ? AND is_group = 0 AND account_currency IS NOT NULL '
        "AND account_currency != ? AND account_type IN ('Bank', 'Cash')",
        [company, base_ccy],
    )
    for a in accts:
        base_bal, ccy_bal = get_account_balances(a["name"], company)
        if abs(ccy_bal) < _EPSILON:
            continue
        closing = get_exchange_rate(a["account_currency"], base_ccy, date)
        closing_base = flt(ccy_bal * closing, 2)
        lines.append(_dict(
            account=a["name"], currency=a["account_currency"], kind="cash", is_asset=True,
            foreign=flt(ccy_bal, 2), current_base=flt(base_bal, 2),
            closing_base=closing_base, unrealized=flt(closing_base - flt(base_bal, 2), 2),
        ))

    return lines


def _build_entries(lines, fx_account, company, date, voucher_no, swap):
    """Control leg moves the base value in the account's natural direction
    (asset -> debit on a gain); the Unrealized FX account is the contra.
    Account-currency amounts stay 0 — only the base translation changes.
    Negative deltas are left negative; make_gl_entries toggles them."""
    entries = []
    for ln in lines:
        d = flt(ln["unrealized"], 2)
        if abs(d) < _EPSILON:
            continue
        ctrl_debit, ctrl_credit = (d, 0) if ln["is_asset"] else (0, d)
        fx_debit, fx_credit = (0, d) if ln["is_asset"] else (d, 0)
        if swap:
            ctrl_debit, ctrl_credit = ctrl_credit, ctrl_debit
            fx_debit, fx_credit = fx_credit, fx_debit
        remark = (f"{'Reversal of ' if swap else ''}unrealized FX revaluation "
                  f"{ln['currency']} ({ln['kind']})")
        entries.append(_dict(
            account=ln["account"], debit=ctrl_debit, credit=ctrl_credit,
            debit_in_account_currency=0, credit_in_account_currency=0,
            voucher_type="Period Revaluation", voucher_no=voucher_no,
            posting_date=date, company=company, remarks=remark,
        ))
        entries.append(_dict(
            account=fx_account, debit=fx_debit, credit=fx_credit,
            debit_in_account_currency=0, credit_in_account_currency=0,
            voucher_type="Period Revaluation", voucher_no=voucher_no,
            posting_date=date, company=company, remarks=remark,
        ))
    return entries


def run_period_revaluation(company, date=None, *, post=True):
    """Restate open foreign balances to the closing rate at `date`.

    Posts a balanced revaluation voucher dated `date` plus an auto-reversal
    dated the next day, then returns a result dict (breakdown + what was
    posted + the net P&L impact). With post=False it's a dry run — the
    breakdown only, nothing posted. Raises if a foreign balance exists but no
    Unrealized Exchange Gain/Loss account is configured.
    """
    db = get_db()
    on_date = date or nowdate()
    base_ccy = db.get_value("Company", company, "default_currency") or "USD"
    lines = collect_revaluation_lines(company, on_date)
    postable = [ln for ln in lines if abs(flt(ln["unrealized"], 2)) >= _EPSILON]

    # Net P&L impact in base currency: a gain on an asset and a (sign-flipped)
    # loss on a liability. Positive = net unrealized gain.
    net_pl = flt(sum(
        (ln["unrealized"] if ln["is_asset"] else -ln["unrealized"]) for ln in lines
    ), 2)

    result = {
        "company": company,
        "date": on_date,
        "base_currency": base_ccy,
        "lines": lines,
        "net_unrealized_pl": net_pl,
        "posted": False,
        "voucher_no": None,
        "reversal_voucher_no": None,
        "reversal_date": None,
    }

    if post and postable:
        fx_account = db.get_value("Company", company, "default_unrealized_exchange_account")
        if not fx_account:
            raise ValidationError(
                "No Unrealized Exchange Gain/Loss account is configured on the company; "
                "cannot post the period revaluation."
            )
        reval_no = new_name("REVAL")
        reversal_no = new_name("REVAL")
        next_date = add_days(on_date, 1).isoformat()
        make_gl_entries(_build_entries(postable, fx_account, company, on_date, reval_no, swap=False))
        make_gl_entries(_build_entries(postable, fx_account, company, next_date, reversal_no, swap=True))
        result.update(posted=True, voucher_no=reval_no,
                      reversal_voucher_no=reversal_no, reversal_date=next_date)

    return result
