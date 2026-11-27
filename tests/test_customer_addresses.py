"""Earlier addresses of a customer, customer notes on delivery notes and
invoices, and department / STOP code on the printed delivery note.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import io
import unittest

from pypdf import PdfReader

from tests import test_document_requirements as fixtures
from api import services
from api.order_actions import list_customer_addresses
from api.routers.masters import create_master_record, update_master_record
from lambda_erp.stock.delivery_note import make_delivery_note, make_sales_invoice_from_delivery_note


class CustomerAddresses(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.set_value('Customer', 'C', {'customer_name': 'Test Hospital', 'address': 'Main Street 1',
                                            'city': 'Old Town', 'zip_code': '1000'})
        self.db.commit()
        self.store = create_master_record('address', {
            'customer': 'C', 'label': 'Central store', 'address': 'Dock Road 5', 'zip_code': '1002',
            'city': 'Harbour', 'is_billing': 0, 'is_default_shipping': 1})['name']
        services.submit_document('stock-entry', services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': 'S', 'qty': 50, 'basic_rate': 5, 't_warehouse': 'A'}]})['name'])

    def order(self, date='2026-11-12', **fields):
        return services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': date,
            'items': [{'item_code': 'S', 'qty': 2, 'rate': 20, 'warehouse': 'A'}], **fields})

    def test_saved_and_earlier_one_off_addresses(self):
        radiology = 'Test Hospital\nRadiologie, Haus B\nSTOP 04\nMain Street 1\n1000 Old Town'
        self.order('2026-11-02', shipping_address_display=radiology)
        self.order('2026-11-20', shipping_address_display='Test Hospital\nRadiologie,  Haus B\nSTOP 04\nMain Street 1\n1000 Old Town')
        self.order('2026-11-10', shipping_address_display='Test Hospital\nKardiologie\nMain Street 1\n1000 Old Town')
        self.order('2026-11-11')  # takes the saved default: not a one-off
        book = list_customer_addresses({'customer': 'C'})
        self.assertEqual([(a['label'], a['for_deliveries'], a['default_delivery']) for a in book['saved']],
                         [('Central store', True, True)])
        used = book['used_delivery_addresses']
        # Same text with other spacing counts as one; newest first; the saved address is not repeated.
        self.assertEqual([(u['text'].splitlines()[1], u['times'], u['last_used']) for u in used],
                         [('Radiologie, Haus B', 2, '2026-11-20'), ('Kardiologie', 1, '2026-11-10')])
        self.assertEqual(used[0]['last_document']['doctype'], 'Sales Order')
        with self.assertRaises(Exception):
            list_customer_addresses({'customer': 'NOPE'})

    def test_customer_notes_are_copied_and_printed_with_department_and_stop_code(self):
        update_master_record('customer', 'C', {
            'delivery_note_text': 'Rechnung per E-Mail an rechnungen@hospital.example',
            'invoice_note_text': 'Bitte Bestellnummer im Zahlungszweck angeben'})
        order = self.order(shipping_address_display='Test Hospital\nAbteilung Radiologie\nSTOP 04\nMain Street 1\n1000 Old Town')
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.save()
        self.assertEqual(note.customer_note, 'Rechnung per E-Mail an rechnungen@hospital.example')
        note.submit()
        invoice = make_sales_invoice_from_delivery_note(note.name)
        invoice.save()
        self.assertEqual(invoice.customer_note, 'Bitte Bestellnummer im Zahlungszweck angeben')
        # Changed for one document, the change stays; the customer's later edit reaches new documents only.
        invoice.customer_note = 'Einmalig: Rechnung per Post'
        invoice.save()
        update_master_record('customer', 'C', {'delivery_note_text': 'Neuer Hinweis'})
        self.assertEqual(self.db.get_value('Delivery Note', note.name, 'customer_note'),
                         'Rechnung per E-Mail an rechnungen@hospital.example')
        self.assertEqual(self.db.get_value('Sales Invoice', invoice.name, 'customer_note'), 'Einmalig: Rechnung per Post')

        from api import pdf
        text = PdfReader(io.BytesIO(pdf.generate_pdf('delivery-note', note.name))).pages[0].extract_text()
        for expected in ('Abteilung Radiologie', 'STOP 04', 'Rechnung per E-Mail an rechnungen@hospital.example'):
            self.assertIn(expected, text)
        text = PdfReader(io.BytesIO(pdf.generate_pdf('sales-invoice', invoice.name))).pages[0].extract_text()
        self.assertIn('Einmalig: Rechnung per Post', text)

    def test_no_note_no_block(self):
        order = self.order()
        services.submit_document('sales-order', order['name'])
        note = make_delivery_note(order['name'])
        note.save()
        self.assertIsNone(note.get('customer_note'))


if __name__ == '__main__':
    unittest.main()
