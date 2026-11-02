"""An offer's printed texts: intro (above the lines) and closing text / terms (`remarks`),
their global defaults, placeholders, and the warning when a layout drops one."""
import os
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'pdf-text-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api.main import app
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db
from tests.test_pdf_templates import layout, pdf_text


def top_of(pdf: bytes, needle: str) -> float:
    """Height of `needle` on page 1 (PDF points from the bottom)."""
    import pypdfium2 as pdfium
    page = pdfium.PdfDocument(pdf)[0]
    textpage = page.get_textpage()
    found = textpage.search(needle).get_next()
    assert found, needle
    return textpage.get_charbox(found[0])[3]


class PdfTextTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'texts@example.com', 'password': 'test-password-123',
                                                          'full_name': 'Text Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    def act(self, name, status=200, **args):
        r = self.client.post('/api/actions/' + name, json=args)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def quotation(self, **extra):
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01',
            valid_till='2026-10-31', items=[dict(item_code='M-PDF', item_name='Excavator Example', qty=2, rate=150)],
            **extra))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def pdf(self, name):
        r = self.client.get(f'/api/documents/quotation/{name}/pdf')
        self.assertEqual(r.status_code, 200, r.text[:300])
        return pdf_text(r.content)

    def test_intro_prints_above_the_lines_and_remarks_after_with_placeholders(self):
        doc = self.quotation(intro_text='Sehr geehrte Damen und Herren von {{ customer_name }}',
                             remarks='Gültig bis {{valid_till}}. {{ unknown_thing }} bleibt.')
        r = self.client.get(f"/api/documents/quotation/{doc['name']}/pdf")
        text = ' '.join(pdf_text(r.content).split())
        self.assertIn(f"Sehr geehrte Damen und Herren von {doc['customer_name']}", text)
        self.assertIn('{{ unknown_thing }} bleibt', text)
        # Top to bottom on the page (text extraction order isn't layout order).
        intro, line, closing = (top_of(r.content, s) for s in
                                ('Sehr geehrte', 'Excavator Example', 'Gültig bis 2026-10-31'))
        self.assertGreater(intro, line)
        self.assertGreater(line, closing)
        self.assertEqual(doc['_validation']['warnings'], [])

    def test_defaults_are_copied_into_new_offers_only(self):
        before = self.quotation(remarks='Alte Konditionen')
        self.act('set_pdf_text_defaults', intro_text='Gerne offerieren wir', remarks='Zahlbar in 30 Tagen')
        self.assertEqual(self.act('get_pdf_text_defaults')['intro_text'], 'Gerne offerieren wir')
        fresh = self.quotation()
        self.assertEqual((fresh['intro_text'], fresh['remarks']), ('Gerne offerieren wir', 'Zahlbar in 30 Tagen'))
        own = self.quotation(intro_text='', remarks='Eigene Konditionen')
        self.assertEqual((own['intro_text'], own['remarks']), ('', 'Eigene Konditionen'))
        # Existing offers keep their texts, also when saved again.
        r = self.client.put(f"/api/documents/quotation/{before['name']}", json={'valid_till': '2026-11-30'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['intro_text'], r.json()['remarks']), (None, 'Alte Konditionen'))
        # A later default change doesn't touch offers that already copied it.
        self.act('set_pdf_text_defaults', intro_text='')
        self.assertEqual(self.act('get_pdf_text_defaults')['intro_text'], '')
        self.assertEqual(self.act('get_pdf_text_defaults')['remarks'], 'Zahlbar in 30 Tagen')
        reloaded = self.client.get(f"/api/documents/quotation/{fresh['name']}").json()
        self.assertEqual(reloaded['intro_text'], 'Gerne offerieren wir')

    def test_layouts_that_drop_a_text_are_flagged(self):
        created = self.act('create_pdf_template', title='Bare', html=layout('Bare'))
        published = self.act('publish_pdf_template', template=created['template'])
        self.assertEqual(published['missing_texts'], ['intro_text', 'remarks'])
        doc = self.quotation(pdf_template=created['template'], intro_text='Hallo', remarks='Tschüss')
        codes = [w['code'] for w in doc['_validation']['warnings']]
        self.assertEqual(codes, ['pdf_intro_text_not_printed', 'pdf_remarks_not_printed'])
        self.assertEqual(doc['_validation']['warnings'][0]['template'], 'Bare')
        # Printing both clears the warnings.
        full = layout('Full', '<div>{{ intro_html|safe }}</div><div>{{ remarks_html|safe }}</div>')
        self.act('update_pdf_template', template=created['template'], html=full, publish=True)
        again = self.client.get(f"/api/documents/quotation/{doc['name']}").json()
        self.assertEqual(again['_validation']['warnings'], [])
        self.assertIn('Hallo', self.pdf(doc['name']))

    def test_permissions(self):
        from api.services import registered_action_allowed
        self.assertTrue(registered_action_allowed('get_pdf_text_defaults', 'viewer'))
        self.assertFalse(registered_action_allowed('set_pdf_text_defaults', 'viewer'))
        self.assertTrue(registered_action_allowed('set_pdf_text_defaults', 'manager'))
        self.assertTrue(registered_action_allowed('set_pdf_text_defaults', 'public_manager'))

    def test_migration_adds_the_column_and_keeps_remarks(self):
        from lambda_erp.database import _m044_quotation_intro_text
        doc = self.quotation(remarks='Bestehende Konditionen')
        _m044_quotation_intro_text(get_db())
        _m044_quotation_intro_text(get_db())
        row = get_db().get_value('Quotation', doc['name'], ['remarks', 'intro_text'])
        self.assertEqual(row['remarks'], 'Bestehende Konditionen')


if __name__ == '__main__':
    unittest.main()
