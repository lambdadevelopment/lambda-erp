"""Smoke-test the HistoricalSimulator against an in-memory DB.

Run a short window first, then a full 3-year run, and verify the trial balance balances.
"""
import sys
import time
from lambda_erp.database import setup
from lambda_erp.utils import _dict, flt, fmt_money
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
from lambda_erp.simulation import HistoricalSimulator


def trial_balance(db, company):
    accounts = db.get_all(
        "Account",
        filters={"company": company, "is_group": 0},
        fields=["name"],
    )
    total_debit = 0.0
    total_credit = 0.0
    for a in accounts:
        entries = db.get_all(
            "GL Entry",
            filters={"account": a["name"], "is_cancelled": 0},
            fields=["debit", "credit"],
        )
        total_debit += sum(flt(e["debit"]) for e in entries)
        total_credit += sum(flt(e["credit"]) for e in entries)
    return total_debit, total_credit


def run(start, end, label):
    print(f"\n{'=' * 60}")
    print(f"  {label}  ({start} -> {end})")
    print(f"{'=' * 60}")

    db = setup()
    company = "Lambda Corp"
    db.insert("Company", _dict(name=company, company_name=company, default_currency="USD"))
    setup_chart_of_accounts(company, "USD")
    setup_cost_center(company)

    sim = HistoricalSimulator(company=company, start=start, end=end, seed=42)
    t0 = time.time()
    stats = sim.run()
    elapsed = time.time() - t0

    print(f"\n  Elapsed: {elapsed:.1f}s")
    print(f"\n  Stats:")
    for k, v in sorted(stats.items()):
        print(f"    {k:<32} {v}")

    td, tc = trial_balance(db, company)
    print(f"\n  Trial balance:")
    print(f"    Total Debit:  {fmt_money(td)}")
    print(f"    Total Credit: {fmt_money(tc)}")
    print(f"    Diff:         {fmt_money(td - tc)}")
    if abs(td - tc) >= 0.01:
        print(f"  !!! TRIAL BALANCE OFF")
        return False
    print(f"  OK: balanced")
    return True


if __name__ == "__main__":
    # 3-month smoke test
    ok = run("2026-01-01", "2026-03-31", "3-month smoke")
    if not ok:
        sys.exit(1)

    # Full 3-year run
    ok = run("2023-04-20", "2026-04-20", "3-year full run")
    if not ok:
        sys.exit(1)
