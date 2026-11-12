"""Amount-only credit / debit notes (price corrections).

A return invoice with `amount_only=1` corrects money only: it leaves billed
and returnable quantities alone and never moves stock.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.accounting.purchase_invoice import make_purchase_return
from lambda_erp.accounting.sales_invoice import make_sales_return
from lambda_erp.exceptions import ValidationError


class AmountOnlyReturns(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def stock(self, qty=10, rate=10):
        return self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                         'items': [{'item_code': 'S', 'qty': qty, 'basic_rate': rate, 't_warehouse': 'A'}]})

    def bin(self, column):
        return float(self.db.get_value('Bin', {'item_code': 'S', 'warehouse': 'A'}, column) or 0)

    def gl_balance(self, account, voucher=None):
        sql = ('SELECT COALESCE(SUM(debit - credit), 0) AS bal FROM "GL Entry" '
               'WHERE account = ? AND is_cancelled = 0')
        params = [account]
        if voucher:
            sql += ' AND voucher_no = ?'
            params.append(voucher)
        return round(float(self.db.sql(sql, params)[0]['bal']), 2)

    def sales_invoice(self, qty=10, **extra):
        line = {'item_code': 'S', 'qty': qty, 'rate': 10, 'warehouse': 'A', **extra.pop('line', {})}
        return self.post('sales-invoice', {'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12',
                                           'items': [line], **extra})

    def correction(self, original, qty, rate, slug='sales-invoice', **extra):
        """An amount-only return: `qty` units get `rate` taken off each."""
        party = {'customer': 'C'} if slug == 'sales-invoice' else {'supplier': 'SUP'}
        line = {key: original['items'][0].get(key) for key in (
            'item_code', 'warehouse', 'sales_order', 'sales_order_item', 'purchase_order', 'purchase_order_item')
            if original['items'][0].get(key)}
        return {'company': 'Test Co', **party, 'posting_date': '2026-11-12', 'is_return': 1, 'amount_only': 1,
                'return_against': original['name'], 'items': [{**line, 'qty': -qty, 'rate': rate}], **extra}

    def outstanding(self, doctype, name):
        return float(self.db.get_value(doctype, name, 'outstanding_amount'))

    # -- defect 7: quantities stay as they are --------------------------------
    def test_price_correction_keeps_billed_and_returnable_quantity(self):
        so = self.post('sales-order', {'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-12',
                       'delivery_date': '2026-11-30', 'items': [{'item_code': 'S', 'qty': 10, 'rate': 10, 'warehouse': 'A'}]})
        so_line = so['items'][0]['name']
        refs = {'sales_order': so['name'], 'sales_order_item': so_line}
        invoice = self.sales_invoice(10, line=refs)
        self.assertEqual(float(self.db.get_value('Sales Order Item', so_line, 'billed_qty')), 10)

        note = self.post('sales-invoice', self.correction(invoice, 10, 1))
        self.assertEqual(float(note['grand_total']), -10)
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 90)
        # The order stays fully billed: it cannot be invoiced a second time.
        self.assertEqual(float(self.db.get_value('Sales Order Item', so_line, 'billed_qty')), 10)
        self.assertEqual(float(self.db.get_value('Sales Order', so['name'], 'per_billed')), 100)
        with self.assertRaisesRegex(ValidationError, 'remaining order quantity'):
            services.create_document('sales-invoice', {
                'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12',
                'items': [{'item_code': 'S', 'qty': 1, 'rate': 10, 'warehouse': 'A', **refs}]})

        # All ten units are still returnable, at the price left after the correction.
        proposed = make_sales_return(invoice['name'])
        self.assertEqual(float(proposed.get('items')[0]['qty']), -10)
        self.assertAlmostEqual(float(proposed.get('items')[0]['rate']), 9)
        proposed.save()
        proposed.submit()
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 0)

    def test_correction_after_partial_return_leaves_the_rest_returnable(self):
        invoice = self.sales_invoice(10)
        first = make_sales_return(invoice['name'])
        first.get('items')[0]['qty'] = -4
        first.save()
        first.submit()
        self.post('sales-invoice', self.correction(invoice, 6, 1))
        rest = make_sales_return(invoice['name'])
        self.assertEqual(float(rest.get('items')[0]['qty']), -6)
        self.assertAlmostEqual(float(rest.get('items')[0]['rate']), 9)
        rest.save()
        rest.submit()
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 0)

    # -- defect 8: no stock movement ------------------------------------------
    def test_price_correction_on_direct_stock_invoice_moves_no_stock(self):
        self.stock(10)
        invoice = self.sales_invoice(4, update_stock=1)
        self.assertEqual(self.bin('actual_qty'), 6)

        # The converter copies update_stock from the original; amount_only wins.
        note = self.post('sales-invoice', self.correction(invoice, 4, 2, update_stock=1))
        self.assertEqual(int(note['update_stock']), 0)
        self.assertEqual(self.bin('actual_qty'), 6)
        self.assertFalse(self.db.get_all('Stock Ledger Entry', filters={'voucher_no': note['name']}))
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 32)
        # Receivable and income only: no cost of goods, no stock account.
        self.assertEqual(len(self.db.get_all('GL Entry', filters={'voucher_no': note['name']})), 2)

        # A real return of the goods still works and does put them back.
        back = make_sales_return(invoice['name'])
        self.assertEqual(int(back.update_stock), 1)
        back.save()
        back.submit()
        self.assertEqual(self.bin('actual_qty'), 10)
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 0)

        services.cancel_document('sales-invoice', back.name)
        services.cancel_document('sales-invoice', note['name'])
        self.assertEqual(self.bin('actual_qty'), 6)
        self.assertEqual(self.outstanding('Sales Invoice', invoice['name']), 40)

    def test_plain_return_must_still_match_update_stock(self):
        self.stock(10)
        invoice = self.sales_invoice(4, update_stock=1)
        data = self.correction(invoice, 4, 10)
        data.pop('amount_only')
        with self.assertRaisesRegex(ValidationError, 'update_stock must match'):
            services.create_document('sales-invoice', data)

    # -- guards ----------------------------------------------------------------
    def test_amount_only_needs_a_return(self):
        with self.assertRaisesRegex(ValidationError, 'Amount only'):
            services.create_document('sales-invoice', {
                'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-12', 'amount_only': 1,
                'items': [{'item_code': 'S', 'qty': 1, 'rate': 10, 'warehouse': 'A'}]})

    def test_amount_only_is_bounded_by_original_quantity_and_value(self):
        invoice = self.sales_invoice(10)
        with self.assertRaisesRegex(ValidationError, 'exceeds the invoiced qty'):
            services.create_document('sales-invoice', self.correction(invoice, 11, 1))
        self.post('sales-invoice', self.correction(invoice, 10, 6))
        with self.assertRaisesRegex(ValidationError, 'exceeds remaining returnable value'):
            services.create_document('sales-invoice', self.correction(invoice, 10, 6))

    # -- purchase side ---------------------------------------------------------
    def test_supplier_price_correction_keeps_stock_and_its_ledger_value(self):
        po = self.post('purchase-order', {'company': 'Test Co', 'supplier': 'SUP', 'transaction_date': '2026-11-12',
                       'items': [{'item_code': 'S', 'qty': 10, 'rate': 10, 'warehouse': 'A'}]})
        po_line = po['items'][0]['name']
        invoice = self.post('purchase-invoice', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12', 'update_stock': 1,
            'items': [{'item_code': 'S', 'qty': 10, 'rate': 10, 'warehouse': 'A',
                       'purchase_order': po['name'], 'purchase_order_item': po_line}]})
        stock_account = invoice['items'][0]['expense_account']
        self.assertEqual(self.gl_balance(stock_account), 100)

        proposed = make_purchase_return(invoice['name'])
        proposed.amount_only = 1
        proposed.get('items')[0]['rate'] = 1
        proposed.save()
        proposed.submit()
        note = services.load_document('purchase-invoice', proposed.name)

        self.assertEqual(int(note['update_stock']), 0)
        self.assertEqual(self.bin('actual_qty'), 10)
        self.assertEqual(self.bin('stock_value'), 100)
        # Stock keeps its value in the GL too: the credit is a price difference.
        self.assertEqual(self.gl_balance(stock_account), 100)
        self.assertNotEqual(note['items'][0]['expense_account'], stock_account)
        self.assertEqual(self.gl_balance(note['items'][0]['expense_account'], note['name']), -10)
        self.assertEqual(self.outstanding('Purchase Invoice', invoice['name']), 90)
        self.assertEqual(float(self.db.get_value('Purchase Order Item', po_line, 'billed_qty')), 10)
        self.assertEqual(float(self.db.get_value('Purchase Order Item', po_line, 'received_qty')), 10)


if __name__ == '__main__':
    unittest.main()
