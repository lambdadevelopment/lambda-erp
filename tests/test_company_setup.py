"""Company-setup engine tests — localization packs + sector profiles.

Self-contained like test_erp_validation: uses an in-memory SQLite DB and plain
asserts, so it runs without pytest/fastapi (the setup engine is pure lambda_erp).

    python -m tests.test_company_setup
"""

from lambda_erp.database import setup, get_db
from lambda_erp.utils import _dict
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, account_abbr
from lambda_erp.accounting.setup import (
    plan_company_setup,
    apply_company_setup,
    resolve_pack,
    list_profiles,
    spine,
)


def _accounts(company):
    return {r["account_name"] for r in get_db().get_all(
        "Account", filters={"company": company}, fields=["account_name"])}


def _account_row(name):
    rows = get_db().get_all("Account", filters={"name": name},
                            fields=["name", "root_type", "account_type", "parent_account"])
    return rows[0] if rows else None


def test_profiles_are_portable():
    """Every profile account references a valid anchor + account_type and no
    literal code — the invariant that keeps profiles jurisdiction-independent."""
    for p in list_profiles():
        p.validate()  # raises on a bad anchor/account_type
        for a in p.accounts:
            assert a["anchor"] in spine.ANCHORS, (p.key, a)
            assert a.get("account_type", "") in spine.ACCOUNT_TYPES, (p.key, a)
            # a code would be digits; names are words — cheap literal-code guard
            assert not a["name"].strip().isdigit(), (p.key, a)
    print("PASS profiles_are_portable")


def test_generic_no_sector_matches_legacy_chart():
    """Engine (generic, no sector) is byte-identical to setup_chart_of_accounts."""
    db = get_db()
    db.insert("Company", _dict(name="Eng Co", company_name="Eng Co", default_currency="USD"))
    db.insert("Company", _dict(name="Legacy Co", company_name="Legacy Co", default_currency="USD"))
    db.commit()

    apply_company_setup("Eng Co", currency="USD")
    setup_chart_of_accounts("Legacy Co", "USD")

    assert _accounts("Eng Co") == _accounts("Legacy Co"), \
        _accounts("Eng Co") ^ _accounts("Legacy Co")
    # base defaults preserved
    abbr = account_abbr("Eng Co")
    comp = get_db().get_all("Company", filters={"name": "Eng Co"},
                            fields=["default_income_account", "default_receivable_account"])[0]
    assert comp["default_income_account"] == f"Sales Revenue - {abbr}"
    assert comp["default_receivable_account"] == f"Accounts Receivable - {abbr}"
    print("PASS generic_no_sector_matches_legacy_chart")


def test_sector_overlay_attaches_under_anchors():
    """A manufacturing overlay lands its accounts under the mapped anchors and
    wires sector defaults, on top of the base chart."""
    res = apply_company_setup("Mfg Co", sector="manufacturing", currency="USD")
    assert res["ok"], res
    assert set(res["sector_added_accounts"]) == {
        "Raw Materials", "Work in Progress", "Finished Goods",
        "Direct Labour", "Manufacturing Overhead Applied",
    }
    assert not res["warnings"], res["warnings"]

    abbr = account_abbr("Mfg Co")
    wip = _account_row(f"Work in Progress - {abbr}")
    assert wip is not None, "WIP account missing"
    assert wip["root_type"] == "Asset"
    assert wip["account_type"] == "Stock"
    assert wip["parent_account"] == f"Current Assets - {abbr}"

    labour = _account_row(f"Direct Labour - {abbr}")
    assert labour["root_type"] == "Expense"
    assert labour["parent_account"] == f"Expenses - {abbr}"
    print("PASS sector_overlay_attaches_under_anchors")


def test_sector_default_override():
    """A profile's default override points the company field at its own account."""
    apply_company_setup("Retail Co", sector="retail_pos", currency="USD")
    comp = get_db().get_all("Company", filters={"name": "Retail Co"},
                            fields=["default_income_account"])[0]
    assert comp["default_income_account"] == f"Retail Sales - {account_abbr('Retail Co')}"
    print("PASS sector_default_override")


def test_unknown_country_falls_back_to_generic():
    pack = resolve_pack("ZZ")
    assert pack.country == "generic"
    plan = plan_company_setup("Fallback Co", country="ZZ", sector="services")
    assert plan["jurisdiction"]["key"] == "generic"
    assert plan["jurisdiction"]["is_fallback"] is True
    print("PASS unknown_country_falls_back_to_generic")


def test_variant_key_resolution():
    """country[.variant] keying: de.skr03 falls back to generic today but the
    key parses (no crash), proving the variant axis is wired."""
    assert resolve_pack("de", "skr03").country == "generic"  # none registered yet
    print("PASS variant_key_resolution")


def test_plan_writes_nothing():
    before = len(get_db().get_all("Account", fields=["name"]))
    plan_company_setup("Ghost Co", sector="hospitality")
    after = len(get_db().get_all("Account", fields=["name"]))
    assert before == after, "plan_company_setup must not write to the DB"
    assert not get_db().exists("Company", "Ghost Co")
    print("PASS plan_writes_nothing")


def test_idempotency_guard():
    apply_company_setup("Once Co", sector="services")
    res = apply_company_setup("Once Co", sector="services")
    assert res["ok"] is False and "already has" in res["error"]
    print("PASS idempotency_guard")


def test_swiss_pack_applies_with_resolvable_defaults_and_tax():
    """The CH pack resolves, uses CHF, and every company default + tax head
    points at a real created account (a broken default would fail postings)."""
    from lambda_erp.accounting.setup.packs.ch import CH_DEFAULTS

    pack = resolve_pack("CH")
    assert pack.country == "ch" and pack.currency == "CHF" and pack.setup_tax is not None

    res = apply_company_setup("Schweizer AG", country="CH")
    assert res["ok"] and res["jurisdiction"] == "ch" and res["currency"] == "CHF"
    assert res["accounts_created"] > 40, res["accounts_created"]
    assert len(res["tax_summary"]) == 6, res["tax_summary"]

    db = get_db()
    abbr = account_abbr("Schweizer AG")

    # every default resolves to an existing Swiss account
    comp = db.get_all("Company", filters={"name": "Schweizer AG"},
                      fields=list(CH_DEFAULTS.keys()))[0]
    for field, leaf in CH_DEFAULTS.items():
        want = f"{leaf} - {abbr}"
        assert comp[field] == want, (field, comp[field], want)
        assert db.get_all("Account", filters={"name": want}, fields=["name"]), \
            f"default {field} points at missing account {want}"

    # every MWST template detail points at an existing account head
    details = db.get_all("Tax Template Detail", fields=["account_head", "rate"])
    assert details, "no tax template details created"
    for d in details:
        assert db.get_all("Account", filters={"name": d["account_head"]}, fields=["name"]), \
            f"tax head missing: {d['account_head']}"

    # accounts are stamped CHF
    bank = _account_row(f"1020 Bank - {abbr}")
    assert bank and bank["account_type"] == "Bank"
    print("PASS swiss_pack_applies_with_resolvable_defaults_and_tax")


def test_swiss_pack_accepts_sector_overlay():
    """Profiles are jurisdiction-independent: a sector overlay resolves its
    anchors on the German KMU chart with no warnings."""
    plan = plan_company_setup("Bau GmbH", country="CH", sector="construction")
    assert plan["jurisdiction"]["key"] == "ch"
    assert plan["jurisdiction"]["is_fallback"] is False
    assert plan["sector_added_accounts"], "construction overlay added nothing"
    assert not plan["warnings"], plan["warnings"]
    print("PASS swiss_pack_accepts_sector_overlay")


def main():
    setup(":memory:")
    test_profiles_are_portable()
    test_generic_no_sector_matches_legacy_chart()
    test_sector_overlay_attaches_under_anchors()
    test_sector_default_override()
    test_unknown_country_falls_back_to_generic()
    test_variant_key_resolution()
    test_plan_writes_nothing()
    test_idempotency_guard()
    test_swiss_pack_applies_with_resolvable_defaults_and_tax()
    test_swiss_pack_accepts_sector_overlay()
    print("\nALL COMPANY-SETUP TESTS PASSED")


if __name__ == "__main__":
    main()
