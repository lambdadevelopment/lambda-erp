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
    """country[.variant] keying: both German variants register, an exact
    de.skr04 wins, and a bare `de` resolves to the alphabetically-first variant
    (skr03) — the default-variant fallback."""
    assert resolve_pack("de", "skr04").key == "de.skr04"
    assert resolve_pack("de", "skr03").key == "de.skr03"
    assert resolve_pack("de").key == "de.skr03"          # bare de -> default variant
    assert resolve_pack("DE", "SKR04").key == "de.skr04"  # case-insensitive
    # an unregistered variant of a known country falls back to the country default
    assert resolve_pack("de", "skr99").key == "de.skr03"
    print("PASS variant_key_resolution")


def test_plan_writes_nothing():
    before = len(get_db().get_all("Account", fields=["name"]))
    plan_company_setup("Ghost Co", sector="hospitality")
    after = len(get_db().get_all("Account", fields=["name"]))
    assert before == after, "plan_company_setup must not write to the DB"
    assert not get_db().exists("Company", "Ghost Co")
    print("PASS plan_writes_nothing")


def test_rerun_configured_company_warns_then_converges():
    """Re-running on an already-configured company warns (needs_confirmation);
    with confirm_existing it converges idempotently — nothing double-created."""
    first = apply_company_setup("Once Co", sector="services")
    assert first["ok"] and first["accounts_created"] > 0

    warn = apply_company_setup("Once Co", sector="services")
    assert warn["ok"] is False and warn["needs_confirmation"] is True and warn["advisory"]

    again = apply_company_setup("Once Co", sector="services", confirm_existing=True)
    assert again["ok"] is True
    assert again["accounts_created"] == 0        # all already present
    assert again["accounts_skipped"] > 0
    print("PASS rerun_configured_company_warns_then_converges")


def test_setup_alongside_incomplete_company():
    """The transcript's case: a company pre-exists with a couple of stray hand-
    made accounts and no defaults. Setup runs alongside it WITHOUT confirmation,
    builds the full chart, fills the empty income default, leaves the strays."""
    db = get_db()
    db.insert("Company", _dict(name="Stray Co", company_name="Stray Co", default_currency="CHF"))
    db.insert("Account", _dict(name="3010 - STRA", account_name="Erlöse Papeteriewaren",
                               company="Stray Co", root_type="Income", account_type="", is_group=0))
    db.insert("Account", _dict(name="Debitoren CHF - STRA", account_name="Debitoren CHF",
                               company="Stray Co", root_type="Asset", account_type="Receivable", is_group=0))
    db.commit()

    res = apply_company_setup("Stray Co", country="CH", sector="services")
    assert res["ok"] is True, res            # not configured + currency matches -> no confirm
    assert res["added_to_existing"] is True and res["accounts_created"] > 40
    abbr = account_abbr("Stray Co")
    assert _account_row("3010 - STRA") is not None   # stray untouched
    assert _account_row(f"1100 Forderungen aus Lieferungen und Leistungen (Debitoren) - {abbr}") is not None
    comp = get_db().get_all("Company", filters={"name": "Stray Co"},
                            fields=["default_income_account"])[0]
    assert comp["default_income_account"] == f"Beratungserlöse - {abbr}"
    print("PASS setup_alongside_incomplete_company")


def test_currency_mismatch_needs_confirmation():
    db = get_db()
    db.insert("Company", _dict(name="USD Co", company_name="USD Co", default_currency="USD",
                               default_income_account="Sales Revenue - USDC"))
    db.commit()
    warn = apply_company_setup("USD Co", country="CH")   # CH -> CHF vs existing USD
    assert warn["ok"] is False and warn["needs_confirmation"] is True
    assert warn["existing"]["currency_mismatch"] is True
    print("PASS currency_mismatch_needs_confirmation")


def test_defaults_fill_empty_never_clobber():
    """A default already set is left untouched on a converging re-run."""
    db = get_db()
    db.insert("Company", _dict(name="Preset Co", company_name="Preset Co",
                               default_currency="USD",
                               default_income_account="My Custom Income - PRES"))
    db.commit()
    res = apply_company_setup("Preset Co", sector="services", confirm_existing=True)
    assert res["ok"] is True
    assert "default_income_account" in res["defaults_left_untouched"]
    comp = get_db().get_all("Company", filters={"name": "Preset Co"},
                            fields=["default_income_account"])[0]
    assert comp["default_income_account"] == "My Custom Income - PRES"
    print("PASS defaults_fill_empty_never_clobber")


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


def test_swiss_overlay_names_localized_and_service_default_resolves():
    """On the German Swiss chart, sector-overlay accounts are created in German
    and the services income default points at a real (German) account — the two
    bugs the German setup surfaced."""
    res = apply_company_setup("Beratung AG", country="CH", sector="services")
    assert res["ok"], res
    abbr = account_abbr("Beratung AG")

    # overlay accounts are German, not English
    assert "Beratungserlöse" in res["sector_added_accounts"], res["sector_added_accounts"]
    assert "Consulting Revenue" not in res["sector_added_accounts"]
    assert _account_row(f"Beratungserlöse - {abbr}") is not None
    assert _account_row(f"Consulting Revenue - {abbr}") is None

    # the services income default resolves to the German overlay account, which
    # exists (the old default 'Service Revenue' does not exist on the CH chart)
    comp = get_db().get_all("Company", filters={"name": "Beratung AG"},
                            fields=["default_income_account"])[0]
    assert comp["default_income_account"] == f"Beratungserlöse - {abbr}"
    assert _account_row(comp["default_income_account"]) is not None

    # generic stays English and its services default resolves too
    apply_company_setup("Consult Inc", sector="services")
    gabbr = account_abbr("Consult Inc")
    gcomp = get_db().get_all("Company", filters={"name": "Consult Inc"},
                             fields=["default_income_account"])[0]
    assert gcomp["default_income_account"] == f"Consulting Revenue - {gabbr}"
    assert _account_row(gcomp["default_income_account"]) is not None
    print("PASS swiss_overlay_names_localized_and_service_default_resolves")


def test_german_packs_apply_with_resolvable_defaults_and_tax():
    """Both DATEV variants resolve, use EUR, create Umsatzsteuer/Vorsteuer
    templates (19 / 7 %), and every company default + tax head points at a real
    created account. A bare `de` lands on SKR03; `de.skr04` on SKR04."""
    from lambda_erp.accounting.setup.packs.de_skr03 import DE_SKR03_DEFAULTS
    from lambda_erp.accounting.setup.packs.de_skr04 import DE_SKR04_DEFAULTS

    cases = [
        ("Muster GmbH", None, "de.skr03", DE_SKR03_DEFAULTS),   # bare de -> skr03
        ("Beispiel AG", "skr04", "de.skr04", DE_SKR04_DEFAULTS),
    ]
    for company, variant, want_key, want_defaults in cases:
        res = apply_company_setup(company, country="DE", variant=variant)
        assert res["ok"] and res["jurisdiction"] == want_key, (want_key, res)
        assert res["currency"] == "EUR", res["currency"]
        assert res["accounts_created"] > 40, res["accounts_created"]
        # 2 sales (19/7) + 2 purchase (19/7)
        assert len(res["tax_summary"]) == 4, res["tax_summary"]

        db = get_db()
        abbr = account_abbr(company)
        comp = db.get_all("Company", filters={"name": company},
                          fields=list(want_defaults.keys()))[0]
        for field, leaf in want_defaults.items():
            want = f"{leaf} - {abbr}"
            assert comp[field] == want, (company, field, comp[field], want)
            assert db.get_all("Account", filters={"name": want}, fields=["name"]), \
                f"{company}: default {field} points at missing account {want}"

        details = db.get_all("Tax Template Detail", fields=["account_head", "rate"])
        for d in details:
            assert db.get_all("Account", filters={"name": d["account_head"]}, fields=["name"]), \
                f"tax head missing: {d['account_head']}"

    # SKR03 numbers a receivable in the class-1 finance band; it is still an Asset
    abbr = account_abbr("Muster GmbH")
    rec = _account_row(f"1400 Forderungen aus Lieferungen und Leistungen - {abbr}")
    assert rec and rec["root_type"] == "Asset" and rec["account_type"] == "Receivable"
    print("PASS german_packs_apply_with_resolvable_defaults_and_tax")


def test_german_overlay_names_localized():
    """Sector overlays render in German on the DATEV chart (pack.language='de')."""
    res = apply_company_setup("Beraterhaus GmbH", country="de", sector="services")
    assert res["ok"], res
    assert res["jurisdiction"] == "de.skr03"
    assert "Beratungserlöse" in res["sector_added_accounts"], res["sector_added_accounts"]
    assert "Consulting Revenue" not in res["sector_added_accounts"]
    abbr = account_abbr("Beraterhaus GmbH")
    assert _account_row(f"Beratungserlöse - {abbr}") is not None
    comp = get_db().get_all("Company", filters={"name": "Beraterhaus GmbH"},
                            fields=["default_income_account"])[0]
    assert comp["default_income_account"] == f"Beratungserlöse - {abbr}"
    print("PASS german_overlay_names_localized")


def main():
    setup(":memory:")
    test_profiles_are_portable()
    test_generic_no_sector_matches_legacy_chart()
    test_sector_overlay_attaches_under_anchors()
    test_sector_default_override()
    test_unknown_country_falls_back_to_generic()
    test_variant_key_resolution()
    test_plan_writes_nothing()
    test_rerun_configured_company_warns_then_converges()
    test_setup_alongside_incomplete_company()
    test_currency_mismatch_needs_confirmation()
    test_defaults_fill_empty_never_clobber()
    test_swiss_pack_applies_with_resolvable_defaults_and_tax()
    test_swiss_pack_accepts_sector_overlay()
    test_swiss_overlay_names_localized_and_service_default_resolves()
    test_german_packs_apply_with_resolvable_defaults_and_tax()
    test_german_overlay_names_localized()
    print("\nALL COMPANY-SETUP TESTS PASSED")


if __name__ == "__main__":
    main()
