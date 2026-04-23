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
                }
            },
            "Depreciation Expense": {"account_type": "Depreciation"},
            "Stock Adjustment": {"account_type": "Stock Adjustment"},
            "Round Off": {"account_type": "Round Off"},
        },
    },
}


def setup_chart_of_accounts(company_name, currency="USD"):
    """Create all accounts for a company from the standard chart.

    This mirrors the reference implementation's setup wizard step that creates the Chart of Accounts
    from a template when a new company is created.
    """
    db = get_db()

    def _create_accounts(tree, parent=None, root_type=None, report_type=None):
        for account_name, details in tree.items():
            rt = details.get("root_type", root_type)
            rpt = details.get("report_type", report_type)
            has_children = "children" in details

            name = f"{account_name} - {company_name[:4].upper()}"

            account = _dict(
                name=name,
                account_name=account_name,
                parent_account=parent,
                company=company_name,
                root_type=rt,
                report_type=rpt,
                account_type=details.get("account_type", ""),
                account_currency=currency,
                is_group=1 if has_children else 0,
            )
            db.insert("Account", account)

            if has_children:
                _create_accounts(details["children"], parent=name, root_type=rt, report_type=rpt)

    _create_accounts(STANDARD_CHART)

    # Set up company defaults
    abbr = company_name[:4].upper()
    db.set_value("Company", company_name, {
        "default_receivable_account": f"Accounts Receivable - {abbr}",
        "default_payable_account": f"Accounts Payable - {abbr}",
        "default_income_account": f"Sales Revenue - {abbr}",
        "default_expense_account": f"Cost of Goods Sold - {abbr}",
        "round_off_account": f"Round Off - {abbr}",
        "stock_in_hand_account": f"Stock In Hand - {abbr}",
        "stock_received_but_not_billed": f"Stock Received But Not Billed - {abbr}",
        "stock_adjustment_account": f"Stock Adjustment - {abbr}",
        "default_opening_balance_equity": f"Opening Balance Equity - {abbr}",
        "default_freight_in_account": f"Freight In - {abbr}",
        "default_customs_account": f"Customs & Duties - {abbr}",
    })

    db.commit()
    return True


def setup_cost_center(company_name):
    """Create default cost center for a company."""
    db = get_db()
    name = f"Main - {company_name[:4].upper()}"
    db.insert("Cost Center", _dict(
        name=name,
        cost_center_name="Main",
        company=company_name,
        is_group=0,
    ))
    db.set_value("Company", company_name, "default_cost_center", name)
    db.set_value("Company", company_name, "round_off_cost_center", name)
    db.commit()
    return name
