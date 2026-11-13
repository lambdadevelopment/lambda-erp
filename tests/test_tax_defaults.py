"""Company tax defaults on invoices the system creates, and money rounding.

Two independent things:

  * an invoice the core creates itself (consignment withdrawal, subscription,
    delivery note -> invoice) carries the same taxes as one created through
    the document service;
  * money is rounded half-up on decimal values, so 3 x 395.00 at 8.1 % is
    95.99, not 95.98.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record, update_master_record
from api.stock_actions import record_consignment_withdrawal
from lambda_erp.accounting.sales_invoice import SalesInvoice, make_sales_return
from lambda_erp.controllers.taxes_and_totals import calculate_taxes_and_totals
from lambda_erp.exceptions import ConflictError, ValidationError
from lambda_erp.stock.delivery_note import make_delivery_note, make_sales_invoice_from_delivery_note
from lambda_erp.stock.stock_ledger import get_stock_details, stock_ledger_agreement
from lambda_erp.utils import flt, money

WITHDRAWAL = {
    'warehouse': 'TEST-CONSIGNMENT', 'external_source': 'synthetic-test',
    'external_reference': 'SYNTHETIC-WITHDRAWAL-1', 'replenish': True, 'replenish_from_warehouse': 'TEST-MAIN',
    'items': [{'item_code': 'TEST-A', 'qty': 1, 'batch_no': 'LOT-A', 'rate': 395},
              {'item_code': 'TEST-B', 'qty': 1, 'batch_no': 'LOT-B', 'rate': 395},
              {'item_code': 'TEST-A', 'qty': 1, 'batch_no': 'LOT-A', 'rate': 395}]}


class TaxDefaults(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Customer', {'name': 'H', 'customer_name': 'Test hospital', 'default_currency': 'USD'})
        db.insert('Warehouse', {'name': 'TEST-MAIN', 'warehouse_name': 'Main', 'company': 'Test Co'})
        db.insert('Warehouse', {'name': 'TEST-CONSIGNMENT', 'warehouse_name': 'Consignment', 'company': 'Test Co'})
        for code in ('TEST-A', 'TEST-B'):
            db.insert('Item', {'name': code, 'item_name': code, 'is_stock_item': 1, 'standard_rate': 395,
                               'has_batch_no': 1})
        self.vat = db.sql('SELECT name FROM "Account" WHERE company = ? AND account_type = ? '
                          'AND COALESCE(is_group, 0) = 0 ORDER BY name LIMIT 1', ['Test Co', 'Tax'])[0]['name']
        self.template('VAT 8.1', 8.1)
        db.set_value('Company', 'Test Co', {'default_sales_tax_template': 'VAT 8.1'})
        db.commit()
        update_master_record('warehouse', 'TEST-CONSIGNMENT', {
            'consignment_customer': 'H', 'replenishment_warehouse': 'TEST-MAIN'})
        self.billing = create_master_record('address', {
            'customer': 'H', 'label': 'Accounting', 'address': 'PO Box 1', 'city': 'Town', 'is_shipping': 0,
            'is_default_billing': 1})['name']
        self.shipping = create_master_record('address', {
            'customer': 'H', 'label': 'Ward', 'address': 'Ward road 2', 'city': 'Town', 'is_billing': 0,
            'is_default_shipping': 1})['name']
        # Stock at cost 0: valuation and sales tax have nothing to do with each other.
        for code, lot in (('TEST-A', 'LOT-A'), ('TEST-B', 'LOT-B')):
            self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Receipt', 'items': [
                {'item_code': code, 'qty': 10, 'basic_rate': 0, 't_warehouse': 'TEST-MAIN', 'batch_no': lot}]})
        self.post('stock-entry', {'company': 'Test Co', 'stock_entry_type': 'Material Transfer', 'items': [
            {'item_code': 'TEST-A', 'qty': 2, 's_warehouse': 'TEST-MAIN', 't_warehouse': 'TEST-CONSIGNMENT', 'batch_no': 'LOT-A'},
            {'item_code': 'TEST-B', 'qty': 1, 's_warehouse': 'TEST-MAIN', 't_warehouse': 'TEST-CONSIGNMENT', 'batch_no': 'LOT-B'}]})

    # -- helpers -------------------------------------------------------------
    def template(self, name, rate):
        self.db.insert('Tax Template', {'name': name, 'company': 'Test Co', 'tax_type': 'Sales'})
        self.db.insert('Tax Template Detail', {'name': f'{name}-1', 'parent': name, 'idx': 1,
                                               'charge_type': 'On Net Total', 'account_head': self.vat, 'rate': rate})

    def post(self, slug, data):
        doc = services.create_document(slug, data)
        return services.submit_document(slug, doc['name'])

    def invoice(self, **fields):
        return services.create_document('sales-invoice', {
            'company': 'Test Co', 'customer': 'H', 'posting_date': '2026-11-12',
            'items': [{'item_code': code, 'qty': 1, 'rate': 395} for code in ('TEST-A', 'TEST-B', 'TEST-A')], **fields})

    def figures(self, doc):
        return (doc['net_total'], doc['total_taxes_and_charges'], doc['grand_total'],
                [(row['account_head'], row['rate'], row['tax_amount']) for row in doc['taxes']])

    def lots(self, warehouse):
        return {(row['item_code'], row['batch_no']): float(row['actual_qty'])
                for code in ('TEST-A', 'TEST-B') for row in get_stock_details(code, warehouse)}

    def ledger(self, voucher):
        rows = self.db.sql('SELECT g.account, a.root_type, a.account_type, SUM(g.debit) AS debit, SUM(g.credit) AS credit '
                           'FROM "GL Entry" g JOIN "Account" a ON a.name = g.account '
                           'WHERE g.voucher_no = ? AND g.is_cancelled = 0 GROUP BY g.account, a.root_type, a.account_type',
                           [voucher])
        return {(row['root_type'], row['account_type'] or ''): (flt(row['debit'], 2), flt(row['credit'], 2)) for row in rows}

    EXPECTED = (1185.0, 95.99, 1280.99)

    # -- 1, 2, 5: the system's own invoice is taxed like any other --------------
    def test_consignment_invoice_takes_the_company_tax_and_rounds_half_up(self):
        before = self.lots('TEST-CONSIGNMENT')
        result = record_consignment_withdrawal(WITHDRAWAL)
        invoice = services.load_document('sales-invoice', result['sales_invoice'])
        self.assertEqual(self.figures(invoice), (*self.EXPECTED, [(self.vat, 8.1, 95.99)]))
        self.assertEqual((invoice['docstatus'], invoice['outstanding_amount']), (1, 1280.99))
        self.assertEqual((invoice['billing_address'], invoice['shipping_address']), (self.billing, self.shipping))
        # The same figures as an invoice made through the document service, and stable over save / reload / submit.
        normal = self.invoice()
        self.assertEqual(self.figures(normal), self.figures(invoice))
        again = services.update_document('sales-invoice', normal['name'], {'remarks': 'saved again'})
        self.assertEqual(self.figures(again), self.figures(invoice))
        submitted = services.submit_document('sales-invoice', normal['name'])
        self.assertEqual(self.figures(services.load_document('sales-invoice', submitted['name'])), self.figures(invoice))
        # The ledger is balanced and right: receivable gross, income net, tax on the tax account.
        ledger = self.ledger(invoice['name'])
        self.assertEqual(ledger[('Asset', 'Receivable')], (1280.99, 0.0))
        self.assertEqual(ledger[('Liability', 'Tax')], (0.0, 95.99))
        self.assertEqual(sum(credit - debit for (root, _), (debit, credit) in ledger.items() if root == 'Income'), 1185.0)
        self.assertEqual(round(sum(debit - credit for debit, credit in ledger.values()), 2), 0.0)
        # Stock: the right lots left the consignment warehouse, nothing else moved.
        after = self.lots('TEST-CONSIGNMENT')
        self.assertEqual({key: before[key] - after.get(key, 0) for key in before},
                         {('TEST-A', 'LOT-A'): 2.0, ('TEST-B', 'LOT-B'): 1.0})
        self.assertEqual(stock_ledger_agreement(), [])

    # -- 6, 7: once, and the replacement is not a second sale -------------------
    def test_withdrawal_is_billed_once_and_replacement_only_moves_stock(self):
        first = record_consignment_withdrawal(WITHDRAWAL)
        count = lambda table: self.db.sql(f'SELECT COUNT(*) AS n FROM "{table}"')[0]['n']
        invoices, gl, sle = count('Sales Invoice'), count('GL Entry'), count('Stock Ledger Entry')
        repeat = record_consignment_withdrawal(WITHDRAWAL)
        self.assertEqual(repeat['sales_invoice'], first['sales_invoice'])
        self.assertEqual((count('Sales Invoice'), count('GL Entry'), count('Stock Ledger Entry')), (invoices, gl, sle))
        with self.assertRaises(ConflictError):
            record_consignment_withdrawal({**WITHDRAWAL, 'items': WITHDRAWAL['items'][:2]})
        self.assertEqual(count('Sales Invoice'), invoices)
        # Submitting the replacement refills the consignment warehouse; no invoice, no tax.
        withdrawal = self.db.get_value('Consignment Withdrawal', first['name'],
                                       ['replacement_stock_entry', 'replenishment_status'])
        self.assertEqual(withdrawal['replenishment_status'], 'Pending')
        main_before = self.lots('TEST-MAIN')
        services.submit_document('stock-entry', withdrawal['replacement_stock_entry'])
        self.assertEqual(self.db.get_value('Consignment Withdrawal', first['name'], 'replenishment_status'), 'Done')
        self.assertEqual(self.lots('TEST-CONSIGNMENT'), {('TEST-A', 'LOT-A'): 2.0, ('TEST-B', 'LOT-B'): 1.0})
        main_after = self.lots('TEST-MAIN')
        self.assertEqual({key: main_before[key] - main_after[key] for key in main_before},
                         {('TEST-A', 'LOT-A'): 2.0, ('TEST-B', 'LOT-B'): 1.0})
        self.assertEqual(count('Sales Invoice'), invoices)
        self.assertEqual(stock_ledger_agreement(), [])

    # -- 3, 9: what the default is, and what it never touches -------------------
    def test_default_explicit_and_absent_taxes(self):
        self.assertEqual(self.figures(self.invoice())[3], [(self.vat, 8.1, 95.99)])
        # An empty list is a deliberate "no tax"; explicit rows are kept, not doubled.
        self.assertEqual(self.figures(self.invoice(taxes=[])), (1185.0, 0.0, 1185.0, []))
        own = self.invoice(taxes=[{'charge_type': 'On Net Total', 'account_head': self.vat, 'rate': 2.6}])
        self.assertEqual(self.figures(own)[3], [(self.vat, 2.6, 30.81)])
        saved = services.update_document('sales-invoice', own['name'], {'remarks': 'x'})
        self.assertEqual(len(saved['taxes']), 1)
        # A new company default applies to new documents, never to ones that exist.
        issued = services.submit_document('sales-invoice', self.invoice()['name'])
        draft = self.invoice()
        self.template('VAT 2.6', 2.6)
        self.db.set_value('Company', 'Test Co', {'default_sales_tax_template': 'VAT 2.6'})
        self.db.commit()
        self.assertEqual(self.figures(services.load_document('sales-invoice', issued['name']))[1], 95.99)
        self.assertEqual(self.figures(services.update_document('sales-invoice', draft['name'], {'remarks': 'y'}))[1], 95.99)
        self.assertEqual(self.figures(self.invoice())[3], [(self.vat, 2.6, 30.81)])
        self.assertEqual(self.figures(services.load_document(
            'sales-invoice', record_consignment_withdrawal(WITHDRAWAL)['sales_invoice']))[1], 30.81)
        # No template: no tax is invented, by either path.
        self.db.set_value('Company', 'Test Co', {'default_sales_tax_template': None})
        self.db.commit()
        self.assertEqual(self.figures(self.invoice()), (1185.0, 0.0, 1185.0, []))

    def test_other_invoices_the_core_creates_are_taxed_too(self):
        # Delivery note -> invoice: the delivery's own tax rows travel.
        order = self.post('sales-order', {
            'company': 'Test Co', 'customer': 'H', 'transaction_date': '2026-11-12', 'delivery_date': '2026-12-30',
            'items': [{'item_code': 'TEST-A', 'qty': 3, 'rate': 395, 'warehouse': 'TEST-MAIN'}]})
        note = make_delivery_note(order['name'])
        note.save()
        note.submit()
        billed = make_sales_invoice_from_delivery_note(note.name)
        billed.save()
        self.assertEqual((billed.net_total, billed.total_taxes_and_charges, billed.grand_total), self.EXPECTED)
        # A subscription invoice takes the company default.
        self.db.insert('Item', {'name': 'SERVICE', 'item_name': 'Service', 'is_stock_item': 0, 'standard_rate': 395})
        self.db.commit()
        subscription = services.create_document('subscription', {
            'company': 'Test Co', 'party_type': 'Customer', 'party': 'H', 'start_date': '2026-01-01',
            'billing_interval': 'Monthly', 'plans': [{'item_code': 'SERVICE', 'qty': 3, 'rate': 395}]})
        from lambda_erp.accounting.subscription import Subscription
        created = Subscription.load(subscription['name']).process()
        invoice = services.load_document('sales-invoice', created['name'])
        self.assertEqual((invoice['net_total'], invoice['total_taxes_and_charges'], invoice['grand_total']), self.EXPECTED)

    # -- 8: nothing half done ----------------------------------------------------
    def test_a_failing_invoice_rolls_the_withdrawal_back(self):
        self.db.insert('Tax Template', {'name': 'Broken', 'company': 'Test Co', 'tax_type': 'Sales'})
        self.db.insert('Tax Template Detail', {'name': 'Broken-1', 'parent': 'Broken', 'idx': 1,
                                               'charge_type': 'On Net Total', 'account_head': None, 'rate': 8.1})
        self.db.set_value('Company', 'Test Co', {'default_sales_tax_template': 'Broken'})
        self.db.commit()
        stock = self.lots('TEST-CONSIGNMENT')
        with self.assertRaisesRegex(ValidationError, 'Account is required'):
            record_consignment_withdrawal(WITHDRAWAL)
        self.assertEqual(self.lots('TEST-CONSIGNMENT'), stock)
        self.assertFalse(self.db.sql('SELECT 1 FROM "Sales Invoice"'))
        self.assertFalse(self.db.sql('SELECT 1 FROM "Consignment Withdrawal" WHERE docstatus = 1'))
        self.assertEqual(stock_ledger_agreement(), [])

    def test_return_reverses_the_same_tax(self):
        result = record_consignment_withdrawal(WITHDRAWAL)
        credit = make_sales_return(result['sales_invoice'])
        credit.save()
        self.assertEqual((credit.net_total, credit.total_taxes_and_charges, credit.grand_total),
                         (-1185.0, -95.99, -1280.99))
        credit.submit()
        self.assertEqual(self.db.get_value('Sales Invoice', result['sales_invoice'], 'outstanding_amount'), 0)
        self.assertEqual(self.ledger(credit.name)[('Liability', 'Tax')], (95.99, 0.0))

    # -- 4: rounding ---------------------------------------------------------------
    def test_money_rounds_half_up_on_decimal_values(self):
        for value, expected in ((95.985, 95.99), (-95.985, -95.99), (2.675, 2.68), (1.005, 1.01), (0.125, 0.13),
                                (0.135, 0.14), (-0.125, -0.13), (1.0049999, 1.0), (1e-9, 0.0), ('31.995', 32.0)):
            self.assertEqual(money(value), expected, value)
            self.assertEqual(flt(value, 2), expected, value)
        self.assertEqual((money(2.5, 0), money(3.5, 0), money(-2.5, 0), flt(0.30000000000000004, 2)), (3.0, 4.0, -3.0, 0.3))

        def totals(items, taxes, **doc):
            data = {'items': [dict(item) for item in items], 'taxes': [dict(tax) for tax in taxes], **doc}
            calculate_taxes_and_totals(data)
            return (data['net_total'], [tax['tax_amount'] for tax in data['taxes']], data['grand_total'])

        vat = {'charge_type': 'On Net Total', 'account_head': self.vat, 'rate': 8.1}
        three = [{'qty': 1, 'rate': 395}] * 3
        # Per tax row over the whole document, then rounded: 95.985 -> 95.99. Not 3 x 32.00.
        self.assertEqual(totals(three, [vat]), (1185.0, [95.99], 1280.99))
        self.assertEqual(totals([{'qty': 3, 'rate': 395}], [vat]), (1185.0, [95.99], 1280.99))
        self.assertEqual(totals([{'qty': -1, 'rate': 395}] * 3, [vat]), (-1185.0, [-95.99], -1280.99))
        # A line amount is rounded on the decimal product, too.
        self.assertEqual(totals([{'qty': 3, 'rate': 0.335}], [])[0], 1.01)
        # A discount in per cent, then tax on the discounted net.
        self.assertEqual(totals([{'qty': 1, 'price_list_rate': 395, 'discount_percentage': 10}] * 3, [vat]),
                         (1066.5, [86.39], 1152.89))
        # A tax on a tax: 2 % on the 95.985 of the first row, each row rounded once.
        chained = [vat, {'charge_type': 'On Previous Row Amount', 'row_id': 1, 'account_head': self.vat, 'rate': 2}]
        self.assertEqual(totals(three, chained), (1185.0, [95.99, 1.92], 1282.91))
        # Another currency: the base amounts are rounded from the rounded document amounts.
        data = {'items': [dict(item) for item in three], 'taxes': [dict(vat)], 'conversion_rate': 0.9435}
        calculate_taxes_and_totals(data)
        self.assertEqual((data['grand_total'], data['base_grand_total'], data['taxes'][0]['base_tax_amount']),
                         (1280.99, 1208.61, 90.57))
        # The whole-unit figure is its own, named thing and never replaces the total.
        self.assertEqual((data['rounded_total'], data['rounding_adjustment']), (1281.0, 0.01))
        # Prices that include the tax: gross stays, net and tax add up to it.
        included = {**vat, 'included_in_print_rate': 1}
        self.assertEqual(totals([{'qty': 1, 'rate': 108.1}] * 3, [included]), (300.0, [24.3], 324.3))


if __name__ == '__main__':
    unittest.main()
