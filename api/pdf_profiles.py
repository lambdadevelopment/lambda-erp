"""Explicit, shared PDF contracts. No database/schema guessing at render time.

Plugins register a PDFProfile (or DisabledPDF with a reason) together with a
new document type. Profiles select public print fields, never whole SQL rows.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PDFTable:
    key: str
    columns: tuple[str, ...]
    required: tuple[str, ...] = ()
    nonempty: bool = False


@dataclass(frozen=True)
class PDFProfile:
    kind: str
    title: str
    fields: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    tables: tuple[PDFTable, ...] = ()
    description: str = ""
    required_any: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        if self.kind not in {'commercial', 'logistics', 'reservation', 'payment', 'journal', 'record', 'proposal'}:
            raise ValueError(f'Unknown PDF profile kind: {self.kind}')
        if not self.title.strip():
            raise ValueError('PDF profile needs a title')
        if self.kind == 'record' and not self.fields:
            raise ValueError('Record PDF profiles must explicitly select printable fields')
        if not set(self.required) <= set(self.fields) | {'name', 'company', 'customer', 'supplier', 'currency', 'items', 'accounts', 'quotations'}:
            raise ValueError('Required PDF fields must be included in the print definition')
        for table in self.tables:
            if not table.columns or not set(table.required) <= set(table.columns):
                raise ValueError('Required PDF table fields must be printable columns')
        for group in self.required_any:
            if not group or not set(group) <= set(self.fields):
                raise ValueError('Alternative PDF requirements must name printable fields')


@dataclass(frozen=True)
class DisabledPDF:
    reason: str

    def __post_init__(self):
        if not self.reason.strip():
            raise ValueError('Disabling PDF requires a reason')


PROFILES: dict[str, PDFProfile | DisabledPDF] = {}
for _dt in ('Quotation', 'Sales Order', 'Sales Invoice', 'POS Invoice', 'Purchase Order', 'Purchase Invoice'):
    PROFILES[_dt] = PDFProfile('commercial', _dt, required=('company', 'currency', 'items'))
for _dt in ('Delivery Note', 'Purchase Receipt'):
    PROFILES[_dt] = PDFProfile('logistics', _dt,
        fields=('company', 'customer' if _dt == 'Delivery Note' else 'supplier', 'posting_date', 'remarks'),
        required=('company', 'posting_date', 'items'),
        tables=(PDFTable('items', ('item_code', 'item_name', 'description', 'qty', 'uom', 'warehouse'), ('qty',), True),))
PROFILES.update({
    'Stock Entry': PDFProfile('logistics', 'Stock movement',
        fields=('company', 'posting_date', 'stock_entry_type', 'from_warehouse', 'to_warehouse', 'remarks'),
        required=('company', 'posting_date', 'stock_entry_type', 'items'),
        tables=(PDFTable('items', ('item_code', 'item_name', 'qty', 'uom', 's_warehouse', 't_warehouse'), ('item_code', 'qty'), True),)),
    'Reservation': PDFProfile('reservation', 'Reservation confirmation',
        fields=('company', 'party_type', 'party', 'allocation_mode', 'asset', 'item_code', 'warehouse', 'qty', 'from_datetime', 'to_datetime', 'purpose', 'notes', 'voucher_type', 'voucher_no'),
        required=('company', 'allocation_mode', 'item_code', 'warehouse', 'qty', 'from_datetime', 'to_datetime'),
        description='Customer/internal booking, identified unit or explicit unassigned pool, yard, quantity, period and status; no invented rental price.'),
    'Payment Entry': PDFProfile('payment', 'Payment record',
        fields=('company', 'posting_date', 'payment_type', 'party_type', 'party', 'currency', 'paid_from', 'paid_to', 'paid_amount', 'received_amount', 'remarks'),
        required=('company', 'posting_date', 'payment_type', 'currency', 'paid_from', 'paid_to', 'paid_amount', 'received_amount'),
        tables=(PDFTable('references', ('reference_doctype', 'reference_name', 'allocated_amount'), ('reference_doctype', 'reference_name', 'allocated_amount')),)),
    'Journal Entry': PDFProfile('journal', 'Journal record',
        fields=('company', 'posting_date', 'voucher_type', 'total_debit', 'total_credit', 'remark'),
        required=('company', 'posting_date', 'accounts'),
        tables=(PDFTable('accounts', ('account', 'party_type', 'party', 'debit', 'credit', 'debit_in_account_currency', 'credit_in_account_currency', 'reference_doctype', 'reference_name'), ('account', 'debit', 'credit'), True),)),
    'Proposal': PDFProfile('proposal', 'Proposal', required=('company', 'customer', 'quotations')),
    'Asset': PDFProfile('record', 'Asset overview',
        fields=('asset_name', 'item_code', 'asset_tag', 'warehouse', 'company', 'purchase_date', 'meter_reading', 'meter_uom', 'disabled'), required=('item_code', 'warehouse')),
    'Subscription': PDFProfile('record', 'Subscription overview',
        fields=('company', 'party_type', 'party', 'start_date', 'end_date', 'billing_interval', 'current_invoice_start', 'current_invoice_end'),
        required=('company', 'party_type', 'party', 'start_date', 'billing_interval'),
        tables=(PDFTable('plans', ('item_code', 'item_name', 'qty', 'rate'), ('item_code', 'qty', 'rate'), True),)),
    'Budget': PDFProfile('record', 'Budget overview',
        fields=('company', 'budget_against', 'cost_center', 'account', 'fiscal_year', 'budget_amount', 'action_if_exceeded'),
        required=('company', 'account', 'fiscal_year', 'budget_amount', 'action_if_exceeded'),
        tables=(PDFTable('monthly_distribution', ('month', 'percentage'), ('month', 'percentage')),)),
    'Pricing Rule': PDFProfile('record', 'Pricing rule overview',
        fields=('title', 'company', 'item_code', 'selling', 'buying', 'rate_or_discount', 'rate', 'discount_percentage', 'discount_amount', 'min_qty', 'valid_from', 'valid_upto', 'priority', 'enabled'), required=('title', 'rate_or_discount')),
    'Bank Account': PDFProfile('record', 'Bank account overview',
        fields=('account_name', 'company', 'account', 'iban', 'currency', 'bank_name', 'bic', 'disabled'), required=('account_name', 'company', 'account', 'iban', 'currency')),
    'Bank Transaction': PDFProfile('record', 'Bank transaction overview',
        fields=('bank_account', 'bank_account_id', 'posting_date', 'value_date', 'currency', 'deposit', 'withdrawal', 'description', 'remittance_information', 'reference_number', 'counterparty_name', 'allocated_amount', 'unallocated_amount', 'reference_doctype', 'reference_name'), required=('posting_date', 'currency', 'deposit', 'withdrawal'),
        tables=(PDFTable('details', ('amount', 'currency', 'credit_debit_indicator', 'debtor_name', 'creditor_name', 'remittance_information'), ('amount', 'currency')),),
        required_any=(('bank_account', 'bank_account_id'),)),
})


def pdf_metadata(doctype: str) -> dict:
    profile = PROFILES.get(doctype)
    if profile is None or isinstance(profile, DisabledPDF):
        return {'supported': False, 'reason': profile.reason if profile else 'No PDF definition registered'}
    return {'supported': True, 'kind': profile.kind, 'title': profile.title,
            'description': profile.description,
            'required_fields': list(profile.required), 'fields': list(profile.fields),
            'required_any': [list(group) for group in profile.required_any],
            'tables': [{'key': t.key, 'columns': list(t.columns), 'required_fields': list(t.required), 'nonempty': t.nonempty} for t in profile.tables],
            'tool': 'generate_document_pdf'}


def validate_pdf_registry() -> None:
    """Startup/CI coverage check, after plugins and their schema are loaded."""
    from api.services import DOCUMENT_CLASSES
    from lambda_erp.database import get_db
    db = get_db()
    for doctype, cls in DOCUMENT_CLASSES.items():
        profile = PROFILES.get(doctype)
        if profile is None:
            raise ValueError(f'{doctype}: register a PDFProfile or DisabledPDF(reason)')
        if isinstance(profile, DisabledPDF):
            continue
        columns = db._get_table_columns(doctype)
        unknown = (set(profile.fields) | set(profile.required)) - columns - set(cls.CHILD_TABLES)
        if unknown:
            raise ValueError(f'{doctype}: unknown PDF fields: {sorted(unknown)}')
        for table in profile.tables:
            if table.key not in cls.CHILD_TABLES:
                raise ValueError(f'{doctype}: unknown PDF child table {table.key}')
            child_columns = db._get_table_columns(cls.CHILD_TABLES[table.key][0])
            if set(table.columns) - child_columns:
                raise ValueError(f'{doctype}.{table.key}: unknown PDF columns {sorted(set(table.columns) - child_columns)}')
