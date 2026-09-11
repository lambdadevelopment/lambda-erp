"""Regression cases for overwritten vouchers, quantities, valuation and no-op payments."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.exceptions import ValidationError, DocumentStatusError
from lambda_erp.accounting.sales_invoice import SalesInvoice
from lambda_erp.stock.stock_entry import StockEntry


class WorkflowIntegrity(unittest.TestCase):
    setUp = fixtures.RequirementsTest.setUp
    tearDown = fixtures.RequirementsTest.tearDown

    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def stock(self, qty=10, rate=10, warehouse='A'):
        return self.post('stock-entry', {'company':'Test Co', 'stock_entry_type':'Material Receipt',
                         'items':[{'item_code':'S', 'qty':qty, 'basic_rate':rate, 't_warehouse':warehouse}]})

    def data(self, qty=2, **extra):
        return {'company':'Test Co', 'customer':'C', 'items':[{'item_code':'S', 'qty':qty, 'rate':10, 'warehouse':'A'}], **extra}

    def supplier(self):
        self.db.insert('Supplier', {'name':'SUP','supplier_name':'Supplier','default_currency':'USD'})

    def bin(self, column, warehouse='A'):
        return self.db.get_value('Bin', {'item_code':'S','warehouse':warehouse}, column)

    def test_create_cannot_overwrite_draft_submitted_or_cancelled(self):
        for state in ('draft', 'submitted', 'cancelled'):
            original = services.create_document('sales-invoice', self.data())
            if state != 'draft': services.submit_document('sales-invoice', original['name'])
            if state == 'cancelled': services.cancel_document('sales-invoice', original['name'])
            before = services.load_document('sales-invoice', original['name'])
            gl = self.db.get_all('GL Entry', fields=['*'])
            with self.assertRaisesRegex(DocumentStatusError, 'already exists'):
                services.create_document('sales-invoice', {**self.data(7), 'name':original['name']})
            self.assertEqual(services.load_document('sales-invoice', original['name']), before)
            self.assertEqual(self.db.get_all('GL Entry', fields=['*']), gl)
        fresh = services.create_document('sales-invoice', {**self.data(), 'name':'EXPLICIT-NEW'})
        self.assertEqual(fresh['name'], 'EXPLICIT-NEW')

    def test_stale_save_submit_cancel_and_discard_are_rejected(self):
        name = services.create_document('sales-invoice', self.data())['name']
        stale = SalesInvoice.load(name)
        services.update_document('sales-invoice', name, self.data(3))
        for action in (stale.save, stale.submit, stale.discard):
            with self.assertRaisesRegex(DocumentStatusError, 'reload'): action()
        current = SalesInvoice.load(name)
        current.submit()
        other = SalesInvoice.load(name)
        current.cancel()
        with self.assertRaises(DocumentStatusError): other.cancel()
        self.assertEqual(len(self.db.get_all('GL Entry', filters={'voucher_no':name})), 4)

    def test_failed_post_rolls_back_stock_gl_and_order_counters(self):
        self.stock()
        original_qty = self.bin('actual_qty')
        name = services.create_document('stock-entry', {'company':'Test Co','stock_entry_type':'Material Issue',
               'items':[{'item_code':'S','qty':2,'s_warehouse':'A'}]})['name']
        with patch.object(StockEntry, '_get_gl_entries', side_effect=ValidationError('injected after SLE')):
            with self.assertRaisesRegex(ValidationError, 'injected'): services.submit_document('stock-entry', name)
        self.assertEqual(self.bin('actual_qty'), original_qty)
        self.assertEqual(self.db.get_all('Stock Ledger Entry', filters={'voucher_no':name}), [])
        self.assertEqual(self.db.get_value('Stock Entry', name, 'docstatus'), 0)
        services.submit_document('stock-entry', name)
        self.assertEqual(self.bin('actual_qty'), original_qty - 2)

    def test_nested_submit_obeys_outer_rollback(self):
        doc = services.create_document('sales-invoice', self.data())
        with self.assertRaisesRegex(RuntimeError, 'outer'):
            with self.db.atomic():
                services.submit_document('sales-invoice', doc['name'])
                raise RuntimeError('outer failed')
        self.assertEqual(self.db.get_value('Sales Invoice', doc['name'], 'docstatus'), 0)
        self.assertEqual(self.db.get_all('GL Entry'), [])

    def test_delivery_and_receipt_returns_are_cumulative_and_party_bound(self):
        self.stock()
        self.supplier()
        self.db.insert('Customer', {'name':'OTHER','customer_name':'Other'})
        self.db.insert('Supplier', {'name':'OTHER','supplier_name':'Other'})
        for slug, party in [('delivery-note', {'customer':'C'}), ('purchase-receipt', {'supplier':'SUP'})]:
            data = {'company':'Test Co', **party, 'items':self.data()['items']}
            original = self.post(slug, data)
            returned = {**data, 'is_return':1, 'return_against':original['name'], 'items':[{**data['items'][0], 'qty':-2}]}
            with self.assertRaisesRegex(ValidationError, 'must match'):
                services.create_document(slug, {**returned, next(iter(party)):'OTHER'})
            with self.assertRaisesRegex(ValidationError, 'negative'):
                services.create_document(slug, {**returned, 'items':data['items']})
            with self.assertRaisesRegex(ValidationError, 'exceeds'):
                services.create_document(slug, {**returned, 'items':returned['items'] * 2})
            first = self.post(slug, returned)
            with self.assertRaisesRegex(ValidationError, 'exceeds'): self.post(slug, returned)
            with self.assertRaisesRegex(ValidationError, 'returns'): services.cancel_document(slug, original['name'])
            services.cancel_document(slug, first['name'])
            self.post(slug, returned)  # cancellation restores returnable quantity

    def test_return_aggregates_duplicate_original_items(self):
        self.stock()
        original = self.post('delivery-note', {**self.data(), 'items':self.data()['items'] * 2})
        returned = self.post('delivery-note', self.data(-4, is_return=1, return_against=original['name']))
        self.assertEqual(returned['docstatus'], 1)
        self.assertEqual(self.bin('actual_qty'), 10)

    def test_return_converter_uses_remaining_quantity(self):
        from lambda_erp.stock.delivery_note import make_delivery_return
        self.stock()
        original = self.post('delivery-note', self.data())
        self.post('delivery-note', self.data(-1, is_return=1, return_against=original['name']))
        proposed = make_delivery_return(original['name'])
        self.assertEqual(proposed.items[0]['qty'], -1)
        proposed.save().submit()
        with self.assertRaisesRegex(ValidationError, 'No remaining'): make_delivery_return(original['name'])

    def test_return_cancel_cannot_overfill_order_after_replacement(self):
        self.stock()
        order = self.post('sales-order', self.data())
        row = {**self.data()['items'][0], 'against_sales_order':order['name'], 'so_detail':order['items'][0]['name']}
        original = self.post('delivery-note', {**self.data(),'items':[row]})
        returned = self.post('delivery-note', {**self.data(),'is_return':1,'return_against':original['name'],'items':[{**row,'qty':-2}]})
        replacement = self.post('delivery-note', {**self.data(),'items':[row]})
        with self.assertRaisesRegex(ValidationError, 'remaining order'): services.cancel_document('delivery-note', returned['name'])
        self.assertEqual(self.db.get_value('Delivery Note', returned['name'], 'docstatus'), 1)
        services.cancel_document('delivery-note', replacement['name'])
        services.cancel_document('delivery-note', returned['name'])
        self.assertEqual(self.bin('reserved_qty'), 0)

    def test_all_order_paths_reject_cumulative_and_duplicate_row_overbooking(self):
        self.stock(30)
        self.supplier()
        for order_slug, party, paths in [
            ('sales-order', {'customer':'C'}, [('delivery-note','against_sales_order','so_detail'), ('sales-invoice','sales_order','sales_order_item')]),
            ('purchase-order', {'supplier':'SUP'}, [('purchase-receipt','against_purchase_order','po_detail'), ('purchase-invoice','purchase_order','purchase_order_item')])]:
            for slug, parent, line in paths:
                data = {'company':'Test Co', **party, 'items':self.data()['items']}
                order = self.post(order_slug, data)
                row = {**data['items'][0], parent:order['name'], line:order['items'][0]['name']}
                with self.assertRaisesRegex(ValidationError, 'remaining order'):
                    services.create_document(slug, {**data, 'items':[row, row]})
                # Two drafts are allowed; recheck at submit after first consumes capacity.
                one = services.create_document(slug, {**data,'items':[row]})
                two = services.create_document(slug, {**data,'items':[row]})
                services.submit_document(slug, one['name'])
                with self.assertRaisesRegex(ValidationError, 'remaining order'): services.submit_document(slug, two['name'])
                self.assertEqual(services.load_document(slug, two['name'])['docstatus'], 0)
                services.cancel_document(slug, one['name'])
                services.submit_document(slug, two['name'])

    def test_planning_tracks_partial_full_return_and_cancel(self):
        self.stock()
        self.supplier()
        for order_slug, slug, party, parent, line, planned in [
            ('sales-order','delivery-note',{'customer':'C'},'against_sales_order','so_detail','reserved_qty'),
            ('purchase-order','purchase-receipt',{'supplier':'SUP'},'against_purchase_order','po_detail','ordered_qty')]:
            data = {'company':'Test Co', **party, 'items':self.data()['items']}
            order = self.post(order_slug, data)
            self.assertEqual(self.bin(planned), 2)
            row = {**data['items'][0], 'qty':1, parent:order['name'], line:order['items'][0]['name']}
            first = self.post(slug, {**data,'items':[row]})
            self.assertEqual(self.bin(planned), 1)
            second = self.post(slug, {**data,'items':[row]})
            self.assertEqual(self.bin(planned), 0)
            returned = self.post(slug, {**data,'is_return':1,'return_against':first['name'],'items':[{**row,'qty':-1}]})
            self.assertEqual(self.bin(planned), 1)
            services.cancel_document(slug, returned['name'])
            self.assertEqual(self.bin(planned), 0)
            for name in (first['name'], second['name']): services.cancel_document(slug, name)
            self.assertEqual(self.bin(planned), 2)
            services.cancel_document(order_slug, order['name'])
            self.assertEqual(self.bin(planned), 0)

    def test_direct_stock_invoices_release_planning(self):
        self.stock()
        self.supplier()
        for order_slug, slug, party, parent, line, planned in [
            ('sales-order','sales-invoice',{'customer':'C'},'sales_order','sales_order_item','reserved_qty'),
            ('purchase-order','purchase-invoice',{'supplier':'SUP'},'purchase_order','purchase_order_item','ordered_qty')]:
            data = {'company':'Test Co', **party, 'items':self.data()['items']}
            order = self.post(order_slug, data)
            invoice = self.post(slug, {**data,'update_stock':1,'items':[{**data['items'][0],parent:order['name'],line:order['items'][0]['name']}]})
            self.assertEqual(self.bin(planned), 0)
            movement, order_ref, line_ref = ('delivery-note','against_sales_order','so_detail') if order_slug == 'sales-order' else ('purchase-receipt','against_purchase_order','po_detail')
            with self.assertRaisesRegex(ValidationError, 'remaining order stock'):
                self.post(movement, {**data,'items':[{**data['items'][0],order_ref:order['name'],line_ref:order['items'][0]['name']}]})
            services.cancel_document(slug, invoice['name'])
            self.assertEqual(self.bin(planned), 2)
            services.cancel_document(order_slug, order['name'])

    def test_order_without_existing_bin_is_reserved(self):
        self.post('sales-order', self.data())
        self.assertEqual(self.bin('reserved_qty'), 2)

    def test_migration_repairs_only_derived_planning_and_is_idempotent(self):
        from lambda_erp.database import _m028_order_planning
        self.stock()
        order = self.post('sales-order', self.data())
        self.post('delivery-note', {**self.data(),'items':[{**self.data()['items'][0],'against_sales_order':order['name'],'so_detail':order['items'][0]['name']}]})
        self.db.set_value('Sales Order Item', order['items'][0]['name'], 'delivered_qty', 0)
        self.db.set_value('Sales Order', order['name'], 'per_delivered', 0)
        self.db.set_value('Bin', 'S-A', 'reserved_qty', 999)
        gl = self.db.get_all('GL Entry', fields=['*'], order_by='name')
        stock = self.db.get_all('Stock Ledger Entry', fields=['*'], order_by='name')
        for _ in range(2):
            _m028_order_planning(self.db)
            self.assertEqual(self.bin('reserved_qty'), 0)
            self.assertEqual(services.load_document('sales-order', order['name'])['per_delivered'], 100)
            self.assertEqual(self.db.get_all('GL Entry', fields=['*'], order_by='name'), gl)
            self.assertEqual(self.db.get_all('Stock Ledger Entry', fields=['*'], order_by='name'), stock)
        self.assertTrue(self.db.sql('SELECT version FROM "_SchemaMigrations" WHERE version = 28'))

    def test_issue_gl_matches_actual_cost_and_cancel_uses_original_cost(self):
        self.stock()
        issue = self.post('stock-entry', {'company':'Test Co','stock_entry_type':'Material Issue',
                          'items':[{'item_code':'S','qty':2,'s_warehouse':'A'}]})
        self.assertEqual(issue['total_outgoing_value'], 20)
        gl = self.db.get_all('GL Entry', filters={'voucher_no':issue['name']}, fields=['debit','credit'])
        self.assertEqual(sum(row.debit for row in gl), 20)
        self.stock(2, 20)  # new average is 12
        services.cancel_document('stock-entry', issue['name'])
        self.assertEqual(self.bin('actual_qty'), 12)
        self.assertEqual(self.bin('stock_value'), 140)

    def test_transfer_preserves_cost_and_posts_between_warehouse_accounts(self):
        self.stock()
        self.db.insert('Account', {'name':'Other Stock','account_name':'Other Stock','company':'Test Co',
                       'account_type':'Stock','root_type':'Asset','is_group':0,'account_currency':'USD'})
        self.db.set_value('Warehouse', 'B', 'account', 'Other Stock')
        doc = self.post('stock-entry', {'company':'Test Co','stock_entry_type':'Material Transfer',
                        'items':[{'item_code':'S','qty':2,'s_warehouse':'A','t_warehouse':'B','basic_rate':999}]})
        self.assertEqual(doc['total_incoming_value'], 20)
        self.assertEqual(doc['total_outgoing_value'], 20)
        self.assertEqual(self.bin('stock_value','B'), 20)
        self.assertEqual(sum(row.debit for row in self.db.get_all('GL Entry', filters={'voucher_no':doc['name']}, fields=['debit'])), 20)
        services.cancel_document('stock-entry', doc['name'])
        self.assertEqual(self.bin('stock_value','B'), 0)
        self.assertEqual(self.bin('stock_value'), 100)

    def test_receipt_requires_rate_and_explicit_zero_is_preserved(self):
        self.stock()
        with self.assertRaisesRegex(ValidationError, 'Basic Rate'):
            self.post('stock-entry', {'company':'Test Co','stock_entry_type':'Material Receipt',
                      'items':[{'item_code':'S','qty':2,'t_warehouse':'A'}]})
        free = self.stock(2, 0)
        self.assertEqual(self.bin('stock_value'), 100)
        self.assertEqual(self.bin('actual_qty'), 12)
        services.cancel_document('stock-entry', free['name'])
        self.assertEqual(self.bin('stock_value'), 100)
        self.assertEqual(self.bin('actual_qty'), 10)

    def test_rest_rejects_collision_and_invalid_payment(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import documents
        from api.errors import register_exception_handlers
        app = FastAPI(); register_exception_handlers(app)
        for dep in (documents._manager, documents._viewer):
            app.dependency_overrides[dep.dependency] = lambda: {'role':'admin','name':'Test'}
        app.include_router(documents.router, prefix='/api')
        original = self.post('sales-invoice', self.data())
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/documents/sales-invoice',json={**self.data(7),'name':original['name']}).status_code,409)
            data = {'company':'Test Co','payment_type':'Receeve','party_type':'Customer','party':'C','paid_amount':10}
            self.assertEqual(client.post('/api/documents/payment-entry',json=data).status_code,422)
            # Existing bad drafts are also rejected on submit.
            self.db.insert('Payment Entry', {**data,'name':'LEGACY-PAYMENT'})
            self.assertEqual(client.post('/api/documents/payment-entry/LEGACY-PAYMENT/submit').status_code,422)
            self.assertEqual(self.db.get_all('GL Entry', filters={'voucher_no':'LEGACY-PAYMENT'}), [])
            fields = client.get('/api/documents/delivery-note/fields').json()
            self.assertIn('Cumulative submitted quantities', str(fields['requirements']))

    def test_payment_accounts_amounts_and_valid_internal_transfer(self):
        valid = {'company':'Test Co','payment_type':'Receive','party_type':'Customer','party':'C','paid_amount':10}
        for changes in ({'paid_amount':float('nan')}, {'received_amount':0}, {'received_amount':-10}, {'received_amount':float('inf')}, {'payment_type':'Internal Transfer'}, {'paid_to':'MISSING'}):
            with self.assertRaises(ValidationError): services.create_document('payment-entry', {**valid, **changes})
        payment = self.post('payment-entry', valid)
        self.assertEqual(sum(row.debit for row in self.db.get_all('GL Entry',filters={'voucher_no':payment['name']},fields=['debit'])), 10)
        self.db.insert('Account', {'name':'Other Bank','account_name':'Other Bank','company':'Test Co', 'account_type':'Bank','root_type':'Asset','is_group':0,'account_currency':'USD'})
        transfer = self.post('payment-entry', {'company':'Test Co','payment_type':'Internal Transfer','paid_from':payment['paid_to'],'paid_to':'Other Bank','paid_amount':3})
        self.assertEqual(sum(row.debit for row in self.db.get_all('GL Entry',filters={'voucher_no':transfer['name']},fields=['debit'])), 3)

    def test_concurrent_submits_consume_order_capacity_once(self):
        self.stock()
        order = self.post('sales-order', self.data())
        data = {**self.data(),'items':[{**self.data()['items'][0],'sales_order':order['name'],'sales_order_item':order['items'][0]['name']}]}
        drafts = [services.create_document('sales-invoice', data) for _ in range(2)]
        barrier = threading.Barrier(2)
        def submit(name):
            barrier.wait(timeout=10)
            try:
                services.submit_document('sales-invoice', name)
                return 'ok'
            except ValidationError:
                return 'rejected'
            finally:
                if not self.db._is_memory:
                    self.db.conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [doc['name'] for doc in drafts]))
        self.assertEqual(sorted(results), ['ok','rejected'])
        self.assertEqual(services.load_document('sales-order', order['name'])['per_billed'], 100)


if __name__ == '__main__':
    unittest.main()
