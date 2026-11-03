"""Document list search also matches the lines: an offer, order or invoice is found
by a line's item code, line name or description, alongside its own columns."""
import os
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'line-search-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api.main import app
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db


class LineSearchTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'search@example.com', 'password': 'test-password-123',
                                                          'full_name': 'Search Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        seed_pdf_documents()
        self.migration = self.offer('Phase 2: Aufbau & Migration', 'Datenübernahme aus dem Altsystem')
        self.analysis = self.offer('Phase 1: Analyse', 'Workshops vor Ort')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    def offer(self, line_name, description):
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01',
            items=[dict(item_code='M-PDF', item_name=line_name, description=description, qty=1, rate=100)]))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['name']

    def search(self, slug, text, fields='name,customer,customer_name'):
        r = self.client.get(f'/api/documents/{slug}', params={'search': text, 'search_fields': fields, 'limit': 50})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        rows = body['rows'] if isinstance(body, dict) else body
        names = {row['name'] for row in rows}
        if isinstance(body, dict) and 'total' in body:
            self.assertEqual(body['total'], len(names))
        return names

    def test_offers_are_found_by_line_name_description_and_item_code(self):
        self.assertEqual(self.search('quotation', 'migration'), {self.migration})
        self.assertEqual(self.search('quotation', 'ALTSYSTEM'), {self.migration})
        self.assertEqual(self.search('quotation', 'workshops'), {self.analysis})
        # The item code is on every line, the fixture offer's too.
        self.assertTrue({self.migration, self.analysis, 'PDF-Quotation'} <= self.search('quotation', 'm-pdf'))
        # The document's own columns still match, and nothing matches nothing.
        self.assertEqual(self.search('quotation', self.analysis), {self.analysis})
        self.assertEqual(self.search('quotation', 'no such thing anywhere'), set())

    def test_other_documents_with_lines(self):
        for slug in ('sales-order', 'sales-invoice', 'delivery-note', 'pos-invoice'):
            self.assertIn(f"PDF-{slug.title().replace('Pos-', 'POS-')}", self.search(slug, 'excavator'), slug)
        for slug in ('purchase-order', 'purchase-invoice', 'purchase-receipt'):
            self.assertIn(f"PDF-{slug.title()}", self.search(slug, 'machine hire', 'name,supplier,supplier_name'), slug)
        self.assertIn('PDF-Stock-Entry', self.search('stock-entry', 'excavator', 'name'))
        self.assertIn('PDF-Subscription', self.search('subscription', 'm-pdf', 'name,party'))
        # Documents without lines are unaffected.
        self.assertEqual(self.search('payment-entry', 'excavator', 'name,party'), set())

    def test_the_service_layer_matches_lines_too(self):
        from api.services import list_documents
        names = {r['name'] for r in list_documents('quotation', filters={'search': 'migration',
                                                                           'search_fields': ['name']})}
        self.assertEqual(names, {self.migration})


if __name__ == '__main__':
    unittest.main()
