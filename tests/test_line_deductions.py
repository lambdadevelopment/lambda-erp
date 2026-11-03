"""A deduction on an offer line ("Abzüglich bereits verrechnet …"): the line keeps its
full price, the deduction comes off it, the PDF prints both, and it carries into the
order and the invoice."""
import os
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'line-deduction-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api.main import app
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db
from tests.test_pdf_templates import layout, pdf_text
from tests.test_pdf_texts import top_of

LABEL = 'Abzüglich bereits verrechnet (Rechnung SINV-0003)'


class LineDeductionTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'deduct@example.com', 'password': 'test-password-123',
                                                          'full_name': 'Deduction Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    def quotation(self, status=200, **phase2):
        # The fixtures' pricing rule gives M-PDF 10 % off; a line with its own
        # deduction keeps the price it was given.
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01', valid_till='2026-12-31',
            items=[dict(item_code='M-PDF', item_name='Phase 1: Analyse', qty=1, rate=11500, ignore_pricing_rule=1),
                   dict(item_code='M-PDF', item_name='Phase 2: Aufbau & Migration', qty=1,
                        **(phase2 or dict(rate=3500, discount_amount=2900, discount_label=LABEL)))]))
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_the_line_counts_the_net_and_the_pdf_prints_full_price_and_deduction(self):
        doc = self.quotation()
        line = doc['items'][1]
        self.assertEqual((line['price_list_rate'], line['discount_amount'], line['rate'], line['amount']),
                         (3500, 2900, 600, 600))
        self.assertEqual(line['discount_label'], LABEL)
        self.assertIsNone(line.get('pricing_rule'))
        self.assertEqual(doc['net_total'], 12100)
        self.assertEqual(doc['_validation']['warnings'], [])

        r = self.client.get(f"/api/documents/quotation/{doc['name']}/pdf")
        self.assertEqual(r.status_code, 200, r.text[:300])
        text = ' '.join(pdf_text(r.content).split())
        self.assertIn('3500.00', text)
        self.assertIn('−2900.00', text)
        self.assertIn(LABEL, text)
        self.assertNotIn(' 600.00', text)
        # The deduction is the sub-line right under its position.
        self.assertGreater(top_of(r.content, 'Phase 2'), top_of(r.content, 'Abzüglich'))

        # Saving it again (the form sends the net rate back) keeps it as it is.
        again = self.client.put(f"/api/documents/quotation/{doc['name']}", json={'items': doc['items']}).json()
        self.assertEqual((again['items'][1]['rate'], again['net_total']), (600, 12100))

    def test_a_deduction_larger_than_the_price_or_negative_is_refused(self):
        r = self.quotation(422, rate=2000, discount_amount=2900, discount_label=LABEL)
        self.assertIn('larger than the price', r['detail'])
        r = self.quotation(422, rate=2000, discount_amount=-5, discount_label=LABEL)
        self.assertIn('discount_amount', r['detail'])

    def test_it_carries_into_the_order_and_the_invoice(self):
        doc = self.quotation()
        r = self.client.post(f"/api/documents/quotation/{doc['name']}/submit")
        self.assertEqual(r.status_code, 200, r.text)
        for target, slug in (('Sales Order', 'sales-order'), ('Sales Invoice', 'sales-invoice')):
            r = self.client.post(f"/api/documents/quotation/{doc['name']}/convert", json={'target_doctype': target})
            self.assertEqual(r.status_code, 200, r.text)
            line = r.json()['items'][1]
            self.assertEqual((line['price_list_rate'], line['discount_amount'], line['rate'], line['discount_label']),
                             (3500, 2900, 600, LABEL))
            self.assertEqual(r.json()['net_total'], 12100)
            if target == 'Sales Order':
                so = r.json()
                self.assertEqual(self.client.post(f"/api/documents/sales-order/{so['name']}/submit").status_code, 200)
                r = self.client.post(f"/api/documents/sales-order/{so['name']}/convert",
                                     json={'target_doctype': 'Sales Invoice'})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual((r.json()['items'][1]['discount_label'], r.json()['net_total']), (LABEL, 12100))
                slug = 'sales-invoice'
            pdf = self.client.get(f"/api/documents/{slug}/{r.json()['name']}/pdf")
            self.assertEqual(pdf.status_code, 200, pdf.text[:300])
            self.assertIn(LABEL, ' '.join(pdf_text(pdf.content).split()))

    def test_a_layout_without_deductions_prints_the_net_and_is_flagged(self):
        created = self.client.post('/api/actions/create_pdf_template', json={'title': 'Bare', 'html': layout('Bare')}).json()
        published = self.client.post('/api/actions/publish_pdf_template', json={'template': created['template']}).json()
        self.assertIn('line_deduction', published['missing_texts'])
        doc = self.quotation()
        r = self.client.put(f"/api/documents/quotation/{doc['name']}", json={'pdf_template': created['template']})
        self.assertEqual([w['code'] for w in r.json()['_validation']['warnings']], ['pdf_line_deduction_not_printed'])
        pdf = self.client.get(f"/api/documents/quotation/{doc['name']}/pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text[:300])
        self.assertIn('600.00', pdf_text(pdf.content))

    def test_a_layout_that_drops_the_line_entirely_is_still_refused(self):
        bad = layout('Bad').replace('{{ "%.2f"|format(item.amount) }}', '{{ item.discount_label or "" }}')
        created = self.client.post('/api/actions/create_pdf_template', json={'title': 'Bad', 'html': bad}).json()
        doc = self.quotation()
        from api.pdf_templates import render_preview
        from api.pdf_contract import PDFError
        with self.assertRaises(PDFError):
            render_preview(created['template'], document=doc['name'])

    def test_migration_is_idempotent(self):
        from lambda_erp.database import _m045_line_discount_label
        _m045_line_discount_label(get_db())
        _m045_line_discount_label(get_db())
        for table in ('Quotation Item', 'Sales Order Item', 'Sales Invoice Item'):
            self.assertIn('discount_label', get_db()._get_table_columns(table))


if __name__ == '__main__':
    unittest.main()
