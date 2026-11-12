"""Batch-tracked stock: per-batch quantities, mandatory batches, returns,
cancels, legacy stock and the ledger agreement check.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record, update_master_record
from lambda_erp.accounting.sales_invoice import make_sales_return
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_delivery_note, make_delivery_return
from lambda_erp.stock.purchase_receipt import make_purchase_receipt_return
from lambda_erp.stock.stock_ledger import get_batch, get_batch_balances, stock_ledger_agreement


class Batches(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        # B: batch-tracked with expiry dates. T: a second tracked item. S (fixture) is untracked.
        self.db.insert('Item', {'name': 'B', 'item_name': 'Batch item', 'is_stock_item': 1, 'standard_rate': 10,
                                'has_batch_no': 1, 'has_expiry_date': 1})
        self.db.insert('Item', {'name': 'T', 'item_name': 'Other batch item', 'is_stock_item': 1,
                                'standard_rate': 10, 'has_batch_no': 1})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def receive(self, batch, qty, item='B', expiry='2027-12-31', warehouse='A', rate=10):
        line = {'item_code': item, 'qty': qty, 'basic_rate': rate, 't_warehouse': warehouse, 'batch_no': batch}
        if expiry:
            line['expiry_date'] = expiry
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt', 'items': [line]})

    def issue(self, batch, qty, item='B', warehouse='A'):
        line = {'item_code': item, 'qty': qty, 's_warehouse': warehouse}
        if batch is not None:
            line['batch_no'] = batch
        return {'company': 'Test Co', 'stock_entry_type': 'Material Issue', 'items': [line]}

    def qty(self, batch, item='B', warehouse='A'):
        rows = get_batch_balances(item, warehouse, batch)
        return float(rows[0]['actual_qty']) if rows else 0.0

    def bin(self, item='B', warehouse='A', column='actual_qty'):
        return float(self.db.get_value('Bin', {'item_code': item, 'warehouse': warehouse}, column) or 0)

    def delivery(self, lines, **extra):
        return {'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12',
                'items': [{'item_code': 'B', 'rate': 20, 'warehouse': 'A', **line} for line in lines], **extra}

    def assert_agreement(self):
        self.assertEqual(stock_ledger_agreement(), [])

    # -- the acceptance criterion: 2 in A, 8 in B, issue 3 from A ------------
    def test_issue_cannot_exceed_the_batch_even_if_the_item_has_enough(self):
        self.receive('LOT-A', 2)
        self.receive('LOT-B', 8)
        self.assertEqual(self.bin(), 10)
        with self.assertRaisesRegex(ValidationError, 'Not enough of batch LOT-A'):
            self.post('stock-entry', self.issue('LOT-A', 3))
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B'), self.bin()), (2, 8, 10))
        self.post('stock-entry', self.issue('LOT-A', 2))
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B'), self.bin()), (0, 8, 8))
        self.assert_agreement()

    def test_failed_line_leaves_no_partial_posting(self):
        self.receive('LOT-A', 2)
        self.receive('LOT-B', 8)
        draft = services.create_document('delivery-note', self.delivery(
            [{'qty': 5, 'batch_no': 'LOT-B'}, {'qty': 3, 'batch_no': 'LOT-A'}]))
        with self.assertRaisesRegex(ValidationError, 'Not enough of batch LOT-A'):
            services.submit_document('delivery-note', draft['name'])
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B'), self.bin()), (2, 8, 10))
        self.assertEqual(self.db.get_value('Delivery Note', draft['name'], 'docstatus'), 0)
        self.assertFalse(self.db.get_all('Stock Ledger Entry', filters={'voucher_no': draft['name']}))
        self.assert_agreement()

    # -- mandatory and allowed combinations ----------------------------------
    def test_tracked_item_needs_a_batch_and_untracked_item_takes_none(self):
        with self.assertRaisesRegex(ValidationError, 'Batch is required for item B'):
            self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                      'items': [{'item_code': 'B', 'qty': 1, 'basic_rate': 10, 't_warehouse': 'A'}]})
        with self.assertRaisesRegex(ValidationError, 'not batch-tracked'):
            self.receive('LOT-X', 1, item='S', expiry=None)
        self.receive('LOT-A', 2)
        with self.assertRaisesRegex(ValidationError, 'Batch is required for item B'):
            self.post('stock-entry', self.issue(None, 1))
        with self.assertRaisesRegex(ValidationError, 'Unknown batch NOPE'):
            self.post('stock-entry', self.issue('NOPE', 1))
        # A draft may wait for its batch (the picker fills it in); posting may not.
        draft = services.create_document('delivery-note', self.delivery([{'qty': 1}]))
        with self.assertRaisesRegex(ValidationError, 'Batch is required for item B'):
            services.submit_document('delivery-note', draft['name'])
        self.assertEqual(self.bin(), 2)

    def test_receipt_creates_the_batch_with_its_expiry(self):
        with self.assertRaisesRegex(ValidationError, 'expiry date is required'):
            self.receive('LOT-A', 2, expiry=None)
        self.receive('LOT-A', 2)
        batch = get_batch('B', 'LOT-A')
        self.assertEqual(batch['expiry_date'], '2027-12-31')
        self.receive('LOT-A', 1, expiry=None)  # a known batch needs no date again
        with self.assertRaisesRegex(ValidationError, 'already has expiry date 2027-12-31'):
            self.receive('LOT-A', 1, expiry='2028-01-31')
        self.assertEqual(self.qty('LOT-A'), 3)
        self.assertEqual(len(self.db.get_all('Batch', filters={'item_code': 'B'})), 1)

    def test_same_label_on_two_items_is_two_batches(self):
        self.receive('LOT-7', 2)
        self.receive('LOT-7', 5, item='T', expiry=None)
        rows = self.db.get_all('Batch', filters={'batch_no': 'LOT-7'}, fields=['item_code'])
        self.assertEqual(sorted(row['item_code'] for row in rows), ['B', 'T'])
        self.assertEqual((self.qty('LOT-7'), self.qty('LOT-7', item='T')), (2, 5))
        from api.routers.reports import _batch_stock
        found = _batch_stock(self.db, batch_no='LOT-7')['rows']
        self.assertEqual(sorted((row['item_code'], row['actual_qty']) for row in found), [('B', 2.0), ('T', 5.0)])

    # -- purchase, transfer, delivery, return, cancel ------------------------
    def test_full_cycle_keeps_quantities_traceable_and_books_balanced(self):
        self.db.insert('Warehouse', {'name': 'Q', 'warehouse_name': 'Q', 'company': 'Test Co'})
        receipt = self.post('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12',
            'items': [{'item_code': 'B', 'qty': 6, 'rate': 10, 'warehouse': 'A', 'batch_no': 'LOT-A',
                       'expiry_date': '2027-06-30'},
                      {'item_code': 'B', 'qty': 4, 'rate': 10, 'warehouse': 'A', 'batch_no': 'LOT-B',
                       'expiry_date': '2027-09-30'}]})
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Transfer',
                  'items': [{'item_code': 'B', 'qty': 2, 's_warehouse': 'A', 't_warehouse': 'Q', 'batch_no': 'LOT-A'}]})
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-A', warehouse='Q')), (4, 2))
        note = self.post('delivery-note', self.delivery([{'qty': 3, 'batch_no': 'LOT-A'}, {'qty': 4, 'batch_no': 'LOT-B'}]))
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B'), self.bin()), (1, 0, 1))

        # The receipt's goods have left: cancelling it is refused, per batch.
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel'):
            services.cancel_document('purchase-receipt', receipt['name'])

        # A return names a batch of the original, and no more of it than was delivered.
        back = make_delivery_return(note['name'])
        self.assertEqual(sorted((row['batch_no'], row['qty']) for row in back.get('items')),
                         [('LOT-A', -3.0), ('LOT-B', -4.0)])
        wrong = self.delivery([{'qty': -1, 'batch_no': 'LOT-Z'}], is_return=1, return_against=note['name'])
        with self.assertRaisesRegex(ValidationError, 'Batch LOT-Z of B is not on Delivery Note'):
            services.create_document('delivery-note', wrong)
        too_many = self.delivery([{'qty': -4, 'batch_no': 'LOT-A'}], is_return=1, return_against=note['name'])
        with self.assertRaisesRegex(ValidationError, 'exceeds what is left to return of that batch'):
            services.create_document('delivery-note', too_many)
        returned = self.post('delivery-note', self.delivery(
            [{'qty': -2, 'batch_no': 'LOT-B'}], is_return=1, return_against=note['name']))
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B')), (1, 2))
        rest = make_delivery_return(note['name'])
        self.assertEqual(sorted((row['batch_no'], row['qty']) for row in rest.get('items')),
                         [('LOT-A', -3.0), ('LOT-B', -2.0)])

        # Every movement is found from the label, with its document and line.
        from api.routers.reports import _batch_movements
        moves = _batch_movements(self.db, batch_no='LOT-B', item_code='B')['rows']
        self.assertEqual([(m['voucher_type'], m['actual_qty'], m['party']) for m in moves],
                         [('Purchase Receipt', 4.0, 'SUP'), ('Delivery Note', -4.0, 'C'), ('Delivery Note', 2.0, 'C')])
        self.assertTrue(all(m['voucher_detail_no'] for m in moves))

        services.cancel_document('delivery-note', returned['name'])
        services.cancel_document('delivery-note', note['name'])
        self.assertEqual((self.qty('LOT-A'), self.qty('LOT-B'), self.bin()), (4, 4, 8))
        self.assert_agreement()
        balance = self.db.sql('SELECT COALESCE(SUM(debit - credit), 0) AS diff FROM "GL Entry" WHERE is_cancelled = 0')
        self.assertAlmostEqual(float(balance[0]['diff']), 0, places=2)
        stock_value = sum(float(row['stock_value']) for row in self.db.get_all('Bin', filters={'item_code': 'B'}, fields=['stock_value']))
        self.assertAlmostEqual(stock_value, 100, places=2)  # 8 in A + 2 in Q, at 10

    def test_supplier_return_takes_the_received_batch(self):
        receipt = self.post('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12',
            'items': [{'item_code': 'B', 'qty': 5, 'rate': 10, 'warehouse': 'A', 'batch_no': 'LOT-A',
                       'expiry_date': '2027-06-30'}]})
        back = make_purchase_receipt_return(receipt['name'])
        self.assertEqual(back.get('items')[0]['batch_no'], 'LOT-A')
        back.get('items')[0]['qty'] = -2
        back.save()
        back.submit()
        self.assertEqual(self.qty('LOT-A'), 3)
        self.assert_agreement()

    def test_direct_stock_invoice_and_its_return_carry_the_batch(self):
        self.receive('LOT-A', 5)
        invoice = self.post('sales-invoice', {
            'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12', 'update_stock': 1,
            'items': [{'item_code': 'B', 'qty': 2, 'rate': 20, 'warehouse': 'A', 'batch_no': 'LOT-A'}]})
        self.assertEqual(self.qty('LOT-A'), 3)
        back = make_sales_return(invoice['name'])
        self.assertEqual(back.get('items')[0]['batch_no'], 'LOT-A')
        back.save()
        back.submit()
        self.assertEqual(self.qty('LOT-A'), 5)
        self.assert_agreement()

    # -- stock from before tracking ------------------------------------------
    def test_switching_an_item_to_tracking_keeps_old_stock_without_a_batch(self):
        old = self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                        'items': [{'item_code': 'S', 'qty': 10, 'basic_rate': 10, 't_warehouse': 'A'}]})
        update_master_record('item', 'S', {'has_batch_no': 1})
        self.assertEqual(self.qty('', item='S'), 10)
        self.assertFalse(self.db.get_all('Batch', filters={'item_code': 'S'}))
        # It cannot leave without a batch, and a batch cannot take more than is unassigned.
        with self.assertRaisesRegex(ValidationError, 'Batch is required for item S'):
            self.post('stock-entry', self.issue(None, 1, item='S'))
        assign = {'company': 'Test Co', 'stock_entry_type': 'Batch Assignment',
                  'items': [{'item_code': 'S', 'qty': 6, 't_warehouse': 'A', 'batch_no': 'OLD-1'}]}
        assigned = self.post('stock-entry', assign)
        with self.assertRaisesRegex(ValidationError, 'Not enough of stock without a batch'):
            self.post('stock-entry', {**assign, 'items': [{**assign['items'][0], 'qty': 5, 'batch_no': 'OLD-2'}]})
        self.assertFalse(self.db.get_all('Batch', filters={'batch_no': 'OLD-2'}))
        self.assertEqual((self.qty('', item='S'), self.qty('OLD-1', item='S'), self.bin('S')), (4, 6, 10))
        self.assertAlmostEqual(self.bin('S', column='stock_value'), 100, places=2)
        self.assertFalse(self.db.get_all('GL Entry', filters={'voucher_no': assigned['name']}))
        self.post('stock-entry', self.issue('OLD-1', 6, item='S'))
        # The old receipt can still be cancelled for what is left without a batch...
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel'):
            services.cancel_document('stock-entry', old['name'])
        # ...and switching tracking off is refused once batches are in the ledger.
        with self.assertRaisesRegex(ValidationError, 'cannot be switched off'):
            update_master_record('item', 'S', {'has_batch_no': 0})
        self.assert_agreement()

    def test_cancel_of_a_voucher_from_before_tracking(self):
        old = self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                        'items': [{'item_code': 'S', 'qty': 10, 'basic_rate': 10, 't_warehouse': 'A'}]})
        update_master_record('item', 'S', {'has_batch_no': 1})
        services.cancel_document('stock-entry', old['name'])
        self.assertEqual((self.qty('', item='S'), self.bin('S')), (0, 0))
        self.assert_agreement()

    # -- master rules -----------------------------------------------------------
    def test_batch_master_rules(self):
        with self.assertRaisesRegex(ValidationError, 'not batch-tracked'):
            create_master_record('batch', {'item_code': 'S', 'batch_no': 'X'})
        with self.assertRaisesRegex(ValidationError, 'expiry_date is required'):
            create_master_record('batch', {'item_code': 'B', 'batch_no': 'X'})
        made = create_master_record('batch', {'item_code': 'B', 'batch_no': 'X', 'expiry_date': '2027-01-31'})
        self.assertTrue(made['name'].startswith('BATCH-'))
        with self.assertRaisesRegex(ValidationError, 'already exists for item B'):
            create_master_record('batch', {'item_code': 'B', 'batch_no': 'X', 'expiry_date': '2027-01-31'})
        create_master_record('batch', {'item_code': 'T', 'batch_no': 'X'})  # same label, other item
        self.receive('X', 1, expiry=None)
        with self.assertRaisesRegex(ValidationError, 'cannot change structural fields'):
            update_master_record('batch', made['name'], {'batch_no': 'Y'})
        with self.assertRaisesRegex(ValidationError, 'has_expiry_date needs has_batch_no'):
            update_master_record('item', 'S', {'has_expiry_date': 1})

    def test_unknown_batch_field_is_no_longer_rejected_on_lines(self):
        from lambda_erp.database import BATCH_LINE_TABLES
        for table in BATCH_LINE_TABLES:
            self.assertIn('batch_no', self.db._get_table_columns(table), table)

    # -- who has a batch, and who changed it --------------------------------------
    def test_recipient_summary_for_a_recall(self):
        self.db.insert('Customer', {'name': 'D', 'customer_name': 'Second customer', 'default_currency': 'USD'})
        self.post('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12',
            'items': [{'item_code': 'B', 'qty': 10, 'rate': 10, 'warehouse': 'A', 'batch_no': 'LOT-R',
                       'expiry_date': '2027-06-30'}]})

        def deliver(customer, qty):
            return self.post('delivery-note', {**self.delivery([{'qty': qty, 'batch_no': 'LOT-R'}]), 'customer': customer})

        first = deliver('C', 4)
        deliver('C', 1)
        deliver('D', 2)
        self.post('delivery-note', {**self.delivery([{'qty': -1, 'batch_no': 'LOT-R'}], is_return=1,
                                                      return_against=first['name'])})
        from api.routers.reports import _batch_recipients
        got = _batch_recipients(self.db, batch_no='LOT-R')
        self.assertEqual([(r['customer'], r['customer_name'], r['delivered_qty'], r['returned_qty'], r['net_qty'],
                           len(r['documents'])) for r in got['rows']],
                         [('C', 'Customer', 5.0, 1.0, 4.0, 3), ('D', 'Second customer', 2.0, 0.0, 2.0, 1)])
        self.assertEqual([(r['warehouse'], r['actual_qty']) for r in got['in_stock']], [('A', 4.0)])
        self.assertEqual([(r['supplier'], r['received_qty']) for r in got['suppliers']], [('SUP', 10.0)])
        # Delivered + in stock accounts for everything that was received.
        self.assertEqual(sum(r['net_qty'] for r in got['rows']) + sum(r['actual_qty'] for r in got['in_stock']), 10)

    def test_changes_of_expiry_and_block_are_recorded(self):
        from api.routers.reports import _batch_changes
        from lambda_erp import permissions
        self.receive('LOT-A', 2)
        name = get_batch('B', 'LOT-A')['name']
        with permissions.acting_as({'name': 'quality', 'user_id': 'quality', 'role': 'manager'}):
            update_master_record('batch', name, {'expiry_date': '2028-01-31'})
            update_master_record('batch', name, {'blocked': 1, 'blocked_reason': 'recall check'})
            update_master_record('batch', name, {'description': 'not audited', 'expiry_date': '2028-01-31'})
        rows = _batch_changes(self.db, batch_no='LOT-A')['rows']
        self.assertEqual([(r['field'], r['old_value'], r['new_value'], r['changed_by'], r['source']) for r in rows], [
            ('expiry_date', '2027-12-31', '2028-01-31', 'quality', 'Batch master'),
            ('blocked', '0', '1', 'quality', 'Batch master'),
            ('blocked_reason', None, 'recall check', 'quality', 'Batch master')])
        self.assertTrue(all(r['changed_at'] and r['item_code'] == 'B' for r in rows))
        # An expiry date first learned from a receipt is recorded too, with the document.
        create_master_record('batch', {'item_code': 'T', 'batch_no': 'LOT-T'})
        entry = self.receive('LOT-T', 1, item='T', expiry='2029-03-31')
        rows = _batch_changes(self.db, batch_no='LOT-T')['rows']
        self.assertEqual([(r['field'], r['old_value'], r['new_value'], r['source']) for r in rows],
                         [('expiry_date', None, '2029-03-31', f"Stock Entry {entry['name']}")])

    # -- upgrading a database that already has stock ---------------------------
    def test_migration_backfills_existing_stock_without_inventing_batches(self):
        from unittest.mock import patch
        from lambda_erp.database import Database, setup
        from tests.test_rest_api import _reset_db
        self.db.conn.close()
        path = _reset_db()
        with patch.object(Database, 'MIGRATIONS', [m for m in Database.MIGRATIONS if m[0] < 51]):
            old = setup(path)
            self.assertNotIn('has_batch_no', old._get_table_columns('Item'))
            old.insert('Item', {'name': 'OLD', 'item_name': 'Old item', 'is_stock_item': 1})
            old.insert('Bin', {'name': 'OLD-A', 'item_code': 'OLD', 'warehouse': 'A', 'actual_qty': 7,
                               'valuation_rate': 10, 'stock_value': 70})
            old.conn.commit()
            old.conn.close()
        for _ in range(2):  # the upgrade, then a restart
            self.db = setup(path)
            rows = get_batch_balances('OLD')
            self.assertEqual([(row['warehouse'], row['batch_no'], float(row['actual_qty'])) for row in rows],
                             [('A', '', 7.0)])
            self.assertFalse(self.db.get_all('Batch'))
            self.assertEqual(int(self.db.get_value('Item', 'OLD', 'has_batch_no') or 0), 0)
            if _ == 0:
                self.db.conn.close()

    # -- two withdrawals at the same time ---------------------------------------
    def test_concurrent_withdrawals_cannot_overdraw_a_batch(self):
        self.receive('LOT-A', 5)
        drafts = [services.create_document('stock-entry', self.issue('LOT-A', 3))['name'] for _ in range(2)]
        barrier = threading.Barrier(2)

        def submit(name):
            barrier.wait(timeout=10)
            try:
                services.submit_document('stock-entry', name)
                return 'ok'
            except ValidationError:
                return 'refused'
            finally:
                if not self.db._is_memory:
                    self.db.conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, drafts))
        self.assertEqual(sorted(results), ['ok', 'refused'])
        self.assertEqual(self.qty('LOT-A'), 2)
        self.assert_agreement()


if __name__ == '__main__':
    unittest.main()
