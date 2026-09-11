"""Invoice settlement invariants shared by payments and manual journals."""
import math

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import flt

INVOICE_TYPES = {'Sales Invoice', 'Purchase Invoice', 'POS Invoice'}


def invoice_account(doctype, invoice):
    return invoice.get('credit_to' if doctype == 'Purchase Invoice' else 'debit_to')


def validate_reduction(doctype, name, reduction, outstanding, *, is_return=False):
    """A signed aggregate must reduce the remaining debt without crossing zero."""
    sign = -1 if is_return else 1
    if outstanding * sign < -0.000001:
        raise ValidationError(f'{doctype} {name}: outstanding has an invalid sign; reconcile the existing settlement before posting another one')
    if not math.isfinite(reduction) or reduction * sign < -0.000001:
        raise ValidationError(f'{doctype} {name}: settlement direction must match the invoice or return')
    if abs(reduction) > abs(outstanding) + 0.000001:
        raise ValidationError(f'Combined allocation on {doctype} {name} exceeds its remaining outstanding ({abs(outstanding)})')


def journal_reduction(row, doctype, invoice):
    """Translate the actual party-account movement into invoice currency."""
    db = get_db()
    expected = invoice_account(doctype, invoice)
    if not expected or row.get('account') != expected:
        raise ValidationError(f'Journal Entry reference must use the invoice receivable/payable account {expected}')
    account = db.get_value('Account', expected, ['company', 'account_currency', 'account_type', 'is_group'])
    wanted = 'Payable' if doctype == 'Purchase Invoice' else 'Receivable'
    if not account or account.company != invoice.company or account.is_group or account.account_type != wanted:
        raise ValidationError('Invoice settlement account must belong to Company and have the correct receivable/payable type')
    base_currency = db.get_value('Company', invoice.company, 'default_currency')
    account_currency = account.account_currency or base_currency
    invoice_currency = invoice.currency or base_currency
    rate = flt(invoice.get('conversion_rate')) or 1
    sign = -1 if doctype == 'Purchase Invoice' else 1
    base = sign * (flt(row.get('credit')) - flt(row.get('debit')))
    account_amount = sign * (flt(row.get('credit_in_account_currency')) - flt(row.get('debit_in_account_currency')))
    if account_currency == base_currency:
        if abs(base - account_amount) > 0.01:
            raise ValidationError('Journal settlement amounts must agree in the company/account currency')
        return base / rate
    if account_currency != invoice_currency:
        raise ValidationError('Journal settlement account currency must match invoice currency or company currency')
    if abs(base - account_amount * rate) > 0.01:
        raise ValidationError('Journal settlement must relieve the invoice at its booked exchange rate; use separate exchange gain/loss rows')
    return account_amount
