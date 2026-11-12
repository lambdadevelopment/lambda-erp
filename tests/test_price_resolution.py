"""Price resolution: levels, dimensions, dates, ties, provenance, explanation.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.pricing_actions import explain
from lambda_erp import permissions
from lambda_erp.controllers import price_resolution
from lambda_erp.exceptions import PermissionDenied, ValidationError
from lambda_erp.selling.quotation import make_sales_order


class PriceResolution(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD', 'supplier_group': 'Importers'})
        db.insert('Customer', {'name': 'H1', 'customer_name': 'Hospital One', 'default_currency': 'USD'})
        db.insert('Customer', {'name': 'H2', 'customer_name': 'Hospital Two', 'default_currency': 'USD'})
        # A family of three items; F3 is not sold to H1 at the family price.
        for code in ('F1', 'F2', 'F3'):
            db.insert('Item', {'name': code, 'item_name': code, 'is_stock_item': 1, 'standard_rate': 0})
        db.insert('Item', {'name': 'X', 'item_name': 'Unpriced', 'is_stock_item': 1, 'standard_rate': 0})
        self.selling = services.create_document('price-list', {
            'price_list_name': 'Selling', 'currency': 'USD', 'selling': 1, 'buying': 0})['name']
        db.set_value('Company', 'Test Co', 'default_price_list', self.selling)
        db.commit()
        # Assignments live with the deployment; here in two dicts.
        self.family = {'F1': ['Stockings'], 'F2': ['Stockings'], 'F3': ['Stockings']}
        self.buying_family = {'F1': ['Vendor range A']}
        self.group = {'H1': ['Buying group North'], 'H2': ['Buying group North']}
        price_resolution.register_price_dimension(
            'Price Family', 'item',
            lambda ctx: (self.family if ctx['side'] == 'selling' else self.buying_family).get(ctx['item_code'], []))
        price_resolution.register_price_dimension(
            'Purchasing Group', 'party', lambda ctx: self.group.get(ctx['customer'], []), party_type='Customer')
        self.addCleanup(price_resolution.unregister_price_dimension, 'Price Family')
        self.addCleanup(price_resolution.unregister_price_dimension, 'Purchasing Group')

    # -- helpers -------------------------------------------------------------
    def list_price(self, item, rate, **extra):
        return services.create_document('item-price', {'item_code': item, 'price_list': self.selling, 'rate': rate, **extra})

    def rule(self, title, rate, **fields):
        return services.create_document('pricing-rule', {
            'title': title, 'selling': 1, 'rate_or_discount': 'Rate', 'rate': rate, 'company': 'Test Co', **fields})

    def order(self, customer, item, date='2026-11-12', qty=1, slug='sales-order', **line):
        date_field = 'posting_date' if slug.endswith('invoice') else 'transaction_date'
        return services.create_document(slug, {
            'company': 'Test Co', 'customer': customer, date_field: date,
            'items': [{'item_code': item, 'qty': qty, 'warehouse': 'A', **line}]})

    def price(self, customer, item, **kw):
        row = self.order(customer, item, **kw)['items'][0]
        return (row['rate'], row['price_source'], row.get('pricing_rule'))

    # -- acceptance: general, family, customer, group, individual exception ---
    def test_price_levels(self):
        for code in ('F1', 'F2', 'F3'):
            self.list_price(code, 100)
        family = self.rule('Family list price', 90, apply_on='Price Family', apply_on_value='Stockings')['name']
        group = self.rule('Group price', 80, apply_on='Price Family', apply_on_value='Stockings',
                          applicable_for='Purchasing Group', applicable_for_value='Buying group North')['name']
        customer = self.rule('Hospital One family price', 70, apply_on='Price Family', apply_on_value='Stockings',
                             applicable_for='Customer', customer='H1')['name']
        exception = self.rule('Hospital One, F3 only', 95, apply_on='Item Code', item_code='F3',
                              applicable_for='Customer', customer='H1')['name']
        self.assertEqual(self.price('C', 'F1'), (90, 'Item Price', family))        # anyone: family price
        self.assertEqual(self.price('H2', 'F1'), (80, 'Item Price', group))        # member of the group
        self.assertEqual(self.price('H1', 'F1'), (70, 'Item Price', customer))     # own price beats the group's
        self.assertEqual(self.price('H1', 'F3'), (95, 'Item Price', exception))    # item beats family for the customer
        self.assertEqual(self.price('H2', 'F3'), (80, 'Item Price', group))
        # The base stays the list price, so the document shows what it is a deduction from.
        self.assertEqual(self.order('H1', 'F1')['items'][0]['price_list_rate'], 100)
        # Priority is the deliberate override of all of this.
        campaign = self.rule('Campaign', 60, apply_on='Price Family', apply_on_value='Stockings', priority=5)['name']
        self.assertEqual(self.price('H1', 'F3'), (60, 'Item Price', campaign))

    def test_buying_and_selling_assignments_differ(self):
        self.rule('Vendor range', 40, apply_on='Price Family', apply_on_value='Vendor range A', selling=0, buying=1,
                  applicable_for='Supplier Group', supplier_group='Importers')
        self.rule('Family list price', 90, apply_on='Price Family', apply_on_value='Stockings')

        def buy(item):
            row = services.create_document('purchase-order', {
                'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-12',
                'items': [{'item_code': item, 'qty': 1, 'rate': 50, 'warehouse': 'A'}]})['items'][0]
            return row['rate'], row['price_source']

        self.assertEqual(buy('F1'), (40, 'Entered'))   # in the vendor range, supplier in the group
        self.assertEqual(buy('F2'), (50, 'Entered'))   # same selling family, but not in the vendor range
        self.assertEqual(self.price('C', 'F2', rate=120)[0], 90)

    def test_rule_keys_are_validated(self):
        with self.assertRaisesRegex(ValidationError, 'Apply On must be one of .*Price Family'):
            self.rule('Bad', 1, apply_on='Colour', apply_on_value='red')
        with self.assertRaisesRegex(ValidationError, 'apply_on_value is required'):
            self.rule('Bad', 1, apply_on='Price Family')
        with self.assertRaisesRegex(ValidationError, 'Applicable For Purchasing Group requires Selling'):
            self.rule('Bad', 1, apply_on='Item Code', item_code='F1', selling=0, buying=1,
                      applicable_for='Purchasing Group', applicable_for_value='Buying group North')
        with self.assertRaises(ValueError):
            price_resolution.register_price_dimension('Customer', 'party', lambda ctx: [], party_type='Customer')

    # -- acceptance: a price valid from tomorrow -----------------------------
    def test_future_price_applies_on_its_date_not_before(self):
        self.list_price('F1', 100)
        self.list_price('F1', 110, valid_from='2026-12-01')
        self.rule('New agreement', 85, apply_on='Item Code', item_code='F1', applicable_for='Customer', customer='H1',
                  valid_from='2026-12-01')
        self.assertEqual(self.price('H1', 'F1', date='2026-11-30'), (100, 'Item Price', None))
        early = self.order('H1', 'F1', date='2026-11-30')
        self.assertEqual(self.price('H1', 'F1', date='2026-12-01')[0], 85)
        self.assertEqual(self.order('H1', 'F1', date='2026-12-01')['items'][0]['price_list_rate'], 110)
        # Saving the earlier document again later does not move it to the new price.
        again = services.update_document('sales-order', early['name'], {'remarks': 'checked'})
        self.assertEqual(again['items'][0]['rate'], 100)
        # An explicit pricing date decides, whatever the document date.
        priced = services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'H1', 'transaction_date': '2026-11-30', 'pricing_date': '2026-12-15',
            'items': [{'item_code': 'F1', 'qty': 1, 'warehouse': 'A'}]})
        self.assertEqual(priced['items'][0]['rate'], 85)

    # -- acceptance: overlapping rules of equal rank, missing sources ----------
    def test_equal_rank_is_decided_the_same_way_every_time(self):
        self.list_price('F1', 100)
        wide = self.rule('Standing', 90, apply_on='Item Code', item_code='F1')['name']
        narrow = self.rule('November', 88, apply_on='Item Code', item_code='F1',
                           valid_from='2026-11-01', valid_upto='2026-11-30')['name']
        newer = self.rule('Since the tenth', 87, apply_on='Item Code', item_code='F1',
                          valid_from='2026-11-10', valid_upto='2026-12-31')['name']
        for _ in range(3):
            self.assertEqual(self.price('C', 'F1')[2], narrow)  # the narrower window wins
        got = explain({'item_code': 'F1', 'customer': 'C', 'date': '2026-11-12'})
        self.assertEqual(got['pricing_rule']['name'], narrow)
        self.assertEqual(sorted(got['tied_with']), sorted([wide, newer]))
        self.assertEqual(len(got['rules_considered']), 3)
        # Strict mode refuses instead of choosing.
        self.db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', ['pricing_strict_conflicts', '1'])
        self.db.commit()
        with self.assertRaisesRegex(ValidationError, 'same priority and the same specificity'):
            self.order('C', 'F1')
        services.update_document('pricing-rule', narrow, {'priority': 1})
        services.update_document('pricing-rule', newer, {'priority': 2})
        self.assertEqual(self.price('C', 'F1')[2], newer)
        # Same window width: the one that starts later.
        self.db.sql('DELETE FROM "Settings" WHERE key = ?', ['pricing_strict_conflicts'])
        self.db.commit()
        services.update_document('pricing-rule', newer, {'priority': 1, 'valid_from': '2026-11-05', 'valid_upto': '2026-12-04'})
        self.assertEqual(self.price('C', 'F1')[2], newer)

    def test_item_price_ties(self):
        self.list_price('F1', 100)
        self.list_price('F1', 95, valid_from='2026-11-01')
        self.list_price('F1', 92, valid_from='2026-11-01', valid_upto='2026-11-30')
        self.list_price('F1', 80, min_qty=10)
        self.list_price('F1', 75, customer='H1')
        self.assertEqual(self.price('C', 'F1')[0], 92)            # narrowest validity
        self.assertEqual(self.price('C', 'F1', date='2026-12-05')[0], 95)   # newer start beats open-ended
        self.assertEqual(self.price('C', 'F1', qty=10)[0], 80)    # volume break
        self.assertEqual(self.price('H1', 'F1', qty=10)[0], 75)   # the customer's own row first

    def test_missing_price_source_is_said_out_loud(self):
        doc = self.order('C', 'X')
        row = doc['items'][0]
        self.assertEqual((row['rate'], row['price_source'], row['price_reference']), (0, 'None', None))
        self.assertIn('price_missing', [w['code'] for w in doc['_validation']['warnings']])
        got = explain({'item_code': 'X', 'customer': 'C'})
        self.assertEqual((got['rate'], got['price_source']), (0, 'None'))
        self.assertIn('No price source', got['explanation'])
        self.db.set_value('Item', 'X', 'standard_rate', 12)
        self.assertEqual(self.price('C', 'X'), (12, 'Standard Rate', None))
        self.assertEqual(self.price('C', 'X', rate=15), (15, 'Entered', None))

    # -- acceptance: the same answer on every path -------------------------------
    def test_same_decision_in_documents_and_explanation(self):
        self.list_price('F1', 100)
        own = self.list_price('F1', 97, customer='H1')['name']
        rule = self.rule('Group price', 80, apply_on='Price Family', apply_on_value='Stockings',
                         applicable_for='Purchasing Group', applicable_for_value='Buying group North')['name']
        for slug, doctype in (('quotation', 'Quotation'), ('sales-order', 'Sales Order'),
                              ('sales-invoice', 'Sales Invoice'), ('pos-invoice', 'POS Invoice')):
            row = self.order('H1', 'F1', slug=slug)['items'][0]
            got = explain({'doctype': slug, 'item_code': 'F1', 'customer': 'H1', 'date': '2026-11-12'})
            self.assertEqual((row['rate'], row['price_source'], row['price_reference'], row['pricing_rule']),
                             (80, 'Item Price', own, rule), doctype)
            self.assertEqual((got['rate'], got['price_source'], got['price_reference'], got['pricing_rule']['name']),
                             (80, 'Item Price', own, rule), doctype)
        self.assertTrue(got['base']['party_specific'])
        self.assertEqual(got['price_list'], self.selling)
        self.assertEqual(len(self.db.get_all('Sales Order')), 1)  # explaining saved nothing

    # -- acceptance: history does not move ---------------------------------------
    def test_documents_keep_their_price_and_say_where_it_came_from(self):
        price = self.list_price('F1', 100)
        rule = self.rule('Agreed', 90, apply_on='Item Code', item_code='F1', applicable_for='Customer', customer='H1')
        quote = services.create_document('quotation', {
            'company': 'Test Co', 'customer': 'H1', 'transaction_date': '2026-11-12', 'valid_till': '2026-12-31',
            'items': [{'item_code': 'F1', 'qty': 2, 'warehouse': 'A'}]})
        services.submit_document('quotation', quote['name'])
        # Master data changes afterwards.
        services.update_document('item-price', price['name'], {'rate': 140})
        services.update_document('pricing-rule', rule['name'], {'rate': 130})
        self.assertEqual(services.load_document('quotation', quote['name'])['items'][0]['rate'], 90)
        order = make_sales_order(quote['name'])
        order.delivery_date = '2026-12-20'
        order.save()
        line = services.load_document('sales-order', order.name)['items'][0]
        self.assertEqual((line['rate'], line['price_source'], line['price_reference'], line['pricing_rule']),
                         (90, 'Item Price', price['name'], rule['name']))
        got = explain({'doctype': 'sales-order', 'name': order.name, 'line': 1})
        self.assertEqual((got['rate'], got['rules_skipped'], got['pricing_rule']['name']), (90, True, rule['name']))
        self.assertIn('carried over from the source document', got['explanation'])
        self.assertEqual(got['base']['current_rate'], 140)
        # A rate changed by hand on a saved line is manual from then on.
        draft = self.order('H1', 'F1')
        row = draft['items'][0]
        saved = services.update_document('sales-order', draft['name'], {'items': [{**row, 'rate': 111}]})
        self.assertEqual((saved['items'][0]['rate'], saved['items'][0]['price_source']), (111, 'Manual'))
        got = explain({'doctype': 'Sales Order', 'name': draft['name'], 'line': saved['items'][0]['name']})
        self.assertTrue(got['manual'])

    # -- the explanation follows access profiles -----------------------------------
    def test_buying_explanation_is_hidden_with_purchase_prices(self):
        restricted = {'restrictions': {'profile': 'Field', 'hidden': permissions.expand_classes(['purchase_price']),
                                       'permissions': {'default': None, 'doctypes': {}}}}
        with permissions.acting_as(restricted):
            with self.assertRaises(PermissionDenied):
                explain({'doctype': 'Purchase Order', 'item_code': 'F1', 'supplier': 'SUP'})
            self.assertEqual(explain({'item_code': 'X', 'customer': 'C'})['price_source'], 'None')
        self.assertIn('rate', explain({'doctype': 'Purchase Order', 'item_code': 'F1', 'supplier': 'SUP'}))


if __name__ == '__main__':
    unittest.main()
