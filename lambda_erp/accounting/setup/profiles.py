"""Sector profiles — the operating-mode lens over a jurisdiction base chart.

A profile is deliberately **jurisdiction-independent**. It describes the accounts
a given kind of business needs in pure spine terms — an anchor (where it hangs)
plus an ``account_type`` (how it posts) — and never names a literal account code
or a pack-specific account name. That is the invariant that lets the same seven
profiles apply on the generic pack today and on the Swiss/German packs later with
zero changes: the pack resolves each anchor to its own real group account.

Each profile also carries ``guidance`` — the sector's nomenclature and standard
practices. This is the in-package knowledge base the setup chat reads aloud to
walk a user through *why* their chart looks the way it does, and ``big_decisions``
are the points the chat must stop and confirm rather than auto-applying.

To add an account to a profile, give it an anchor from ``spine.ANCHORS`` and an
``account_type`` from ``spine.ACCOUNT_TYPES``. Do NOT reference a numeric code.
"""

from dataclasses import dataclass, field
from typing import Optional

from lambda_erp.accounting.setup import spine


@dataclass(frozen=True)
class SectorProfile:
    """One operating-mode overlay.

    Attributes:
        key:      stable slug (e.g. "manufacturing").
        label:    human name shown in chat / UI.
        summary:  one-line description for the picker.
        guidance: the sector knowledge base — nomenclature + standard practice.
                  Read by the setup chat to explain the chart to the user.
        accounts: overlay accounts, each a dict with keys ``anchor`` (a
                  ``spine.ANCHORS`` value), ``name``, and ``account_type`` (a
                  ``spine.ACCOUNT_TYPES`` value); optional ``is_group`` /
                  ``children`` for a sub-group.
        defaults: ``{company_field: leaf_account_name}`` overrides layered on top
                  of the pack's base defaults. The named leaf must exist in the
                  base chart or be added by this profile's ``accounts``.
        big_decisions: points the setup chat must confirm before applying.
    """

    key: str
    label: str
    summary: str
    guidance: str
    accounts: list = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    big_decisions: list = field(default_factory=list)

    def validate(self) -> None:
        """Fail loudly on an anchor/account_type typo (portability guard)."""
        for a in self.accounts:
            if a["anchor"] not in spine.ANCHORS:
                raise ValueError(
                    f"profile {self.key!r}: unknown anchor {a['anchor']!r}"
                )
            if a.get("account_type", "") not in spine.ACCOUNT_TYPES:
                raise ValueError(
                    f"profile {self.key!r}: account {a['name']!r} has invalid "
                    f"account_type {a.get('account_type')!r}"
                )


def _acct(anchor, name, account_type=""):
    return {"anchor": anchor, "name": name, "account_type": account_type}


# ---------------------------------------------------------------------------
# The seven operating-mode profiles. Ordered from lightest to most specialised.
# ---------------------------------------------------------------------------

SERVICES = SectorProfile(
    key="services",
    label="Services / consulting",
    summary="Agencies, consultancies, professional services — labour, not goods.",
    guidance=(
        "Service businesses sell time and expertise, so there is little or no "
        "inventory and 'cost of goods sold' is really cost of delivery — labour "
        "and subcontractors. Revenue is often earned over time: work performed "
        "but not yet invoiced sits in **Unbilled Revenue / WIP** (an asset), and "
        "retainers or prepayments billed ahead of delivery sit in **Deferred "
        "Revenue** (a liability) until earned. Reimbursable client expenses are "
        "tracked separately so they can be re-billed at cost."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Unbilled Revenue / WIP", ""),
        _acct(spine.CURRENT_LIABILITIES, "Deferred Revenue", ""),
        _acct(spine.INCOME, "Consulting Revenue", "Income Account"),
        _acct(spine.OPERATING_EXPENSES, "Subcontractor Costs", ""),
        _acct(spine.OPERATING_EXPENSES, "Reimbursable Client Expenses", ""),
    ],
    defaults={"default_income_account": "Service Revenue"},
    big_decisions=[
        "Recognise revenue over time (unbilled WIP + deferred revenue) rather "
        "than only at invoice? Recommended for retainers and long engagements.",
    ],
)

RETAIL_POS = SectorProfile(
    key="retail_pos",
    label="Retail / point-of-sale",
    summary="Shops and POS — high transaction volume, card float, sales tax.",
    guidance=(
        "Retail runs on high-volume small transactions. Cash taken at the till "
        "sits in a **Cash Register / Till** account; card takings clear through a "
        "**Merchant Card Clearing** account (money in transit between the terminal "
        "and the bank, net of processor fees). Sales tax / VAT collected from "
        "customers is not income — it is **Sales Tax Payable**, a liability owed to "
        "the authority. Physical stock means **shrinkage** (theft, breakage, "
        "miscounts) is a normal recurring expense reconciled at stock-take."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Cash Register / Till", "Cash"),
        _acct(spine.CURRENT_ASSETS, "Merchant Card Clearing", ""),
        _acct(spine.CURRENT_LIABILITIES, "Sales Tax Payable", "Tax"),
        _acct(spine.INCOME, "Retail Sales", "Income Account"),
        _acct(spine.OPERATING_EXPENSES, "Inventory Shrinkage", "Stock Adjustment"),
    ],
    defaults={"default_income_account": "Retail Sales"},
    big_decisions=[
        "Track card takings through a merchant-clearing account (recommended if "
        "you accept cards) so bank deposits reconcile net of processor fees?",
    ],
)

HOSPITALITY = SectorProfile(
    key="hospitality",
    label="Hospitality / food & beverage",
    summary="Restaurants, cafés, bars — split food vs beverage, tips pass-through.",
    guidance=(
        "F&B management lives on cost percentages, so food and beverage are kept "
        "on separate revenue and cost lines: **food cost %** (Food Cost ÷ Food "
        "Sales) and **pour cost %** (Beverage Cost ÷ Beverage Sales) are the "
        "headline KPIs. Tips collected on behalf of staff are **Tips Payable**, a "
        "pass-through liability, never revenue. Inventory is perishable, so waste "
        "and spoilage are expected and tracked."
    ),
    accounts=[
        _acct(spine.INCOME, "Food Sales", "Income Account"),
        _acct(spine.INCOME, "Beverage Sales", "Income Account"),
        _acct(spine.DIRECT_COSTS, "Food Cost", "Cost of Goods Sold"),
        _acct(spine.DIRECT_COSTS, "Beverage Cost", "Cost of Goods Sold"),
        _acct(spine.CURRENT_LIABILITIES, "Tips Payable", ""),
        _acct(spine.OPERATING_EXPENSES, "Spoilage & Waste", "Stock Adjustment"),
    ],
    defaults={"default_income_account": "Food Sales"},
    big_decisions=[
        "Split revenue and cost into Food vs Beverage so you can track food-cost "
        "and pour-cost percentages separately? Recommended for any F&B operation.",
    ],
)

DISTRIBUTION = SectorProfile(
    key="distribution",
    label="Distribution / wholesale",
    summary="Buy-and-resell at volume — landed cost, freight-out, rebates.",
    guidance=(
        "Wholesale is a thin-margin volume game, so cost accuracy matters. "
        "Inbound freight is part of **landed cost** and is capitalised into "
        "inventory, whereas **Freight Out / Delivery** to customers is an "
        "operating expense. Stock owned but not yet arrived sits in **Goods in "
        "Transit**. Volume customers negotiate **rebates**, which accrue as a "
        "liability and reduce net revenue rather than being an expense."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Inventory - Finished Goods", "Stock"),
        _acct(spine.CURRENT_ASSETS, "Goods in Transit", "Stock"),
        _acct(spine.INCOME, "Wholesale Revenue", "Income Account"),
        _acct(spine.OPERATING_EXPENSES, "Freight Out / Delivery", ""),
        _acct(spine.CURRENT_LIABILITIES, "Customer Rebates Payable", ""),
    ],
    defaults={"default_income_account": "Wholesale Revenue"},
    big_decisions=[
        "Capitalise inbound freight into inventory as landed cost (recommended "
        "for accurate margins) rather than expensing it immediately?",
    ],
)

IMPORT_EXPORT = SectorProfile(
    key="import_export",
    label="Import / export trade",
    summary="Cross-border goods — landed cost, customs, import VAT, FX.",
    guidance=(
        "Cross-border trade layers customs and currency onto distribution. "
        "**Landed cost** capitalises duty, inbound freight and insurance into "
        "inventory — so **Customs Duties & Import Taxes** and **Inbound Freight & "
        "Insurance** are direct costs, not overhead. Incoterms decide when title "
        "passes, which is when **Goods in Transit (Imports)** is recognised. "
        "Import VAT/GST paid at the border is usually reclaimable and tracked as "
        "**Import VAT / GST Payable**; exports are commonly zero-rated. Foreign-"
        "currency invoices create FX gains/losses (the base chart's Exchange "
        "Gain/Loss accounts already cover these)."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Goods in Transit (Imports)", "Stock"),
        _acct(spine.DIRECT_COSTS, "Customs Duties & Import Taxes", "Chargeable"),
        _acct(spine.DIRECT_COSTS, "Inbound Freight & Insurance", "Chargeable"),
        _acct(spine.CURRENT_LIABILITIES, "Import VAT / GST Payable", "Tax"),
        _acct(spine.INCOME, "Export Sales", "Income Account"),
    ],
    defaults={
        "default_income_account": "Export Sales",
        "default_customs_account": "Customs Duties & Import Taxes",
    },
    big_decisions=[
        "Capitalise duty, inbound freight and insurance into inventory as landed "
        "cost (recommended) so margins reflect the true cost of imported goods?",
        "Track import VAT/GST separately as a reclaimable liability?",
    ],
)

MANUFACTURING = SectorProfile(
    key="manufacturing",
    label="Manufacturing / production",
    summary="Make goods — raw materials → WIP → finished goods, labour + overhead.",
    guidance=(
        "Manufacturing moves inventory through three stages: **Raw Materials** → "
        "**Work in Progress** → **Finished Goods**. Product cost is the sum of "
        "materials consumed, **Direct Labour**, and applied **Manufacturing "
        "Overhead**; the difference between overhead applied and actually incurred "
        "is a variance. Cost of goods sold is recognised from Finished Goods only "
        "when the product is sold, not when it is made."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Raw Materials", "Stock"),
        _acct(spine.CURRENT_ASSETS, "Work in Progress", "Stock"),
        _acct(spine.CURRENT_ASSETS, "Finished Goods", "Stock"),
        _acct(spine.DIRECT_COSTS, "Direct Labour", ""),
        _acct(spine.DIRECT_COSTS, "Manufacturing Overhead Applied", ""),
    ],
    defaults={},
    big_decisions=[
        "Use a three-stage perpetual inventory (Raw Materials → WIP → Finished "
        "Goods) with labour and overhead absorbed into cost? Recommended for real "
        "production; skip it if you only assemble to order.",
    ],
)

CONSTRUCTION = SectorProfile(
    key="construction",
    label="Construction / project-based",
    summary="Jobs and contracts — WIP, over/under-billing, retention.",
    guidance=(
        "Project work is accounted per job over its life. Costs and revenue rarely "
        "line up in the same period, so the gap is carried on the balance sheet: "
        "**Costs in Excess of Billings** (an asset — work done, not yet billed) and "
        "**Billings in Excess of Costs** (a liability — billed ahead of work). "
        "Customers withhold **retention** until completion, tracked on both sides "
        "(**Retention Receivable** / **Retention Payable**). Costs are captured per "
        "job — materials and subcontractors — typically via cost centers per "
        "project."
    ),
    accounts=[
        _acct(spine.CURRENT_ASSETS, "Costs in Excess of Billings", ""),
        _acct(spine.CURRENT_ASSETS, "Retention Receivable", "Receivable"),
        _acct(spine.CURRENT_LIABILITIES, "Billings in Excess of Costs", ""),
        _acct(spine.CURRENT_LIABILITIES, "Retention Payable", ""),
        _acct(spine.INCOME, "Contract Revenue", "Income Account"),
        _acct(spine.DIRECT_COSTS, "Job Costs - Materials", ""),
        _acct(spine.DIRECT_COSTS, "Job Costs - Subcontractors", ""),
    ],
    defaults={"default_income_account": "Contract Revenue"},
    big_decisions=[
        "Track percentage-of-completion via over/under-billing accounts and hold "
        "retention on both sides? Recommended for contracts spanning periods.",
    ],
)


_PROFILES: dict[str, SectorProfile] = {}
for _p in (SERVICES, RETAIL_POS, HOSPITALITY, DISTRIBUTION,
           IMPORT_EXPORT, MANUFACTURING, CONSTRUCTION):
    _p.validate()
    _PROFILES[_p.key] = _p


def get_profile(key: Optional[str]) -> Optional[SectorProfile]:
    """Look up a profile by slug. ``None``/unknown → ``None`` (generic-only)."""
    if not key:
        return None
    return _PROFILES.get(key.strip().lower())


def list_profiles() -> list[SectorProfile]:
    """All profiles in presentation order."""
    return [SERVICES, RETAIL_POS, HOSPITALITY, DISTRIBUTION,
            IMPORT_EXPORT, MANUFACTURING, CONSTRUCTION]
