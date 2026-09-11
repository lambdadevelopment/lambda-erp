"""Synthetic persisted documents covering every built-in PDF contract."""
from lambda_erp.database import get_db
from api.services import DOCUMENT_CLASSES


def seed_pdf_documents():
    db = get_db()
    db.insert('Company', {'name':'PDF Co', 'company_name':'PDF Company', 'default_currency':'CHF'})
    db.insert('Customer', {'name':'C-PDF', 'customer_name':'Ada Example', 'default_currency':'CHF'})
    db.insert('Supplier', {'name':'S-PDF', 'supplier_name':'Supplier Example', 'default_currency':'CHF'})
    db.insert('Warehouse', {'name':'W-PDF', 'warehouse_name':'St. Gallen Yard', 'company':'PDF Co'})
    db.insert('Warehouse', {'name':'W-PDF-2', 'warehouse_name':'Chur Yard', 'company':'PDF Co'})
    db.insert('Item', {'name':'M-PDF', 'item_name':'Excavator Example', 'is_stock_item':1,'is_asset_tracked':1})
    db.insert('Account', {'name':'BANK-PDF','account_name':'Bank account','account_type':'Bank','company':'PDF Co','account_currency':'CHF'})
    db.insert('Account', {'name':'AR-PDF','account_name':'Receivable','account_type':'Receivable','company':'PDF Co','account_currency':'CHF'})
    common = dict(company='PDF Co', customer='C-PDF', supplier='S-PDF', currency='CHF', transaction_date='2026-09-16', posting_date='2026-09-16', docstatus=0, status='Draft', net_total=20, grand_total=20, total_taxes_and_charges=0,
                  items=[dict(item_code='M-PDF',item_name='Excavator Example',description='Machine hire',qty=2,uom='Day',rate=10,amount=20,net_rate=10,net_amount=20,warehouse='W-PDF',frequency='One-time')])
    records={}
    for dt in ('Quotation','Sales Order','Sales Invoice','POS Invoice','Purchase Order','Purchase Invoice','Delivery Note','Purchase Receipt'):
        records[dt] = dict(common)
    records.update({
        'Stock Entry': dict(company='PDF Co', posting_date='2026-09-16', stock_entry_type='Material Transfer',
                            items=[dict(item_code='M-PDF',item_name='Excavator Example',qty=3,uom='Nos',s_warehouse='W-PDF',t_warehouse='W-PDF-2')]),
        'Asset': dict(item_code='M-PDF', asset_name='Machine unit', asset_tag='SG-123', warehouse='W-PDF', status='Available'),
        'Reservation': dict(company='PDF Co', party_type='Customer',party='C-PDF',allocation_mode='Unit',asset='PDF-Asset',item_code='M-PDF',warehouse='W-PDF',qty=1,from_datetime='2026-09-16 08:00:00',to_datetime='2026-09-18 08:00:00',status='Reserved'),
        'Payment Entry': dict(company='PDF Co',posting_date='2026-09-16',payment_type='Receive',party_type='Customer',party='C-PDF',currency='CHF',paid_from='AR-PDF',paid_to='BANK-PDF',paid_amount=20,received_amount=20,references=[dict(reference_doctype='Sales Invoice',reference_name='PDF-Sales-Invoice',allocated_amount=20)]),
        'Journal Entry': dict(company='PDF Co', posting_date='2026-09-16', total_debit=20,total_credit=20, accounts=[dict(account='BANK-PDF',debit=20,credit=0,debit_in_account_currency=20,credit_in_account_currency=0),dict(account='AR-PDF',debit=0,credit=20,debit_in_account_currency=0,credit_in_account_currency=20)]),
        'Subscription': dict(company='PDF Co',party_type='Customer',party='C-PDF',start_date='2026-09-16',billing_interval='Monthly',plans=[dict(item_code='M-PDF',qty=1,rate=29)]),
        'Budget': dict(company='PDF Co', account='AR-PDF',fiscal_year='2026',budget_amount=1200,action_if_exceeded='Warn',monthly_distribution=[dict(month='January',percentage=100)]),
        'Pricing Rule': dict(title='Machine discount',company='PDF Co',item_code='M-PDF',selling=1,rate_or_discount='Discount Percentage',discount_percentage=10),
        'Bank Account': dict(account_name='Business account',company='PDF Co',account='BANK-PDF',iban='CH9300762011623852957',currency='CHF'),
        'Bank Transaction': dict(bank_account='BANK-PDF',posting_date='2026-09-16',currency='CHF',deposit=20,withdrawal=0,description='Payment received',details=[dict(amount=20,currency='CHF',debtor_name='Ada Example')]),
        'Proposal': dict(company='PDF Co',customer='C-PDF',title='Equipment proposal',proposal_date='2026-09-16',quotations=[dict(quotation='PDF-Quotation',position_title='Machine rental')]),
    })
    # Create parents before FK-bearing children, including reservation's unit.
    for dt,data in records.items():
        name = 'PDF-'+dt.replace(' ','-')
        row={k:v for k,v in data.items() if k in db._get_table_columns(dt)}
        db.insert(dt, dict(row,name=name,creation='2026-09-11',modified='2026-09-11'))
    for dt,data in records.items():
        for key,(table,_) in DOCUMENT_CLASSES[dt].CHILD_TABLES.items():
            for i,row in enumerate(data.get(key) or []):
                values={k:v for k,v in row.items() if k in db._get_table_columns(table)}
                db.insert(table,dict(values,name=f'PDF-{dt}-{key}-{i}',parent='PDF-'+dt.replace(' ','-'),idx=i+1))
    db.conn.commit()
    return {dt:'PDF-'+dt.replace(' ','-') for dt in records}
