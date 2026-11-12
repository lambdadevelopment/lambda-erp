"""Consignment: stocking, withdrawal, replacement, recall, idempotency.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import update_master_record
from api.routers.reports import _stock_detail
from api.stock_actions import (
    prepare_consignment_recall, prepare_consignment_stocking, record_consignment_withdrawal,
)
from lambda_erp.accounting.sales_invoice import make_sales_return
from lambda_erp.exceptions import ConflictError, ValidationError
from lambda_erp.stock.stock_ledger import get_stock_details, stock_ledger_agreement


class Consignment(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        db.insert('Customer', {'name': 'H', 'customer_name': 'Hospital', 'default_currency': 'USD'})
        db.insert('Customer', {'name': 'O', 'customer_name': 'Other clinic', 'default_currency': 'USD'})
        db.insert('Warehouse', {'name': 'MAIN', 'warehouse_name': 'Main', 'company': 'Test Co'})
        db.insert('Warehouse', {'name': 'QUAR', 'warehouse_name': 'Quarantine', 'company': 'Test Co'})
        db.insert('Warehouse', {'name': 'CONSI-H', 'warehouse_name': 'Consignment Hospital', 'company': 'Test Co'})
        db.insert('Item', {'name': 'B', 'item_name': 'Implant', 'is_stock_item': 1, 'standard_rate': 200,
                           'has_batch_no': 1, 'has_expiry_date': 1})
        db.commit()
        update_master_record('warehouse', 'CONSI-H', {'consignment_customer': 'H', 'replenishment_warehouse': 'MAIN'})

    # -- helpers -------------------------------------------------------------
    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def receive(self, batch, qty, expiry):
        return self.post('purchase-receipt', {
            'company': 'Test Co', 'supplier': 'SUP', 'posting_date': '2026-11-12', 'items': [
                {'item_code': 'B', 'qty': qty, 'rate': 80, 'warehouse': 'MAIN', 'batch_no': batch, 'expiry_date': expiry}]})

    def qty(self, warehouse, batch=None):
        return sum(float(r['actual_qty']) for r in get_stock_details('B', warehouse, batch))

    def gl(self, root_type):
        rows = self.db.sql(
            'SELECT COALESCE(SUM(g.credit - g.debit), 0) AS total FROM "GL Entry" g JOIN "Account" a ON a.name = g.account '
            'WHERE a.root_type = ? AND g.is_cancelled = 0', [root_type])
        return round(float(rows[0]['total']), 2)

    def stock_value(self):
        return round(sum(float(r['stock_value']) for r in self.db.get_all('Bin', filters={'item_code': 'B'}, fields=['stock_value'])), 2)

    def stocked(self, qty=6):
        self.receive('LOT-1', 4, '2027-06-30')
        self.receive('LOT-2', 6, '2028-06-30')
        draft = prepare_consignment_stocking({'warehouse': 'CONSI-H', 'items': [{'item_code': 'B', 'qty': qty}]})
        return services.submit_document('stock-entry', draft['name'])

    def withdraw(self, lines, **extra):
        return record_consignment_withdrawal({'warehouse': 'CONSI-H', 'items': lines, **extra})

    # -- the end-to-end case of the work order --------------------------------
    def test_receipt_stocking_withdrawal_invoice_replacement_recall(self):
        # Receipt and stocking: nothing is sold, the total we own is unchanged.
        self.receive('LOT-1', 4, '2027-06-30')
        self.receive('LOT-2', 6, '2028-06-30')
        owned, value, revenue = self.qty(None), self.stock_value(), self.gl('Income')
        draft = prepare_consignment_stocking({'warehouse': 'CONSI-H', 'items': [{'item_code': 'B', 'qty': 6}]})
        self.assertEqual(draft['docstatus'], 0)
        self.assertEqual([(r['batch_no'], r['qty']) for r in draft['items']], [('LOT-1', 4.0), ('LOT-2', 2.0)])  # FEFO
        self.assertEqual(self.qty('CONSI-H'), 0)  # a draft moves nothing
        stocking = services.submit_document('stock-entry', draft['name'])
        self.assertEqual((self.qty('MAIN'), self.qty('CONSI-H'), self.qty(None)), (4, 6, owned))
        self.assertEqual((self.stock_value(), self.gl('Income')), (value, revenue))
        self.assertFalse(self.db.get_all('Sales Invoice'))
        self.assertFalse(self.db.get_all('GL Entry', filters={'voucher_no': stocking['name']}))

        # Partial withdrawal with replacement: billed once, from the right batch.
        got = self.withdraw([{'item_code': 'B', 'qty': 3, 'batch_no': 'LOT-1'}],
                            external_source='hospital-orders', external_reference='ENT-1001', replenish=True)
        self.assertFalse(got['replayed'])
        invoice = services.load_document('sales-invoice', got['sales_invoice'])
        self.assertEqual((invoice['customer'], invoice['docstatus'], invoice['grand_total'], int(invoice['update_stock'])),
                         ('H', 1, 600, 1))
        self.assertEqual((self.qty('CONSI-H', 'LOT-1'), self.qty('CONSI-H', 'LOT-2'), self.qty('MAIN')), (1, 2, 4))
        self.assertEqual(self.gl('Income') - revenue, 600)
        self.assertEqual(self.stock_value(), value - 3 * 80)
        self.assertFalse(self.db.get_all('Delivery Note'))  # no second movement of the same goods

        # The replacement is its own document; the withdrawal is billed regardless.
        self.assertEqual(got['replenishment_status'], 'Pending')
        replacement = services.load_document('stock-entry', got['replacement_stock_entry'])
        self.assertEqual((replacement['docstatus'], replacement['stock_entry_type']), (0, 'Material Transfer'))
        self.assertEqual([(r['s_warehouse'], r['t_warehouse'], r['batch_no'], r['qty']) for r in replacement['items']],
                         [('MAIN', 'CONSI-H', 'LOT-2', 3.0)])
        services.submit_document('stock-entry', replacement['name'])
        withdrawal = services.load_document('consignment-withdrawal', got['name'])
        self.assertEqual((withdrawal['replenishment_status'], withdrawal['status']), ('Done', 'Billed'))
        self.assertEqual((self.qty('CONSI-H'), self.qty('MAIN')), (6, 1))

        # Recall of what is left: a transfer, no credit note, books unchanged.
        income = self.gl('Income')
        recall = prepare_consignment_recall({'warehouse': 'CONSI-H', 'to_warehouse': 'QUAR'})
        self.assertEqual(sorted((r['batch_no'], r['qty']) for r in recall['items']), [('LOT-1', 1.0), ('LOT-2', 5.0)])
        services.submit_document('stock-entry', recall['name'])
        self.assertEqual((self.qty('CONSI-H'), self.qty('QUAR'), self.qty(None)), (0, 6, 7))
        self.assertEqual(self.gl('Income'), income)
        self.assertEqual(len(self.db.get_all('Sales Invoice')), 1)
        self.assertEqual(stock_ledger_agreement(), [])
        balance = self.db.sql('SELECT COALESCE(SUM(debit - credit), 0) AS diff FROM "GL Entry" WHERE is_cancelled = 0')
        self.assertAlmostEqual(float(balance[0]['diff']), 0, places=2)

    # -- the same report twice ---------------------------------------------------
    def test_a_repeated_report_bills_once(self):
        self.stocked()
        lines = [{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-2'}, {'item_code': 'B', 'qty': 2, 'batch_no': 'LOT-1'}]
        first = self.withdraw(lines, external_source='hospital-orders', external_reference='ENT-7')
        again = self.withdraw(list(reversed(lines)), external_source='hospital-orders', external_reference='ENT-7')
        self.assertEqual((again['replayed'], again['name'], again['sales_invoice']), (True, first['name'], first['sales_invoice']))
        self.assertEqual((len(self.db.get_all('Sales Invoice')), self.qty('CONSI-H')), (1, 3))
        # The same number with other content is not silently accepted or overwritten.
        with self.assertRaisesRegex(ConflictError, 'with different content'):
            self.withdraw([{'item_code': 'B', 'qty': 3, 'batch_no': 'LOT-1'}],
                          external_source='hospital-orders', external_reference='ENT-7')
        with self.assertRaisesRegex(ConflictError, 'already recorded'):
            services.create_document('consignment-withdrawal', {
                'warehouse': 'CONSI-H', 'external_source': 'hospital-orders', 'external_reference': 'ENT-7',
                'items': [{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-1'}]})
        self.assertEqual(self.qty('CONSI-H'), 3)
        # The explicit correction: cancel, then record the corrected report under its number.
        services.cancel_document('consignment-withdrawal', first['name'])
        self.assertEqual((self.qty('CONSI-H'), self.db.get_value('Sales Invoice', first['sales_invoice'], 'docstatus')), (6, 2))
        fixed = self.withdraw([{'item_code': 'B', 'qty': 3, 'batch_no': 'LOT-1'}],
                              external_source='hospital-orders', external_reference='ENT-7')
        self.assertEqual((fixed['replayed'], self.qty('CONSI-H', 'LOT-1')), (False, 1))
        # Another source may use the same number; without a number every call counts.
        self.withdraw([{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-1'}], external_source='other', external_reference='ENT-7')
        self.assertEqual(self.qty('CONSI-H'), 2)

    # -- only what really lies there, only for that customer ----------------------
    def test_withdrawal_is_checked_against_the_consignment_stock(self):
        self.stocked()
        for lines, message in (
                ([{'item_code': 'B', 'qty': 5, 'batch_no': 'LOT-1'}], 'Not enough of batch LOT-1'),   # only 4 there
                ([{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-9'}], 'Unknown batch LOT-9'),         # mistyped lot
                ([{'item_code': 'B', 'qty': 1}], 'Batch is required')):
            with self.assertRaisesRegex(ValidationError, message):
                self.withdraw(lines, external_source='hospital-orders', external_reference='ENT-X')
        self.assertEqual((self.qty('CONSI-H'), len(self.db.get_all('Sales Invoice')),
                          len(self.db.get_all('Consignment Withdrawal'))), (6, 0, 0))
        with self.assertRaisesRegex(ValidationError, 'not a consignment warehouse'):
            record_consignment_withdrawal({'warehouse': 'MAIN', 'items': [{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-2'}]})
        # Goods set out for one customer are not sold to another from there.
        note = {'company': 'Test Co', 'customer': 'O', 'items': [
            {'item_code': 'B', 'qty': 1, 'rate': 200, 'warehouse': 'CONSI-H', 'batch_no': 'LOT-1'}]}
        with self.assertRaisesRegex(ValidationError, 'consignment warehouse of customer H'):
            self.post('delivery-note', note)
        with self.assertRaisesRegex(ValidationError, 'consignment warehouse of customer H'):
            self.post('sales-invoice', {**note, 'update_stock': 1})
        self.post('delivery-note', {**note, 'customer': 'H'})
        self.assertEqual(self.qty('CONSI-H'), 5)
        # The combined stock view says who it lies with.
        rows = _stock_detail(self.db, consignment_customer='H')['rows']
        self.assertEqual(sorted((r['warehouse'], r['batch_no'], r['actual_qty']) for r in rows),
                         [('CONSI-H', 'LOT-1', 3.0), ('CONSI-H', 'LOT-2', 2.0)])
        self.assertEqual({r['warehouse'] for r in _stock_detail(self.db, consignment=False)['rows']}, {'MAIN'})

    # -- a replacement that cannot be shipped ---------------------------------------
    def test_withdrawal_is_billed_when_the_replacement_is_short(self):
        self.stocked(qty=10)  # everything is at the customer; nothing left to replace from
        got = self.withdraw([{'item_code': 'B', 'qty': 2, 'batch_no': 'LOT-1'}], replenish=True)
        self.assertEqual((got['status'], got['replenishment_status']), ('Billed', 'Pending'))
        self.assertEqual(self.db.get_value('Sales Invoice', got['sales_invoice'], 'docstatus'), 1)
        replacement = services.load_document('stock-entry', got['replacement_stock_entry'])
        self.assertEqual([(r['qty'], r.get('batch_no')) for r in replacement['items']], [(2.0, None)])
        with self.assertRaisesRegex(ValidationError, 'Batch is required|Negative stock'):
            services.submit_document('stock-entry', replacement['name'])
        # Dropping the replacement leaves the billed withdrawal as it is.
        services.discard_document('stock-entry', replacement['name'])
        withdrawal = services.load_document('consignment-withdrawal', got['name'])
        self.assertEqual((withdrawal['status'], withdrawal['replenishment_status']), ('Billed', 'Cancelled'))

    # -- cancelling and returning a billed withdrawal ----------------------------------
    def test_cancel_and_return_of_a_billed_withdrawal(self):
        self.stocked()
        got = self.withdraw([{'item_code': 'B', 'qty': 2, 'batch_no': 'LOT-1'}])
        with self.assertRaisesRegex(ValidationError, 'cancel the withdrawal'):
            services.cancel_document('sales-invoice', got['sales_invoice'])
        # Paid: the withdrawal cannot simply be cancelled...
        receivable = self.db.get_value('Sales Invoice', got['sales_invoice'], 'debit_to')
        bank = self.db.get_value('Account', {'company': 'Test Co', 'account_type': 'Bank', 'is_group': 0}, 'name') or \
            self.db.get_value('Account', {'company': 'Test Co', 'account_type': 'Cash', 'is_group': 0}, 'name')
        payment = self.post('payment-entry', {
            'company': 'Test Co', 'payment_type': 'Receive', 'party_type': 'Customer', 'party': 'H',
            'paid_from': receivable, 'paid_to': bank, 'paid_amount': 400, 'posting_date': '2026-11-12',
            'references': [{'reference_doctype': 'Sales Invoice', 'reference_name': got['sales_invoice'],
                            'allocated_amount': 400}]})
        with self.assertRaisesRegex(ValidationError, 'Credit it with a return invoice instead'):
            services.cancel_document('consignment-withdrawal', got['name'])
        self.assertEqual((self.qty('CONSI-H'), self.db.get_value('Consignment Withdrawal', got['name'], 'docstatus')), (4, 1))
        # ...but the goods can come back with a return invoice, into the consignment warehouse.
        services.cancel_document('payment-entry', payment['name'])
        back = make_sales_return(got['sales_invoice'])
        back.get('items')[0]['qty'] = -1
        back.save()
        back.submit()
        self.assertEqual(self.qty('CONSI-H', 'LOT-1'), 3)
        services.cancel_document('sales-invoice', back.name)
        # Unpaid: cancelling the withdrawal reverses its invoice and stock.
        services.cancel_document('consignment-withdrawal', got['name'])
        self.assertEqual((self.qty('CONSI-H'), self.db.get_value('Sales Invoice', got['sales_invoice'], 'docstatus')), (6, 2))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_warehouse_rules(self):
        self.stocked()
        with self.assertRaisesRegex(ValidationError, 'cannot be changed while stock lies in it'):
            update_master_record('warehouse', 'CONSI-H', {'consignment_customer': 'O'})
        with self.assertRaisesRegex(ValidationError, 'must be one of your own warehouses'):
            update_master_record('warehouse', 'MAIN', {'replenishment_warehouse': 'CONSI-H'})
        with self.assertRaisesRegex(ValidationError, 'stocked from one of your own warehouses'):
            prepare_consignment_stocking({'warehouse': 'CONSI-H', 'from_warehouse': 'CONSI-H',
                                          'items': [{'item_code': 'B', 'qty': 1}]})
        with self.assertRaisesRegex(ValidationError, 'does not hold 9 of B'):
            prepare_consignment_recall({'warehouse': 'CONSI-H', 'to_warehouse': 'MAIN',
                                        'items': [{'item_code': 'B', 'qty': 9}]})
        part = prepare_consignment_recall({'warehouse': 'CONSI-H', 'to_warehouse': 'MAIN',
                                           'items': [{'item_code': 'B', 'qty': 1, 'batch_no': 'LOT-2'}]})
        self.assertEqual([(r['batch_no'], r['qty']) for r in part['items']], [('LOT-2', 1.0)])


if __name__ == '__main__':
    unittest.main()
