"""Storage locations inside a warehouse: mandatory locations, moves inside a
warehouse, whole-location moves, stock from before, and the combined view.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record, update_master_record
from api.routers.reports import _batch_movements, _batch_stock, _stock_detail
from api.stock_actions import prepare_location_move
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_delivery_return
from lambda_erp.stock.stock_ledger import get_stock_details, stock_ledger_agreement


class StorageLocations(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        # Warehouse L uses locations A1, A2, B4; warehouse A (fixture) does not.
        self.db.insert('Warehouse', {'name': 'L', 'warehouse_name': 'L', 'company': 'Test Co', 'use_locations': 1})
        for label in ('A1', 'A2', 'B4'):
            create_master_record('storage-location', {'warehouse': 'L', 'location': label})
        self.db.insert('Item', {'name': 'B', 'item_name': 'Batch item', 'is_stock_item': 1, 'standard_rate': 10,
                                'has_batch_no': 1, 'has_expiry_date': 1, 'item_group': 'Implants'})
        self.db.insert('Item', {'name': 'S2', 'item_name': 'Second', 'is_stock_item': 1, 'standard_rate': 10,
                                'item_group': 'Consumables'})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def entry(self, kind, **line):
        return {'company': 'Test Co', 'stock_entry_type': kind, 'items': [line]}

    def receive(self, item, qty, location, warehouse='L', **extra):
        line = {'item_code': item, 'qty': qty, 'basic_rate': 10, 't_warehouse': warehouse, **extra}
        if location:
            line['t_location'] = location
        return self.post('stock-entry', self.entry('Material Receipt', **line))

    def at(self, item, location, warehouse='L', batch=''):
        rows = get_stock_details(item, warehouse, batch, location)
        return float(rows[0]['actual_qty']) if rows else 0.0

    def bin(self, item, warehouse='L', column='actual_qty'):
        return float(self.db.get_value('Bin', {'item_code': item, 'warehouse': warehouse}, column) or 0)

    def assert_sums(self):
        self.assertEqual(stock_ledger_agreement(), [])

    # -- mandatory and allowed combinations ----------------------------------
    def test_location_is_required_where_used_and_refused_elsewhere(self):
        with self.assertRaisesRegex(ValidationError, 'Storage location is required in warehouse L'):
            self.receive('S', 5, None)
        with self.assertRaisesRegex(ValidationError, 'Unknown storage location Z9 in warehouse L'):
            self.receive('S', 5, 'Z9')
        with self.assertRaisesRegex(ValidationError, 'does not use storage locations'):
            self.receive('S', 5, 'A1', warehouse='A')
        self.receive('S', 5, 'A1')
        self.assertEqual((self.at('S', 'A1'), self.bin('S')), (5, 5))
        with self.assertRaisesRegex(ValidationError, 'Storage location is required in warehouse L'):
            self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=1, s_warehouse='L'))
        draft = services.create_document('delivery-note', {
            'company': 'Test Co', 'customer': 'C', 'items': [{'item_code': 'S', 'qty': 1, 'rate': 20, 'warehouse': 'L'}]})
        with self.assertRaisesRegex(ValidationError, 'Storage location is required'):
            services.submit_document('delivery-note', draft['name'])
        self.assertEqual(self.bin('S'), 5)

    def test_stock_is_checked_per_location(self):
        self.receive('S', 2, 'A1')
        self.receive('S', 8, 'A2')
        with self.assertRaisesRegex(ValidationError, 'in storage location A1 of warehouse L'):
            self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=3, s_warehouse='L', s_location='A1'))
        self.assertEqual((self.at('S', 'A1'), self.at('S', 'A2'), self.bin('S')), (2, 8, 10))
        self.assert_sums()

    # -- acceptance: a partial move changes only the two locations -----------
    def test_partial_move_inside_the_warehouse(self):
        self.receive('S', 10, 'A1')
        self.receive('S2', 4, 'A1')
        value = self.bin('S', column='stock_value')
        move = self.post('stock-entry', self.entry('Material Transfer', item_code='S', qty=3,
                         s_warehouse='L', t_warehouse='L', s_location='A1', t_location='B4'))
        self.assertEqual((self.at('S', 'A1'), self.at('S', 'B4'), self.at('S2', 'A1')), (7, 3, 4))
        self.assertEqual(self.bin('S'), 10)
        self.assertAlmostEqual(self.bin('S', column='stock_value'), value, places=2)
        self.assertFalse(self.db.get_all('GL Entry', filters={'voucher_no': move['name']}))
        self.assertEqual(float(move['value_difference']), 0)
        for bad in ({'t_location': 'A1'}, {'t_location': None}):
            with self.assertRaisesRegex(ValidationError, 'moves stock between storage locations'):
                services.create_document('stock-entry', self.entry(
                    'Material Transfer', **{'item_code': 'S', 'qty': 1, 's_warehouse': 'L', 't_warehouse': 'L',
                                            's_location': 'A1', **bad}))
        with self.assertRaisesRegex(ValidationError, 'must be different'):
            services.create_document('stock-entry', self.entry(
                'Material Transfer', item_code='S', qty=1, s_warehouse='A', t_warehouse='A'))
        services.cancel_document('stock-entry', move['name'])
        self.assertEqual((self.at('S', 'A1'), self.at('S', 'B4')), (10, 0))
        self.assert_sums()

    # -- acceptance: a whole location moves atomically -----------------------
    def test_whole_location_move_is_one_document(self):
        self.receive('S', 5, 'A1')
        self.receive('B', 2, 'A1', batch_no='LOT-1', expiry_date='2027-06-30')
        self.receive('B', 3, 'A1', batch_no='LOT-2', expiry_date='2027-09-30')
        self.receive('S2', 9, 'A2')
        draft = prepare_location_move({'warehouse': 'L', 'from_location': 'A1', 'to_location': 'B4'})
        self.assertEqual(draft['docstatus'], 0)
        self.assertEqual(sorted((r['item_code'], r.get('batch_no'), r['qty']) for r in draft['items']),
                         [('B', 'LOT-1', 2.0), ('B', 'LOT-2', 3.0), ('S', None, 5.0)])
        self.assertEqual(self.at('S', 'A1'), 5)  # a draft moves nothing
        # Something leaves A1 before the move is confirmed: all of it is refused.
        self.post('stock-entry', self.entry('Material Issue', item_code='B', qty=1, s_warehouse='L',
                  s_location='A1', batch_no='LOT-2'))
        with self.assertRaisesRegex(ValidationError, 'Not enough of batch LOT-2'):
            services.submit_document('stock-entry', draft['name'])
        self.assertEqual((self.at('S', 'A1'), self.at('B', 'A1', batch='LOT-1'), self.at('S', 'B4')), (5, 2, 0))
        services.discard_document('stock-entry', draft['name'])
        moved = prepare_location_move({'warehouse': 'L', 'from_location': 'A1', 'to_location': 'B4'})
        services.submit_document('stock-entry', moved['name'])
        self.assertEqual(sorted((r['item_code'], r['batch_no'], r['actual_qty']) for r in get_stock_details(warehouse='L', location='B4')),
                         [('B', 'LOT-1', 2.0), ('B', 'LOT-2', 2.0), ('S', '', 5.0)])
        self.assertFalse([r for r in get_stock_details(warehouse='L', location='A1') if r['actual_qty']])
        self.assertEqual(self.at('S2', 'A2'), 9)
        with self.assertRaisesRegex(ValidationError, 'is empty'):
            prepare_location_move({'warehouse': 'L', 'from_location': 'A1', 'to_location': 'B4'})
        self.assert_sums()

    # -- delivery, return, transfer to a warehouse without locations ---------
    def test_delivery_return_and_transfer_out(self):
        self.receive('B', 6, 'A1', batch_no='LOT-1', expiry_date='2027-06-30')
        note = self.post('delivery-note', {'company': 'Test Co', 'customer': 'C', 'items': [
            {'item_code': 'B', 'qty': 4, 'rate': 20, 'warehouse': 'L', 'location': 'A1', 'batch_no': 'LOT-1'}]})
        self.assertEqual(self.at('B', 'A1', batch='LOT-1'), 2)
        back = make_delivery_return(note['name'])
        self.assertEqual(back.get('items')[0]['location'], 'A1')
        back.get('items')[0]['qty'] = -1
        back.get('items')[0]['location'] = 'A2'  # put back somewhere else
        back.save()
        back.submit()
        self.assertEqual((self.at('B', 'A1', batch='LOT-1'), self.at('B', 'A2', batch='LOT-1')), (2, 1))
        self.post('stock-entry', self.entry('Material Transfer', item_code='B', qty=2, s_warehouse='L',
                  t_warehouse='A', s_location='A1', batch_no='LOT-1'))
        self.assertEqual((self.at('B', 'A1', batch='LOT-1'), self.at('B', '', warehouse='A', batch='LOT-1')), (0, 2))
        moves = _batch_movements(self.db, batch_no='LOT-1', item_code='B')['rows']
        self.assertEqual([(m['warehouse'], m['location'], m['actual_qty']) for m in moves],
                         [('L', 'A1', 6.0), ('L', 'A1', -4.0), ('L', 'A2', 1.0), ('L', 'A1', -2.0), ('A', None, 2.0)])
        self.assertEqual([(r['warehouse'], r['actual_qty']) for r in _batch_stock(self.db, batch_no='LOT-1')['rows']],
                         [('A', 2.0), ('L', 1.0)])
        self.assert_sums()

    # -- stock from before, switching on and off -----------------------------
    def test_switching_a_warehouse_to_locations_keeps_old_stock_unplaced(self):
        self.receive('S', 10, None, warehouse='B')
        update_master_record('warehouse', 'B', {'use_locations': 1})
        create_master_record('storage-location', {'warehouse': 'B', 'location': 'U06'})
        self.assertEqual(self.at('S', '', warehouse='B'), 10)
        with self.assertRaisesRegex(ValidationError, 'Storage location is required in warehouse B'):
            self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=1, s_warehouse='B'))
        self.post('stock-entry', self.entry('Material Transfer', item_code='S', qty=6,
                  s_warehouse='B', t_warehouse='B', t_location='U06'))
        with self.assertRaisesRegex(ValidationError, 'without a storage location'):
            self.post('stock-entry', self.entry('Material Transfer', item_code='S', qty=5,
                      s_warehouse='B', t_warehouse='B', t_location='U06'))
        self.assertEqual((self.at('S', '', warehouse='B'), self.at('S', 'U06', warehouse='B'), self.bin('S', 'B')), (4, 6, 10))
        with self.assertRaisesRegex(ValidationError, 'cannot be switched off'):
            update_master_record('warehouse', 'B', {'use_locations': 0})
        self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=6, s_warehouse='B', s_location='U06'))
        update_master_record('warehouse', 'B', {'use_locations': 0})
        self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=4, s_warehouse='B'))
        self.assertEqual(self.bin('S', 'B'), 0)
        self.assert_sums()

    def test_location_master_rules(self):
        with self.assertRaisesRegex(ValidationError, 'already exists in warehouse L'):
            create_master_record('storage-location', {'warehouse': 'L', 'location': 'A1'})
        create_master_record('storage-location', {'warehouse': 'B', 'location': 'A1'})  # same label, other warehouse
        row = self.db.get_value('Storage Location', {'warehouse': 'L', 'location': 'A2'}, 'name')
        self.receive('S', 3, 'A2')
        with self.assertRaisesRegex(ValidationError, 'cannot change structural fields'):
            update_master_record('storage-location', row, {'location': 'A3'})
        # A disabled location takes no new stock but can still be emptied.
        update_master_record('storage-location', row, {'disabled': 1})
        with self.assertRaisesRegex(ValidationError, 'is disabled'):
            self.receive('S', 1, 'A2')
        self.post('stock-entry', self.entry('Material Issue', item_code='S', qty=3, s_warehouse='L', s_location='A2'))
        self.assertEqual(self.at('S', 'A2'), 0)

    # -- upgrading a database that already has stock and batches -----------------
    def test_migration_keeps_existing_balances_without_a_location(self):
        from unittest.mock import patch
        from lambda_erp.database import Database, setup
        from tests.test_rest_api import _reset_db
        self.db.conn.close()
        path = _reset_db()
        with patch.object(Database, 'MIGRATIONS', [m for m in Database.MIGRATIONS if m[0] < 53]):
            old = setup(path)
            self.assertNotIn('location', old._get_table_columns('Stock Balance Detail'))
            old.insert('Stock Balance Detail', {'name': 'x1', 'item_code': 'OLD', 'warehouse': 'A',
                                                'batch_no': 'LOT-1', 'actual_qty': 7})
            old.insert('Stock Balance Detail', {'name': 'x2', 'item_code': 'OLD', 'warehouse': 'A',
                                                'batch_no': '', 'actual_qty': 2})
            old.conn.commit()
            old.conn.close()
        for attempt in range(2):  # the upgrade, then a restart
            self.db = setup(path)
            rows = sorted((r['batch_no'], r['location'], float(r['actual_qty'])) for r in get_stock_details('OLD'))
            self.assertEqual(rows, [('', '', 2.0), ('LOT-1', '', 7.0)])
            self.assertFalse(self.db.get_all('Storage Location'))
            if attempt == 0:
                self.db.conn.close()
        # The new key lets the same batch lie in two locations of one warehouse.
        self.db.insert('Stock Balance Detail', {'name': 'x3', 'item_code': 'OLD', 'warehouse': 'A',
                                                'batch_no': 'LOT-1', 'location': 'A1', 'actual_qty': 1})
        self.db.commit()

    # -- the combined view -----------------------------------------------------
    def test_combined_view_and_filters(self):
        self.receive('B', 2, 'A1', batch_no='LOT-1', expiry_date='2027-03-31')
        self.receive('B', 3, 'B4', batch_no='LOT-2', expiry_date='2028-01-31')
        self.receive('S2', 9, 'A1')
        self.receive('S', 4, None, warehouse='A')

        def view(**filters):
            return [(r['item_code'], r['warehouse'], r['location'], r['batch_no'], r['expiry_date'], r['actual_qty'])
                    for r in _stock_detail(self.db, **filters)['rows']]

        self.assertEqual(view(), [
            ('B', 'L', 'A1', 'LOT-1', '2027-03-31', 2.0), ('B', 'L', 'B4', 'LOT-2', '2028-01-31', 3.0),
            ('S', 'A', '', '', None, 4.0), ('S2', 'L', 'A1', '', None, 9.0)])
        self.assertEqual([r[0] for r in view(warehouse='L', location='A1')], ['B', 'S2'])
        self.assertEqual([r[0] for r in view(item_group='Implants')], ['B', 'B'])
        self.assertEqual([r[0] for r in view(item_prefix='S')], ['S', 'S2'])
        self.assertEqual([r[3] for r in view(expiring_before='2027-12-31')], ['LOT-1'])
        self.assertEqual([r[3] for r in view(expiring_after='2027-12-31', expiring_before='2028-12-31')], ['LOT-2'])
        self.assertEqual(sum(r[5] for r in view(warehouse='L')), self.bin('B') + self.bin('S2'))
        self.assert_sums()


if __name__ == '__main__':
    unittest.main()
