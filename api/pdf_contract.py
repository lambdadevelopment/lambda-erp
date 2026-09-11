"""Read-only preparation and validation of the data actually printed in a PDF."""
import math
import re
import unicodedata
from datetime import datetime

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from api.pdf_profiles import PROFILES, DisabledPDF


class PDFError(ValidationError):
    def __init__(self, message, *, code='pdf_incomplete', fields=()):
        super().__init__(message)
        self.code = code
        self.fields = list(fields)


DISPLAY_FIELDS = {'Company': 'company_name', 'Customer': 'customer_name', 'Supplier': 'supplier_name',
                  'Item': 'item_name', 'Warehouse': 'warehouse_name', 'Asset': 'asset_name',
                  'Account': 'account_name', 'Cost Center': 'cost_center_name', 'Bank Account': 'account_name',
                  'Lead': 'company_name', 'Contact': 'full_name'}
LINKS = {'company': 'Company', 'customer': 'Customer', 'supplier': 'Supplier', 'item_code': 'Item',
         'warehouse': 'Warehouse', 'from_warehouse': 'Warehouse', 'to_warehouse': 'Warehouse',
         's_warehouse': 'Warehouse', 't_warehouse': 'Warehouse', 'asset': 'Asset', 'account': 'Account',
         'paid_from': 'Account', 'paid_to': 'Account', 'cost_center': 'Cost Center',
         'bank_account': 'Account', 'bank_account_id': 'Bank Account'}
NUMERIC = {'qty', 'rate', 'amount', 'net_rate', 'net_amount', 'net_total', 'grand_total', 'total_taxes_and_charges',
           'paid_amount', 'received_amount', 'allocated_amount', 'unallocated_amount', 'deposit', 'withdrawal',
           'budget_amount', 'percentage', 'discount_percentage', 'discount_amount', 'min_qty', 'priority',
           'debit', 'credit', 'total_debit', 'total_credit', 'debit_in_account_currency', 'credit_in_account_currency', 'meter_reading'}


def missing(value):
    return value is None or value == '' or value == [] or (isinstance(value, str) and not value.strip())


def require(data, fields, path=''):
    absent = [path + f for f in fields if missing(data.get(f))]
    if absent:
        raise PDFError('PDF requires: ' + ', '.join(absent), fields=absent)


def number(value, field):
    try:
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError()
        return float(value)
    except (ValueError, TypeError, OverflowError):
        raise PDFError(f'PDF requires a finite number: {field}', fields=[field]) from None


def resolve(table, name):
    if not name:
        return None
    db = get_db()
    if not db.exists(table, name):
        raise PDFError(f'PDF reference cannot be resolved: {table} {name}')
    fields = db._get_table_columns(table)
    display = DISPLAY_FIELDS.get(table)
    return (db.get_value(table, name, display) if display in fields else None) or name


def state_for(doctype, doc):
    from api.services import DOCUMENT_CLASSES
    if doc.get('discarded'):
        return 'Discarded'
    if getattr(DOCUMENT_CLASSES[doctype], 'SUBMITTABLE', False):
        return {0: 'Draft', 1: 'Submitted', 2: 'Cancelled'}.get(doc.get('docstatus'), 'Unknown')
    return doc.get('status') or 'Record'


def prepare(doctype, doc):
    """Validate output, without calling save/validate or re-running live business rules.

    Historical cancelled documents and inactive masters remain printable; rental
    availability, current credit limits, etc. do not rewrite historical output.
    """
    from api.services import DOCUMENT_CLASSES
    declared_links = DOCUMENT_CLASSES[doctype].LINK_FIELDS
    profile = PROFILES.get(doctype)
    if profile is None or isinstance(profile, DisabledPDF):
        reason = profile.reason if isinstance(profile, DisabledPDF) else 'No PDF definition registered'
        raise PDFError(f'PDF is not supported for {doctype}: {reason}', code='pdf_unsupported')
    require(doc, ('name',) + profile.required)
    for group in profile.required_any:
        if not any(not missing(doc.get(key)) for key in group):
            raise PDFError('PDF requires at least one of: ' + ', '.join(group), fields=group)
    state = state_for(doctype, doc)
    tokens = [str(doc['name']), state]
    warnings = []
    if state in {'Draft', 'Cancelled', 'Discarded'}:
        warnings.append({'code': 'pdf_document_state', 'message': f'{doctype} {doc["name"]}: {state}'})
    company = {}
    if doc.get('company'):
        resolve('Company', doc['company'])
        company = dict(get_db().get_value('Company', doc['company'], list(get_db()._get_table_columns('Company'))) or {})
    party_type = 'Customer' if doc.get('customer') else 'Supplier' if doc.get('supplier') else doc.get('party_type')
    party_id = doc.get('customer') or doc.get('supplier') or doc.get('party')
    if party_id and party_type not in {'Customer', 'Supplier'}:
        raise PDFError('PDF requires a supported party_type', fields=['party_type'])
    party_name = resolve(party_type, party_id) if party_id else ''
    if profile.kind in {'commercial', 'logistics', 'proposal'} and doctype != 'Stock Entry':
        if not party_id:
            raise PDFError('PDF requires a customer or supplier', fields=['customer' if not doctype.startswith('Purchase') else 'supplier'])
    if party_name:
        tokens.append(str(party_name))
    if profile.kind == 'commercial':
        require(doc, ('transaction_date' if doctype in {'Quotation', 'Sales Order', 'Purchase Order'} else 'posting_date', 'net_total', 'grand_total', 'total_taxes_and_charges'))
        tokens.append(str(doc['currency']))
        for key in ('net_total', 'grand_total'):
            tokens.append(format(number(doc[key], key), '.2f'))
        number(doc['total_taxes_and_charges'], 'total_taxes_and_charges')
        for i, row in enumerate(doc['items']):
            require(row, ('qty', 'rate', 'amount'), f'items[{i}].')
            for key in ('qty', 'rate', 'amount'):
                number(row[key], f'items[{i}].{key}')
            tokens.append(format(number(row['amount'], f'items[{i}].amount'), '.2f'))
            _item(row, tokens, i)
            _quantity(row, f'items[{i}].qty', bool(doc.get('is_return')))
        for i, tax in enumerate(doc.get('taxes') or []):
            require(tax, ('tax_amount',), f'taxes[{i}].')
            number(tax['tax_amount'], f'taxes[{i}].tax_amount')
        from lambda_erp.controllers.taxes_and_totals import split_by_frequency
        for group in split_by_frequency(doc)[1]:
            # Totals for each billing period must survive custom templates too.
            tokens.extend(format(number(group[key], key), '.2f')
                          for key in ('net_total', 'grand_total'))
    for table in profile.tables:
        rows = doc.get(table.key) or []
        if not isinstance(rows, list) or table.nonempty and not rows:
            raise PDFError(f'PDF requires nonempty {table.key}', fields=[table.key])
        for i, row in enumerate(rows):
            require(row, table.required, f'{table.key}[{i}].')
            for key in table.columns:
                if key in NUMERIC and not missing(row.get(key)):
                    number(row[key], f'{table.key}[{i}].{key}')
            if table.key in {'items', 'plans'}:
                _item(row, tokens, i)
                _quantity(row, f'{table.key}[{i}].qty', bool(doc.get('is_return')))
            if doctype in {'Delivery Note', 'Purchase Receipt'}:
                if row.get('item_code') and get_db().get_value('Item', row['item_code'], 'is_stock_item'):
                    require(row, ('warehouse',), f'{table.key}[{i}].')
            if doctype == 'Stock Entry':
                movement = doc['stock_entry_type']
                if movement not in {'Material Receipt', 'Opening Stock', 'Material Issue', 'Material Transfer'}:
                    raise PDFError('PDF requires a supported stock_entry_type')
                keys = ('s_warehouse', 't_warehouse') if movement == 'Material Transfer' else ('s_warehouse',) if movement == 'Material Issue' else ('t_warehouse',)
                require(row, keys, f'items[{i}].')
    if profile.kind == 'reservation':
        _quantity(doc, 'qty')
        if number(doc['qty'], 'qty') % 1:
            raise PDFError('Reservation PDF requires a whole quantity', fields=['qty'])
        try:
            start = datetime.fromisoformat(str(doc['from_datetime']))
            end = datetime.fromisoformat(str(doc['to_datetime']))
            if end <= start:
                raise ValueError()
        except (ValueError, TypeError):
            raise PDFError('Reservation PDF requires a valid start/end period', fields=['from_datetime', 'to_datetime']) from None
        if not party_id:
            require(doc, ('purpose',))
        if doc['allocation_mode'] == 'Unit':
            require(doc, ('asset',))
            asset = get_db().get_value('Asset', doc['asset'], ['item_code'])
            # A machine can move yards after a historical booking. Print the
            # booking's stored yard without re-running current availability.
            if not asset or asset['item_code'] != doc['item_code'] or float(doc['qty']) != 1:
                raise PDFError('Reservation PDF: unit, item and quantity must agree')
        elif doc['allocation_mode'] == 'Pool':
            if doc.get('asset') or doc.get('status') == 'Out':
                raise PDFError('Reservation PDF: invalid pool/unit allocation')
            warnings.append({'code': 'asset_unassigned', 'message': 'Pool booking: no individual machine assigned.'})
            tokens.append('Pool')
        else:
            raise PDFError('Reservation PDF requires Unit or Pool allocation_mode')
        tokens.extend([str(doc['from_datetime']), str(doc['to_datetime'])])
    if profile.kind == 'payment':
        if doc['payment_type'] not in {'Receive', 'Pay', 'Internal Transfer'}:
            raise PDFError('Payment PDF requires Receive, Pay or Internal Transfer')
        if doc['payment_type'] != 'Internal Transfer' and not party_id:
            raise PDFError('Payment PDF requires a party', fields=['party'])
        if any(number(doc[k], k) <= 0 for k in ('paid_amount', 'received_amount')):
            raise PDFError('Payment PDF requires positive amounts')
    if profile.kind == 'journal':
        for key in ('total_debit', 'total_credit'):
            require(doc, (key,)); number(doc[key], key)
        if abs(float(doc['total_debit']) - float(doc['total_credit'])) > .005:
            raise PDFError('Journal PDF requires balanced debit and credit')
    for key in profile.fields:
        if key in NUMERIC and not missing(doc.get(key)):
            number(doc[key], key)
    # Explicit scalar/child selections. Do not expose raw records to record templates.
    fields = []
    for key in profile.fields:
        if not missing(doc.get(key)):
            value = _display(key, doc[key], doc, declared_links)
            fields.append({'key': key, 'value': value})
            tokens.append(value)
    tables = []
    for table in profile.tables:
        source_rows = doc.get(table.key) or []
        columns = [key for key in table.columns if key in table.required or any(not missing(row.get(key)) for row in source_rows)]
        rows = [[_display(key, row.get(key), row, DOCUMENT_CLASSES[doctype].CHILD_LINK_FIELDS.get(table.key, {})) for key in columns] for row in source_rows]
        if rows:
            tables.append({'key': table.key, 'columns': columns, 'rows': rows})
            for values in rows:
                tokens.extend(value for value in values if value != '—')
    # Only use a declared company currency; never invent USD or an amount.
    company_currency = company.get('default_currency') or ''
    if doctype in {'Subscription', 'Budget', 'Journal Entry'} and not company_currency:
        raise PDFError('PDF requires Company.default_currency', fields=['company.default_currency'])
    if doctype == 'Subscription':
        party_currency = get_db().get_value(party_type, party_id, 'default_currency') if party_id else None
        fields.append({'key': 'currency', 'value': party_currency or company_currency})
    return {'profile': profile, 'status': state, 'warnings': warnings, 'tokens': tokens,
            'fields': fields, 'tables': tables, 'party_name': party_name,
            'company': company, 'company_currency': company_currency}


def _quantity(row, field, is_return=False):
    qty = number(row.get('qty'), field)
    if (qty >= 0 if is_return else qty <= 0):
        raise PDFError(f'PDF requires {"negative return" if is_return else "positive"} quantity: {field}', fields=[field])


def _item(row, tokens, index):
    if not any(not missing(row.get(k)) for k in ('item_code', 'item_name', 'description')):
        raise PDFError(f'PDF requires item identity: items[{index}]')
    if row.get('item_code'):
        live = resolve('Item', row['item_code'])
        row['item_name'] = live  # only the isolated output snapshot is changed
    tokens.append(str(row.get('item_name') or row.get('item_code') or row['description']))


def _display(key, value, row, links=None):
    if missing(value):
        return '—'
    table = (links or {}).get(key) or LINKS.get(key)
    if key == 'party':
        table = row.get('party_type')
    if table:
        display = resolve(table, value)
        if table == 'Account':
            currency = get_db().get_value('Account', value, 'account_currency')
            if currency:
                return f'{display} ({value}, {currency})'
        return str(value) if str(display) == str(value) else f'{display} ({value})'
    if isinstance(value, float):
        number(value, key)
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


def validate_rendered_pdf(data, expected):
    """Catch empty/broken custom layouts using the rendered PDF, not HTTP/size."""
    import io
    from pypdf import PdfReader
    try:
        pages = PdfReader(io.BytesIO(data)).pages
        text = ''.join(page.extract_text() or '' for page in pages)
    except Exception as exc:
        raise PDFError('PDF could not be read after rendering', code='pdf_render_failed') from exc
    def normalized(value):
        return re.sub(r"[\s,‘’']+", '', unicodedata.normalize('NFKC', str(value))).casefold()
    haystack = normalized(text)
    # Page footers can interrupt paragraphs. Check every line in short word
    # windows as well as every individual word, so missing tail text still fails.
    checks = []
    for token in expected:
        for line in str(token).splitlines():
            words = line.split()
            if len(words) <= 12:
                checks.append(line)
            else:
                checks.extend(words)
                checks.extend(' '.join(words[i:i + 4]) for i in range(0, len(words), 4))
    absent = [token for token in checks if normalized(token) not in haystack]
    if not pages or absent:
        raise PDFError('PDF layout omitted required content: ' + ', '.join(token[:120] for token in absent[:6]), code='pdf_render_incomplete')
