"""Units of measure: lines entered in a pack are stored per stock unit.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp import units
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_delivery_note
from lambda_erp.stock.stock_ledger import stock_ledger_agreement


class Units(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        db.insert('Item', {'name': 'GLOVE', 'item_name': 'Glove', 'is_stock_item': 1, 'stock_uom': 'Stk',
                           'standard_rate': 3})
        db.insert('Item', {'name': 'SWAB', 'item_name': 'Swab', 'is_stock_item': 1, 'stock_uom': 'Stk',
                           'standard_rate': 4})
        db.insert('Item', {'name': 'PLAIN', 'item_name': 'Plain', 'is_stock_item': 1, 'stock_uom': 'Stk',
                           'standard_rate': 1})
        db.commit()
        self.carton = services.create_document('item-unit', {
            'item_code': 'GLOVE', 'uom': 'Karton', 'conversion_factor': 100})
        services.create_document('item-unit', {'item_code': 'SWAB', 'uom': 'Pack', 'conversion_factor': 3})

    def order(self, lines, slug='sales-order', **fields):
        date = 'posting_date' if slug in ('sales-invoice', 'delivery-note', 'pos-invoice') else 'transaction_date'
        if slug == 'sales-order':
            fields = {'delivery_date': '2026-12-30', **fields}
        return services.create_document(slug, {
            'company': 'Test Co', 'customer': 'C', date: '2026-11-13',
            'items': [{'warehouse': 'A', **line} for line in lines], **fields})

    def receive(self, item, qty, rate, **line):
        entry = services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': item, 'qty': qty, 'basic_rate': rate, 't_warehouse': 'A', **line}]})
        services.submit_document('stock-entry', entry['name'])
        return services.load_document('stock-entry', entry['name'])

    def bin(self, item, field='actual_qty'):
        return self.db.get_value('Bin', {'item_code': item, 'warehouse': 'A'}, field)

    def test_unit_definition(self):
        self.assertEqual((self.carton['uom'], self.carton['conversion_factor'], self.carton['enabled']),
                         ('Karton', 100, 1))
        self.assertEqual(units.units_of('GLOVE'), {'Karton': 100})
        for data, message in (
                ({'item_code': 'GLOVE', 'uom': 'Karton', 'conversion_factor': 50}, 'already has the unit'),
                ({'item_code': 'GLOVE', 'uom': 'Stk', 'conversion_factor': 1}, 'is the stock unit'),
                ({'item_code': 'GLOVE', 'uom': 'Box', 'conversion_factor': 0}, 'must be positive'),
                ({'item_code': 'GLOVE', 'uom': 'Box', 'conversion_factor': -5}, 'must be positive'),
                ({'item_code': 'GLOVE', 'uom': 'Box', 'conversion_factor': 'many'}, 'must be a number'),
                ({'item_code': 'NOPE', 'uom': 'Box', 'conversion_factor': 10}, 'NOPE')):
            with self.assertRaisesRegex(ValidationError, message):
                services.create_document('item-unit', data)

    def test_a_line_in_a_further_unit_is_stored_per_stock_unit(self):
        order = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}])
        line = order['items'][0]
        self.assertEqual((line['qty'], line['uom'], line['rate'], line['amount']), (200, 'Stk', 2.9, 580))
        self.assertEqual((line['order_qty'], line['order_uom'], line['conversion_factor']), (2, 'Karton', 100))
        self.assertEqual(order['net_total'], 580)
        # Saving the stored line again converts nothing.
        again = services.update_document('sales-order', order['name'], {'items': order['items']})
        self.assertEqual((again['items'][0]['qty'], again['items'][0]['rate'], again['net_total']), (200, 2.9, 580))
        # Entering it in the unit again does.
        more = services.update_document('sales-order', order['name'], {'items': [
            {'item_code': 'GLOVE', 'qty': 3, 'uom': 'Karton', 'rate': 290, 'warehouse': 'A'}]})
        self.assertEqual((more['items'][0]['qty'], more['items'][0]['order_qty'], more['net_total']), (300, 3, 870))
        # A quantity in stock units keeps the snapshot and says how many units that is.
        row = dict(more['items'][0], qty=150)
        part = services.update_document('sales-order', order['name'], {'items': [row]})
        self.assertEqual((part['items'][0]['qty'], part['items'][0]['order_qty'], part['net_total']), (150, 1.5, 435))
        # Changing the unit later does not touch the stored document.
        services.update_document('item-unit', self.carton['name'], {'conversion_factor': 50})
        self.assertEqual(services.load_document('sales-order', order['name'])['items'][0]['conversion_factor'], 100)
        self.assertEqual(self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}])['items'][0]['qty'], 100)

    def test_unknown_units(self):
        with self.assertRaisesRegex(ValidationError, 'VE is not a unit of item GLOVE.*Karton'):
            self.order([{'item_code': 'GLOVE', 'qty': 1, 'uom': 'VE'}])
        # An item without any Item Unit keeps free text in uom, as before.
        plain = self.order([{'item_code': 'PLAIN', 'qty': 5, 'uom': 'Each'}])['items'][0]
        self.assertEqual((plain['qty'], plain['uom'], plain.get('order_uom')), (5, 'Each', None))
        # A disabled unit is no unit.
        services.update_document('item-unit', self.carton['name'], {'enabled': 0})
        self.assertEqual(self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton'}])['items'][0]['qty'], 2)

    def test_prices_per_unit_and_per_stock_unit(self):
        selling = services.create_document('price-list', {
            'price_list_name': 'Selling', 'currency': 'USD', 'selling': 1, 'buying': 0})['name']
        self.db.set_value('Company', 'Test Co', 'default_price_list', selling)
        self.db.commit()
        services.create_document('item-price', {'item_code': 'GLOVE', 'price_list': selling, 'rate': 3.10})
        # Without a price for the carton the price per piece applies.
        line = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton'}])['items'][0]
        self.assertEqual((line['qty'], line['rate'], line['amount'], line['price_source']), (200, 3.1, 620, 'Item Price'))
        services.create_document('item-price', {'item_code': 'GLOVE', 'price_list': selling, 'rate': 290, 'uom': 'Karton'})
        line = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton'}])['items'][0]
        self.assertEqual((line['qty'], line['rate'], line['amount'], line['price_source']), (200, 2.9, 580, 'Item Price'))
        # A line in pieces does not take the carton price.
        line = self.order([{'item_code': 'GLOVE', 'qty': 200}])['items'][0]
        self.assertEqual((line['rate'], line['amount']), (3.1, 620))

    def test_amounts_stay_exact_for_awkward_factors(self):
        # 10.00 for a pack of 3 is 3.3333... a piece; the amount is still 70.00.
        line = self.order([{'item_code': 'SWAB', 'qty': 7, 'uom': 'Pack', 'rate': 10}])['items'][0]
        self.assertEqual((line['qty'], line['amount'], line['order_qty']), (21, 70, 7))
        # 12.99 a carton with 5 % off: 0.123405 a piece, not 0.12.
        line = self.order([{'item_code': 'GLOVE', 'qty': 1, 'uom': 'Karton', 'price_list_rate': 12.99,
                            'discount_percentage': 5}])['items'][0]
        self.assertEqual((line['qty'], line['amount']), (100, 12.34))

    def test_stock_valuation_delivery_and_backlog_in_stock_units(self):
        # Bought by the carton: cost is per piece.
        receipt = self.receive('GLOVE', 2, 250, uom='Karton')
        row = receipt['items'][0]
        self.assertEqual((row['qty'], row['uom'], row['order_qty'], row['basic_rate']), (200, 'Stk', 2, 2.5))
        self.assertEqual((self.bin('GLOVE'), self.bin('GLOVE', 'valuation_rate'), self.bin('GLOVE', 'stock_value')),
                         (200, 2.5, 500))
        order = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}])
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.get('items')[0]['qty'] = 100
        note.save()
        line = note.get('items')[0]
        self.assertEqual((line['qty'], line['order_uom'], line['order_qty'], line['amount']), (100, 'Karton', 1, 290))
        note.submit()
        self.assertEqual(self.bin('GLOVE'), 100)
        self.assertEqual(self.db.get_value('Sales Order', order['name'], 'per_delivered'), 50)
        self.assertEqual(self.db.get_value('Sales Order Item', {'parent': order['name']}, 'delivered_qty'), 100)
        self.assertEqual(stock_ledger_agreement(), [])
        # The invoice made from the order says what it was ordered as, too.
        invoice = services.convert_document('sales-order', order['name'], 'Sales Invoice')
        line = invoice['items'][0]
        self.assertEqual((line['qty'], line['order_uom'], line['order_qty'], line['amount']), (200, 'Karton', 2, 580))

    def test_a_hidden_purchase_price_survives_re_entering_the_line_in_a_unit(self):
        from lambda_erp import permissions
        order = services.create_document('purchase-order', {
            'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-13', 'schedule_date': '2026-12-01',
            'items': [{'item_code': 'GLOVE', 'qty': 200, 'rate': 2.4, 'warehouse': 'A'}]})
        restricted = {'restrictions': {'profile': 'Field', 'hidden': permissions.expand_classes(['purchase_price']),
                                       'permissions': {'default': None, 'doctypes': {}}}}
        with permissions.acting_as(restricted):
            seen = services.load_document('purchase-order', order['name'])['items'][0]
            self.assertNotIn('rate', {key for key, value in seen.items() if value not in (None, '')})
            services.update_document('purchase-order', order['name'], {'items': [
                {'name': seen['name'], 'item_code': 'GLOVE', 'qty': 3, 'uom': 'Karton', 'warehouse': 'A'}]})
        line = services.load_document('purchase-order', order['name'])['items'][0]
        self.assertEqual((line['qty'], line['order_qty'], round(line['rate'], 6), line['amount']), (300, 3, 2.4, 720))

    def test_a_unit_handled_whole_only(self):
        services.update_document('item-unit', self.carton['name'], {'must_be_whole_number': 1})
        with self.assertRaisesRegex(ValidationError, 'whole Karton only, not 1.5'):
            self.order([{'item_code': 'GLOVE', 'qty': 1.5, 'uom': 'Karton', 'rate': 290}])
        # The same quantity in stock units is fine, and so is a partial quantity later.
        order = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}])
        row = dict(order['items'][0], qty=150)
        part = services.update_document('sales-order', order['name'], {'items': [row]})
        self.assertEqual((part['items'][0]['qty'], part['items'][0]['order_qty']), (150, 1.5))

    def test_a_price_per_several_units_is_not_a_pack(self):
        selling = services.create_document('price-list', {
            'price_list_name': 'Selling', 'currency': 'USD', 'selling': 1, 'buying': 0})['name']
        self.db.set_value('Company', 'Test Co', 'default_price_list', selling)
        self.db.commit()
        price = services.create_document('item-price', {
            'item_code': 'PLAIN', 'price_list': selling, 'rate': 12.5, 'per_qty': 100})
        self.assertEqual(price['per_qty'], 100)
        line = self.order([{'item_code': 'PLAIN', 'qty': 40}])['items'][0]
        self.assertEqual((line['qty'], line['rate'], line['amount'], line.get('order_uom')), (40, 0.125, 5, None))
        # A discount on a price finer than a cent is not rounded to the cent.
        line = self.order([{'item_code': 'PLAIN', 'qty': 1000, 'price_list_rate': 0.125,
                            'discount_percentage': 10}])['items'][0]
        self.assertEqual((line['rate'], line['amount']), (0.1125, 112.5))
        with self.assertRaisesRegex(ValidationError, 'Per Qty must be positive'):
            services.create_document('item-price', {'item_code': 'PLAIN', 'price_list': selling, 'rate': 1, 'per_qty': 0})

    def test_return_and_cancel_in_units(self):
        self.receive('GLOVE', 3, 250, uom='Karton')
        order = self.order([{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}])
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.save()
        note.submit()
        self.assertEqual(self.bin('GLOVE'), 100)
        from lambda_erp.stock.delivery_note import make_delivery_return
        back = make_delivery_return(note.name)
        back.get('items')[0].update(qty=-1, uom='Karton', rate=290)
        back.save()
        line = back.get('items')[0]
        self.assertEqual((line['qty'], line['order_qty'], line['rate'], line['amount']), (-100, -1, 2.9, -290))
        back.submit()
        self.assertEqual(self.bin('GLOVE'), 200)
        self.assertEqual(self.db.get_value('Sales Order Item', {'parent': order['name']}, 'returned_qty'), 100)
        # More than is left to return cannot come back: 2 Karton are 200 pieces, 100 are left.
        again = make_delivery_return(note.name)
        again.get('items')[0].update(qty=-2, uom='Karton', rate=290)
        with self.assertRaises(ValidationError):
            again.save()
        back = {'name': back.name}
        services.cancel_document('delivery-note', back['name'])
        self.assertEqual(self.bin('GLOVE'), 100)
        services.cancel_document('delivery-note', note.name)
        self.assertEqual((self.bin('GLOVE'), self.bin('GLOVE', 'valuation_rate')), (300, 2.5))
        self.assertEqual(self.db.get_value('Sales Order', order['name'], 'per_delivered'), 0)
        self.assertEqual(stock_ledger_agreement(), [])

    def test_two_orders_compete_for_the_last_carton(self):
        from lambda_erp.stock import reservation
        self.receive('GLOVE', 1, 250, uom='Karton')
        first = self.order([{'item_code': 'GLOVE', 'qty': 1, 'uom': 'Karton', 'rate': 290}])
        second = self.order([{'item_code': 'GLOVE', 'qty': 1, 'uom': 'Karton', 'rate': 290}])
        for order in (first, second):
            services.submit_document('sales-order', order['name'])
        got = reservation.reserve_sales_order(first['name'])
        self.assertEqual((got['lines'][0]['reserved_qty'], got['fully_reserved']), (100, True))
        got = reservation.reserve_sales_order(second['name'])
        self.assertEqual((got['lines'][0]['reserved_qty'], got['lines'][0]['short_qty'], got['fully_reserved']),
                         (0, 100, False))

    def test_consignment_withdrawal_reported_by_the_carton(self):
        from api.routers.masters import update_master_record
        from lambda_erp.exceptions import ConflictError
        from lambda_erp.stock.consignment import record_withdrawal
        db = self.db
        db.insert('Customer', {'name': 'H', 'customer_name': 'Hospital', 'default_currency': 'USD'})
        db.insert('Warehouse', {'name': 'CONSI-H', 'warehouse_name': 'Consignment Hospital', 'company': 'Test Co'})
        db.commit()
        update_master_record('warehouse', 'CONSI-H', {'consignment_customer': 'H'})
        entry = services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': 'GLOVE', 'qty': 3, 'uom': 'Karton', 'basic_rate': 250, 't_warehouse': 'CONSI-H'}]})
        services.submit_document('stock-entry', entry['name'])
        report = [{'item_code': 'GLOVE', 'qty': 1, 'uom': 'Karton', 'rate': 290}]
        got = record_withdrawal('CONSI-H', report, external_source='portal', external_reference='R-1')
        self.assertFalse(got['replayed'])
        row = services.load_document('consignment-withdrawal', got['name'])['items'][0]
        self.assertEqual((row['qty'], row['order_qty'], row['order_uom'], row['rate']), (100, 1, 'Karton', 2.9))
        line = services.load_document('sales-invoice', got['sales_invoice'])['items'][0]
        self.assertEqual((line['qty'], line['amount'], line['order_uom'], line['order_qty']), (100, 290, 'Karton', 1))
        self.assertEqual(db.get_value('Bin', {'item_code': 'GLOVE', 'warehouse': 'CONSI-H'}, 'actual_qty'), 200)
        # The same report again, by the carton or in pieces, is the same report.
        self.assertTrue(record_withdrawal('CONSI-H', report, external_source='portal', external_reference='R-1')['replayed'])
        self.assertTrue(record_withdrawal('CONSI-H', [{'item_code': 'GLOVE', 'qty': 100, 'rate': 2.9}],
                                          external_source='portal', external_reference='R-1')['replayed'])
        with self.assertRaises(ConflictError):
            record_withdrawal('CONSI-H', [{'item_code': 'GLOVE', 'qty': 2, 'uom': 'Karton', 'rate': 290}],
                              external_source='portal', external_reference='R-1')
        self.assertEqual(db.get_value('Bin', {'item_code': 'GLOVE', 'warehouse': 'CONSI-H'}, 'actual_qty'), 200)
        self.assertEqual(stock_ledger_agreement(), [])

    def test_stock_count_by_the_carton(self):
        self.receive('GLOVE', 2, 250, uom='Karton')
        count = services.create_document('stock-count', {
            'warehouse': 'A', 'reason': 'Year-end count',
            'items': [{'item_code': 'GLOVE', 'counted_qty': 3, 'uom': 'Karton'}]})
        row = count['items'][0]
        self.assertEqual((row['system_qty'], row['counted_qty'], row['difference_qty'], row['order_qty'], row['order_uom']),
                         (200, 300, 100, 3, 'Karton'))
        services.submit_document('stock-count', count['name'])
        self.assertEqual(self.bin('GLOVE'), 300)
        self.assertEqual(stock_ledger_agreement(), [])

    def test_purchase_by_the_pack(self):
        receipt = services.create_document('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-13',
            'items': [{'item_code': 'GLOVE', 'qty': 3, 'uom': 'Karton', 'rate': 240, 'warehouse': 'A'}]})
        line = receipt['items'][0]
        self.assertEqual((line['qty'], line['rate'], line['amount'], line['order_qty']), (300, 2.4, 720, 3))
        services.submit_document('purchase-receipt', receipt['name'])
        self.assertEqual((self.bin('GLOVE'), self.bin('GLOVE', 'valuation_rate')), (300, 2.4))
        self.assertEqual(stock_ledger_agreement(), [])


if __name__ == '__main__':
    unittest.main()
