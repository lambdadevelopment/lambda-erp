"""Line deductions in percent and free of charge, with a reason, priced and
printed. Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set."""
import io
import unittest

from pypdf import PdfReader

from tests import test_document_requirements as fixtures
from api import services


class DeductionReasons(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)

    def quotation(self, **line):
        return services.create_document('quotation', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-27',
            'items': [{'item_code': 'S', 'qty': 4, 'warehouse': 'A', **line}]})

    def test_percent_deduction_with_reason(self):
        doc = self.quotation(price_list_rate=100, discount_percentage=15, discount_label='Kulanz – Lieferverzug')
        row = doc['items'][0]
        self.assertEqual((row['rate'], row['discount_amount'], row['amount']), (85, 15, 340))
        # A saved deduction keeps its agreed price.
        again = services.update_document('quotation', doc['name'], {'modified': doc['modified'], 'items': doc['items']})
        self.assertEqual(again['items'][0]['rate'], 85)
        from api import pdf
        text = PdfReader(io.BytesIO(pdf.generate_pdf('quotation', doc['name']))).pages[0].extract_text()
        self.assertIn('Kulanz – Lieferverzug', text)
        self.assertIn('400.00', text)   # the full price is printed
        self.assertIn('60.00', text)    # and the deduction for the line

    def test_free_of_charge_prints_the_full_price_and_the_reason(self):
        doc = self.quotation(price_list_rate=25, discount_percentage=100, discount_label='Gratis')
        row = doc['items'][0]
        self.assertEqual((row['rate'], row['discount_amount'], row['amount']), (0, 25, 0))
        self.assertEqual(doc['net_total'], 0)
        from api import pdf
        text = PdfReader(io.BytesIO(pdf.generate_pdf('quotation', doc['name']))).pages[0].extract_text()
        self.assertIn('Gratis', text)
        self.assertIn('100.00', text)

    def test_amount_deduction_still_works(self):
        doc = self.quotation(price_list_rate=50, discount_amount=5, discount_label='Rabatt')
        self.assertEqual((doc['items'][0]['rate'], doc['items'][0]['amount']), (45, 180))


if __name__ == '__main__':
    unittest.main()
