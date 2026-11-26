"""Closing the rest of an order line, deliveries in several steps, and one
invoice for several deliveries (collective / monthly invoice).

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.order_actions import close_lines, prepare_collective_invoice, reopen_lines
from api.routers.reports import _purchase_backlog, _sales_backlog, _unbilled_deliveries
from api.stock_actions import reserve_order
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_collective_sales_invoice, make_delivery_note
from lambda_erp.stock.purchase_receipt import make_purchase_receipt
from lambda_erp.selling.sales_order import make_sales_invoice


class Base(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Item', {'name': 'T', 'item_name': 'Second stock item', 'is_stock_item': 1, 'standard_rate': 5})
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        self.db.insert('Customer', {'name': 'D', 'customer_name': 'Second customer', 'default_currency': 'USD'})
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt', 'items': [
            {'item_code': 'S', 'qty': 100, 'basic_rate': 10, 't_warehouse': 'A'},
            {'item_code': 'T', 'qty': 100, 'basic_rate': 2, 't_warehouse': 'A'}]})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def order(self, customer='C', lines=(('S', 4, 20), ('T', 5, 8)), **extra):
        return self.post('sales-order', {
            'company': 'Test Co', 'customer': customer, 'transaction_date': '2026-11-12', **extra,
            'items': [{'item_code': item, 'qty': qty, 'rate': rate, 'warehouse': 'A'} for item, qty, rate in lines]})

    def deliver(self, order, quantities, posting_date='2026-11-15'):
        """quantities: item -> qty delivered now; an item left out (or 0) is not delivered this time."""
        note = make_delivery_note(order['name'])
        rows = [row for row in note.get('items') if quantities.get(row['item_code'])]
        for row in rows:
            row['qty'] = quantities[row['item_code']]
        note.set('items', rows)
        note.posting_date = posting_date
        note.save()
        return note.submit()

    def lines(self, order):
        rows = self.db.get_all('Sales Order Item', filters={'parent': order['name']}, fields=['*'], order_by='idx')
        return {row['item_code']: row for row in rows}

    def head(self, order):
        return self.db.get_value('Sales Order', order['name'], ['status', 'per_delivered', 'per_billed'])

    def close(self, order, lines=None, reason='Customer cancelled the rest', **extra):
        return close_lines({'order': order['name'], 'lines': lines, 'reason': reason, **extra},
                           {'user_id': 'user-1'})



class OrderClosing(Base):
    # -- partial deliveries in several steps, then the customer cancels the rest
    def test_three_partial_deliveries_and_closing_the_rest(self):
        order = self.order()
        line_t = self.lines(order)['T']['name']
        self.deliver(order, {'S': 3}, '2026-11-13')           # T not delivered this time
        self.deliver(order, {'T': 2}, '2026-11-14')
        self.deliver(order, {'T': 1}, '2026-11-15')
        got = self.lines(order)
        self.assertEqual((got['S']['delivered_qty'], got['T']['delivered_qty']), (3, 3))
        backlog = {row['item_code']: row for row in _sales_backlog(self.db, sales_order=order['name'])['rows']}
        self.assertEqual((backlog['S']['open_qty'], backlog['T']['open_qty']), (1, 2))

        # The customer no longer wants the rest of T: only T is closed, S stays open.
        result = self.close(order, [line_t])
        self.assertEqual([(row['item_code'], row['closed_now'], row['open_qty']) for row in result['lines']],
                         [('T', 2, 0)])
        got = self.lines(order)
        self.assertEqual((got['T']['qty'], got['T']['closed_qty'], got['T']['delivered_qty'], got['T']['close_reason'],
                          got['T']['closed_by'], bool(got['T']['closed_at'])),
                         (5, 2, 3, 'Customer cancelled the rest', 'user-1', True))
        backlog = _sales_backlog(self.db, sales_order=order['name'])['rows']
        self.assertEqual([(row['item_code'], row['open_qty']) for row in backlog], [('S', 1)])
        # 6 of 7 still to deliver are delivered (4 + 5 − 2 closed).
        self.assertEqual(round(float(self.head(order)['per_delivered']), 2), 85.71)

        # The next delivery proposes only S, and the closed quantity of T cannot be delivered.
        proposal = make_delivery_note(order['name'])
        self.assertEqual([(row['item_code'], row['qty']) for row in proposal.get('items')], [('S', 1)])
        sneaky = make_delivery_note(order['name'])
        sneaky.append('items', {**dict(proposal.get('items')[0]), 'name': None, 'item_code': 'T', 'qty': 1,
                                'so_detail': line_t})
        with self.assertRaisesRegex(ValidationError, '2 of the line closed'):
            sneaky.save()

        # Last delivery, then everything is delivered; billing is what is left.
        self.deliver(order, {'S': 1}, '2026-11-20')
        head = self.head(order)
        self.assertEqual((float(head['per_delivered']), head['status']), (100, 'To Bill'))
        # Billing straight from the order bills only what the line still stands for.
        invoice = make_sales_invoice(order['name'])
        self.assertEqual(sorted((row['item_code'], row['qty']) for row in invoice.get('items')),
                         [('S', 4), ('T', 3)])

    def test_close_needs_a_reason_and_open_quantity(self):
        order = self.order()
        with self.assertRaises(ValueError):  # pydantic: empty reason
            self.close(order, reason='')
        with self.assertRaisesRegex(ValidationError, 'needs a reason'):
            from lambda_erp.order_closing import close_order_lines
            close_order_lines('Sales Order', order['name'], reason='  ')
        self.deliver(order, {'S': 4})
        line_s = self.lines(order)['S']['name']
        with self.assertRaisesRegex(ValidationError, 'nothing open to close'):
            self.close(order, [line_s])
        with self.assertRaisesRegex(ValidationError, 'has no line'):
            self.close(order, ['nope'])
        # Partial closing of one line, by position.
        self.close(order, ['2'], qty=2)
        self.assertEqual(self.lines(order)['T']['closed_qty'], 2)
        with self.assertRaisesRegex(ValidationError, 'exceeds what is open'):
            self.close(order, ['2'], qty=4)
        with self.assertRaisesRegex(ValidationError, 'one line'):
            self.close(self.order(), qty=1)
        # A draft order has nothing to close, and a closing sent with a draft is dropped.
        draft = services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
            'items': [{'item_code': 'S', 'qty': 2, 'rate': 20, 'warehouse': 'A', 'closed_qty': 2}]})
        self.assertFalse(draft['items'][0].get('closed_qty'))
        with self.assertRaisesRegex(ValidationError, 'submitted'):
            self.close(draft)

    def test_invoiced_quantity_cannot_be_closed(self):
        order = self.order(lines=(('S', 4, 20),))
        invoice = make_sales_invoice(order['name'])
        invoice.get('items')[0]['qty'] = 3
        invoice.save()
        invoice.submit()
        result = self.close(order)
        self.assertEqual(result['lines'][0]['closed_now'], 1)

    def test_fully_closed_order_is_completed_and_reopen_restores_it(self):
        order = self.order(lines=(('S', 4, 20),))
        self.deliver(order, {'S': 1})
        invoice = services.convert_document('delivery-note', self.db.get_all(
            'Delivery Note Item', filters={'so_detail': self.lines(order)['S']['name']}, fields=['parent'])[0]['parent'],
            'Sales Invoice')
        services.submit_document('sales-invoice', invoice['name'])
        self.close(order)
        head = self.head(order)
        self.assertEqual((float(head['per_delivered']), float(head['per_billed']), head['status']),
                         (100, 100, 'Completed'))
        self.assertEqual(_sales_backlog(self.db, sales_order=order['name'])['rows'], [])

        result = reopen_lines({'order': order['name']})
        self.assertEqual(result['lines'][0]['reopened_qty'], 3)
        got = self.lines(order)['S']
        self.assertEqual((got['closed_qty'], got['close_reason']), (0, None))
        self.assertEqual(self.head(order)['status'], 'To Deliver and Bill')
        with self.assertRaisesRegex(ValidationError, 'no closed quantity'):
            reopen_lines({'order': order['name']})

        # Nothing delivered, everything closed: nothing left to do either.
        other = self.order(lines=(('T', 2, 8),))
        self.close(other)
        self.assertEqual(self.head(other)['status'], 'Completed')

    def test_closing_releases_the_reservation_and_planning(self):
        order = self.order(lines=(('S', 10, 20),))
        reserve_order({'sales_order': order['name']})
        self.deliver(order, {'S': 4})
        bin_ = lambda: self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'reserved_qty')
        self.assertEqual(bin_(), 6)
        self.close(order)
        reservation = self.db.get_value('Stock Reservation', {'sales_order': order['name']},
                                        ['reserved_qty', 'status'])
        self.assertEqual((reservation['reserved_qty'], reservation['status']), (4, 'Delivered'))
        self.assertEqual(bin_(), 0)

        # Closed before anything was delivered: the reservation is released.
        second = self.order(lines=(('S', 3, 20),))
        reserve_order({'sales_order': second['name']})
        self.close(second)
        self.assertEqual(self.db.get_value('Stock Reservation', {'sales_order': second['name']}, 'status'), 'Released')

    def test_purchase_order_rest_can_be_closed(self):
        order = self.post('purchase-order', {
            'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-12',
            'items': [{'item_code': 'S', 'qty': 20, 'rate': 10, 'warehouse': 'A'},
                      {'item_code': 'T', 'qty': 10, 'rate': 2, 'warehouse': 'A'}]})
        receipt = make_purchase_receipt(order['name'])
        receipt.set('items', [row for row in receipt.get('items') if row['item_code'] == 'S'])
        receipt.get('items')[0]['qty'] = 10
        receipt.save()
        receipt.submit()
        ordered = lambda: self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'ordered_qty')
        self.assertEqual(ordered(), 10)
        close_lines({'doctype': 'Purchase Order', 'order': order['name'], 'lines': ['1'],
                     'reason': 'Supplier discontinued the item'})
        self.assertEqual(ordered(), 0)
        rows = _purchase_backlog(self.db, supplier='SUP')['rows']
        self.assertEqual([(row['item_code'], row['open_qty']) for row in rows], [('T', 10)])
        proposal = make_purchase_receipt(order['name'])
        self.assertEqual([(row['item_code'], row['qty']) for row in proposal.get('items')], [('T', 10)])


class CollectiveInvoice(Base):
    """Deliver through the month, bill once at the end."""

    def test_collective_invoice_bills_every_delivery_once(self):
        first = self.order()
        second = self.order(lines=(('S', 2, 20),))
        other = self.order(customer='D', lines=(('S', 1, 20),))
        n1 = self.deliver(first, {'S': 3, 'T': 5}, '2026-11-03')
        n2 = self.deliver(first, {'S': 1}, '2026-11-17')
        n3 = self.deliver(second, {'S': 2}, '2026-11-24')
        self.deliver(other, {'S': 1}, '2026-11-10')

        rows = _unbilled_deliveries(self.db, customer='C', from_date='2026-11-01', to_date='2026-11-30')['rows']
        self.assertEqual([(row['delivery_note'], row['item_code'], row['unbilled_qty']) for row in rows],
                         [(n1.name, 'S', 3), (n1.name, 'T', 5), (n2.name, 'S', 1), (n3.name, 'S', 2)])

        draft = prepare_collective_invoice({'customer': 'C', 'from_date': '2026-11-01', 'to_date': '2026-11-30'})
        self.assertEqual([(row['delivery_note'], row['item_code'], row['qty'], bool(row['sales_order_item']))
                          for row in draft['items']],
                         [(n1.name, 'S', 3, True), (n1.name, 'T', 5, True), (n2.name, 'S', 1, True),
                          (n3.name, 'S', 2, True)])
        self.assertEqual((draft['customer'], draft['update_stock'], draft['net_total']), ('C', 0, 160))
        stock_before = self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty')
        services.submit_document('sales-invoice', draft['name'])
        self.assertEqual(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, 'actual_qty'), stock_before)

        # Every delivery and both orders know they are billed; nothing is offered again.
        for note in (n1, n2, n3):
            self.assertEqual(self.db.get_value('Delivery Note', note.name, 'status'), 'Completed')
        self.assertEqual(self.head(first)['status'], 'Completed')
        self.assertEqual(self.head(second)['status'], 'Completed')
        self.assertEqual(_unbilled_deliveries(self.db, customer='C')['rows'], [])
        with self.assertRaisesRegex(ValidationError, 'Nothing delivered is left to bill'):
            prepare_collective_invoice({'customer': 'C'})
        # A second draft made before the first was submitted cannot bill twice.
        services.cancel_document('sales-invoice', draft['name'])
        one = make_collective_sales_invoice('C')
        two = make_collective_sales_invoice('C')
        one.save()
        one.submit()
        with self.assertRaisesRegex(ValidationError, 'left to bill of the delivery|remaining order quantity'):
            two.save()
            two.submit()

    def test_selection_by_notes_or_lines_and_one_customer_per_invoice(self):
        first = self.order()
        other = self.order(customer='D', lines=(('S', 1, 20),))
        n1 = self.deliver(first, {'S': 2}, '2026-11-03')
        n2 = self.deliver(first, {'S': 2, 'T': 5}, '2026-11-04')
        n3 = self.deliver(other, {'S': 1}, '2026-11-05')
        with self.assertRaisesRegex(ValidationError, 'several customers'):
            make_collective_sales_invoice(delivery_notes=[n1.name, n3.name])
        with self.assertRaisesRegex(ValidationError, 'Name the customer'):
            make_collective_sales_invoice()
        only = make_collective_sales_invoice(delivery_notes=[n2.name])
        self.assertEqual({row['delivery_note'] for row in only.get('items')}, {n2.name})
        t_line = [row for row in n2.get('items') if row['item_code'] == 'T'][0]['name']
        picked = make_collective_sales_invoice('C', lines=[t_line])
        self.assertEqual([(row['item_code'], row['qty']) for row in picked.get('items')], [('T', 5)])
        with self.assertRaisesRegex(ValidationError, 'not open to bill'):
            make_collective_sales_invoice('C', lines=['missing'])
        # A partly billed delivery offers only the rest.
        from lambda_erp.stock.delivery_note import make_sales_invoice_from_delivery_note
        single = make_sales_invoice_from_delivery_note(n2.name)
        single.set('items', [row for row in single.get('items') if row['item_code'] == 'T'])
        single.get('items')[0]['qty'] = 3
        single.save()
        single.submit()
        rows = _unbilled_deliveries(self.db, customer='C')['rows']
        self.assertEqual(sorted((row['delivery_note'], row['item_code'], row['unbilled_qty']) for row in rows),
                         sorted([(n1.name, 'S', 2), (n2.name, 'S', 2), (n2.name, 'T', 2)]))

    def test_closing_and_collective_invoice_together(self):
        """The pilot case: a hospital order delivered in two steps, the rest of
        one line cancelled, both deliveries on one invoice at month end."""
        order = self.order(lines=(('S', 10, 20), ('T', 6, 8)))
        n1 = self.deliver(order, {'S': 6, 'T': 6}, '2026-11-05')
        n2 = self.deliver(order, {'S': 2}, '2026-11-19')
        self.close(order, [self.lines(order)['S']['name']], reason='Hospital cancelled the remaining 2')
        self.assertEqual(self.head(order)['status'], 'To Bill')
        draft = prepare_collective_invoice({'customer': 'C', 'to_date': '2026-11-30'})
        self.assertEqual(sorted((row['delivery_note'], row['item_code'], row['qty']) for row in draft['items']),
                         sorted([(n1.name, 'S', 6), (n1.name, 'T', 6), (n2.name, 'S', 2)]))
        services.submit_document('sales-invoice', draft['name'])
        got = self.lines(order)
        self.assertEqual((got['S']['qty'], got['S']['delivered_qty'], got['S']['billed_qty'], got['S']['closed_qty']),
                         (10, 8, 8, 2))
        self.assertEqual(self.head(order)['status'], 'Completed')


if __name__ == '__main__':
    unittest.main()
