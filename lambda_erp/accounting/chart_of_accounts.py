"""
Standard Chart of Accounts setup.

In the reference implementation, the Chart of Accounts is a tree structure defined in JSON files
under reference_impl/accounts/doctype/account/chart_of_accounts/. Each country
has its own chart.

This module provides a simplified standard chart that covers the essential
account types needed for a working ERP system.
"""

from lambda_erp.utils import _dict, new_name
from lambda_erp.database import get_db


# Standard chart of accounts - matches the essential structure from the reference implementation
STANDARD_CHART = {
    "Assets": {
        "root_type": "Asset",
        "report_type": "Balance Sheet",
        "children": {
            "Current Assets": {
                "children": {
                    "Accounts Receivable": {
                        "account_type": "Receivable",
                    },
                    "Bank Accounts": {
                        "children": {
                            "Primary Bank": {"account_type": "Bank"},
                        }
                    },
                    "Cash": {"account_type": "Cash"},
                    "Stock In Hand": {"account_type": "Stock"},
                    "Stock Received But Not Billed": {
                        "account_type": "Stock Received But Not Billed",
                    },
                }
            },
            "Fixed Assets": {
                "children": {
                    "Fixed Asset Account": {"account_type": "Fixed Asset"},
                    "Accumulated Depreciation": {
                        "account_type": "Accumulated Depreciation",
                    },
                }
            },
        },
    },
    "Liabilities": {
        "root_type": "Liability",
        "report_type": "Balance Sheet",
        "children": {
            "Current Liabilities": {
                "children": {
                    "Accounts Payable": {"account_type": "Payable"},
                    "Tax Payable": {"account_type": "Tax"},
                    "Salary Payable": {"account_type": "Payable"},
                }
            },
        },
    },
    "Equity": {
        "root_type": "Equity",
        "report_type": "Balance Sheet",
        "children": {
            "Opening Balance Equity": {},
            "Retained Earnings": {},
        },
    },
    "Income": {
        "root_type": "Income",
        "report_type": "Profit and Loss",
        "children": {
            "Sales Revenue": {"account_type": "Income Account"},
            "Service Revenue": {"account_type": "Income Account"},
            "Other Income": {},
        },
    },
    "Expenses": {
        "root_type": "Expense",
        "report_type": "Profit and Loss",
        "children": {
            "Cost of Goods Sold": {"account_type": "Cost of Goods Sold"},
            "Operating Expenses": {
                "children": {
                    "Administrative Expenses": {},
                    "Marketing Expenses": {},
                    "Salary Expense": {},
                    # Standard charge accounts for supplier-invoice charges
                    # that aren't item lines — freight/shipping, customs,
                    # import duties. Referenced via Company.default_* so the
                    # chat (or a human) has a sensible target when parsing
                    # a supplier bill with those charges on it.
                    "Freight In": {"account_type": "Chargeable"},
                    "Customs & Duties": {"account_type": "Chargeable"},
                    # Realized FX gain/loss on settling foreign-currency
                    # invoices (Payment Entry). Net of debits (losses) and
                    # credits (gains); sits in P&L.
                    "Exchange Gain/Loss": {},
                    # Unrealized FX from period-end revaluation of open foreign
                    # monetary balances. Posted at period end and reversed the
                    # next period (kept separate from realized for reporting).
                    "Unrealized Exchange Gain/Loss": {},
                }
            },
            "Depreciation Expense": {"account_type": "Depreciation"},
            "Stock Adjustment": {"account_type": "Stock Adjustment"},
            "Round Off": {"account_type": "Round Off"},
        },
    },
}


# Company default-account wiring for the standard/generic chart, keyed
# {company_field: leaf_account_name} (un-suffixed). The company abbreviation
# suffix (" - ABBR") is appended at write time by apply_company_defaults. The
# generic localization pack reuses this so the new setup engine and this legacy
# path stay in lockstep.
STANDARD_DEFAULTS = {
    "default_receivable_account": "Accounts Receivable",
    "default_payable_account": "Accounts Payable",
    "default_income_account": "Sales Revenue",
    "default_expense_account": "Cost of Goods Sold",
    "round_off_account": "Round Off",
    "stock_in_hand_account": "Stock In Hand",
    "stock_received_but_not_billed": "Stock Received But Not Billed",
    "stock_adjustment_account": "Stock Adjustment",
    "default_opening_balance_equity": "Opening Balance Equity",
    "default_freight_in_account": "Freight In",
    "default_customs_account": "Customs & Duties",
    "default_exchange_gain_loss_account": "Exchange Gain/Loss",
    "default_unrealized_exchange_account": "Unrealized Exchange Gain/Loss",
}


def account_abbr(company_name):
    """The 4-char company suffix used across the chart (e.g. 'Lambda' -> 'LAMB')."""
    return company_name[:4].upper()


def create_accounts_from_tree(company_name, tree, currency="USD"):
    """Write an account tree for a company. Shared by the legacy setup and the
    localization-pack engine so there is exactly one account writer.

    ``tree`` is ``{account_name: {root_type?, report_type?, account_type?,
    children?}}``. root_type / report_type inherit from the parent when omitted.
    Does NOT commit — the caller owns the transaction boundary.
    """
    db = get_db()
    created: list[str] = []
    skipped: list[str] = []

    def _create(subtree, parent=None, root_type=None, report_type=None):
        for account_name, details in subtree.items():
            rt = details.get("root_type", root_type)
            rpt = details.get("report_type", report_type)
            has_children = "children" in details

            name = f"{account_name} - {account_abbr(company_name)}"

            # Idempotent: skip an account that already exists rather than failing
            # on the primary key. Lets setup run alongside an existing deployment
            # and fill in only what's missing. Recursion into children still runs
            # so a partially-built chart converges to complete.
            if db.exists("Account", name):
                skipped.append(name)
            else:
                db.insert("Account", _dict(
                    name=name,
                    account_name=account_name,
                    parent_account=parent,
                    company=company_name,
                    root_type=rt,
                    report_type=rpt,
                    account_type=details.get("account_type", ""),
                    account_currency=currency,
                    is_group=1 if has_children else 0,
                ))
                created.append(name)

            if has_children:
                _create(details["children"], parent=name, root_type=rt, report_type=rpt)

    _create(tree)
    return {"created": created, "skipped": skipped}


def apply_company_defaults(company_name, defaults):
    """Point Company default-account fields at their leaf accounts.

    ``defaults`` is ``{company_field: leaf_account_name}`` (un-suffixed); the
    company suffix is appended here. **Fill-only-empty:** a field already set is
    left untouched (never clobber a configured company). A target account that
    doesn't exist is skipped (never point a default at a missing account). Does
    NOT commit. Returns ``{"set", "left", "missing"}``.
    """
    abbr = account_abbr(company_name)
    db = get_db()
    to_set: dict[str, str] = {}
    result = {"set": [], "left": [], "missing": []}
    for field, leaf in defaults.items():
        if db.get_value("Company", company_name, field):
            result["left"].append(field)          # already configured — keep it
            continue
        target = f"{leaf} - {abbr}"
        if not db.exists("Account", target):
            result["missing"].append((field, target))
            continue
        to_set[field] = target
        result["set"].append(field)
    if to_set:
        db.set_value("Company", company_name, to_set)
    return result


def setup_chart_of_accounts(company_name, currency="USD"):
    """Create all accounts for a company from the standard chart.

    This mirrors the reference implementation's setup wizard step that creates the Chart of Accounts
    from a template when a new company is created. It is the jurisdiction-neutral
    legacy path; the localization-pack engine (``lambda_erp.accounting.setup``)
    builds on the same writer to support sector overlays and other jurisdictions.
    """
    db = get_db()
    create_accounts_from_tree(company_name, STANDARD_CHART, currency)
    apply_company_defaults(company_name, STANDARD_DEFAULTS)
    db.commit()
    return True


def setup_cost_center(company_name):
    """Create the default cost center for a company (idempotent).

    Skips creation if it already exists, and fills the cost-center company
    defaults only when empty, so it is safe to run alongside an existing setup.
    """
    db = get_db()
    name = f"Main - {company_name[:4].upper()}"
    if not db.exists("Cost Center", name):
        db.insert("Cost Center", _dict(
            name=name,
            cost_center_name="Main",
            company=company_name,
            is_group=0,
        ))
    if not db.get_value("Company", company_name, "default_cost_center"):
        db.set_value("Company", company_name, "default_cost_center", name)
    if not db.get_value("Company", company_name, "round_off_cost_center"):
        db.set_value("Company", company_name, "round_off_cost_center", name)
    db.commit()
    return name
