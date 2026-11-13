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
