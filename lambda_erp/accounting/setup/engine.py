"""The setup engine — pack + profile → a company's books.

This module is deliberately jurisdiction-agnostic: it never names "generic" or
"Switzerland". It resolves a :class:`LocalizationPack` (base chart + anchors +
defaults + tax hook) and an optional :class:`SectorProfile` (an anchor-keyed
overlay), merges them, and writes the result through the single account writer
in ``chart_of_accounts``. Adding a country or a sector needs no change here.

Two entry points:

* :func:`plan_company_setup` — pure preview, **no DB writes**. Returns the
  proposed chart, defaults, sector guidance, and the "big decisions" the chat
  should confirm. The setup conversation walks the user through this.
* :func:`apply_company_setup` — writes the accounts, company defaults, any
  pack tax accounts, and the default cost center, in one transaction.
"""

import copy
from typing import Optional

from lambda_erp.database import get_db
from lambda_erp.utils import _dict
from lambda_erp.accounting.chart_of_accounts import (
    create_accounts_from_tree,
    apply_company_defaults,
    account_abbr,
    setup_cost_center,
)
from lambda_erp.accounting.setup.pack import resolve_pack, LocalizationPack
from lambda_erp.accounting.setup.profiles import get_profile, SectorProfile


# ---------------------------------------------------------------------------
# Tree merge (pack base chart + sector overlay)
# ---------------------------------------------------------------------------

def _find_node(tree: dict, name: str) -> Optional[dict]:
    """Depth-first search for the details dict of the account named ``name``."""
    for account_name, details in tree.items():
        if account_name == name:
            return details
        children = details.get("children")
        if children:
            found = _find_node(children, name)
            if found is not None:
                return found
    return None


def _attach(tree: dict, parent_name: str, overlay_acct: dict) -> bool:
    """Attach one overlay account under the group account ``parent_name``.

    Returns False if the parent can't be found or the child name already exists
    there (so a profile can't silently shadow a base account). Mutates ``tree``.
    """
    parent = _find_node(tree, parent_name)
    if parent is None:
        return False
    children = parent.setdefault("children", {})
    if overlay_acct["name"] in children:
        return False
    details: dict = {"account_type": overlay_acct.get("account_type", "")}
    if overlay_acct.get("children"):
        details["children"] = overlay_acct["children"]
    children[overlay_acct["name"]] = details
    return True


def _localize_name(acct: dict, language: str) -> str:
    """The overlay account's name in the pack's language, English otherwise.

    Profiles carry a jurisdiction-neutral English ``name`` plus an ``i18n`` map
    ({lang: name}); on a German (Swiss) chart the German name is used so the
    overlay reads in the same language as the base chart.
    """
    if language and language != "en":
        localized = (acct.get("i18n") or {}).get(language)
        if localized:
            return localized
    return acct["name"]


def _merge(pack: LocalizationPack, profile: Optional[SectorProfile]):
    """Return ``(merged_tree, merged_defaults, added_names, skipped)``.

    ``added_names`` are the overlay accounts actually attached (in the pack's
    language); ``skipped`` are overlay accounts whose anchor was unmapped or
    whose name collided — surfaced so a mis-wired profile/pack pairing is visible
    rather than silent.
    """
    tree = copy.deepcopy(pack.base_chart)
    defaults = dict(pack.defaults)
    added: list[str] = []
    skipped: list[dict] = []

    if profile is not None:
        # Map each profile account's neutral English name -> the localized name
        # actually created, so profile.defaults (which reference the English
        # name) resolve to the localized account on this pack.
        name_map: dict[str, str] = {}
        for acct in profile.accounts:
            parent_name = pack.anchor_account(acct["anchor"])
            if parent_name is None:
                skipped.append({**acct, "reason": "anchor not mapped in pack"})
                continue
            display = _localize_name(acct, pack.language)
            localized = {**acct, "name": display}
            if _attach(tree, parent_name, localized):
                added.append(display)
                name_map[acct["name"]] = display
            else:
                skipped.append({**acct, "reason": "parent missing or name collision"})
        # Sector default overrides layer on top of the pack's base defaults,
        # remapped to the localized account names created above.
        for field, val in profile.defaults.items():
            defaults[field] = name_map.get(val, val)

    return tree, defaults, added, skipped


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------

def _tree_outline(tree: dict) -> list:
    """Render the merged tree as a nested, display-friendly outline."""
    out = []
    for name, details in tree.items():
        node = {"name": name}
        if details.get("account_type"):
            node["account_type"] = details["account_type"]
        children = details.get("children")
        if children:
            node["is_group"] = True
            node["children"] = _tree_outline(children)
        out.append(node)
    return out


def _resolved_defaults(company_name: str, defaults: dict) -> dict:
    abbr = account_abbr(company_name)
    return {field: f"{leaf} - {abbr}" for field, leaf in defaults.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_company_setup(company_name: str,
                       country: Optional[str] = None,
                       variant: Optional[str] = None,
                       sector: Optional[str] = None,
                       currency: Optional[str] = None) -> dict:
    """Preview a company's books without writing anything.

    Returns the resolved jurisdiction, currency, sector guidance + big decisions,
    the full account outline (base + sector overlay, with the sector-added
    accounts flagged), and the company default-account wiring. Feed this to the
    setup chat so it can explain the chart and confirm the big decisions before
    calling :func:`apply_company_setup`.
    """
    pack = resolve_pack(country, variant)
    profile = get_profile(sector)
    ccy = currency or pack.currency

    tree, defaults, added, skipped = _merge(pack, profile)

    plan = {
        "company": company_name,
        "currency": ccy,
        "jurisdiction": {
            "key": pack.key,
            "label": pack.label,
            "requested_country": country,
            "is_fallback": bool(country) and pack.country == "generic"
                           and (country or "").lower() != "generic",
            "notes": pack.notes,
            "has_standard_tax": pack.setup_tax is not None,
        },
        "sector": None,
        "accounts": _tree_outline(tree),
        "sector_added_accounts": added,
        "defaults": _resolved_defaults(company_name, defaults),
        "warnings": skipped,
    }
    if profile is not None:
        plan["sector"] = {
            "key": profile.key,
            "label": profile.label,
            "summary": profile.summary,
            "guidance": profile.guidance,
            "big_decisions": profile.big_decisions,
        }
    return plan


def apply_company_setup(company_name: str,
                        country: Optional[str] = None,
                        variant: Optional[str] = None,
                        sector: Optional[str] = None,
                        currency: Optional[str] = None,
                        force: bool = False) -> dict:
    """Create the company's chart of accounts, defaults, tax, and cost center.

    Idempotency guard: refuses if the company already has accounts unless
    ``force=True``. Creates the Company row if it does not exist yet (so the chat
    can drive setup end-to-end); an already-created company is left in place.
    Runs in a single transaction.
    """
    db = get_db()
    pack = resolve_pack(country, variant)
    profile = get_profile(sector)
    ccy = currency or pack.currency

    existing = db.get_all("Account", filters={"company": company_name}, fields=["name"])
    if existing and not force:
        return {
            "ok": False,
            "error": f"{company_name} already has {len(existing)} accounts. "
                     "Pass force=True to rebuild (this does not delete the old ones).",
        }

    if not db.exists("Company", company_name):
        db.insert("Company", _dict(
            name=company_name,
            company_name=company_name,
            default_currency=ccy,
            country=(country or ""),
        ))

    tree, defaults, added, skipped = _merge(pack, profile)

    create_accounts_from_tree(company_name, tree, ccy)
    apply_company_defaults(company_name, defaults)

    tax_summary = []
    if pack.setup_tax is not None:
        tax_summary = pack.setup_tax(company_name, ccy) or []

    cost_center = setup_cost_center(company_name)  # commits internally
    db.commit()

    def _count(nodes):
        n = 0
        for _name, d in nodes.items():
            children = d.get("children")
            if children:
                n += _count(children)
            else:
                n += 1
        return n

    return {
        "ok": True,
        "company": company_name,
        "currency": ccy,
        "jurisdiction": pack.key,
        "sector": profile.key if profile else None,
        "accounts_created": _count(tree),
        "sector_added_accounts": added,
        "cost_center": cost_center,
        "tax_summary": tax_summary,
        "warnings": skipped,
    }
