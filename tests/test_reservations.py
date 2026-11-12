"""Sales reservations, availability buckets, shelf life and FEFO picking.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import datetime
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import update_master_record
from api.routers.reports import _stock_availability
from api.stock_actions import reserve_order, suggest
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock import reservation
from lambda_erp.stock.delivery_note import make_delivery_note, make_delivery_return
from lambda_erp.stock.stock_ledger import stock_ledger_agreement


def day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class Reservations(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Item', {'name': 'B', 'item_name': 'Batch item', 'is_stock_item': 1, 'standard_rate': 10,
                                'has_batch_no': 1, 'has_expiry_date': 1})
        self.addCleanup(reservation._AVAILABILITY_RULES.clear)

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def receive(self, item, qty, **extra):
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt', 'items': [
            {'item_code': item, 'qty': qty, 'basic_rate': 10, 't_warehouse': 'A', **extra}]})

    def order(self, qty, item='S', **extra):
        return self.post('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12', 'delivery_date': '2026-12-30',
            'items': [{'item_code': item, 'qty': qty, 'rate': 20, 'warehouse': 'A'}], **extra})

    def buckets(self, item='S'):
        row = reservation.stock_buckets(item, 'A')
        return (row.physical_qty, row.blocked_qty, row.reserved_qty, row.available_qty)

    def deliver(self, order, qty=None, submit=True):
        note = make_delivery_note(order['name'])
        if qty is not None:
            note.get('items')[0]['qty'] = qty
        note.save()
        return note.submit() if submit else note

    def reservation_of(self, order):
        return self.db.get_value('Stock Reservation', {'sales_order': order['name']},
                                 ['name', 'status', 'reserved_qty'])

    # -- acceptance: the last unit can be reserved once -----------------------
    def test_two_orders_cannot_reserve_the_same_stock(self):
        self.receive('S', 5)
        first, second = self.order(4), self.order(4)
        got = reserve_order({'sales_order': first['name']})
        self.assertTrue(got['fully_reserved'])
        self.assertEqual(self.buckets(), (5, 0, 4, 1))
        got = reserve_order({'sales_order': second['name']})
        self.assertEqual((got['lines'][0]['reserved_qty'], got['lines'][0]['short_qty'], got['fully_reserved']),
                         (1, 3, False))
        self.assertEqual(self.buckets(), (5, 0, 5, 0))
        with self.assertRaisesRegex(ValidationError, 'only 0 available'):
            services.create_document('stock-reservation', {
                'item_code': 'S', 'warehouse': 'A', 'reserved_qty': 1, 'remarks': 'demo for a customer visit'})
        # Reserving again changes nothing while no stock arrives...
        self.assertEqual(reserve_order({'sales_order': second['name']})['lines'][0]['newly_reserved_qty'], 0)
        # ...and tops the reservation up when it does.
        self.receive('S', 10)
        self.assertEqual(reserve_order({'sales_order': second['name']})['lines'][0]['reserved_qty'], 4)
        self.assertEqual(self.buckets(), (15, 0, 8, 7))

    def test_concurrent_reservations_take_the_last_unit_once(self):
        self.receive('S', 1)
        orders = [self.order(1)['name'] for _ in range(2)]
        barrier = threading.Barrier(2)

        def reserve(name):
            barrier.wait(timeout=10)
            try:
                return reservation.reserve_sales_order(name)['lines'][0]['reserved_qty']
            finally:
                if not self.db._is_memory:
                    self.db.conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(reserve, orders))
        self.assertEqual(results, [0, 1])
        self.assertEqual(self.buckets(), (1, 0, 1, 0))

    # -- nobody takes what is reserved -----------------------------------------
    def test_reserved_stock_cannot_leave_for_someone_else(self):
        self.receive('S', 5)
        reserved, other = self.order(4), self.order(3)
        reserve_order({'sales_order': reserved['name']})
        with self.assertRaisesRegex(ValidationError, f"is reserved .*Reserved for: {reserved['name']} \\(4\\)"):
            self.deliver(other)
        with self.assertRaisesRegex(ValidationError, 'is reserved'):
            self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                      'items': [{'item_code': 'S', 'qty': 2, 's_warehouse': 'A'}]})
        with self.assertRaisesRegex(ValidationError, 'is reserved'):
            self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Transfer',
                      'items': [{'item_code': 'S', 'qty': 2, 's_warehouse': 'A', 't_warehouse': 'B'}]})
        with self.assertRaisesRegex(ValidationError, 'is reserved'):
            self.post('sales-invoice', {'company': 'Test Co', 'customer': 'C', 'update_stock': 1,
                      'items': [{'item_code': 'S', 'qty': 2, 'rate': 20, 'warehouse': 'A'}]})
        self.assertEqual(self.buckets(), (5, 0, 4, 1))
        # The one free unit, and the reserved order itself, go out fine.
        self.deliver(other, 1)
        self.deliver(reserved)
        self.assertEqual(self.buckets(), (0, 0, 0, 0))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_cancelling_a_receipt_that_backs_a_reservation_is_refused(self):
        receipt = self.receive('S', 5)
        order = self.order(4)
        reserve_order({'sales_order': order['name']})
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel Stock Entry .* is reserved'):
            services.cancel_document('stock-entry', receipt['name'])
        self.assertEqual(self.buckets(), (5, 0, 4, 1))

    # -- acceptance: partial delivery, return, cancel ---------------------------
    def test_partial_delivery_return_and_cancel_follow_the_order(self):
        self.receive('S', 25)
        order = self.order(25, reserve_stock=1)  # reserved on submit
        self.assertEqual(self.reservation_of(order)['status'], 'Reserved')
        self.assertEqual(self.buckets(), (25, 0, 25, 0))
        note = self.deliver(order, 15)
        self.assertEqual(self.buckets(), (10, 0, 10, 0))
        self.assertEqual(self.reservation_of(order)['status'], 'Partially Delivered')
        back = make_delivery_return(note.name)
        back.get('items')[0]['qty'] = -5
        back.save()
        back.submit()
        self.assertEqual(self.buckets(), (15, 0, 15, 0))  # returned goods are held for the order again
        services.cancel_document('delivery-note', back.name)
        services.cancel_document('delivery-note', note.name)
        self.assertEqual(self.buckets(), (25, 0, 25, 0))
        self.assertEqual(self.reservation_of(order)['status'], 'Reserved')
        full = self.deliver(order)
        self.assertEqual((self.buckets(), self.reservation_of(order)['status']), ((0, 0, 0, 0), 'Delivered'))
        services.cancel_document('delivery-note', full.name)
        services.cancel_document('sales-order', order['name'])
        self.assertEqual((self.buckets(), self.reservation_of(order)['status']), ((25, 0, 0, 25), 'Cancelled'))

    def test_release_and_free_standing_reservation(self):
        self.receive('S', 5)
        order = self.order(5)
        reserve_order({'sales_order': order['name']})
        name = self.reservation_of(order)['name']
        with self.assertRaisesRegex(ValidationError, 'Cancelled is set when'):
            services.update_document('stock-reservation', name, {'status': 'Cancelled'})
        with self.assertRaisesRegex(ValidationError, 'exceeds the ordered qty'):
            services.update_document('stock-reservation', name, {'reserved_qty': 6})
        services.update_document('stock-reservation', name, {'reserved_qty': 3})
        self.assertEqual(self.buckets(), (5, 0, 3, 2))
        services.update_document('stock-reservation', name, {'status': 'Released'})
        self.assertEqual(self.buckets(), (5, 0, 0, 5))
        with self.assertRaisesRegex(ValidationError, 'remarks must say'):
            services.create_document('stock-reservation', {'item_code': 'S', 'warehouse': 'A', 'reserved_qty': 2})
        aside = services.create_document('stock-reservation', {
            'item_code': 'S', 'warehouse': 'A', 'reserved_qty': 2, 'remarks': 'set aside for a field rep'})
        self.assertEqual((aside['status'], self.buckets()), ('Reserved', (5, 0, 2, 3)))
        with self.assertRaisesRegex(ValidationError, 'already has reservation'):
            services.create_document('stock-reservation', {
                'item_code': 'S', 'warehouse': 'A', 'reserved_qty': 1, 'sales_order': order['name'],
                'sales_order_item': order['items'][0]['name']})
        rows = _stock_availability(self.db, item_code='S')['rows']
        self.assertEqual([(r['physical_qty'], r['reserved_qty'], r['available_qty'], r['open_sales_order_qty'])
                          for r in rows], [(5, 2, 3, 5)])

    # -- blocked and expired stock ----------------------------------------------
    def test_expired_and_blocked_batches_are_not_available_and_not_sellable(self):
        self.receive('B', 4, batch_no='OLD', expiry_date=day(-1))
        self.receive('B', 3, batch_no='HOLD', expiry_date=day(400))
        self.receive('B', 6, batch_no='GOOD', expiry_date=day(300))
        hold = self.db.get_value('Batch', {'item_code': 'B', 'batch_no': 'HOLD'}, 'name')
        update_master_record('batch', hold, {'blocked': 1, 'blocked_reason': 'recall check'})
        self.assertEqual(self.buckets('B'), (13, 7, 0, 6))
        order = self.order(8, item='B')
        got = reserve_order({'sales_order': order['name']})
        self.assertEqual((got['lines'][0]['reserved_qty'], got['lines'][0]['short_qty']), (6, 2))

        def sell(batch):
            return self.post('delivery-note', {'company': 'Test Co', 'customer': 'C', 'items': [
                {'item_code': 'B', 'qty': 1, 'rate': 20, 'warehouse': 'A', 'batch_no': batch}]})

        with self.assertRaisesRegex(ValidationError, 'expired on .* and cannot be sold'):
            sell('OLD')
        with self.assertRaisesRegex(ValidationError, r'is blocked \(recall check\) and cannot be sold'):
            sell('HOLD')
        # Not sellable is not immovable: expired stock can be written off or moved away.
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                  'items': [{'item_code': 'B', 'qty': 4, 's_warehouse': 'A', 'batch_no': 'OLD'}]})
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Transfer',
                  'items': [{'item_code': 'B', 'qty': 3, 's_warehouse': 'A', 't_warehouse': 'B', 'batch_no': 'HOLD'}]})
        self.assertEqual(self.buckets('B'), (6, 0, 6, 0))

    def test_minimum_shelf_life_needs_an_exception(self):
        update_master_record('item', 'B', {'min_shelf_life_days': 90})
        self.receive('B', 5, batch_no='SHORT', expiry_date=day(30))
        line = {'item_code': 'B', 'qty': 1, 'rate': 20, 'warehouse': 'A', 'batch_no': 'SHORT'}
        note = {'company': 'Test Co', 'customer': 'C', 'items': [line]}
        with self.assertRaisesRegex(ValidationError, 'has 30 day\\(s\\) left, less than the 90 day\\(s\\) required'):
            self.post('delivery-note', note)
        done = self.post('delivery-note', {**note, 'items': [{**line, 'shelf_life_exception': 'approved by sales lead'}]})
        self.assertEqual(done['items'][0]['shelf_life_exception'], 'approved by sales lead')
        # A deployment rule: this customer takes short-dated goods.
        reservation.register_availability_rule(
            lambda item_code, customer, days: 0 if customer == 'C' else None)
        self.post('delivery-note', note)
        self.assertEqual(self.buckets('B')[0], 3)

    # -- acceptance: FEFO is reproducible and changes nothing --------------------
    def test_fefo_suggestion(self):
        self.receive('B', 2, batch_no='LATE', expiry_date=day(500))
        self.receive('B', 3, batch_no='EARLY', expiry_date=day(100))
        self.receive('B', 4, batch_no='MID', expiry_date=day(200))
        self.receive('B', 5, batch_no='GONE', expiry_date=day(-3))
        before = (self.buckets('B'), len(self.db.get_all('Stock Ledger Entry')), len(self.db.get_all('Stock Reservation')))
        got = suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 6})
        self.assertEqual([(p['batch_no'], p['qty']) for p in got['picks']], [('EARLY', 3), ('MID', 3)])
        self.assertEqual((got['picked_qty'], got['shortfall_qty'], got['strategy']), (6, 0, 'FEFO'))
        self.assertEqual([(e['batch_no'], e['reason']) for e in got['excluded']], [('GONE', 'expired')])
        self.assertEqual(suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 6}), got)
        after = (self.buckets('B'), len(self.db.get_all('Stock Ledger Entry')), len(self.db.get_all('Stock Reservation')))
        self.assertEqual(before, after)
        self.assertFalse(self.db.get_all('Sales Invoice'))
        # Short-dated stock is left out, or offered with a flag.
        update_master_record('item', 'B', {'min_shelf_life_days': 150})
        got = suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 6})
        self.assertEqual([(p['batch_no'], p['qty']) for p in got['picks']], [('MID', 4), ('LATE', 2)])
        got = suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 6, 'include_short_dated': True})
        self.assertEqual([(p['batch_no'], p['needs_exception']) for p in got['picks']], [('EARLY', True), ('MID', False)])
        # Stock reserved for another order is not offered; an order's own is.
        update_master_record('item', 'B', {'min_shelf_life_days': 0})
        mine, theirs = self.order(5, item='B'), self.order(4, item='B')
        reserve_order({'sales_order': theirs['name']})
        got = suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 9})
        self.assertEqual((got['picked_qty'], got['shortfall_qty'], got['reserved_for_others_qty']), (5, 4, 4))
        got = suggest({'item_code': 'B', 'warehouse': 'A', 'qty': 4, 'sales_order_item': theirs['items'][0]['name']})
        self.assertEqual(got['picked_qty'], 4)
        # The delivery note converter proposes the same picks, one line per batch.
        note = make_delivery_note(mine['name'])
        self.assertEqual([(row['batch_no'], row['qty']) for row in note.get('items')], [('EARLY', 3), ('MID', 2)])
        note.save()
        note.submit()
        self.assertEqual(self.buckets('B'), (9, 5, 4, 0))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_untracked_items_convert_as_before(self):
        self.receive('S', 3)
        order = self.order(5)
        note = make_delivery_note(order['name'])
        self.assertEqual([(row['qty'], row.get('batch_no')) for row in note.get('items')], [(5, None)])


if __name__ == '__main__':
    unittest.main()
