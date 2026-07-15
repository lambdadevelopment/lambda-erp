"""Shared German (DATEV) helpers for the SKR03 / SKR04 packs.

The two German charts differ only in their *numbering scheme* — SKR03 is
process-ordered (Prozessgliederung), SKR04 balance-sheet-ordered
(Abschlussgliederung) — but the tax mechanics are identical: the same German
VAT (Umsatzsteuer / Vorsteuer) at the same two federal rates. Rather than copy
the ~30-line tax-template builder into both variant modules, the shared factory
lives here and each variant passes in its own tax-account leaf names.

German VAT rates (federal, unchanged since 2007; the 2020 COVID cut reverted
2021-01-01):
  * **19 %** Regelsteuersatz (standard rate)
  * **7 %**  ermäßigter Steuersatz (reduced rate — food, books, transit, …)

Output tax = *Umsatzsteuer* (a liability), input tax = *Vorsteuer* (recoverable).
"""

from lambda_erp.utils import _dict
from lambda_erp.database import get_db
from lambda_erp.accounting.chart_of_accounts import account_abbr

# The two federal rates, shared by both charts.
STANDARD_RATE = 19.0
REDUCED_RATE = 7.0


def make_de_setup_tax(sales_taxes, purchase_taxes):
    """Build a pack ``setup_tax(company, currency)`` hook for a German chart.

    ``sales_taxes`` / ``purchase_taxes`` are lists of ``(title, rate,
    account_leaf)`` where ``account_leaf`` is the tax account's name *within this
    variant's chart* (the ``- <abbr>`` company suffix is appended here). Mirrors
    ``ch.ch_setup_tax``: idempotent, does not commit (the engine owns the
    transaction), returns a short human summary.
    """

    def de_setup_tax(company_name, currency):
        db = get_db()
        abbr = account_abbr(company_name)
        summary = []

        def _template(title, tax_type, rate, account_leaf):
            name = f"{title} - {abbr}"
            if db.exists("Tax Template", name):
                return                  # idempotent: leave an existing template
            db.insert("Tax Template", _dict(
                name=name, title=title, company=company_name, tax_type=tax_type,
            ))
            db.insert("Tax Template Detail", _dict(
                name=f"{name}-1",
                parent=name,
                charge_type="On Net Total",
                account_head=f"{account_leaf} - {abbr}",
                rate=rate,
                description=title,
                idx=1,
            ))
            summary.append(f"{tax_type}: {title} @ {rate}%")

        for title, rate, leaf in sales_taxes:
            _template(title, "Sales", rate, leaf)
        for title, rate, leaf in purchase_taxes:
            _template(title, "Purchase", rate, leaf)

        return summary

    return de_setup_tax
