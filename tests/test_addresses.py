"""Several addresses per customer, and the invoice and delivery address of a
sales document.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set, like the other fixture-based suites.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.routers.masters import create_master_record, delete_master_record, update_master_record
from lambda_erp import permissions
from lambda_erp.exceptions import PermissionDenied, ValidationError
from lambda_erp.selling.quotation import make_sales_order
from lambda_erp.stock.delivery_note import make_delivery_note, make_sales_invoice_from_delivery_note


class Addresses(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.set_value('Customer', 'C', {'customer_name': 'General Hospital', 'address': 'Main Street 1',
                                            'city': 'Old Town', 'zip_code': '1000'})
        self.db.insert('Customer', {'name': 'D', 'customer_name': 'Second customer', 'default_currency': 'USD'})
        self.db.commit()
        self.accounting = self.address('Accounting', address='PO Box 12', zip_code='1001', city='Old Town',
                                       is_shipping=0, is_default_billing=1, email='invoices@example.com')
        self.store = self.address('Central store', address='Dock Road 5\nRamp 3', zip_code='1002', city='Harbour',
                                  is_billing=0, is_default_shipping=1, instructions='Deliveries 07:00-15:00 only')
        self.ward = self.address('Radiology', address='Main Street 1, Wing B', zip_code='1000', city='Old Town',
                                 recipient_name='General Hospital, Radiology', contact_person='Ward clerk')

    # -- helpers -------------------------------------------------------------
    def address(self, label, customer='C', **fields):
        return create_master_record('address', {'customer': customer, 'label': label, **fields})['name']

    def order(self, slug='sales-order', customer='C', **fields):
        date = 'posting_date' if slug in ('sales-invoice', 'delivery-note') else 'transaction_date'
        return services.create_document(slug, {
            'company': 'Test Co', 'customer': customer, date: '2026-11-12',
            'items': [{'item_code': 'S', 'qty': 2, 'rate': 20, 'warehouse': 'A'}], **fields})

    def addresses(self, doc):
        return (doc['billing_address'], doc['shipping_address'])

    # -- the master ------------------------------------------------------------
    def test_address_master(self):
        row = self.db.get_all('Address', filters={'name': self.store}, fields=['*'])[0]
        self.assertEqual((row['address_title'], row['name'][:5]), ('General Hospital, Central store, Harbour', 'ADDR-'))
        for fields, message in (({'label': ''}, 'label is required'),
                                ({'label': 'Empty'}, 'at least the street'),
                                ({'label': 'Nowhere', 'city': 'X', 'is_billing': 0, 'is_shipping': 0}, 'for invoices, for deliveries'),
                                ({'label': 'Ghost', 'city': 'X', 'customer': 'NOPE'}, 'existing Customer')):
            with self.assertRaisesRegex(ValidationError, message):
                create_master_record('address', {'customer': 'C', **fields})
        # One default of each kind per customer: a new one takes over.
        second = self.address('Second store', city='Harbour', is_billing=0, is_default_shipping=1)
        self.assertEqual(self.db.get_value('Address', self.store, 'is_default_shipping'), 0)
        self.assertEqual(self.db.get_value('Address', second, 'is_default_shipping'), 1)
        self.assertEqual(self.db.get_value('Address', self.accounting, 'is_default_billing'), 1)
        # A default must be usable for what it is the default of.
        update_master_record('address', second, {'is_shipping': 0, 'is_billing': 1})
        self.assertEqual(self.db.get_value('Address', second, 'is_default_shipping'), 0)
        # Another customer's defaults are untouched, and the title follows the label.
        other = self.address('Office', customer='D', city='Elsewhere', is_default_shipping=1)
        update_master_record('address', self.store, {'is_default_shipping': 1, 'label': 'Goods in'})
        self.assertEqual(self.db.get_value('Address', other, 'is_default_shipping'), 1)
        self.assertEqual(self.db.get_value('Address', self.store, 'address_title'), 'General Hospital, Goods in, Harbour')
        # Found by the customer's name when picking.
        found = services.list_master_records('address', search='General Hosp')
        self.assertIn(self.ward, [row['name'] for row in found['rows']])

    # -- on documents ----------------------------------------------------------
    def test_documents_take_defaults_and_keep_a_snapshot(self):
        order = self.order()
        self.assertEqual(self.addresses(order), (self.accounting, self.store))
        self.assertEqual(order['billing_address_display'], 'General Hospital\nPO Box 12\n1001 Old Town')
        self.assertEqual(order['shipping_address_display'], 'General Hospital\nDock Road 5\nRamp 3\n1002 Harbour')
        # Picking another address replaces the text; the master changing later does not.
        order = services.update_document('sales-order', order['name'], {'shipping_address': self.ward})
        self.assertEqual(order['shipping_address_display'],
                         'General Hospital, Radiology\nWard clerk\nMain Street 1, Wing B\n1000 Old Town')
        update_master_record('address', self.ward, {'address': 'New Wing 9'})
        again = services.update_document('sales-order', order['name'], {'remarks': 'unchanged address'})
        self.assertIn('Main Street 1, Wing B', again['shipping_address_display'])
        # The text can be changed for one document, and cleared to fill it again.
        typed = services.update_document('sales-order', order['name'], {
            'shipping_address_display': 'General Hospital\nTemporary entrance, Gate 4\n1000 Old Town'})
        self.assertEqual((typed['shipping_address'], typed['shipping_address_display'].splitlines()[1]),
                         (self.ward, 'Temporary entrance, Gate 4'))
        cleared = services.update_document('sales-order', order['name'], {'shipping_address_display': ''})
        self.assertIn('New Wing 9', cleared['shipping_address_display'])
        # An address the order names, without one on record.
        free = self.order(shipping_address_display='Dr. Example\nPractice at the lake\n2000 Lakeside')
        self.assertEqual((free['shipping_address'], free['billing_address']), (None, self.accounting))
        self.assertEqual(free['shipping_address_display'].splitlines()[0], 'Dr. Example')

    def test_address_rules_on_documents(self):
        foreign = self.address('Office', customer='D', city='Elsewhere')
        for fields, message in (({'shipping_address': foreign}, 'belongs to customer D'),
                                ({'shipping_address': 'ADDR-99999'}, 'does not exist'),
                                ({'shipping_address': self.accounting}, 'not marked for deliveries'),
                                ({'billing_address': self.store}, 'not marked for invoices')):
            with self.assertRaisesRegex(ValidationError, message):
                self.order(**fields)
        update_master_record('address', self.ward, {'disabled': 1})
        with self.assertRaisesRegex(ValidationError, 'is disabled'):
            self.order(shipping_address=self.ward)
        # A customer without addresses works as before: nothing is filled.
        plain = self.order(customer='D')
        self.assertEqual((plain['billing_address'], plain['billing_address_display']), (None, None))
        # Changing the customer of a draft drops the old customer's addresses.
        order = self.order()
        moved = services.update_document('sales-order', order['name'], {'customer': 'D'})
        self.assertEqual(self.addresses(moved), (None, None))
        self.assertIsNone(moved['shipping_address_display'])
        # An address in use cannot be deleted, only disabled.
        self.order()
        result = delete_master_record('address', self.accounting)
        self.assertEqual((result['status'], self.db.get_value('Address', self.accounting, 'disabled')), ('disabled', 1))

    def test_addresses_follow_the_order_through_delivery_and_invoice(self):
        services.submit_document('stock-entry', services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': 'S', 'qty': 10, 'basic_rate': 5, 't_warehouse': 'A'}]})['name'])
        quotation = self.order('quotation', shipping_address=self.ward,
                               billing_address_display='General Hospital\nc/o Shared services\n3000 Capital')
        services.submit_document('quotation', quotation['name'])
        order = make_sales_order(quotation['name'])
        order.delivery_date = '2026-12-30'
        order.save()
        self.assertEqual((order.shipping_address, order.billing_address_display.splitlines()[1]),
                         (self.ward, 'c/o Shared services'))
        order.submit()
        # The defaults change afterwards; documents made from the order still follow the order.
        update_master_record('address', self.store, {'address': 'Somewhere else'})
        note = make_delivery_note(order.name)
        note.save()
        self.assertEqual((note.shipping_address, note.billing_address_display.splitlines()[1]),
                         (self.ward, 'c/o Shared services'))
        note.submit()
        invoice = make_sales_invoice_from_delivery_note(note.name)
        invoice.save()
        self.assertEqual((invoice.shipping_address, invoice.billing_address_display.splitlines()[1]),
                         (self.ward, 'c/o Shared services'))
        invoice.submit()
        # Submitted documents keep what they said.
        update_master_record('address', self.ward, {'city': 'Moved'})
        self.assertIn('Old Town', self.db.get_value('Sales Invoice', invoice.name, 'shipping_address_display'))

    def test_pdfs_print_the_documents_addresses(self):
        from api import pdf
        captured = {}
        original = pdf._finish

        def capture(doctype, name, context, file, *args, **kwargs):
            captured[file] = context
            return original(doctype, name, context, file, *args, **kwargs)

        services.submit_document('stock-entry', services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': 'S', 'qty': 10, 'basic_rate': 5, 't_warehouse': 'A'}]})['name'])
        order = self.order()
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.save()
        note.submit()
        pdf._finish = capture
        try:
            self.assertTrue(pdf.generate_pdf('sales-order', order['name']).startswith(b'%PDF'))
            self.assertTrue(pdf.generate_pdf('delivery-note', note.name).startswith(b'%PDF'))
        finally:
            pdf._finish = original
        ordered, delivered = captured['document.html'], captured['delivery_note.html']
        self.assertEqual(ordered['address_lines'], ['General Hospital', 'PO Box 12', '1001 Old Town'])
        self.assertEqual(ordered['delivery_lines'][1:3], ['Dock Road 5', 'Ramp 3'])
        self.assertEqual(delivered['address_lines'][1], 'Dock Road 5')
        self.assertEqual((delivered['delivery_lines'], delivered['delivery_instructions']),
                         ([], 'Deliveries 07:00-15:00 only'))
        # Without document addresses the customer's main address prints, as before.
        plain = self.order(customer='D')
        pdf._finish = capture
        try:
            pdf.generate_pdf('sales-order', plain['name'])
        finally:
            pdf._finish = original
        self.assertEqual((captured['document.html']['address_lines'], captured['document.html']['delivery_lines']), ([], []))

    def test_addresses_follow_record_scope(self):
        foreign = self.address('Office', customer='D', city='Elsewhere')
        principal = {'name': 'U-rep', 'user_id': 'U-rep', 'role': 'manager',
                     'restrictions': permissions.load_restrictions(None, {'customer': ['C']})}
        with permissions.acting_as(principal):
            listed = services.list_master_records('address')
            names = [row['name'] for row in listed['rows']]
            self.assertIn(self.ward, names)
            self.assertNotIn(foreign, names)
            with self.assertRaises(PermissionDenied):
                create_master_record('address', {'customer': 'D', 'label': 'Mine now', 'city': 'X'})
            self.assertTrue(create_master_record('address', {'customer': 'C', 'label': 'Lab', 'city': 'Old Town'}))


if __name__ == '__main__':
    unittest.main()
