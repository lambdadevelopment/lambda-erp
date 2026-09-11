"""Regression coverage for invoice settlement, company links and explicit zero prices."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.exceptions import ValidationError
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center


class SettlementGuards(unittest.TestCase):
    setUp = fixtures.RequirementsTest.setUp
    tearDown = fixtures.RequirementsTest.tearDown

    def post(self, slug, data):
        return services.submit_document(slug, services.create_document(slug, data)['name'])

    def invoice(self, **extra):
        return self.post('sales-invoice', {'company':'Test Co','customer':'C','items':[{'item_code':'M','qty':10,'rate':10}], **extra})

    def payment_data(self, invoice, amounts=(100,), **extra):
        return {'company':'Test Co','party_type':'Customer','party':'C','payment_type':'Receive','paid_amount':sum(amounts),
                'references':[{'reference_doctype':'Sales Invoice','reference_name':invoice['name'],'allocated_amount':amount} for amount in amounts], **extra}

    def account(self, field):
        return self.db.get_value('Company','Test Co',field)

    def journal_data(self, invoice, amounts=(100,), **extra):
        rows = [{'account':self.account('default_expense_account'),'debit':sum(amounts)}]
        rows += [{'account':invoice['debit_to'],'credit':amount,'party_type':'Customer','party':'C',
                  'reference_doctype':'Sales Invoice','reference_name':invoice['name'], **extra} for amount in amounts]
        return {'company':'Test Co','accounts':rows}

    def outstanding(self, doc):
        return self.db.get_value('Sales Invoice',doc['name'],'outstanding_amount')

    def other_company(self):
        self.db.insert('Company',{'name':'Other Co','company_name':'Other Co','default_currency':'USD'})
        setup_chart_of_accounts('Other Co','USD'); setup_cost_center('Other Co')
        self.db.insert('Warehouse',{'name':'OTHER-YARD','warehouse_name':'Other Yard','company':'Other Co'})

    def test_duplicate_rows_are_capped_but_valid_splits_and_cancel_work(self):
        for kind, build in [('payment-entry',self.payment_data), ('journal-entry',self.journal_data)]:
            invoice = self.invoice()
            before = len(self.db.get_all('GL Entry'))
            with self.assertRaisesRegex(ValidationError,'exceeds.*remaining outstanding'):
                self.post(kind,build(invoice,(60,60)))
            self.assertEqual(self.outstanding(invoice),100)
            self.assertEqual(len(self.db.get_all('GL Entry')),before)
            valid = self.post(kind,build(invoice,(40,60)))
            self.assertEqual(self.outstanding(invoice),0)
            services.cancel_document(kind,valid['name'])
            self.assertEqual(self.outstanding(invoice),100)

    def test_refund_and_its_cancellation_use_original_sign(self):
        from lambda_erp.accounting.sales_invoice import make_sales_return
        original = self.invoice()
        for kind in ('payment-entry','journal-entry'):
            if kind == 'journal-entry': original = self.invoice()
            returned = make_sales_return(original['name']).save().submit().as_dict()
            data = self.payment_data(returned,payment_type='Pay') if kind == 'payment-entry' else self.journal_data(returned,(-100,))
            wrong = self.payment_data(returned) if kind == 'payment-entry' else self.journal_data(returned)
            with self.assertRaisesRegex(ValidationError,'direction'):
                self.post(kind,wrong)
            settled = self.post(kind,data)
            self.assertEqual(self.outstanding(returned),0)
            services.cancel_document(kind,settled['name'])
            self.assertEqual(self.outstanding(returned),-100)

    def test_journal_checks_net_movement_per_invoice(self):
        invoice = self.invoice()
        journal = self.post('journal-entry',self.journal_data(invoice,(110,-10)))
        self.assertEqual(self.outstanding(invoice),0)
        services.cancel_document('journal-entry',journal['name'])
        self.assertEqual(self.outstanding(invoice),100)

    def test_legacy_journal_reference_alias_cannot_bypass_guards(self):
        invoice = self.invoice()
        data = self.journal_data(invoice)
        row = data['accounts'][1]
        row['reference_type'] = row.pop('reference_doctype')
        journal = self.post('journal-entry',data)
        self.assertEqual(self.outstanding(invoice),0)
        self.db.set_value('Journal Entry Account',journal['accounts'][1]['name'],'reference_doctype',None)
        with self.assertRaisesRegex(ValidationError,'Journal Entry'):
            services.cancel_document('sales-invoice',invoice['name'])
        services.cancel_document('journal-entry',journal['name'])
        self.assertEqual(self.outstanding(invoice),100)
        row['reference_doctype'] = 'Purchase Invoice'
        with self.assertRaisesRegex(ValidationError,'must agree'):
            services.create_document('journal-entry',data)

    def test_cross_company_settlements_and_wrong_party_account_rejected(self):
        self.other_company()
        invoice = self.invoice(company='Other Co')
        for kind, data in [('payment-entry',self.payment_data(invoice)), ('journal-entry',self.journal_data(invoice))]:
            with self.assertRaisesRegex(ValidationError,'Company'):
                self.post(kind,data)
            self.assertEqual(self.outstanding(invoice),100)
        invoice = self.invoice()
        bank = self.db.get_value('Account',{'company':'Test Co','account_type':'Bank','is_group':0},'name')
        with self.assertRaisesRegex(ValidationError,'receivable/payable account'):
            self.post('journal-entry',self.journal_data(invoice,account=bank))
        self.assertEqual(self.outstanding(invoice),100)

    def test_journal_and_payment_must_use_custom_invoice_account(self):
        self.db.insert('Account',{'name':'Custom AR','account_name':'Custom AR','company':'Test Co','account_currency':'USD','account_type':'Receivable','root_type':'Asset','is_group':0})
        invoice = self.invoice(debit_to='Custom AR')
        with self.assertRaisesRegex(ValidationError,'receivable/payable account'):
            self.post('payment-entry',self.payment_data(invoice))
        with self.assertRaisesRegex(ValidationError,'receivable/payable account'):
            self.post('journal-entry',self.journal_data(invoice,account=self.account('default_receivable_account')))
        self.post('payment-entry',self.payment_data(invoice,paid_from='Custom AR'))
        self.assertEqual(self.outstanding(invoice),0)

    def test_journal_uses_booked_exchange_rate(self):
        invoice = self.invoice(currency='EUR',conversion_rate=1.2)
        journal = self.post('journal-entry',self.journal_data(invoice,(120,)))
        self.assertEqual(self.outstanding(invoice),0)
        services.cancel_document('journal-entry',journal['name'])
        self.assertEqual(self.outstanding(invoice),100)
        with self.assertRaisesRegex(ValidationError,'amounts must agree'):
            self.post('journal-entry',self.journal_data(invoice,(120,),credit_in_account_currency=100))
        self.db.insert('Account',{'name':'EUR AR','account_name':'EUR AR','company':'Test Co','account_currency':'EUR','account_type':'Receivable','root_type':'Asset','is_group':0})
        invoice = self.invoice(currency='EUR',conversion_rate=1.2,debit_to='EUR AR')
        self.post('journal-entry',self.journal_data(invoice,(120,),credit_in_account_currency=100))
        self.assertEqual(self.outstanding(invoice),0)

    def test_invoice_cancel_requires_reversal_of_linked_journal(self):
        invoice = self.invoice()
        journal = self.post('journal-entry',self.journal_data(invoice))
        with self.assertRaisesRegex(ValidationError,'Journal Entry'):
            services.cancel_document('sales-invoice',invoice['name'])
        self.assertEqual(self.db.get_value('Sales Invoice',invoice['name'],'docstatus'),1)
        services.cancel_document('journal-entry',journal['name'])
        services.cancel_document('sales-invoice',invoice['name'])

    def test_purchase_and_pos_journal_references_also_block_cancellation(self):
        self.db.insert('Supplier',{'name':'SUP','supplier_name':'Supplier','default_currency':'USD'})
        bank = self.db.get_value('Account',{'company':'Test Co','account_type':'Bank','is_group':0},'name')
        for slug, doctype, party, extra in [('purchase-invoice','Purchase Invoice',{'supplier':'SUP'},{}),
                                          ('pos-invoice','POS Invoice',{'customer':'C'},{'payments':[{'account':bank,'amount':1}]})]:
            invoice = self.post(slug,{'company':'Test Co', **party, **extra,'items':[{'item_code':'M','qty':10,'rate':10}]})
            buying = slug == 'purchase-invoice'
            amount = invoice['outstanding_amount']
            row = {'account':invoice['credit_to' if buying else 'debit_to'],'debit' if buying else 'credit':amount,
                   'party_type':'Supplier' if buying else 'Customer','party':'SUP' if buying else 'C',
                   'reference_doctype':doctype,'reference_name':invoice['name']}
            journal = self.post('journal-entry',{'company':'Test Co','accounts':[row,{'account':self.account('default_expense_account'),'credit' if buying else 'debit':amount}]})
            self.assertEqual(self.db.get_value(doctype,invoice['name'],'outstanding_amount'),0)
            with self.assertRaisesRegex(ValidationError,'Journal Entry'): services.cancel_document(slug,invoice['name'])
            services.cancel_document('journal-entry',journal['name'])
            self.assertEqual(self.db.get_value(doctype,invoice['name'],'outstanding_amount'),amount)
            services.cancel_document(slug,invoice['name'])

    def test_supplier_refund_split_and_cancellation(self):
        from lambda_erp.accounting.purchase_invoice import make_purchase_return
        self.db.insert('Supplier',{'name':'SUP','supplier_name':'Supplier','default_currency':'USD'})
        invoice = self.post('purchase-invoice',{'company':'Test Co','supplier':'SUP','items':[{'item_code':'M','qty':10,'rate':10}]})
        returned = make_purchase_return(invoice['name']).save().submit()
        data = {'company':'Test Co','party_type':'Supplier','party':'SUP','payment_type':'Receive','paid_amount':100,
                'references':[{'reference_doctype':'Purchase Invoice','reference_name':returned.name,'allocated_amount':amount} for amount in (40,60)]}
        paid = self.post('payment-entry',data)
        self.assertEqual(self.db.get_value('Purchase Invoice',returned.name,'outstanding_amount'),0)
        services.cancel_document('payment-entry',paid['name'])
        self.assertEqual(self.db.get_value('Purchase Invoice',returned.name,'outstanding_amount'),-100)

    def test_warehouse_company_checked_on_all_stock_movement_paths(self):
        self.other_company()
        self.db.insert('Supplier',{'name':'SUP','supplier_name':'Supplier'})
        for kind, party in [('delivery-note',{'customer':'C'}), ('sales-invoice',{'customer':'C'}), ('purchase-receipt',{'supplier':'SUP'}), ('purchase-invoice',{'supplier':'SUP'})]:
            data = {'company':'Test Co', **party, 'items':[{'item_code':'S','qty':1,'rate':10,'warehouse':'OTHER-YARD'}]}
            if kind.endswith('invoice'): data['update_stock']=1
            with self.assertRaisesRegex(ValidationError,'must belong to Company'):
                services.create_document(kind,data)
        self.assertEqual(self.db.get_all('Stock Ledger Entry'),[])

    def test_zero_and_missing_prices_across_transaction_types(self):
        self.db.insert('Supplier',{'name':'SUP','supplier_name':'Supplier'})
        for kind in ('quotation','sales-order','sales-invoice','pos-invoice','delivery-note','purchase-order','purchase-invoice','purchase-receipt'):
            party = {'supplier':'SUP'} if kind.startswith('purchase-') else {'customer':'C'}
            data = {'company':'Test Co', **party, 'items':[{'item_code':'S','qty':2,'warehouse':'A'}]}
            for prices in ({'rate':0}, {'price_list_rate':0}, {'rate':0,'price_list_rate':50}):
                doc = services.create_document(kind,{**data,'items':[{**data['items'][0],**prices}]})
                self.assertEqual(doc['grand_total'],0,(kind,prices,doc))
                self.assertEqual(doc['items'][0]['rate'],0)
            doc = services.create_document(kind,data)
            self.assertEqual(doc['grand_total'],20,kind)
            for rate in ('invalid',float('nan'),float('inf'),-1):
                with self.assertRaisesRegex(ValidationError,'finite number'):
                    services.create_document(kind,{**data,'items':[{**data['items'][0],'rate':rate}]})

    def test_free_purchase_receipt_and_cancel_do_not_invent_stock_value(self):
        self.db.insert('Supplier',{'name':'SUP','supplier_name':'Supplier'})
        self.post('stock-entry',{'company':'Test Co','stock_entry_type':'Material Receipt','items':[{'item_code':'S','qty':10,'basic_rate':10,'t_warehouse':'A'}]})
        receipt = self.post('purchase-receipt',{'company':'Test Co','supplier':'SUP','items':[{'item_code':'S','qty':2,'rate':0,'warehouse':'A'}]})
        self.assertEqual(self.db.get_value('Bin','S-A','stock_value'),100)
        services.cancel_document('purchase-receipt',receipt['name'])
        self.assertEqual(self.db.get_value('Bin','S-A','stock_value'),100)
        self.assertEqual(self.db.get_value('Bin','S-A','actual_qty'),10)

    def test_concurrent_payment_and_journal_cannot_settle_same_debt(self):
        invoice = self.invoice()
        payment = services.create_document('payment-entry',self.payment_data(invoice))
        journal = services.create_document('journal-entry',self.journal_data(invoice))
        barrier = threading.Barrier(2)
        def submit(pair):
            barrier.wait(timeout=10)
            try:
                services.submit_document(*pair)
                return 'ok'
            except ValidationError:
                return 'rejected'
            finally:
                if not self.db._is_memory: self.db.conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit,[('payment-entry',payment['name']),('journal-entry',journal['name'])]))
        self.assertEqual(sorted(results),['ok','rejected'])
        self.assertEqual(self.outstanding(invoice),0)

    def test_rest_returns_actionable_errors_and_preserves_zero(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import documents
        from api.errors import register_exception_handlers
        app = FastAPI(); register_exception_handlers(app)
        for dep in (documents._manager,documents._viewer):
            app.dependency_overrides[dep.dependency]=lambda: {'role':'admin','name':'Test'}
        app.include_router(documents.router,prefix='/api')
        invoice = self.invoice()
        with TestClient(app) as client:
            result = client.post('/api/documents/payment-entry',json=self.payment_data(invoice,(60,60)))
            self.assertEqual(result.status_code,422)
            self.assertIn('remaining outstanding',result.json()['detail'])
            result = client.post('/api/documents/sales-invoice',json={'company':'Test Co','customer':'C','items':[{'item_code':'S','qty':2,'rate':0}]})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['grand_total'],0)


if __name__ == '__main__':
    unittest.main()
