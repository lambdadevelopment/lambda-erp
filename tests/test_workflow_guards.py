"""Business outcomes for the follow-up workflow audit; synthetic SQLite/PG data."""
import unittest
from unittest.mock import patch
from tests import test_document_requirements as fixtures
from api import services, chat
from api.routers.masters import create_master_record, update_master_record
from lambda_erp.exceptions import ValidationError
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.selling.proposal import Proposal

class WorkflowGuards(unittest.TestCase):
    setUp = fixtures.RequirementsTest.setUp
    tearDown = fixtures.RequirementsTest.tearDown

    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def test_nested_atomic_db_failure_preserves_outer_work(self):
        with self.db.atomic():
            self.db.insert('Customer', {'name':'OUTER','customer_name':'Outer'})
            try:
                with self.db.atomic():
                    self.db.insert('Customer', {'name':'INNER','customer_name':'Inner'})
                    self.db.insert('Customer', {'name':'C','customer_name':'Duplicate'})
            except Exception:
                pass
            else:
                self.fail('duplicate must fail')
            self.assertFalse(self.db.exists('Customer','INNER'))
            self.assertTrue(self.db.exists('Customer','OUTER'))
        self.assertFalse(self.db._in_transaction)
        self.assertTrue(self.db.exists('Customer','OUTER'))

    def test_rest_fields_and_error_classification(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import masters, documents
        from api.errors import register_exception_handlers
        app = FastAPI()
        register_exception_handlers(app)
        for module in (masters, documents):
            for dep in (module._manager, module._viewer):
                app.dependency_overrides[dep.dependency] = lambda: {'role':'admin','name':'Test'}
            app.include_router(module.router, prefix='/api')
        @app.post('/test-required-constraint')
        def constraint():
            self.db.insert('Customer', {'name':'NO-DISPLAY'})
        with TestClient(app) as client:
            for method, path, data in [
                ('post','/api/masters/customer',{}),
                ('post','/api/masters/customer',{'customer_name':'Test','contact_emal':'lost'}),
                ('put','/api/masters/customer/C',{'contact_emal':'lost'}),
                ('post','/api/documents/stock-entry',{'company':'Test Co','stock_entry_type':'Material Receipt','items':[{'item_code':'S','t_warehouse':'A'}]}),
            ]:
                res = getattr(client,method)(path,json=data)
                self.assertEqual(res.status_code,422,res.text)
            res = client.post('/test-required-constraint')
            self.assertEqual(res.status_code,422,res.text)
            self.assertIn('required',res.json()['detail'])
            self.assertEqual(client.post('/api/masters/customer',json={'name':'C','customer_name':'Duplicate'}).status_code,409)
            fields = client.get('/api/masters/item/fields').json()
            self.assertEqual(fields['link_fields']['default_warehouse'],'Warehouse')
            self.assertIn('stock_entry_type',client.get('/api/documents/stock-entry/fields').json()['requirements']['required'])

    def test_stock_guards_and_valid_posting(self):
        good = {'company':'Test Co','stock_entry_type':'Material Receipt','items':[{'item_code':'S','qty':5,'basic_rate':10,'t_warehouse':'A'}]}
        for change in [{'company':None}, {'stock_entry_type':'Transfer'}, {'items':[{'item_code':'S','t_warehouse':'A'}]}, {'items':[{'qty':1,'t_warehouse':'A'}]}, {'items':[{'item_code':'S','qty':-1,'t_warehouse':'A'}]}]:
            with self.assertRaises(ValidationError):
                services.create_document('stock-entry', {**good, **change})
        self.assertEqual(self.db.get_all('Stock Ledger Entry'), [])
        self.assertEqual(self.db.get_all('Stock Entry'), [])
        self.post('stock-entry', good)
        self.assertEqual(self.db.get_value('Bin', {'item_code':'S','warehouse':'A'}, 'actual_qty'), 5)
        self.assertEqual(len(self.db.get_all('GL Entry')), 2)

    def test_order_reference_pairs_and_progress(self):
        self.post('stock-entry', {'company':'Test Co','stock_entry_type':'Material Receipt','items':[{'item_code':'S','qty':5,'basic_rate':10,'t_warehouse':'A'}]})
        self.db.insert('Supplier', {'name':'SUP','supplier_name':'Supplier','default_currency':'USD'})
        for order_slug, party, kinds in [
            ('sales-order', {'customer':'C'}, [('delivery-note','against_sales_order','so_detail','per_delivered','delivered_qty'),('sales-invoice','sales_order','sales_order_item','per_billed','billed_qty')]),
            ('purchase-order', {'supplier':'SUP'}, [('purchase-receipt','against_purchase_order','po_detail','per_received','received_qty'),('purchase-invoice','purchase_order','purchase_order_item',None,'billed_qty')]),
        ]:
            order = self.post(order_slug, {'company':'Test Co', **party,'items':[{'item_code':'S','qty':2,'rate':10,'warehouse':'A'}]})
            for slug, parent, line, percent, quantity in kinds:
                row = {'item_code':'S','qty':2,'rate':10,'warehouse':'A', parent:order['name']}
                with self.assertRaisesRegex(ValidationError, 'supplied together'):
                    services.create_document(slug, {'company':'Test Co',**party,'items':[row]})
                with self.assertRaisesRegex(ValidationError, 'belong'):
                    services.create_document(slug, {'company':'Test Co',**party,'items':[{**row,line:'BAD-LINE'}]})
                created = self.post(slug, {'company':'Test Co',**party,'items':[{**row,line:order['items'][0]['name']}]})
                current = services.load_document(order_slug, order['name'])
                self.assertEqual(current['items'][0][quantity],2)
                if percent: self.assertEqual(current[percent],100)
                if slug.endswith('invoice'):
                    services.cancel_document(slug, created['name'])
                    self.assertEqual(services.load_document(order_slug,order['name'])['items'][0][quantity],0)

    def test_subscription_validates_early_and_process_is_atomic(self):
        good = {'company':'Test Co','party_type':'Customer','party':'C','start_date':'2026-01-01','billing_interval':'Monthly','plans':[{'item_code':'M','qty':1,'rate':10}]}
        for change in [{'billing_interval':'Weekly'}, {'party':'MISSING'}, {'party_type':'Anything'}, {'plans':[{'item_code':'MISSING','qty':1,'rate':1}]}, {'plans':[{'item_code':'M','rate':1}]}]:
            with self.assertRaises(ValidationError):
                services.create_document('subscription',{**good,**change})
        self.db.insert('Company',{'name':'Other','company_name':'Other'})
        with self.assertRaisesRegex(ValidationError,'Company is required'):
            services.create_document('subscription',{**good,'company':None})
        doc = services.create_document('subscription',good)
        with patch.object(Subscription,'_persist',side_effect=RuntimeError('period write failed')):
            with self.assertRaises(RuntimeError): Subscription.load(doc['name']).process()
        self.assertEqual(self.db.get_all('Sales Invoice'),[])
        self.assertEqual(Subscription.load(doc['name']).current_invoice_end,'2026-02-01')
        invoice = Subscription.load(doc['name']).process()
        self.assertEqual(invoice['grand_total'],10)
        self.assertEqual(Subscription.load(doc['name']).current_invoice_end,'2026-03-01')
        free = services.create_document('subscription',{**good,'plans':[{'item_code':'S','qty':1,'rate':0}]})
        self.assertEqual(Subscription.load(free['name']).process()['grand_total'],0)

    def test_order_line_ownership_and_party_must_match(self):
        order = self.post('sales-order', {'company':'Test Co','customer':'C','items':[{'item_code':'M','qty':1,'rate':10}]})
        other = self.post('sales-order', {'company':'Test Co','customer':'C','items':[{'item_code':'M','qty':1,'rate':10}]})
        self.db.insert('Customer',{'name':'OTHER','customer_name':'Other'})
        row = {'item_code':'M','qty':1,'rate':10,'sales_order':order['name'],'sales_order_item':order['items'][0]['name']}
        for party, change in [('C',{'sales_order_item':other['items'][0]['name']}),('OTHER',{}),('C',{'item_code':'S'})]:
            with self.assertRaises(ValidationError):
                services.create_document('sales-invoice',{'company':'Test Co','customer':party,'items':[{**row,**change}]})
        self.assertEqual(self.db.get_all('Sales Invoice'),[])
        self.assertEqual(self.db.get_all('GL Entry'),[])

    def test_proposal_drafts_and_output_boundary(self):
        draft = services.create_document('proposal',{})
        self.assertEqual(draft['_validation']['warnings'][0]['code'],'proposal_incomplete')
        with self.assertRaises(ValidationError): Proposal.load(draft['name']).validate_for_output()
        q = services.create_document('quotation',{'company':'Test Co','customer':'C','items':[{'item_code':'M','qty':1,'rate':10}]})
        self.db.insert('Customer',{'name':'OTHER','customer_name':'Other'})
        data = {'company':'Test Co','customer':'C','quotations':[{'quotation':q['name']}]}
        with self.assertRaisesRegex(ValidationError,'must match'):
            services.create_document('proposal',{**data,'customer':'OTHER'})
        p = services.create_document('proposal',data)
        Proposal.load(p['name']).validate_for_output()
        from api.pdf import generate_pdf
        self.assertTrue(generate_pdf('proposal',p['name']).startswith(b'%PDF'))
        services.update_document('quotation',q['name'],{'customer':'OTHER'})
        with self.assertRaisesRegex(ValidationError,'must match'):
            generate_pdf('proposal',p['name'])

    def test_master_field_and_link_guards(self):
        with self.assertRaisesRegex(ValidationError,'Unknown field'):
            create_master_record('customer',{'customer_name':'Test','contact_emal':'lost'})
        with self.assertRaisesRegex(ValidationError,'required'):
            create_master_record('customer',{})
        with self.assertRaisesRegex(ValidationError,'Unknown field'):
            update_master_record('customer','C',{'contact_emal':'lost'})
        with self.assertRaisesRegex(ValidationError,'existing Warehouse'):
            update_master_record('item','S',{'default_warehouse':'BAD'})
        result = update_master_record('customer','C',{'contact_email':'saved@example.invalid'})
        self.assertEqual(result['contact_email'],'saved@example.invalid')
        self.assertIn('customer_name',chat._handle_get_master_fields({'master_type':'customer'})['requirements']['required'])

    def test_batch_preserves_warnings_and_failures(self):
        doc = services.create_document('reservation',{**self.pool,'asset':self.a.name})
        result = services.batch_update_documents('reservation',[
            {'name':doc['name'],'data':{'asset':None,'allocation_mode':'Pool'}},
            {'name':doc['name'],'data':{'status':'Out'}},
        ])
        self.assertEqual((result['updated'],result['failed']),(1,1))
        self.assertEqual(result['results'][0]['warnings'][0]['code'],'asset_unassigned')
        self.assertEqual(result['_validation']['warnings'][0]['name'],doc['name'])
        self.assertFalse(result['results'][1]['ok'])

if __name__ == '__main__': unittest.main()
