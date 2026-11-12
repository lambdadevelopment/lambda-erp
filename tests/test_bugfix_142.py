"""Regression coverage for the 1.4.2 core fixes.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import os
import unittest
from unittest.mock import patch

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record
from lambda_erp.exceptions import ValidationError
from lambda_erp.selling.quotation import make_sales_order
from lambda_erp.stock.delivery_note import make_delivery_note


class Bugfix142(unittest.TestCase):
    setUp = fixtures.RequirementsTest.setUp
    tearDown = fixtures.RequirementsTest.tearDown

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def stock(self, qty=10, rate=10, warehouse='A'):
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                         'items': [{'item_code': 'S', 'qty': qty, 'basic_rate': rate, 't_warehouse': warehouse}]})

    def bin(self, column, warehouse='A'):
        return self.db.get_value('Bin', {'item_code': 'S', 'warehouse': warehouse}, column)

    def rule(self, **fields):
        data = {'title': 'Rule', 'apply_on': 'Item Code', 'item_code': 'S', 'selling': 1,
                'rate_or_discount': 'Discount Percentage', 'discount_percentage': 10, 'company': 'Test Co', **fields}
        return services.create_document('pricing-rule', data)

    def invoice(self, **line):
        return services.create_document('sales-invoice', {
            'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12',
            'items': [{'item_code': 'S', 'qty': 1, 'warehouse': 'A', **line}]})

    def line(self, doc):
        return doc['items'][0]

    def gl_balance(self, account):
        rows = self.db.sql('SELECT COALESCE(SUM(debit - credit), 0) AS bal FROM "GL Entry" '
                           'WHERE account = ? AND is_cancelled = 0', [account])
        return round(float(rows[0]['bal']), 2)

    # -- 1: cancel cannot drive stock negative --------------------------------
    def test_cancel_receipt_after_consumption_is_refused(self):
        receipt = self.stock(10)
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Issue',
                  'items': [{'item_code': 'S', 'qty': 8, 's_warehouse': 'A'}]})
        with self.assertRaisesRegex(ValidationError, 'Cannot cancel'):
            services.cancel_document('stock-entry', receipt['name'])
        self.assertEqual(float(self.bin('actual_qty')), 2)
        self.assertEqual(self.db.get_value('Stock Entry', receipt['name'], 'docstatus'), 1)

    # -- 2: rules match on the document date ----------------------------------
    def test_rule_matches_document_date_not_today(self):
        self.rule(valid_from='2027-01-01', valid_upto='2027-01-31')
        inside = services.create_document('quotation', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2027-01-15', 'valid_till': '2027-02-15',
            'items': [{'item_code': 'S', 'qty': 1}]})
        outside = services.create_document('quotation', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12', 'valid_till': '2026-12-12',
            'items': [{'item_code': 'S', 'qty': 1}]})
        self.assertEqual(float(self.line(inside)['rate']), 9)
        self.assertEqual(float(self.line(outside)['rate']), 10)

    # -- 3: a manual rate survives re-save ------------------------------------
    def test_manual_rate_survives_resave(self):
        self.rule()
        inv = self.invoice()
        self.assertEqual(float(self.line(inv)['rate']), 9)
        row = {**self.line(inv), 'rate': 9.5}
        saved = services.update_document('sales-invoice', inv['name'], {'items': [row]})
        self.assertEqual(float(self.line(saved)['rate']), 9.5)
        self.assertEqual(int(self.line(saved)['ignore_pricing_rule']), 1)
        again = services.update_document('sales-invoice', inv['name'], {'items': [self.line(saved)]})
        self.assertEqual(float(self.line(again)['rate']), 9.5)
        self.assertEqual(float(again['grand_total']), 9.5)

    # -- 4: a Rate rule keeps the list price; a gone rule falls back ----------
    def test_rate_rule_keeps_list_price_and_disabling_restores_it(self):
        rule = self.rule(rate_or_discount='Rate', rate=7, discount_percentage=0)
        inv = self.invoice()
        line = self.line(inv)
        self.assertEqual((float(line['rate']), float(line['price_list_rate'])), (7, 10))
        self.assertEqual(line['pricing_rule'], rule['name'])
        services.update_document('pricing-rule', rule['name'], {'enabled': 0})
        saved = services.update_document('sales-invoice', inv['name'], {'items': [line]})
        self.assertEqual(float(self.line(saved)['rate']), 10)
        self.assertFalse(self.line(saved)['pricing_rule'])

    # -- 5: conversions keep the agreed price ----------------------------------
    def test_conversion_keeps_agreed_price(self):
        qtn = self.post('quotation', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                                      'valid_till': '2026-12-31', 'items': [{'item_code': 'S', 'qty': 2}]})
        self.assertEqual(float(self.line(qtn)['rate']), 10)
        self.rule(discount_percentage=20)
        so = make_sales_order(qtn['name'])
        so.delivery_date = '2026-12-01'
        so.save()
        self.assertEqual(float(so.get('items')[0]['rate']), 10)

    # -- 6: Rate / Discount Amount rules are per currency ----------------------
    def test_rule_currency(self):
        own = self.rule(rate_or_discount='Discount Amount', discount_amount=3, discount_percentage=0)
        self.assertEqual(own['currency'], 'USD')
        services.update_document('pricing-rule', own['name'], {'currency': 'EUR'})
        self.assertEqual(float(self.line(self.invoice())['rate']), 10)  # EUR amount not applied to USD
        self.rule(discount_percentage=10, currency='EUR')  # a percentage applies in any currency
        self.assertEqual(float(self.line(self.invoice())['rate']), 9)

    # -- 9 + 10: partial delivery, then direct-stock invoice for the rest -----
    def test_partly_delivered_line_can_ship_rest_with_invoice(self):
        self.stock(30)
        so = self.post('sales-order', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                       'delivery_date': '2026-11-30', 'items': [{'item_code': 'S', 'qty': 25, 'rate': 10, 'warehouse': 'A'}]})
        dn = make_delivery_note(so['name'])
        dn.get('items')[0]['qty'] = 15
        dn.save()
        dn.submit()
        so_line = so['items'][0]['name']
        rest = {'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12', 'update_stock': 1,
                'items': [{'item_code': 'S', 'qty': 11, 'rate': 10, 'warehouse': 'A',
                           'sales_order': so['name'], 'sales_order_item': so_line}]}
        with self.assertRaisesRegex(ValidationError, 'remaining order stock quantity'):
            services.create_document('sales-invoice', rest)
        rest['items'][0]['qty'] = 10
        self.post('sales-invoice', rest)
        self.assertEqual(float(self.bin('actual_qty')), 5)
        # Fully delivered, billed only 10 of 25: what is left is billing.
        self.assertEqual(self.db.get_value('Sales Order', so['name'], 'status'), 'To Bill')

    def test_status_fully_delivered_unbilled_is_to_bill(self):
        self.stock(10)
        so = self.post('sales-order', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                       'delivery_date': '2026-11-30', 'items': [{'item_code': 'S', 'qty': 4, 'rate': 10, 'warehouse': 'A'}]})
        dn = make_delivery_note(so['name'])
        dn.save()
        dn.submit()
        self.assertEqual(self.db.get_value('Sales Order', so['name'], 'status'), 'To Bill')

    # -- 11: each warehouse's stock account ------------------------------------
    def test_warehouse_stock_account_on_both_sides(self):
        parent = self.db.get_value('Account', {'company': 'Test Co', 'account_type': 'Stock', 'is_group': 0}, 'parent_account')
        self.db.insert('Account', {'name': 'Stock B - TC', 'account_name': 'Stock B', 'company': 'Test Co',
                                   'parent_account': parent, 'root_type': 'Asset', 'report_type': 'Balance Sheet',
                                   'account_type': 'Stock', 'is_group': 0, 'account_currency': 'USD'})
        self.db.set_value('Warehouse', 'B', 'account', 'Stock B - TC')
        self.stock(10, rate=10, warehouse='B')
        self.post('sales-invoice', {'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12', 'update_stock': 1,
                                    'items': [{'item_code': 'S', 'qty': 4, 'rate': 20, 'warehouse': 'B'}]})
        self.assertEqual(float(self.bin('stock_value', 'B')), 60)
        self.assertEqual(self.gl_balance('Stock B - TC'), 60)

    # -- 13: valuation method says what happens --------------------------------
    def test_item_valuation_method(self):
        created = create_master_record('item', {'name': 'N', 'item_name': 'New', 'is_stock_item': 1})
        self.assertEqual(self.db.get_value('Item', created['name'], 'valuation_method'), 'Moving Average')
        with self.assertRaisesRegex(ValidationError, 'Moving Average'):
            create_master_record('item', {'name': 'F', 'item_name': 'Fifo', 'valuation_method': 'FIFO'})

    # -- 14: switching the public demo off disables its user -------------------
    def test_public_demo_switched_off(self):
        from api.auth import disable_public_demo_if_switched_off
        self.db.insert('User', {'name': 'public-demo', 'email': 'demo@example.invalid', 'full_name': 'Demo',
                                'role': 'public_manager', 'enabled': 1, 'hashed_password': 'x'})
        with patch.dict(os.environ, {'LAMBDA_ERP_ENABLE_PUBLIC_DEMO': '1'}):
            self.assertEqual(disable_public_demo_if_switched_off(), 0)
        with patch.dict(os.environ, {'LAMBDA_ERP_ENABLE_PUBLIC_DEMO': '0'}):
            self.assertEqual(disable_public_demo_if_switched_off(), 1)
        self.assertEqual(int(self.db.get_value('User', 'public-demo', 'enabled')), 0)


if __name__ == '__main__':
    unittest.main()
