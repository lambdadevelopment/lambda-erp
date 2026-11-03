"""PDF templates beyond offers: sales orders, invoices, purchase orders, delivery notes,
proposals and reservations, each with its own built-in layout, default and context."""
import os
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'pdf-all-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api import pdf_templates
from api.main import app
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db
from tests.test_pdf_templates import pdf_text

SLUGS = {'Sales Order': 'sales-order', 'Sales Invoice': 'sales-invoice', 'Purchase Order': 'purchase-order',
         'Delivery Note': 'delivery-note', 'Proposal': 'proposal', 'Reservation': 'reservation'}


def marked(html, marker):
    """The built-in layout with a marker right after <body>."""
    return html.replace('<body>', f'<body><p>{marker}</p>', 1)


class AllDocumentTemplateTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'all@example.com', 'password': 'test-password-123',
                                                          'full_name': 'All Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.docs = seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    def act(self, name, status=200, **args):
        r = self.client.post('/api/actions/' + name, json=args)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def pdf(self, slug, name):
        r = self.client.get(f'/api/documents/{slug}/{name}/pdf')
        self.assertEqual(r.status_code, 200, r.text[:300])
        return ' '.join(pdf_text(r.content).split())

    def invoice(self):
        r = self.client.post('/api/documents/sales-invoice', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', posting_date='2026-10-01', due_date='2026-10-31',
            items=[dict(item_code='M-PDF', item_name='Excavator Example', qty=1, rate=100, ignore_pricing_rule=1)]))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def template(self, doctype, marker, **extra):
        standard = self.act('get_pdf_template', template='standard', doctype=doctype)
        self.assertEqual(standard['doctype'], doctype)
        created = self.act('create_pdf_template', title=marker, doctype=doctype, **extra)
        if 'html' not in extra and 'based_on' not in extra:
            self.act('update_pdf_template', template=created['template'], html=marked(standard['html'], marker))
        return created['template']

    def test_every_type_prints_with_its_own_published_default(self):
        for doctype, slug in SLUGS.items():
            with self.subTest(doctype=doctype):
                tid = self.template(doctype, f'MARK-{slug}')
                reference = self.act('get_pdf_template', template=tid)['context_reference']
                self.assertIn(f'reference for {doctype} layouts', reference)
                self.act('publish_pdf_template', template=tid)
                self.act('set_default_pdf_template', doctype=doctype, template=tid)
                self.assertIn(f'MARK-{slug}', self.pdf(slug, self.docs[doctype]))
        # The offer still prints with its own default.
        self.assertNotIn('MARK-', self.pdf('quotation', self.docs['Quotation']))
        listed = self.act('list_pdf_templates', doctype='sales-invoice')
        self.assertEqual([t['title'] for t in listed['templates'] if not t['standard']], ['MARK-sales-invoice'])

    def test_built_in_layouts_per_type(self):
        delivery = self.pdf('delivery-note', self.docs['Delivery Note'])
        self.assertIn('Excavator Example', delivery)
        self.assertIn('Ada Example', delivery)
        self.assertNotIn('Grand Total', delivery)  # no prices on a delivery note
        self.assertIn('Delivery Note', self.act('get_pdf_template', template='standard',
                                                 doctype='Delivery Note')['context_reference'])
        # The proposal layout includes a built-in partial; copies of it render too.
        tid = self.template('Proposal', 'MARK-proposal')
        self.assertIn('_proposal_totals.html', self.act('get_pdf_template', template=tid)['draft_html'])
        from api.pdf_templates import render_preview
        rendered, name, _ = render_preview(tid, document=self.docs['Proposal'])
        self.assertIn('MARK-proposal', pdf_text(rendered.data))

    def test_submit_pins_and_archives_orders_invoices_and_delivery_notes(self):
        tid = self.template('Sales Order', 'MARK-ISSUED')
        self.act('publish_pdf_template', template=tid)
        r = self.client.post('/api/documents/sales-order', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01', delivery_date='2026-10-10',
            pdf_template=tid, items=[dict(item_code='M-PDF', item_name='Excavator Example', qty=1, rate=100,
                                          warehouse='W-PDF', ignore_pricing_rule=1)]))
        self.assertEqual(r.status_code, 200, r.text)
        name = r.json()['name']
        r = self.client.post(f'/api/documents/sales-order/{name}/submit')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['pdf_template_version'].startswith(tid + '@'))
        archived = get_db().sql('SELECT template FROM "PDF Archive" WHERE doctype = ? AND document_name = ?',
                                ['Sales Order', name])
        self.assertEqual([row['template'] for row in archived], [tid])
        issued = self.pdf('sales-order', name)
        self.assertIn('MARK-ISSUED', issued)
        self.assertNotIn('To Deliver and Bill', issued)  # the status changes later; the archive doesn't print it
        # Later edits don't change the issued PDF.
        self.act('update_pdf_template', template=tid, html=marked(
            self.act('get_pdf_template', template='standard', doctype='Sales Order')['html'], 'MARK-LATER'), publish=True)
        self.assertIn('MARK-ISSUED', self.pdf('sales-order', name))
        self.assertEqual(self.act('get_pdf_template', template=tid)['used_by_documents'], 1)
        # A used template is kept; its version lists the order.
        self.act('delete_pdf_template', 422, template=tid)
        self.assertEqual(self.act('get_pdf_template', template=tid)['versions'][0]['example_document'], name)

    def test_a_layout_can_start_from_another_types_template(self):
        offer = self.template('Quotation', 'MARK-OFFER-STYLE')
        invoice = self.act('create_pdf_template', title='Invoice', doctype='Sales Invoice', based_on=offer)
        self.assertIn('MARK-OFFER-STYLE', self.act('get_pdf_template', template=invoice['template'])['draft_html'])
        rendered, _, _ = pdf_templates.render_preview(invoice['template'], document=self.docs['Sales Invoice'])
        self.assertIn('MARK-OFFER-STYLE', pdf_text(rendered.data))
        # A template only applies to its own type.
        self.act('publish_pdf_template', template=offer)
        r = self.client.put(f"/api/documents/sales-order/{self.docs['Sales Order']}", json={'pdf_template': offer})
        self.assertEqual(r.status_code, 422, r.text)

    def test_text_defaults_per_type(self):
        self.act('set_pdf_text_defaults', doctype='Sales Invoice', intro_text='Wir verrechnen {{ customer_name }}',
                 remarks='Zahlbar bis {{ due_date }}')
        self.assertEqual(self.act('get_pdf_text_defaults')['intro_text'], '')  # offers keep their own
        invoice = self.invoice()
        self.assertEqual(invoice['intro_text'], 'Wir verrechnen {{ customer_name }}')
        text = self.pdf('sales-invoice', invoice['name'])
        self.assertIn('Wir verrechnen Ada Example', text)
        self.assertIn('Zahlbar bis 2026-10-31', text)
        self.act('get_pdf_text_defaults', 422, doctype='Proposal')

    def test_required_plugin_content_is_flagged(self):
        from api import pdf
        def qr_provider(doctype, name, context):
            return {'qr_bill_img': 'data:image/png;base64,iVBORw0KGgo='} if doctype == 'Sales Invoice' else None
        pdf._pdf_context_providers.append(qr_provider)
        pdf_templates.register_pdf_template_requirement('Sales Invoice', 'qr_bill_img', 'QR bill')
        try:
            tid = self.template('Sales Invoice', 'MARK-NO-QR')
            published = self.act('publish_pdf_template', template=tid)
            self.assertEqual(published['missing_variables'], [{'variable': 'qr_bill_img', 'label': 'QR bill'}])
            self.act('publish_pdf_template', template=tid)
            r = self.client.put(f"/api/documents/sales-invoice/{self.invoice()['name']}", json={'pdf_template': tid})
            self.assertEqual(r.status_code, 200, r.text)
            codes = [(w['code'], w.get('variable')) for w in r.json()['_validation']['warnings']]
            self.assertIn(('pdf_variable_not_printed', 'qr_bill_img'), codes)
            # Offers aren't affected.
            self.assertNotIn('qr_bill_img', [x['variable'] for x in
                                             self.act('get_pdf_template', template='standard')['required']])
        finally:
            pdf._pdf_context_providers.remove(qr_provider)
            pdf_templates._REQUIRED_VARIABLES.pop('Sales Invoice', None)

    def test_reservation_and_delivery_layouts_must_show_number_and_party(self):
        minimal = ('<html><body><p>{{ doc.name }} {{ party_name }} {{ pdf_state_notice }}</p>'
                   '{% for item in items %}{{ item.item_name }}{% endfor %}</body></html>')
        for doctype in ('Reservation', 'Delivery Note'):
            with self.subTest(doctype=doctype):
                tid = self.template(doctype, f'MIN-{doctype}', html=minimal)
                rendered, _, _ = pdf_templates.render_preview(tid, document=self.docs[doctype])
                self.assertIn('Ada Example', pdf_text(rendered.data))
                broken = self.template(doctype, f'BROKEN-{doctype}', html='<html><body>{{ doc.name }}</body></html>')
                from api.pdf_contract import PDFError
                with self.assertRaises(PDFError):
                    pdf_templates.render_preview(broken, document=self.docs[doctype])

    def test_migration_is_idempotent(self):
        from lambda_erp.database import _m046_pdf_templates_all_documents
        _m046_pdf_templates_all_documents(get_db())
        _m046_pdf_templates_all_documents(get_db())
        for table in ('Sales Order', 'Sales Invoice', 'Purchase Order', 'Delivery Note'):
            self.assertTrue({'pdf_template', 'pdf_template_version', 'intro_text'} <= get_db()._get_table_columns(table))
        for table in ('Proposal', 'Reservation'):
            self.assertIn('pdf_template', get_db()._get_table_columns(table))


if __name__ == '__main__':
    unittest.main()
