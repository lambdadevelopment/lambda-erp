"""Serial numbers: one number per unit, in one place at a time.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import update_master_record
from api.routers.reports import _serial_trace
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_delivery_note, make_delivery_return
from lambda_erp.stock.serial import get_serial, serial_agreement, serials_in_stock
from lambda_erp.stock.stock_ledger import stock_ledger_agreement


class Serials(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        self.db.insert('Item', {'name': 'D', 'item_name': 'Device', 'is_stock_item': 1, 'standard_rate': 100,
                                'has_serial_no': 1})
        self.db.insert('Item', {'name': 'E', 'item_name': 'Other device', 'is_stock_item': 1, 'standard_rate': 100,
                                'has_serial_no': 1})
        self.db.insert('Item', {'name': 'P', 'item_name': 'Probe', 'is_stock_item': 1, 'standard_rate': 100,
                                'has_serial_no': 1, 'has_batch_no': 1})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def receive(self, serials, item='D', warehouse='A', qty=None, **extra):
        return self.post('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12', 'items': [{
                'item_code': item, 'qty': qty if qty is not None else len(serials.replace(',', '\n').replace(';', '\n').split()),
                'rate': 50, 'warehouse': warehouse, 'serial_no': serials, **extra}]})

    def deliver(self, serials, qty=None, item='D', warehouse='A', **extra):
        return {'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12', 'items': [{
            'item_code': item, 'qty': qty if qty is not None else len(serials.split(',')), 'rate': 100,
            'warehouse': warehouse, 'serial_no': serials, **extra}]}

    def where(self, serial, item='D'):
        unit = get_serial(item, serial)
        return (unit['warehouse'], unit['status']) if unit else None

    def stock(self, item='D', warehouse='A'):
        return [row['serial_no'] for row in serials_in_stock(item, warehouse)]

    def healthy(self):
        self.assertEqual(serial_agreement(), [])
        self.assertEqual(stock_ledger_agreement(), [])

    # -- one number per unit ---------------------------------------------------
    def test_every_unit_needs_its_own_number(self):
        with self.assertRaisesRegex(ValidationError, 'Serial numbers are required for item D'):
            self.receive('', qty=2)
        with self.assertRaisesRegex(ValidationError, '2 serial number\\(s\\) for a quantity of 3'):
            self.receive('SN1, SN2', qty=3)
        with self.assertRaisesRegex(ValidationError, 'listed twice'):
            self.receive('SN1\nSN1', qty=2)
        with self.assertRaisesRegex(ValidationError, 'must be a whole number'):
            self.receive('SN1', qty=1.5)
        with self.assertRaisesRegex(ValidationError, 'not serial-tracked'):
            self.receive('SN1', item='S')
        self.assertFalse(self.db.get_all('Serial No'))
        self.receive('SN1, SN2;SN3')            # commas, semicolons and line breaks all separate
        self.assertEqual(self.stock(), ['SN1', 'SN2', 'SN3'])
        with self.assertRaisesRegex(ValidationError, 'SN2 of item D is already in stock in warehouse A'):
            self.receive('SN2')
        # The same number on another item is another unit.
        self.receive('SN1', item='E')
        self.assertEqual((self.stock('D'), self.stock('E')), (['SN1', 'SN2', 'SN3'], ['SN1']))
        self.healthy()

    def test_a_unit_leaves_only_from_where_it_is(self):
        self.receive('SN1, SN2')
        self.receive('SN9', warehouse='B')
        for serials, message in (('SN7', 'Unknown serial number SN7'),
                                 ('SN9', 'SN9 of item D is not in warehouse A: it is in warehouse B'),
                                 ('', 'Serial numbers are required')):
            with self.assertRaisesRegex(ValidationError, message):
                self.post('delivery-note', self.deliver(serials, qty=1))
        self.assertEqual(self.stock(), ['SN1', 'SN2'])
        note = self.post('delivery-note', self.deliver('SN1'))
        self.assertEqual((self.where('SN1'), get_serial('D', 'SN1')['customer']), ((None, 'Delivered'), 'C'))
        with self.assertRaisesRegex(ValidationError, 'SN1 of item D is not in warehouse A: it is not in stock \\(Delivered\\)'):
            self.post('delivery-note', self.deliver('SN1'))
        # A multi-line document with one bad unit posts nothing.
        mixed = self.deliver('SN2')
        mixed['items'].append({**mixed['items'][0], 'serial_no': 'SN1'})
        with self.assertRaisesRegex(ValidationError, 'not in warehouse A'):
            self.post('delivery-note', mixed)
        self.assertEqual(self.stock(), ['SN2'])
        # Cancelling the delivery brings the unit back; cancelling a receipt whose unit left is refused.
        services.cancel_document('delivery-note', note['name'])
        self.assertEqual(self.where('SN1'), ('A', 'In Stock'))
        self.healthy()

    def test_transfer_return_and_cancel_follow_the_unit(self):
        receipt = self.receive('SN1, SN2, SN3')
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Transfer', 'items': [
            {'item_code': 'D', 'qty': 1, 's_warehouse': 'A', 't_warehouse': 'B', 'serial_no': 'SN3'}]})
        self.assertEqual((self.stock('D', 'A'), self.stock('D', 'B')), (['SN1', 'SN2'], ['SN3']))
        note = self.post('delivery-note', self.deliver('SN1,SN2'))
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel Purchase Receipt'):
            services.cancel_document('purchase-receipt', receipt['name'])
        # A return brings back units of that delivery, and only those.
        back = make_delivery_return(note['name'])
        self.assertEqual(back.get('items')[0]['serial_no'], 'SN1\nSN2')
        wrong = {**self.deliver('SN3', qty=-1), 'is_return': 1, 'return_against': note['name']}
        with self.assertRaisesRegex(ValidationError, 'SN3 .* was not on Delivery Note'):
            services.create_document('delivery-note', wrong)
        back.get('items')[0].update({'qty': -1, 'serial_no': 'SN2'})
        back.save()
        back.submit()
        self.assertEqual((self.where('SN2'), self.where('SN1')), (('A', 'In Stock'), (None, 'Delivered')))
        # Everything that happened to one unit, with documents and parties.
        trace = _serial_trace(self.db, serial_no='SN2')['rows'][0]
        self.assertEqual((trace['status'], trace['warehouse']), ('In Stock', 'A'))
        self.assertEqual([(m['voucher_type'], m['actual_qty'], m['party']) for m in trace['movements']],
                         [('Purchase Receipt', 1.0, 'SUP'), ('Delivery Note', -1.0, 'C'), ('Delivery Note', 1.0, 'C')])
        self.assertEqual(sorted(r['item_code'] for r in _serial_trace(self.db, serial_no='SN1')['rows']), ['D'])
        self.healthy()

    def test_serial_within_batch_and_fefo_proposal(self):
        self.receive('P1, P2', item='P', batch_no='LOT-LATE', expiry_date='2028-06-30')
        self.receive('P3', item='P', batch_no='LOT-EARLY', expiry_date='2027-06-30')
        with self.assertRaisesRegex(ValidationError, 'P1 of item P belongs to batch LOT-LATE, not LOT-EARLY'):
            self.post('delivery-note', self.deliver('P1', item='P', batch_no='LOT-EARLY'))
        order = self.post('sales-order', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                          'delivery_date': '2026-12-30', 'items': [{'item_code': 'P', 'qty': 2, 'rate': 100, 'warehouse': 'A'}]})
        note = make_delivery_note(order['name'])
        self.assertEqual([(r['batch_no'], r['qty'], r['serial_no']) for r in note.get('items')],
                         [('LOT-EARLY', 1.0, 'P3'), ('LOT-LATE', 1.0, 'P1')])
        note.save()
        note.submit()
        self.assertEqual(self.stock('P'), ['P2'])
        self.healthy()

    # -- stock from before tracking -----------------------------------------------
    def test_switching_an_item_to_serials_keeps_old_units_unnumbered(self):
        old = self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                        'items': [{'item_code': 'S', 'qty': 3, 'basic_rate': 10, 't_warehouse': 'A'}]})
        update_master_record('item', 'S', {'has_serial_no': 1})
        self.assertEqual(self.stock('S'), [])
        with self.assertRaisesRegex(ValidationError, 'Serial numbers are required for item S'):
            self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                      'items': [{'item_code': 'S', 'qty': 1, 's_warehouse': 'A'}]})
        assign = {'company': 'Test Co', 'stock_entry_type': 'Serial Assignment',
                  'items': [{'item_code': 'S', 'qty': 2, 't_warehouse': 'A', 'serial_no': 'OLD-1, OLD-2'}]}
        done = self.post('stock-entry', assign)
        self.assertEqual(self.stock('S'), ['OLD-1', 'OLD-2'])
        self.assertEqual(float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty')), 3)
        self.assertFalse(self.db.get_all('GL Entry', filters={'voucher_no': done['name']}))
        # Only one unit is left without a number.
        with self.assertRaisesRegex(ValidationError, 'Not enough stock without a serial number'):
            self.post('stock-entry', {**assign, 'items': [{**assign['items'][0], 'serial_no': 'OLD-3, OLD-4'}]})
        with self.assertRaisesRegex(ValidationError, 'cannot be switched off'):
            update_master_record('item', 'S', {'has_serial_no': 0})
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel'):
            services.cancel_document('stock-entry', old['name'])
        self.healthy()

    # -- two documents, one unit ------------------------------------------------------
    def test_concurrent_deliveries_cannot_ship_the_same_unit(self):
        self.receive('SN1, SN2')
        drafts = [services.create_document('delivery-note', self.deliver('SN1'))['name'] for _ in range(2)]
        barrier = threading.Barrier(2)

        def submit(name):
            barrier.wait(timeout=10)
            try:
                services.submit_document('delivery-note', name)
                return 'ok'
            except ValidationError:
                return 'refused'
            finally:
                if not self.db._is_memory:
                    self.db.conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(submit, drafts))
        self.assertEqual(results, ['ok', 'refused'])
        self.assertEqual(self.stock(), ['SN2'])
        self.healthy()


if __name__ == '__main__':
    unittest.main()
