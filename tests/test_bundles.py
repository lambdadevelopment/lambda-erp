"""Item bundles: one item code that expands into the lines of its items.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp import bundles
from lambda_erp.exceptions import ValidationError
from lambda_erp.stock.delivery_note import make_delivery_note
from lambda_erp.stock.stock_ledger import stock_ledger_agreement


class Bundles(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Item', {'name': 'IMPLANT', 'item_name': 'Implant', 'is_stock_item': 1, 'standard_rate': 300})
        db.insert('Item', {'name': 'SCREW', 'item_name': 'Screw', 'is_stock_item': 1, 'standard_rate': 12,
                           'has_batch_no': 1})
        db.insert('Item', {'name': 'HOOK-L', 'item_name': 'Hook, left hand', 'is_stock_item': 1, 'standard_rate': 40})
        for code, name in (('SET', 'Knee set'), ('SET-XL', 'Knee set with spare screws'), ('LOOP', 'Loop')):
            db.insert('Item', {'name': code, 'item_name': name, 'is_stock_item': 0, 'standard_rate': 0})
        db.commit()
        self.bundle = services.create_document('item-bundle', {'item_code': 'SET', 'items': [
            {'item_code': 'IMPLANT', 'qty': 1}, {'item_code': 'SCREW', 'qty': 4}]})

    def order(self, lines, slug='sales-order', **fields):
        date = 'posting_date' if slug in ('sales-invoice', 'delivery-note', 'pos-invoice') else 'transaction_date'
        if slug == 'sales-order':
            fields = {'delivery_date': '2026-12-30', **fields}
        return services.create_document(slug, {
            'company': 'Test Co', 'customer': 'C', date: '2026-11-12',
            'items': [{'warehouse': 'A', **line} for line in lines], **fields})

    def lines(self, doc):
        return [(row['item_code'], row['qty'], row['rate'], row.get('bundle_item'), row.get('bundle_qty'))
                for row in doc['items']]

    def test_bundle_definition(self):
        self.assertEqual((self.bundle['bundle_name'], self.bundle['items'][1]['item_name']), ('Knee set', 'Screw'))
        for data, message in (
                ({'item_code': 'IMPLANT', 'items': [{'item_code': 'SCREW', 'qty': 1}]}, 'is a stock item'),
                ({'item_code': 'SET', 'items': [{'item_code': 'SCREW', 'qty': 1}]}, 'already has bundle'),
                ({'item_code': 'LOOP', 'items': [{'item_code': 'LOOP', 'qty': 1}]}, 'cannot contain itself'),
                ({'item_code': 'LOOP', 'items': [{'item_code': 'SCREW', 'qty': 0}]}, 'must be positive'),
                ({'item_code': 'LOOP', 'items': [{'item_code': 'SCREW', 'qty': 1}, {'item_code': 'SCREW', 'qty': 2}]},
                 'listed twice'),
                ({'item_code': 'LOOP', 'items': [{'item_code': 'NOPE', 'qty': 1}]}, 'does not exist|must reference')):
            with self.assertRaisesRegex(ValidationError, message):
                services.create_document('item-bundle', data)
        # A bundle inside a bundle, but never a circle.
        services.create_document('item-bundle', {'item_code': 'SET-XL', 'items': [
            {'item_code': 'SET', 'qty': 1}, {'item_code': 'SCREW', 'qty': 2}]})
        self.assertEqual(bundles.components('SET-XL', 2), [('IMPLANT', 2), ('SCREW', 8), ('SCREW', 4)])
        with self.assertRaisesRegex(ValidationError, 'contains itself|contains SET'):
            services.update_document('item-bundle', self.bundle['name'], {'items': [
                {'item_code': 'IMPLANT', 'qty': 1}, {'item_code': 'SET-XL', 'qty': 1}]})

    def test_a_bundle_line_becomes_the_lines_of_its_items(self):
        order = self.order([{'item_code': 'SET', 'qty': 2}, {'item_code': 'HOOK-L', 'qty': 1}])
        self.assertEqual(self.lines(order), [('IMPLANT', 2, 300, 'SET', 2), ('SCREW', 8, 12, 'SET', 2),
                                             ('HOOK-L', 1, 40, None, None)])
        self.assertEqual(order['grand_total'], 2 * 300 + 8 * 12 + 40)
        self.assertEqual({row['warehouse'] for row in order['items']}, {'A'})
        # From here they are ordinary lines: changed, removed, saved again without expanding twice.
        rows = order['items']
        rows[1]['qty'] = 6
        again = services.update_document('sales-order', order['name'], {'items': rows[:2]})
        self.assertEqual(self.lines(again), [('IMPLANT', 2, 300, 'SET', 2), ('SCREW', 6, 12, 'SET', 2)])
        # Adding the bundle again on a saved draft expands only the new line.
        more = services.update_document('sales-order', order['name'], {'items': [*again['items'], {
            'item_code': 'SET', 'qty': 1, 'warehouse': 'A'}]})
        self.assertEqual([row[:2] for row in self.lines(more)],
                         [('IMPLANT', 2), ('SCREW', 6), ('IMPLANT', 1), ('SCREW', 4)])
        # A bundle has no price or batch of its own.
        for extra, message in (({'rate': 500}, 'no rate of its own'), ({'qty': 0}, 'needs a quantity|Qty')):
            with self.assertRaisesRegex(ValidationError, message):
                self.order([{'item_code': 'SET', 'qty': 1, **extra}])
        with self.assertRaisesRegex(ValidationError, 'no batch_no of its own'):
            self.order([{'item_code': 'SET', 'qty': 1, 'batch_no': 'B1'}], slug='delivery-note')
        # Every sales document expands; a purchase order does not know bundles.
        for slug in ('quotation', 'sales-invoice', 'delivery-note'):
            doc = self.order([{'item_code': 'SET', 'qty': 1}], slug=slug)
            self.assertEqual([row[:2] for row in self.lines(doc)], [('IMPLANT', 1), ('SCREW', 4)], slug)

    def test_components_are_priced_delivered_and_stocked_on_their_own(self):
        selling = services.create_document('price-list', {
            'price_list_name': 'Selling', 'currency': 'USD', 'selling': 1, 'buying': 0})['name']
        self.db.set_value('Company', 'Test Co', 'default_price_list', selling)
        self.db.commit()
        services.create_document('item-price', {'item_code': 'IMPLANT', 'price_list': selling, 'rate': 280})
        services.create_document('item-price', {'item_code': 'SCREW', 'price_list': selling, 'rate': 10, 'customer': 'C'})
        for item, extra in (('IMPLANT', {}), ('SCREW', {'batch_no': 'LOT-1'})):
            services.submit_document('stock-entry', services.create_document('stock-entry', {
                'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                'items': [{'item_code': item, 'qty': 20, 'basic_rate': 5, 't_warehouse': 'A', **extra}]})['name'])
        order = self.order([{'item_code': 'SET', 'qty': 2}])
        self.assertEqual([(row['rate'], row['price_source']) for row in order['items']],
                         [(280, 'Item Price'), (10, 'Item Price')])
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.save()
        self.assertEqual([(row['item_code'], row['qty'], row.get('batch_no')) for row in note.get('items')],
                         [('IMPLANT', 2, None), ('SCREW', 8, 'LOT-1')])
        note.submit()
        self.assertEqual(self.db.get_value('Bin', {'item_code': 'SCREW', 'warehouse': 'A'}, 'actual_qty'), 12)
        self.assertEqual(self.db.get_value('Sales Order', order['name'], 'per_delivered'), 100)
        self.assertEqual(stock_ledger_agreement(), [])
        # Changing the bundle changes new lines, not documents that exist.
        services.update_document('item-bundle', self.bundle['name'], {'items': [{'item_code': 'IMPLANT', 'qty': 1}]})
        self.assertEqual(len(services.load_document('sales-order', order['name'])['items']), 2)
        self.assertEqual(len(self.order([{'item_code': 'SET', 'qty': 1}])['items']), 1)
        # A disabled bundle is an ordinary (unstocked) item again.
        services.update_document('item-bundle', self.bundle['name'], {'disabled': 1})
        self.assertEqual([row['item_code'] for row in self.order([{'item_code': 'SET', 'qty': 1, 'rate': 5}])['items']],
                         ['SET'])


if __name__ == '__main__':
    unittest.main()
