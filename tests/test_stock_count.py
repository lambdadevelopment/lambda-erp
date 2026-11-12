"""Stock Count: prepare, count, post with a reason, and who may do which.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record, update_master_record
from api.routers.reports import _batch_movements
from api.stock_actions import prepare_count, reserve_order
from lambda_erp import permissions
from lambda_erp.exceptions import PermissionDenied, ValidationError
from lambda_erp.stock.stock_ledger import get_stock_details, stock_ledger_agreement


class StockCounts(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Item', {'name': 'B', 'item_name': 'Batch item', 'is_stock_item': 1, 'standard_rate': 10,
                                'has_batch_no': 1, 'has_expiry_date': 1})
        self.db.insert('Warehouse', {'name': 'L', 'warehouse_name': 'L', 'company': 'Test Co', 'use_locations': 1})
        for label in ('A1', 'A2'):
            create_master_record('storage-location', {'warehouse': 'L', 'location': label})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def receive(self, item, qty, warehouse='A', rate=10, **extra):
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt', 'items': [
            {'item_code': item, 'qty': qty, 'basic_rate': rate, 't_warehouse': warehouse, **extra}]})

    def qty(self, item, warehouse='A', batch=None, location=None):
        return sum(float(r['actual_qty']) for r in get_stock_details(item, warehouse, batch, location))

    def bin(self, item, warehouse='A', column='actual_qty'):
        return float(self.db.get_value('Bin', {'item_code': item, 'warehouse': warehouse}, column) or 0)

    def counted(self, draft, quantities, **header):
        """Save `quantities` ({(item, batch, location): qty}) on a prepared draft."""
        lines = []
        for row in draft['items']:
            key = (row['item_code'], row.get('batch_no'), row.get('location'))
            lines.append({**row, 'counted_qty': quantities.get(key)})
        return services.update_document('stock-count', draft['name'], {'items': lines, **header})

    def gl(self, voucher):
        rows = self.db.sql('SELECT account, debit, credit FROM "GL Entry" WHERE voucher_no = ? AND is_cancelled = 0', [voucher])
        return sorted((r['account'], float(r['debit']), float(r['credit'])) for r in rows)

    # -- the wrong-lot case: 3 and 5 on the shelf are really 4 and 4 ------------
    def test_count_corrects_batches_with_a_reason_and_keeps_history(self):
        self.receive('B', 3, batch_no='LOT-1', expiry_date='2027-06-30')
        self.receive('B', 5, batch_no='LOT-2', expiry_date='2027-09-30')
        self.receive('S', 10)
        draft = prepare_count({'warehouse': 'A'})
        self.assertEqual(draft['docstatus'], 0)
        self.assertEqual([(r['item_code'], r.get('batch_no'), r['system_qty'], r.get('counted_qty')) for r in draft['items']],
                         [('B', 'LOT-1', 3.0, None), ('B', 'LOT-2', 5.0, None), ('S', None, 10.0, None)])
        saved = self.counted(draft, {('B', 'LOT-1', None): 4, ('B', 'LOT-2', None): 4, ('S', None, None): 10})
        self.assertEqual([r['difference_qty'] for r in saved['items']], [1.0, -1.0, 0.0])
        self.assertEqual((saved['counted_lines'], saved['difference_lines']), (3, 2))
        # A draft changes nothing.
        self.assertEqual((self.qty('B', batch='LOT-1'), self.qty('B', batch='LOT-2')), (3, 5))
        with self.assertRaisesRegex(ValidationError, 'a reason is required'):
            services.submit_document('stock-count', draft['name'])
        services.update_document('stock-count', draft['name'], {'reason': 'Wrong lot picked for a delivery; found on shelf'})
        value = self.bin('B', column='stock_value')
        done = services.submit_document('stock-count', draft['name'])
        self.assertEqual(done['status'], 'Posted')
        self.assertEqual((self.qty('B', batch='LOT-1'), self.qty('B', batch='LOT-2'), self.bin('B'), self.bin('S')), (4, 4, 8, 10))
        self.assertAlmostEqual(self.bin('B', column='stock_value'), value, places=2)   # 1 out, 1 in, same cost
        self.assertEqual(self.gl(draft['name']), [])                                     # so nothing for the ledger
        # The correction is on record against the batch, with the count as its document.
        moves = _batch_movements(self.db, batch_no='LOT-1', item_code='B')['rows']
        self.assertEqual([(m['voucher_type'], m['actual_qty']) for m in moves],
                         [('Stock Entry', 3.0), ('Stock Count', 1.0)])
        self.assertEqual(self.db.get_value('Stock Count', draft['name'], 'reason'),
                         'Wrong lot picked for a delivery; found on shelf')
        self.assertEqual(stock_ledger_agreement(), [])
        # Cancelling a posted count puts the quantities back.
        services.cancel_document('stock-count', draft['name'])
        self.assertEqual((self.qty('B', batch='LOT-1'), self.qty('B', batch='LOT-2')), (3, 5))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_shortage_and_found_stock_are_valued_and_booked(self):
        self.receive('S', 10, rate=10)
        self.receive('B', 2, batch_no='LOT-1', expiry_date='2027-06-30')
        draft = prepare_count({'warehouse': 'A', 'item_code': 'S'})
        self.assertEqual(len(draft['items']), 1)
        # Stock the system does not know: a line added by hand, with a new batch.
        lines = [{**draft['items'][0], 'counted_qty': 7},
                 {'item_code': 'B', 'batch_no': 'FOUND', 'counted_qty': 2}]
        with self.assertRaisesRegex(ValidationError, 'expiry date is required'):
            services.update_document('stock-count', draft['name'], {'items': lines, 'reason': 'Annual count'})
            services.submit_document('stock-count', draft['name'])
        lines[1]['expiry_date'] = '2028-01-31'
        saved = services.update_document('stock-count', draft['name'], {'items': lines, 'reason': 'Annual count'})
        self.assertEqual([(r['system_qty'], r['difference_qty']) for r in saved['items']], [(10.0, -3.0), (0.0, 2.0)])
        services.submit_document('stock-count', draft['name'])
        self.assertEqual((self.bin('S'), self.qty('B', batch='FOUND'), self.qty('B', batch='LOT-1')), (7, 2, 2))
        stock = self.db.get_value('Company', 'Test Co', 'stock_in_hand_account') or \
            self.db.get_value('Account', {'company': 'Test Co', 'account_type': 'Stock', 'is_group': 0}, 'name')
        adjustment = self.db.get_value('Company', 'Test Co', 'stock_adjustment_account')
        # -30 for S, +20 for B at their cost: 10 written off.
        self.assertEqual(self.gl(draft['name']), sorted([(stock, 0.0, 10.0), (adjustment, 10.0, 0.0)]))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_rules_of_the_ledger_apply_to_counts(self):
        self.receive('S', 4, warehouse='L', t_location='A1')
        draft = prepare_count({'warehouse': 'L', 'location': 'A1'})
        self.assertEqual([(r['location'], r['system_qty']) for r in draft['items']], [('A1', 4.0)])
        with self.assertRaisesRegex(ValidationError, 'is listed twice'):
            services.update_document('stock-count', draft['name'], {'items': [draft['items'][0], {**draft['items'][0], 'name': None}]})
        # Found in another location: that location must exist, and be named.
        for bad, message in (({'location': 'Z9'}, 'Unknown storage location Z9'), ({}, 'Storage location is required')):
            lines = [{**draft['items'][0], 'counted_qty': 3}, {'item_code': 'S', 'counted_qty': 1, **bad}]
            services.update_document('stock-count', draft['name'], {'items': lines, 'reason': 'Moved by hand'})
            with self.assertRaisesRegex(ValidationError, message):
                services.submit_document('stock-count', draft['name'])
            self.assertEqual(self.qty('S', 'L'), 4)
        lines = [{**draft['items'][0], 'counted_qty': 3}, {'item_code': 'S', 'counted_qty': 1, 'location': 'A2'}]
        services.update_document('stock-count', draft['name'], {'items': lines})
        services.submit_document('stock-count', draft['name'])
        self.assertEqual((self.qty('S', 'L', location='A1'), self.qty('S', 'L', location='A2')), (3, 1))
        with self.assertRaisesRegex(ValidationError, 'cannot be negative'):
            services.create_document('stock-count', {'warehouse': 'A', 'items': [{'item_code': 'S', 'counted_qty': -1}]})
        with self.assertRaisesRegex(ValidationError, 'There is no stock to count'):
            prepare_count({'warehouse': 'B'})

    def test_stock_from_before_tracking_can_be_counted_into_batches(self):
        self.receive('S', 6)
        update_master_record('item', 'S', {'has_batch_no': 1})
        draft = prepare_count({'warehouse': 'A'})
        self.assertEqual([(r.get('batch_no'), r['system_qty']) for r in draft['items']], [(None, 6.0)])
        lines = [{**draft['items'][0], 'counted_qty': 0}, {'item_code': 'S', 'batch_no': 'LOT-A', 'counted_qty': 6}]
        services.update_document('stock-count', draft['name'], {'items': lines, 'reason': 'Lot numbers read off the boxes'})
        services.submit_document('stock-count', draft['name'])
        self.assertEqual((self.qty('S', batch=''), self.qty('S', batch='LOT-A'), self.bin('S')), (0, 6, 6))
        self.assertEqual(stock_ledger_agreement(), [])

    # -- stock that moved in the meantime, and reserved stock ---------------------
    def test_posting_is_refused_when_stock_moved_since_the_count(self):
        self.receive('S', 10)
        draft = prepare_count({'warehouse': 'A'})
        self.counted(draft, {('S', None, None): 9}, reason='Cycle count')
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                  'items': [{'item_code': 'S', 'qty': 2, 's_warehouse': 'A'}]})
        with self.assertRaisesRegex(ValidationError, 'Stock has moved since this count was prepared: row 1 .*10 when prepared, 8 now'):
            services.submit_document('stock-count', draft['name'])
        self.assertEqual(self.bin('S'), 8)
        refreshed = prepare_count({'warehouse': 'A', 'stock_count': draft['name']})
        self.assertEqual((refreshed['items'][0]['system_qty'], refreshed['items'][0]['counted_qty'],
                          refreshed['items'][0]['difference_qty']), (8.0, 9.0, 1.0))
        # The counter looks again: 8 it is. Nothing to correct, the count is still on record.
        self.counted(refreshed, {('S', None, None): 8})
        services.submit_document('stock-count', draft['name'])
        self.assertEqual(self.bin('S'), 8)
        self.assertFalse(self.db.get_all('Stock Ledger Entry', filters={'voucher_no': draft['name']}))

    def test_count_cannot_remove_reserved_stock(self):
        self.receive('S', 5)
        order = self.post('sales-order', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                          'delivery_date': '2026-12-30', 'items': [{'item_code': 'S', 'qty': 5, 'rate': 20, 'warehouse': 'A'}]})
        reserve_order({'sales_order': order['name']})
        draft = prepare_count({'warehouse': 'A'})
        self.counted(draft, {('S', None, None): 3}, reason='Two missing')
        with self.assertRaisesRegex(ValidationError, f"is reserved .*Reserved for: {order['name']}"):
            services.submit_document('stock-count', draft['name'])
        self.assertEqual(self.bin('S'), 5)

    # -- acceptance: hand in a count, but do not post it ---------------------------
    def test_counter_can_save_a_count_but_not_post_it(self):
        self.receive('S', 10)
        counter = {'name': 'counter', 'role': 'manager', 'restrictions': {
            'profile': 'Counter', 'hidden': permissions.expand_classes(['purchase_price']),
            'permissions': {'default': ['read'], 'doctypes': {'Stock Count': ['read', 'create', 'update']}}}}
        with permissions.acting_as(counter):
            draft = prepare_count({'warehouse': 'A'})
            saved = self.counted(draft, {('S', None, None): 8}, reason='Counted at the customer')
            self.assertEqual(saved['items'][0]['difference_qty'], -2.0)
            self.assertNotIn('valuation_rate', saved['items'][0])
            with self.assertRaisesRegex(PermissionDenied, 'submit Stock Count'):
                services.submit_document('stock-count', draft['name'])
            # No other way to correct stock either.
            with self.assertRaises(PermissionDenied):
                services.create_document('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                                         'items': [{'item_code': 'S', 'qty': 2, 's_warehouse': 'A'}]})
        self.assertEqual(self.bin('S'), 10)
        # Someone who may post reviews the differences and books them.
        review = services.load_document('stock-count', draft['name'])
        self.assertEqual((review['docstatus'], review['difference_lines']), (0, 1))
        services.submit_document('stock-count', draft['name'])
        self.assertEqual(self.bin('S'), 8)


if __name__ == '__main__':
    unittest.main()
