"""The universal account spine — jurisdiction-independent taxonomy.

Every chart of accounts we will ever ship — generic today, Swiss/German/… later —
maps onto the *same* Frappe taxonomy: a ``root_type`` (Asset/Liability/Equity/
Income/Expense) plus an optional ``account_type`` (Receivable, Payable, Bank, …).
That taxonomy is what drives GL posting rules, the balance sheet / P&L split, and
reconciliation. Codes and names are just labels on top of it, and they are the
*only* thing that varies between jurisdictions.

Because the spine is universal, a **sector profile** can describe "this business
needs a work-in-progress asset" purely in spine terms (root_type=Asset,
account_type=Stock, attached at the CURRENT_ASSETS anchor) without knowing whether
the active jurisdiction numbers it 1105 (generic) or 1500 (Swiss). That is the
whole reason the profiles stay portable across packs.

Nothing here is per-country. If you find yourself adding a country-specific value
to this file, it belongs in a pack instead.
"""

# ---------------------------------------------------------------------------
# root_type — the five top-level buckets. Fixed set, drives the report split.
# ---------------------------------------------------------------------------
ROOT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")

# report_type is a pure function of root_type (Asset/Liability/Equity → Balance
# Sheet; Income/Expense → Profit and Loss). Packs never set it directly.
REPORT_TYPE_BY_ROOT = {
    "Asset": "Balance Sheet",
    "Liability": "Balance Sheet",
    "Equity": "Balance Sheet",
    "Income": "Profit and Loss",
    "Expense": "Profit and Loss",
}

# ---------------------------------------------------------------------------
# account_type — the leaf-level roles the posting engine understands. This is
# the exact set already recognised across lambda_erp/accounting. A pack or
# profile may only use values from here; anything else is a silent no-op at
# posting time.
# ---------------------------------------------------------------------------
ACCOUNT_TYPES = (
    "",  # a plain sub-account with no special posting role
    "Receivable",
    "Payable",
    "Bank",
    "Cash",
    "Stock",
    "Tax",
    "Chargeable",
    "Income Account",
    "Cost of Goods Sold",
    "Fixed Asset",
    "Accumulated Depreciation",
    "Depreciation",
    "Stock Adjustment",
    "Stock Received But Not Billed",
    "Round Off",
)

# ---------------------------------------------------------------------------
# Anchors — the semantic parent slots a sector profile attaches accounts to.
#
# A pack maps each anchor to a *real* group account in its own tree (English
# "Current Assets" in the generic pack, German "Umlaufvermögen" in a Swiss pack).
# A profile only ever names an anchor, never the pack's account name — this is
# the seam that keeps the ~7 profiles working on every jurisdiction unchanged.
# ---------------------------------------------------------------------------
CURRENT_ASSETS = "CURRENT_ASSETS"
FIXED_ASSETS = "FIXED_ASSETS"
CURRENT_LIABILITIES = "CURRENT_LIABILITIES"
EQUITY = "EQUITY"
INCOME = "INCOME"
DIRECT_COSTS = "DIRECT_COSTS"          # COGS / cost-of-sales parent
OPERATING_EXPENSES = "OPERATING_EXPENSES"

ANCHORS = (
    CURRENT_ASSETS,
    FIXED_ASSETS,
    CURRENT_LIABILITIES,
    EQUITY,
    INCOME,
    DIRECT_COSTS,
    OPERATING_EXPENSES,
)


def report_type_for(root_type: str) -> str:
    """Balance Sheet vs Profit and Loss for a root_type (spine invariant)."""
    return REPORT_TYPE_BY_ROOT.get(root_type, "")
