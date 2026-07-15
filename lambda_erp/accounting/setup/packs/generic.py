"""The generic / international localization pack.

This is instance #1 in the pack registry and the permanent fallback for any
country we have not localized. It is deliberately hand-authored (not derived
from any GPL/proprietary source): the base chart and defaults are the existing
``STANDARD_CHART`` / ``STANDARD_DEFAULTS`` that the rest of the ERP already
posts against, so switching the setup flow onto the pack engine is behaviour-
preserving for a generic company with no sector.

A real international small business can run on this chart indefinitely. Country
packs (ch, de, …) will sit *beside* it in the registry, never replace it.

Tax hook: the generic pack ships **no** standard indirect tax — US sales tax
varies by state and there is no single VAT regime to assume. ``setup_tax`` is
therefore ``None``; a jurisdiction with a real VAT/GST regime supplies its own
hook (that is the "hybrid" half of the design — flat CSV data can't express
reverse-charge / fiscal-position rules, so tax stays in Python per pack).
"""

from lambda_erp.accounting.chart_of_accounts import STANDARD_CHART, STANDARD_DEFAULTS
from lambda_erp.accounting.setup import spine
from lambda_erp.accounting.setup.pack import LocalizationPack, register_pack, GENERIC


# Map each semantic anchor to a real group account in STANDARD_CHART. A sector
# profile attaches its overlay accounts to these anchors without ever naming the
# English account below — a Swiss pack will map the same anchors to its own
# (German) group accounts, and the profiles carry over unchanged.
GENERIC_ANCHORS = {
    spine.CURRENT_ASSETS: "Current Assets",
    spine.FIXED_ASSETS: "Fixed Assets",
    spine.CURRENT_LIABILITIES: "Current Liabilities",
    spine.EQUITY: "Equity",
    spine.INCOME: "Income",
    # COGS is a leaf directly under the Expense root, so direct-cost overlays
    # (raw material consumed, direct labour, …) hang off "Expenses" beside it.
    spine.DIRECT_COSTS: "Expenses",
    spine.OPERATING_EXPENSES: "Operating Expenses",
}


GENERIC_PACK = register_pack(LocalizationPack(
    country=GENERIC,
    variant=None,
    label="Generic / International",
    currency="USD",
    base_chart=STANDARD_CHART,
    anchors=GENERIC_ANCHORS,
    defaults=STANDARD_DEFAULTS,
    setup_tax=None,
    notes=(
        "Jurisdiction-neutral English chart. Hand-authored; no standard indirect "
        "tax. Fallback pack for any unlocalized country."
    ),
))
