"""PDF template fonts: what a PDF uses, font files as chat uploads and template assets.

Self-contained: the font under test is built here with fontTools, so the
results don't depend on the fonts installed on the machine running the tests.
"""
import io
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('JWT_SECRET_KEY', 'pdf-font-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api import pdf_fonts
from api.main import app
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db
from tests.test_pdf_templates import layout


def build_font(family='Test Brand Sans', style='Bold', fs_type=0, flavor=None):
    """A small TrueType font whose letters and digits are boxes."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    chars = [chr(c) for c in range(0x20, 0x7f)]
    names = ['.notdef'] + [f'u{ord(c):04X}' for c in chars]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(c): f'u{ord(c):04X}' for c in chars})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name not in ('.notdef', 'u0020'):
            pen.moveTo((50, 0)); pen.lineTo((50, 700)); pen.lineTo((550, 700)); pen.lineTo((550, 0)); pen.closePath()
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (600, 50) for name in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({'familyName': family, 'styleName': style})
    fb.setupOS2(fsType=fs_type, usWeightClass=700 if style == 'Bold' else 400)
    fb.setupPost()
    if flavor:
        fb.font.flavor = flavor
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


class PdfFontTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'font@example.com', 'password': 'test-password-123',
                                                          'full_name': 'Font Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.user = self.client.get('/api/auth/me').json()
        seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    def act(self, action, **args):
        r = self.client.post('/api/actions/' + action, json=args)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def upload(self, session_id, filename, data, content_type='application/octet-stream'):
        r = self.client.post('/api/chat/attachments', data={'session_id': session_id},
                             files={'file': (filename, data, content_type)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def test_font_files_are_identified_with_their_licence_flag(self):
        info = pdf_fonts.font_file_info(build_font())
        self.assertEqual((info['family'], info['style'], info['embedding']), ('Test Brand Sans', 'Bold', 'installable'))
        self.assertEqual(pdf_fonts.font_file_info(build_font(flavor='woff2'))['format'], 'woff2')
        self.assertIn('restricted', pdf_fonts.font_file_info(build_font(fs_type=2))['embedding'])
        with self.assertRaises(ValueError):
            pdf_fonts.font_file_info(b'not a font at all')

    def test_availability_and_the_reference_font_line(self):
        families = ('DejaVu Sans', 'Liberation Sans')
        subs = {'Helvetica': 'Liberation Sans', 'Frutiger': 'DejaVu Sans', 'NoSuchFontFamily': 'DejaVu Sans'}
        with patch.object(pdf_fonts, 'installed_families', return_value=families), \
             patch.object(pdf_fonts, 'renders_as', side_effect=lambda f: subs.get(f, 'DejaVu Sans')):
            self.assertEqual(pdf_fonts.availability('liberation sans')['available'], 'installed')
            self.assertEqual(pdf_fonts.availability('Helvetica'),
                             {**pdf_fonts.availability('Helvetica'), 'available': 'metric_compatible',
                              'renders_as': 'Liberation Sans'})
            self.assertEqual(pdf_fonts.availability('Frutiger')['available'], 'missing')
            line = pdf_fonts.fonts_line()
            self.assertIn('DejaVu Sans, Liberation Sans', line)
            self.assertIn('Helvetica → Liberation Sans', line)
            reference = self.act('get_pdf_template', template='standard')['context_reference']
            self.assertIn('Helvetica → Liberation Sans', reference)
            self.assertNotIn('<<FONTS>>', reference)

    def test_fonts_reach_a_template_through_the_chat_and_print(self):
        from api import chat
        from api.attachments import build_multimodal_content, get_attachments_by_ids
        sid = chat.create_session(user_id=self.user['name'])['id']
        font_id = self.upload(sid, 'Brand Sans Bold.woff2', build_font(flavor='woff2'))
        note = build_multimodal_content(get_attachments_by_ids([font_id], self.user['name'])[0])
        self.assertEqual(note['type'], 'text')
        self.assertIn('Test Brand Sans', note['text'])

        tid = self.act('create_pdf_template', title='Brand')['template']
        added = chat._handle_add_attachment_to_pdf_template(
            {'template': tid, 'attachment_id': font_id}, self.user, sid, self.user['name'])
        self.assertEqual(added['asset'], 'Brand-Sans-Bold.woff2')
        self.assertEqual(added['font']['family'], 'Test Brand Sans')
        self.assertIn('url("Brand-Sans-Bold.woff2")', added['font']['css'])
        css = added['font']['css'] + ' body { font-family: "Test Brand Sans"; }'
        self.act('update_pdf_template', template=tid, html=layout('Branded', f'<style>{css}</style>'))
        name = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01',
            items=[dict(item_code='M-PDF', item_name='Excavator Example', qty=1, rate=10)])).json()['name']
        from api.pdf_templates import render_preview
        rendered, _, _ = render_preview(tid, document=name)

        # inspect_pdf_fonts reads it back from a PDF uploaded to the chat.
        pdf_id = self.upload(sid, 'reference.pdf', rendered.data, 'application/pdf')
        with patch.object(pdf_fonts, 'installed_families', return_value=('DejaVu Sans',)), \
             patch.object(pdf_fonts, 'renders_as', return_value='DejaVu Sans'):
            fonts = chat._handle_inspect_pdf_fonts({'attachment_id': pdf_id}, sid, self.user['name'])['fonts']
            brand = next(f for f in fonts if f['family'] == 'Test Brand Sans')
            self.assertTrue(brand['embedded'])
            self.assertEqual((brand['available'], brand['renders_as']), ('missing', 'DejaVu Sans'))
            font_file = chat._handle_inspect_pdf_fonts({'attachment_id': font_id}, sid, self.user['name'])
            self.assertEqual(font_file['fonts'][0]['family'], 'Test Brand Sans')

        # Only fonts and images become assets; another user's upload is out of reach.
        self.assertIn('error', chat._handle_add_attachment_to_pdf_template(
            {'template': tid, 'attachment_id': pdf_id}, self.user, sid, self.user['name']))
        self.assertIn('error', chat._handle_inspect_pdf_fonts({'attachment_id': pdf_id}, sid, 'USR-someone-else'))
        # A file named .ttf that isn't a font is refused as an asset.
        bogus = self.upload(sid, 'fake.ttf', b'%PDF-1.4 not a font')
        self.assertIn('Not a readable font', chat._handle_add_attachment_to_pdf_template(
            {'template': tid, 'attachment_id': bogus}, self.user, sid, self.user['name'])['error'])

    def test_permissions(self):
        from api.routers import mcp
        from api.tool_permissions import tool_allowed
        self.assertTrue(tool_allowed('inspect_pdf_fonts', 'viewer'))
        self.assertTrue(tool_allowed('add_attachment_to_pdf_template', 'manager'))
        self.assertFalse(tool_allowed('add_attachment_to_pdf_template', 'public_manager'))
        self.assertFalse(tool_allowed('add_attachment_to_pdf_template', 'viewer'))
        names = {t['name'] for t in mcp._tools('admin')}
        self.assertFalse({'inspect_pdf_fonts', 'add_attachment_to_pdf_template'} & names)


if __name__ == '__main__':
    unittest.main()
