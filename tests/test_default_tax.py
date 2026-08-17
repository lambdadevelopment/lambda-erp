#!/usr/bin/env python3
"""A company's default sales Tax Template auto-applies to new documents.

The CH pack points `Company.default_sales_tax_template` at the 8.1% MWST
template; a Sales Invoice created WITHOUT a `taxes` table then gets MWST
automatically (the case the chat model kept forgetting), while an explicit
`taxes: []` is honoured as a deliberately tax-free document.
See lambda_erp/accounting/setup/packs/ch.py + api/services.py (_default_tax_rows).

Run:  python -m tests.test_default_tax
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_default_tax
"""
import os


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_default_tax():
    from lambda_erp.database import setup, get_db
    from lambda_erp.accounting.setup import apply_company_setup
    from lambda_erp.accounting.chart_of_accounts import account_abbr
    from lambda_erp.utils import flt
    import api.services as services

    db = setup(_reset_db())
    res = apply_company_setup("Schweizer AG", country="CH")
    assert res["ok"] and res["jurisdiction"] == "ch", res
    abbr = account_abbr("Schweizer AG")

    # --- The CH pack wired the company's default sales tax to the 8.1% template.
    default = db.get_value("Company", "Schweizer AG", "default_sales_tax_template")
    assert default == f"MWST Normalsatz 8.1% - {abbr}", default
    print(f"  CH pack default_sales_tax_template = {default}")

    # --- Unit: the hook seeds the taxes rows only when `taxes` is omitted. ------
    _, inv_cls = services.get_document_class("sales-invoice")
    seeded = services._default_tax_rows(inv_cls, {"company": "Schweizer AG"})
    assert seeded and len(seeded) == 1 and abs(flt(seeded[0]["rate"]) - 8.1) < 1e-6, seeded
    assert services._default_tax_rows(inv_cls, {"company": "Schweizer AG", "taxes": []}) is None
    assert services._default_tax_rows(inv_cls, {}) is None  # no company -> nothing
    # A doctype without a taxes table is untouched.
    _, pay_cls = services.get_document_class("payment-entry")
    assert services._default_tax_rows(pay_cls, {"company": "Schweizer AG"}) is None
    print("  _default_tax_rows: seeds only on omitted taxes; empty/[] and no-company left alone")

    # --- End to end: an invoice created without taxes picks up 8.1% MWST. ------
    db.insert("Customer", {"name": "CUST-1", "customer_name": "Muster AG", "disabled": 0})
    db.insert("Item", {"name": "SVC-1", "item_name": "Beratung", "stock_uom": "Std.",
                       "standard_rate": 200, "is_stock_item": 0, "disabled": 0})
    db.conn.commit()

    inv = services.create_document("sales-invoice", {
        "customer": "CUST-1", "company": "Schweizer AG", "posting_date": "2026-08-17",
        "items": [{"item_code": "SVC-1", "qty": 2, "rate": 200}],
    })
    net = flt(inv.get("net_total") or inv.get("total"))
    taxes = inv.get("taxes") or []
    assert net == 400.0, net
    assert len(taxes) == 1 and abs(flt(taxes[0]["rate"]) - 8.1) < 1e-6, taxes
    assert abs(flt(inv["total_taxes_and_charges"]) - net * 0.081) < 0.05, inv["total_taxes_and_charges"]
    assert abs(flt(inv["grand_total"]) - net * 1.081) < 0.05, inv["grand_total"]
    print(f"  auto MWST: net {net} + tax {inv['total_taxes_and_charges']} = grand {inv['grand_total']}")

    # --- Explicit empty taxes -> deliberately tax-free (default NOT applied). ---
    free = services.create_document("sales-invoice", {
        "customer": "CUST-1", "company": "Schweizer AG", "posting_date": "2026-08-17",
        "items": [{"item_code": "SVC-1", "qty": 1, "rate": 200}], "taxes": [],
    })
    assert (free.get("taxes") or []) == [], free.get("taxes")
    assert abs(flt(free["total_taxes_and_charges"])) < 1e-6, free["total_taxes_and_charges"]
    print("  explicit taxes:[] -> tax-free respected")

    print("PASS")


if __name__ == "__main__":
    check_default_tax()
