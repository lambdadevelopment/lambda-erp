"""Partial quantities, backorders and order progress.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import datetime
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.reports import _purchase_backlog, _sales_backlog
from api.stock_actions import confirm_purchase_order, reserve_order
from lambda_erp import hooks
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import (
    make_delivery_note, make_delivery_return, make_sales_invoice_from_delivery_note,
)
from lambda_erp.stock.purchase_receipt import make_purchase_receipt


def day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class OrderProgress(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        self.db.insert('Customer', {'name': 'D', 'customer_name': 'Second customer', 'default_currency': 'USD'})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def stock(self, qty):
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                         'items': [{'item_code': 'S', 'qty': qty, 'basic_rate': 10, 't_warehouse': 'A'}]})

    def order(self, qty, customer='C', delivery_date=None):
        return self.post('sales-order', {
            'company': 'Test Co', 'customer': customer, 'transaction_date': '2026-11-12',
            'delivery_date': delivery_date or day(30),
            'items': [{'item_code': 'S', 'qty': qty, 'rate': 20, 'warehouse': 'A'}]})

    def purchase(self, qty, schedule_date=None):
        return self.post('purchase-order', {
            'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-12',
            'schedule_date': schedule_date, 'items': [{'item_code': 'S', 'qty': qty, 'rate': 10, 'warehouse': 'A'}]})

    def deliver(self, order, qty=None):
        note = make_delivery_note(order['name'])
        if qty is not None:
            note.get('items')[0]['qty'] = qty
        note.save()
        return note.submit()

    def line(self, order):
        return self.db.get_value('Sales Order Item', order['items'][0]['name'],
                                 ['qty', 'delivered_qty', 'returned_qty', 'billed_qty'])

    def backlog(self, **filters):
        return _sales_backlog(self.db, **filters)['rows']

    # -- acceptance: 25 ordered, 15 delivered, exactly 10 open ----------------
    def test_partial_delivery_leaves_exactly_the_rest_open(self):
        self.stock(15)
        order = self.order(25)
        note = self.deliver(order, 15)
        row = self.backlog(sales_order=order['name'])[0]
        self.assertEqual((row['ordered_qty'], row['delivered_qty'], row['open_qty'], row['deliverable_qty'], row['short_qty']),
                         (25, 15, 10, 0, 10))
        self.assertEqual(float(self.db.get_value('Sales Order', order['name'], 'per_delivered')), 60)
        # The next conversion proposes the remainder, and no more can be booked.
        again = make_delivery_note(order['name'])
        self.assertEqual(again.get('items')[0]['qty'], 10)
        again.get('items')[0]['qty'] = 11
        with self.assertRaisesRegex(ValidationError, 'remaining order quantity'):
            again.save()
        # A return and a cancellation both show up in the line at once.
        back = make_delivery_return(note.name)
        back.get('items')[0]['qty'] = -4
        back.save()
        back.submit()
        got = self.line(order)
        self.assertEqual((got['delivered_qty'], got['returned_qty']), (11, 4))
        self.assertEqual(self.backlog(sales_order=order['name'])[0]['open_qty'], 14)
        services.cancel_document('delivery-note', back.name)
        got = self.line(order)
        self.assertEqual((got['delivered_qty'], got['returned_qty']), (15, 0))
        services.cancel_document('delivery-note', note.name)
        self.assertEqual(self.backlog(sales_order=order['name'])[0]['open_qty'], 25)

    # -- acceptance: a receipt makes backorders deliverable, and books nothing --
    def test_receipt_shows_what_can_now_be_delivered(self):
        first = self.order(6, delivery_date=day(5))
        second = self.order(6, customer='D', delivery_date=day(10))
        purchase = self.purchase(10, schedule_date=day(20))
        self.assertEqual([r['deliverable_qty'] for r in self.backlog()], [0, 0])
        self.assertEqual([(r['on_purchase_order_qty'], r['next_expected_date']) for r in self.backlog()],
                         [(10, day(20)), (10, day(20))])
        self.assertEqual(self.backlog(only_deliverable=True), [])
        # The supplier confirms an earlier date for this order.
        got = confirm_purchase_order({'purchase_order': purchase['name'], 'confirmed_date': day(7),
                                      'confirmation_reference': 'AB-4711'}, {'name': 'buyer'})
        self.assertEqual(len(got['lines']), 1)
        row = self.backlog()[0]
        self.assertEqual(row['next_expected_date'], day(7))
        self.assertEqual(row['purchase_orders'], [{
            'purchase_order': purchase['name'], 'open_qty': 10, 'confirmed_date': day(7),
            'schedule_date': day(20), 'confirmation_reference': 'AB-4711'}])
        # Goods arrive: 10 units for 12 open. The earlier order is served first.
        before = (len(self.db.get_all('Delivery Note')), len(self.db.get_all('Sales Invoice')))
        receipt = make_purchase_receipt(purchase['name'])
        receipt.save()
        receipt.submit()
        rows = self.backlog(only_deliverable=True)
        self.assertEqual([(r['sales_order'], r['deliverable_qty'], r['short_qty']) for r in rows],
                         [(first['name'], 6, 0), (second['name'], 4, 2)])
        self.assertEqual(sum(r['deliverable_qty'] for r in rows), 10)
        self.assertEqual(rows[0]['on_purchase_order_qty'], 0)
        self.assertEqual((len(self.db.get_all('Delivery Note')), len(self.db.get_all('Sales Invoice'))), before)
        # A reservation for the later order takes precedence over due-date order.
        reserve_order({'sales_order': second['name']})
        rows = self.backlog()
        self.assertEqual([(r['sales_order'], r['reserved_qty'], r['deliverable_qty']) for r in rows],
                         [(first['name'], 0, 4), (second['name'], 6, 6)])

    def test_supplier_confirmations(self):
        purchase = self.purchase(10, schedule_date=day(-3))
        draft = services.create_document('purchase-order', {
            'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-12',
            'items': [{'item_code': 'S', 'qty': 1, 'rate': 10, 'warehouse': 'A'}]})
        with self.assertRaisesRegex(ValidationError, 'submitted purchase order'):
            confirm_purchase_order({'purchase_order': draft['name'], 'confirmed_date': day(3)})
        with self.assertRaisesRegex(ValidationError, 'has no line'):
            confirm_purchase_order({'purchase_order': purchase['name'], 'confirmed_date': day(3), 'lines': ['9']})
        rows = _purchase_backlog(self.db, overdue_only=True)['rows']
        self.assertEqual([(r['purchase_order'], r['confirmed'], r['overdue'], r['days_overdue']) for r in rows],
                         [(purchase['name'], False, True, 3)])
        confirm_purchase_order({'purchase_order': purchase['name'], 'confirmed_date': day(4), 'lines': ['1'],
                                'confirmed_qty': 8, 'confirmation_reference': 'AB-1'}, {'name': 'buyer'})
        row = _purchase_backlog(self.db)['rows'][0]
        self.assertEqual((row['expected_date'], row['confirmed'], row['overdue'], row['confirmed_qty'],
                          row['confirmation_reference'], row['open_qty']), (day(4), True, False, 8, 'AB-1', 10))
        stored = self.db.get_value('Purchase Order Item', purchase['items'][0]['name'], ['confirmed_by', 'confirmed_at'])
        self.assertEqual(stored['confirmed_by'], 'buyer')
        self.assertTrue(stored['confirmed_at'])
        self.assertNotIn('rate', row)  # quantities and dates only
        # Fully received lines leave the backlog.
        receipt = make_purchase_receipt(purchase['name'])
        receipt.save()
        receipt.submit()
        self.assertEqual(_purchase_backlog(self.db)['rows'], [])

    # -- deliver first, invoice after --------------------------------------------
    def test_invoice_after_delivery_bills_what_was_delivered_once(self):
        self.stock(25)
        order = self.order(25)
        note = self.deliver(order, 15)
        self.assertEqual(self.db.get_value('Delivery Note', note.name, 'status'), 'Submitted')
        invoice = make_sales_invoice_from_delivery_note(note.name)
        row = invoice.get('items')[0]
        self.assertEqual((row['qty'], row['rate'], row['delivery_note'], row['sales_order_item']),
                         (15, 20, note.name, order['items'][0]['name']))
        self.assertEqual(int(invoice.update_stock or 0), 0)
        row['qty'] = 10  # a partial invoice of the delivery
        invoice.save()
        stock_before = float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty'))
        invoice.submit()
        self.assertEqual(float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty')), stock_before)
        self.assertEqual(float(self.db.get_value('Delivery Note Item', note.get('items')[0]['name'], 'billed_qty')), 10)
        self.assertEqual(self.line(order)['billed_qty'], 10)
        self.assertEqual(self.db.get_value('Delivery Note', note.name, 'status'), 'To Bill')
        # The rest, once: a second attempt for more is refused, a third has nothing left.
        rest = make_sales_invoice_from_delivery_note(note.name)
        self.assertEqual(rest.get('items')[0]['qty'], 5)
        over = make_sales_invoice_from_delivery_note(note.name)
        over.get('items')[0]['qty'] = 6
        with self.assertRaisesRegex(ValidationError, 'left to bill of the delivery \\(5\\)'):
            over.save()
        rest.save()
        rest.submit()
        self.assertEqual((self.db.get_value('Delivery Note', note.name, 'status'),
                          float(self.db.get_value('Delivery Note', note.name, 'per_billed'))), ('Completed', 100))
        with self.assertRaisesRegex(ValidationError, 'already billed in full'):
            make_sales_invoice_from_delivery_note(note.name)
        # Through the generic conversion too.
        with self.assertRaisesRegex(ValidationError, 'already billed in full'):
            services.convert_document('delivery-note', note.name, 'Sales Invoice')
        # It cannot ship the goods again.
        with self.assertRaisesRegex(ValidationError, 'cannot update stock'):
            services.create_document('sales-invoice', {
                'company': 'Test Co', 'customer': 'C', 'update_stock': 1, 'items': [{
                    'item_code': 'S', 'qty': 1, 'rate': 20, 'warehouse': 'A',
                    'delivery_note': note.name, 'dn_detail': note.get('items')[0]['name']}]})
        # Cancelling an invoice reopens the delivery for billing.
        services.cancel_document('sales-invoice', rest.name)
        self.assertEqual(self.db.get_value('Delivery Note', note.name, 'status'), 'To Bill')
        self.assertEqual(self.line(order)['billed_qty'], 10)

    # -- a workflow step inside the transaction -----------------------------------
    def test_on_submit_hook_runs_inside_the_transaction(self):
        self.stock(5)
        seen = []

        def packing_check(doc):
            seen.append(float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty')))
            raise ValidationError('packing was not confirmed')

        hooks.register_hook('Delivery Note:on_submit', packing_check)
        self.addCleanup(hooks.clear_hooks)
        order = self.order(5)
        with self.assertRaisesRegex(ValidationError, 'packing was not confirmed'):
            self.deliver(order)
        self.assertEqual(seen, [0.0])  # the handler saw the posted stock
        self.assertEqual(float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty')), 5)
        self.assertEqual(self.line(order)['delivered_qty'], 0)


if __name__ == '__main__':
    unittest.main()
