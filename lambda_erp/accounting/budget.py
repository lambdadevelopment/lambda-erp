"""
Budget.

Budget defines an annual spending limit for an account + cost center.
When GL entries are posted, validate_expense_against_budget() checks
whether the new expense would exceed the budget and raises an error
(Stop) or warning (Warn) accordingly.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

import warnings
import math


def validate_budget_action(action):
    if action not in ('Stop', 'Warn'):
        raise ValidationError('Budget action_if_exceeded must be Stop or Warn; correct the budget before posting')

class Budget(Document):
    DOCTYPE = "Budget"
    CHILD_TABLES = {
        "monthly_distribution": ("Monthly Distribution", None),
    }
    PREFIX = "BDG"
    LINK_FIELDS = {'company': 'Company', 'account': 'Account', 'cost_center': 'Cost Center'}
    CONDITIONAL_REQUIREMENTS = (
        'action_if_exceeded must be Stop or Warn (default). Unsupported values are rejected on save and when an existing budget is used during posting.',
        'Budget Amount must be positive and finite; linked account and cost center must belong to Company.',
    )

    def validate(self):
        if not self.account:
            raise ValidationError("Account is required")
        if not self.fiscal_year:
            raise ValidationError("Fiscal Year is required")
        if not self.company:
            raise ValidationError("Company is required")
        if not math.isfinite(flt(self.budget_amount)) or flt(self.budget_amount) <= 0:
            raise ValidationError("Budget Amount must be greater than 0")

        if not self.cost_center:
            db = get_db()
            self._data["cost_center"] = db.get_value(
                "Company", self.company, "default_cost_center"
            )

        if self._data.get("action_if_exceeded") in (None, ''):
            self._data["action_if_exceeded"] = "Warn"
        validate_budget_action(self.action_if_exceeded)

def validate_expense_against_budget(gl_entry):
    """Check if a GL entry would exceed any active budget.

    Called from make_gl_entries() in general_ledger.py before saving.
    Only checks debit entries to expense accounts.
    """
    debit = flt(gl_entry.get("debit", 0))
    if debit <= 0:
        return

    account = gl_entry.get("account")
    if not account:
        return

    db = get_db()

    # Only check expense accounts (root_type = Expense)
    account_data = db.get_value("Account", account, ["root_type", "is_group"])
    if not account_data or account_data.root_type != "Expense":
        return

    cost_center = gl_entry.get("cost_center")
    company = gl_entry.get("company")
    posting_date = gl_entry.get("posting_date") or nowdate()

    # Determine fiscal year from posting date
    year = getdate(posting_date).year

    # Find matching budgets
    filters = {"account": account, "company": company}
    if cost_center:
        filters["cost_center"] = cost_center

    budgets = db.get_all("Budget", filters=filters, fields=["*"])

    for budget in budgets:
        fiscal_year = budget.get("fiscal_year", "")
        if fiscal_year and str(year) not in fiscal_year:
            continue
        action = budget.get('action_if_exceeded')
        validate_budget_action(action)
        budget_amount = flt(budget.get('budget_amount'))
        if not math.isfinite(budget_amount) or budget_amount <= 0:
            raise ValidationError(f'Budget {budget["name"]}: Budget Amount must be positive and finite; correct it before posting')

        # Sum existing expenses for this account + cost_center in this year
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

        result = db.sql(
            """
            SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0) as total
            FROM "GL Entry"
            WHERE account = ?
              AND company = ?
              AND posting_date >= ? AND posting_date <= ?
              AND is_cancelled = 0
            """,
            [account, company, start_date, end_date],
        )

        actual_expense = flt(result[0].get("total", 0)) if result else 0
        total_with_new = actual_expense + debit

        if total_with_new > budget_amount:
            msg = (
                f"Budget exceeded for {account}: "
                f"Budget {budget_amount:.2f}, "
                f"Actual {actual_expense:.2f} + New {debit:.2f} = {total_with_new:.2f}"
            )
            if action == "Stop":
                raise ValidationError(msg)
            elif action == "Warn":
                warnings.warn(msg)
