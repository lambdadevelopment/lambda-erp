"""Billing terms on the customer: type, per-delivery or monthly invoicing,
payment terms, and the credit limit as a warning only.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.order_actions import _credit_limit_warning, initialize_order_actions
from api.routers.masters import create_master_record, update_master_record
from api.routers.reports import _unbilled_deliveries
from lambda_erp.accounting.sales_invoice import SalesInvoice
from lambda_erp.exceptions import ValidationError
from lambda_erp.selling.sales_order import SalesOrder
from lambda_erp.stock.delivery_note import make_delivery_note


class CustomerTerms(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        initialize_order_actions()
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
                  'items': [{'item_code': 'S', 'qty': 100, 'basic_rate': 10, 't_warehouse': 'A'}]})

    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def invoice(self, customer='C', qty=1, rate=100, submit=True, posting_date='2026-11-02'):
        data = {'company': 'Test Co', 'customer': customer, 'posting_date': posting_date,
                'items': [{'item_code': 'S', 'qty': qty, 'rate': rate, 'warehouse': 'A'}]}
        return self.post('sales-invoice', data) if submit else services.create_document('sales-invoice', data)

    def test_terms_are_validated_on_the_customer(self):
        created = create_master_record('customer', {
            'name': 'HOSP', 'customer_name': 'Synthetic hospital', 'customer_type': 'Company',
            'billing_cycle': 'Monthly', 'payment_terms_days': '60', 'credit_limit': 1000})
        stored = self.db.get_value('Customer', created['name'], ['customer_type', 'billing_cycle', 'payment_terms_days'])
        self.assertEqual((stored['customer_type'], stored['billing_cycle'], stored['payment_terms_days']),
                         ('Company', 'Monthly', 60))
        for bad, phrase in ((({'customer_type': 'Clinic'}), 'customer_type must be one of'),
                            (({'billing_cycle': 'Weekly'}), 'billing_cycle must be one of'),
                            (({'payment_terms_days': '30.5'}), 'whole number'),
                            (({'payment_terms_days': 400}), 'between 0 and 365'),
                            (({'credit_limit': -1}), 'cannot be negative')):
            with self.assertRaisesRegex(ValidationError, phrase):
                update_master_record('customer', 'HOSP', bad)
        update_master_record('customer', 'HOSP', {'billing_cycle': '', 'payment_terms_days': None})
        from lambda_erp.customer_terms import billing_cycle, payment_terms_days
        self.assertEqual((billing_cycle('HOSP'), payment_terms_days('HOSP')), ('Per Delivery', 30))

    def test_payment_terms_set_the_due_date(self):
        self.assertEqual(str(self.invoice()['due_date'])[:10], '2026-12-02')  # default 30 days
        self.db.set_value('Customer', 'C', 'payment_terms_days', 90)
        self.db.commit()
        self.assertEqual(str(self.invoice()['due_date'])[:10], '2027-01-31')
        # A due date given explicitly stays.
        doc = services.create_document('sales-invoice', {
            'company': 'Test Co', 'customer': 'C', 'posting_date': '2026-11-02', 'due_date': '2026-11-20',
            'items': [{'item_code': 'S', 'qty': 1, 'rate': 100, 'warehouse': 'A'}]})
        self.assertEqual(str(doc['due_date'])[:10], '2026-11-20')

    def test_credit_limit_warns_and_never_blocks(self):
        self.db.set_value('Customer', 'C', 'credit_limit', 250)
        self.db.commit()
        self.invoice(rate=200)  # open: 200
        within = services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-03',
            'items': [{'item_code': 'S', 'qty': 1, 'rate': 40, 'warehouse': 'A'}]})
        self.assertFalse([w for w in within['_validation']['warnings'] if w['code'] == 'credit_limit_exceeded'])
        over = services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-03',
            'items': [{'item_code': 'S', 'qty': 1, 'rate': 80, 'warehouse': 'A'}]})
        warning, = [w for w in over['_validation']['warnings'] if w['code'] == 'credit_limit_exceeded']
        self.assertEqual((warning['limit'], warning['open'], warning['this_document'], warning['total']),
                         (250, 200, 80, 280))
        # Only a warning: the order is submitted all the same.
        self.assertEqual(services.submit_document('sales-order', over['name'])['docstatus'], 1)
        # Once the invoice is paid down below the limit, no warning.
        self.db.sql('UPDATE "Sales Invoice" SET outstanding_amount = 0 WHERE customer = ?', ['C'])
        self.db.commit()
        self.assertEqual(_credit_limit_warning(SalesOrder.load(over['name'])), [])
        # No limit, no warning; returns never warn.
        self.db.set_value('Customer', 'C', 'credit_limit', 0)
        self.db.commit()
        draft = self.invoice(rate=10_000, submit=False)
        self.assertEqual(_credit_limit_warning(SalesInvoice.load(draft['name'])), [])

    def test_unbilled_deliveries_say_and_filter_the_billing_cycle(self):
        self.db.insert('Customer', {'name': 'M', 'customer_name': 'Monthly customer', 'default_currency': 'USD',
                                    'billing_cycle': 'Monthly'})
        self.db.commit()
        for customer in ('C', 'M'):
            order = self.post('sales-order', {
                'company': 'Test Co', 'customer': customer, 'transaction_date': '2026-11-03',
                'items': [{'item_code': 'S', 'qty': 2, 'rate': 20, 'warehouse': 'A'}]})
            note = make_delivery_note(order['name'])
            note.save()
            note.submit()
        rows = _unbilled_deliveries(self.db)['rows']
        self.assertEqual(sorted((r['customer'], r['billing_cycle']) for r in rows),
                         [('C', 'Per Delivery'), ('M', 'Monthly')])
        monthly = _unbilled_deliveries(self.db, billing_cycle='Monthly')['rows']
        self.assertEqual([r['customer'] for r in monthly], ['M'])


if __name__ == '__main__':
    unittest.main()
