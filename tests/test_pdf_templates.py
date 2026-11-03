"""User-editable PDF templates: editing, publishing, pinning at submit, archive, sandbox."""
import base64
import os
import unittest

os.environ.setdefault('JWT_SECRET_KEY', 'pdf-template-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')
from fastapi.testclient import TestClient

from api.main import app
from api.pdf_templates import DOCTYPES
from lambda_erp.database import get_db
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db

# 1x1 transparent PNG
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')


def layout(marker, extra=''):
    """A minimal custom layout that prints everything the PDF contract requires."""
    return f'''<html><head><style>@page {{ size: A4; margin: 15mm }}</style></head><body>
<h1>{marker} {{{{ title }}}} {{{{ doc.name }}}}</h1>
{{% if pdf_state_notice %}}<p>{{{{ pdf_state_notice }}}}</p>{{% endif %}}
<p>{{{{ party_name }}}} {{{{ currency }}}}</p>
<table>{{% for item in items %}}<tr><td>{{{{ item.item_name }}}}</td><td>{{{{ "%.2f"|format(item.amount) }}}}</td></tr>{{% endfor %}}</table>
<p>Net {{{{ "%.2f"|format(doc.net_total) }}}} Total {{{{ "%.2f"|format(doc.grand_total) }}}}</p>
{extra}</body></html>'''


def pdf_text(data):
    import io
    from pypdf import PdfReader
    return ''.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)


class PdfTemplateTests(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email': 'tpl@example.com', 'password': 'test-password-123',
                                                          'full_name': 'Template Admin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.docs = seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        get_db().conn.close()

    # -- helpers --------------------------------------------------------

    def act(self, name, status=200, **args):
        r = self.client.post('/api/actions/' + name, json=args)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def template(self, title='Branded', html=None, publish=True):
        created = self.act('create_pdf_template', title=title, **({'html': html} if html else {}))
        if publish:
            self.act('publish_pdf_template', template=created['template'])
        return created['template']

    def quotation(self, **extra):
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', currency='CHF', transaction_date='2026-10-01',
            items=[dict(item_code='M-PDF', item_name='Excavator Example', qty=2, rate=150)], **extra))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['name']

    def submit(self, name, status=200):
        r = self.client.post(f'/api/documents/quotation/{name}/submit')
        self.assertEqual(r.status_code, status, r.text)
        return r

    def pdf(self, name):
        r = self.client.get(f'/api/documents/quotation/{name}/pdf')
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.content

    def view(self, **args):
        r = self.client.post('/api/actions/view_pdf_pages', json=args)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def stored(self, name):
        return get_db().get_value('Quotation', name, ['pdf_template', 'pdf_template_version'])

    # -- editing --------------------------------------------------------

    def test_new_template_copies_standard_and_edits_leave_no_versions(self):
        listed = self.act('list_pdf_templates')
        self.assertEqual([t['template'] for t in listed['templates']], ['standard'])
        self.assertEqual(listed['defaults'], {dt: 'standard' for dt in DOCTYPES})
        created = self.act('create_pdf_template', title='Branded')
        tid = created['template']
        self.assertFalse(created['published'])
        detail = self.act('get_pdf_template', template=tid)
        standard = self.act('get_pdf_template', template='standard')
        self.assertEqual(detail['draft_html'], standard['html'])
        self.assertIn('items', detail['context_reference'])
        stamp = detail['draft_modified']
        for i in range(25):
            self.act('update_pdf_template', template=tid, html=layout(f'Round {i}'))
        detail = self.act('get_pdf_template', template=tid)
        self.assertIn('Round 24', detail['draft_html'])
        self.assertEqual(detail['versions'], [])
        self.assertEqual(get_db().sql('SELECT COUNT(*) AS n FROM "PDF Template Version"')[0]['n'], 0)
        # A stale editor doesn't overwrite a newer save.
        r = self.client.post('/api/actions/update_pdf_template',
                             json={'template': tid, 'html': layout('Stale'), 'expected_draft_modified': stamp})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('changed by someone else', r.json()['detail'])
        # Syntax errors are rejected with the line.
        r = self.client.post('/api/actions/update_pdf_template', json={'template': tid, 'html': '<p>\n{% for x in %}'})
        self.assertEqual(r.status_code, 422)
        self.assertIn('line 2', r.json()['detail'])

    def test_publish_preview_and_revert(self):
        tid = self.act('create_pdf_template', title='Branded', html=layout('Version A'))['template']
        name = self.quotation()
        # Only published templates can be chosen.
        r = self.client.put(f'/api/documents/quotation/{name}', json={'pdf_template': tid})
        self.assertEqual(r.status_code, 422, r.text)
        self.act('publish_pdf_template', template=tid)
        self.act('update_pdf_template', template=tid, html=layout('Version B'))
        draft = self.client.get(f'/api/pdf-templates/{tid}/preview?document={name}')
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertIn('Version B', pdf_text(draft.content))
        live = self.client.get(f'/api/pdf-templates/{tid}/preview?source=live&document={name}')
        self.assertIn('Version A', pdf_text(live.content))
        self.assertTrue(self.act('get_pdf_template', template=tid)['unpublished_changes'])
        # The document prints with the published layout, not the working copy.
        r = self.client.put(f'/api/documents/quotation/{name}', json={'pdf_template': tid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('Version A', pdf_text(self.pdf(name)))
        preview = self.act('preview_pdf_template', template=tid, document=name)
        self.assertTrue(preview['rendered'])
        self.assertIn('Version B', pdf_text(self.client.get(preview['download_url']).content))
        self.act('revert_pdf_template_draft', template=tid)
        self.assertIn('Version A', self.act('get_pdf_template', template=tid)['draft_html'])

    def test_publish_rejects_layouts_that_omit_required_content(self):
        tid = self.act('create_pdf_template', title='Bare', html='<p>{{ doc.name }} only</p>')['template']
        self.quotation()
        r = self.client.post('/api/actions/publish_pdf_template', json={'template': tid})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('omitted required content', r.json()['detail'])
        self.assertFalse(self.act('get_pdf_template', template=tid)['published'])

    # -- issuing --------------------------------------------------------

    def test_submit_pins_a_shared_version_and_archives_the_pdf(self):
        tid = self.template(html=layout('Layout One'))
        first, second = self.quotation(pdf_template=tid), self.quotation(pdf_template=tid)
        self.submit(first)
        self.submit(second)
        self.assertEqual(self.stored(first)['pdf_template'], tid)
        version = self.stored(first)['pdf_template_version']
        self.assertTrue(version.startswith(tid + '@'))
        self.assertEqual(self.stored(second)['pdf_template_version'], version)
        issued = self.pdf(first)
        self.assertIn('Layout One', pdf_text(issued))
        # Editing and publishing changes new offers only; the issued PDF stays
        # byte for byte, even when the customer's name changes later.
        self.act('update_pdf_template', template=tid, html=layout('Layout Two'), publish=True)
        get_db().set_value('Customer', 'C-PDF', 'customer_name', 'Renamed Customer AG')
        get_db().conn.commit()
        self.assertEqual(self.pdf(first), issued)
        third = self.quotation(pdf_template=tid)
        self.submit(third)
        self.assertIn('Layout Two', pdf_text(self.pdf(third)))
        versions = self.act('get_pdf_template', template=tid)['versions']
        self.assertEqual([(v['label'], v['documents']) for v in versions], [('v1', 2), ('v2', 1)])
        # A pinned version can't be supplied or changed through the API.
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', pdf_template_version=version,
            items=[dict(item_code='M-PDF', qty=1, rate=1)]))
        self.assertEqual(r.status_code, 422, r.text)

    def test_cancelled_offer_reprints_its_pinned_layout_with_the_notice(self):
        tid = self.template(html=layout('Pinned Layout'))
        name = self.quotation(pdf_template=tid)
        self.submit(name)
        self.act('update_pdf_template', template=tid, html=layout('Later Layout'), publish=True)
        r = self.client.post(f'/api/documents/quotation/{name}/cancel')
        self.assertEqual(r.status_code, 200, r.text)
        text = pdf_text(self.pdf(name))
        self.assertIn('Pinned Layout', text)
        self.assertIn('Cancelled', text)

    def test_default_template_and_standard_offers(self):
        plain = self.quotation()
        self.submit(plain)
        self.assertEqual(self.stored(plain)['pdf_template'], 'standard')
        self.assertTrue(self.stored(plain)['pdf_template_version'].startswith('standard@'))
        self.assertTrue(get_db().sql('SELECT 1 FROM "PDF Archive" WHERE document_name = ?', [plain]))
        tid = self.template(html=layout('House Style'))
        self.act('set_default_pdf_template', doctype='Quotation', template=tid)
        name = self.quotation()
        self.assertIn('House Style', pdf_text(self.pdf(name)))
        self.submit(name)
        self.assertEqual(self.stored(name)['pdf_template'], tid)
        # Unpublished templates can't become the default.
        draft = self.act('create_pdf_template', title='Unpublished')['template']
        r = self.client.post('/api/actions/set_default_pdf_template', json={'template': draft})
        self.assertEqual(r.status_code, 422, r.text)

    def test_bulk_seeding_pins_without_archiving(self):
        from api import services
        from api.pdf_templates import pin_only
        name = self.quotation()
        with pin_only():
            services.submit_document('quotation', name)
        self.assertTrue(self.stored(name)['pdf_template_version'].startswith('standard@'))
        self.assertFalse(get_db().sql('SELECT 1 FROM "PDF Archive" WHERE document_name = ?', [name]))
        self.assertIn('Excavator Example', pdf_text(self.pdf(name)))

    def test_offers_issued_before_templates_still_print(self):
        name = self.docs['Quotation']
        get_db().set_value('Quotation', name, 'docstatus', 1)
        get_db().set_value('Quotation', name, 'status', 'Open')
        get_db().conn.commit()
        self.assertIn('Ada Example', pdf_text(self.pdf(name)))

    # -- deleting -------------------------------------------------------

    def test_only_unused_templates_can_be_deleted(self):
        used = self.template('Used', html=layout('Used'))
        self.submit(self.quotation(pdf_template=used))
        r = self.client.post('/api/actions/delete_pdf_template', json={'template': used})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('Archive it', r.json()['detail'])
        self.act('archive_pdf_template', template=used)
        listed = [t['template'] for t in self.act('list_pdf_templates')['templates']]
        self.assertNotIn(used, listed)
        # An archived template can't be chosen for a new offer.
        r = self.client.post('/api/documents/quotation', json=dict(
            customer='C-PDF', company='PDF Co', pdf_template=used, items=[dict(item_code='M-PDF', qty=1, rate=5)]))
        self.assertEqual(r.status_code, 422, r.text)

        picked = self.template('Picked', html=layout('Picked'))
        draft = self.quotation(pdf_template=picked)
        r = self.client.post('/api/actions/delete_pdf_template', json={'template': picked})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn(draft, r.json()['detail'])
        unused = self.template('Unused', html=layout('Unused'))
        self.act('set_default_pdf_template', template=unused)
        self.assertEqual(self.act('delete_pdf_template', template=unused)['deleted'], unused)
        self.assertEqual(self.act('list_pdf_templates')['defaults'], {dt: 'standard' for dt in DOCTYPES})

    # -- sandbox --------------------------------------------------------

    def test_templates_cannot_reach_python_files_or_the_network(self):
        self.quotation()
        tid = self.act('create_pdf_template', title='Hostile',
                       html=layout('Hostile', "{{ ''.__class__.__mro__[1].__subclasses__() }}"))['template']
        r = self.client.post('/api/actions/publish_pdf_template', json={'template': tid})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn('unsafe', r.json()['detail'])
        for url in ('file:///etc/passwd', 'http://169.254.169.254/latest/meta-data/', '../../../../etc/hostname',
                    'https://example.com/logo.png'):
            self.act('update_pdf_template', template=tid, html=layout('Hostile', f'<img src="{url}">'
                                                                       f'<style>@import url("{url}");</style>'))
            r = self.client.post('/api/actions/publish_pdf_template', json={'template': tid})
            self.assertEqual(r.status_code, 422, (url, r.text))
            self.assertIn('upload images and fonts as template assets', r.json()['detail'].lower())

    def test_uploaded_assets_render_and_are_versioned_with_the_layout(self):
        tid = self.act('create_pdf_template', title='Logo', html=layout('Logo', '<img src="logo.png">'))['template']
        name = self.quotation()
        r = self.client.post(f'/api/actions/publish_pdf_template', json={'template': tid})
        self.assertEqual(r.status_code, 422, r.text)  # logo.png isn't uploaded yet
        r = self.client.post(f'/api/pdf-templates/{tid}/assets', files={'file': ('logo.png', PNG, 'image/png')})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([a['filename'] for a in r.json()['assets']], ['logo.png'])
        bad = self.client.post(f'/api/pdf-templates/{tid}/assets', files={'file': ('run.js', b'alert(1)', 'text/javascript')})
        self.assertEqual(bad.status_code, 422)
        self.act('publish_pdf_template', template=tid, document=name)
        self.assertEqual(self.client.get(f'/api/pdf-templates/{tid}/assets/logo.png').content, PNG)
        self.client.put(f'/api/documents/quotation/{name}', json={'pdf_template': tid})
        self.submit(name)
        version = self.stored(name)['pdf_template_version']
        # Replacing the logo later doesn't touch the issued version's asset.
        self.client.post(f'/api/pdf-templates/{tid}/assets', files={'file': ('logo.png', PNG + b'\0', 'image/png')})
        self.act('publish_pdf_template', template=tid)
        import json
        row = get_db().sql('SELECT assets FROM "PDF Template Version" WHERE name = ?', [version])[0]
        import hashlib
        self.assertEqual(json.loads(row['assets']), {'logo.png': hashlib.sha256(PNG).hexdigest()})

    # -- permissions and channels ---------------------------------------

    # -- looking at rendered pages ---------------------------------------

    def test_view_pdf_pages_returns_the_pages_as_images(self):
        import io
        from PIL import Image
        bar = '<div style="position: fixed; top: -15mm; left: -15mm; width: 210mm; height: 3mm; background: #dc008b"></div>'
        tid = self.template(html=layout('Look', bar), publish=False)
        name = self.quotation()
        seen = self.view(template=tid, name=name)
        self.assertEqual((seen['document'], seen['source'], seen['pages_total'], seen['pages_shown']),
                         (name, 'draft', 1, [1]))
        page = Image.open(io.BytesIO(base64.b64decode(seen['_images'][0]['data'])))
        self.assertEqual(page.format, 'PNG')
        self.assertAlmostEqual(page.width, 210 / 25.4 * 110, delta=3)  # A4 at 110 dpi
        # The fixed bar reaches the top edge of the sheet.
        self.assertEqual(page.convert('RGB').getpixel((page.width // 2, 2)), (220, 0, 139))
        top = self.view(template=tid, name=name, area='top')['_images'][0]
        self.assertAlmostEqual(top['width'], 2 * page.width, delta=3)
        self.assertLess(top['height'], top['width'])
        # A document renders as it downloads.
        self.assertEqual(self.view(doctype='quotation', name=name)['pages_total'], 1)
        for bad in ({'template': tid, 'pages': [2]}, {'template': tid, 'pages': []},
                    {'template': tid, 'pages': [1, 1, 1, 1, 1]}, {'doctype': 'quotation'}):
            r = self.client.post('/api/actions/view_pdf_pages', json=bad)
            self.assertEqual(r.status_code, 422, (bad, r.text))

    def test_view_pdf_pages_shows_uploaded_references(self):
        import io
        from PIL import Image
        from api import chat, pdf_pages
        user = self.client.get('/api/auth/me').json()
        sid = chat.create_session(user_id=user['name'])['id']

        def upload(filename, data, content_type):
            r = self.client.post('/api/chat/attachments', data={'session_id': sid},
                                 files={'file': (filename, data, content_type)})
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()['id']
        name = self.quotation()
        pdf_id = upload('reference.pdf', self.pdf(name), 'application/pdf')
        whole = self.view(attachment_id=pdf_id)
        self.assertEqual((whole['filename'], whole['pages_total'], whole['pages_shown']), ('reference.pdf', 1, [1]))
        top = self.view(attachment_id=pdf_id, area='top')['_images'][0]
        self.assertAlmostEqual(top['width'], 2 * whole['_images'][0]['width'], delta=3)

        buf = io.BytesIO()
        Image.new('RGB', (3000, 4000), (36, 39, 134)).save(buf, format='PNG')
        png_id = upload('mockup.png', buf.getvalue(), 'image/png')
        shot = self.view(attachment_id=png_id)['_images'][0]
        self.assertEqual(shot['height'], 1290)  # scaled like a rendered page
        self.assertAlmostEqual(shot['width'], 1290 * 3 / 4, delta=1)

        broken = upload('broken.png', b'\x89PNG not really', 'image/png')
        for bad in ({'attachment_id': png_id, 'pages': [2]}, {'attachment_id': broken},
                    {'attachment_id': pdf_id, 'template': 'standard'}, {'attachment_id': 'nope'}):
            r = self.client.post('/api/actions/view_pdf_pages', json=bad)
            self.assertEqual(r.status_code, 422, (bad, r.text))
        # Uploads stay with their owner.
        with self.assertRaisesRegex(ValueError, 'not found'):
            pdf_pages.view_pdf_pages({'attachment_id': pdf_id}, {'name': 'USR-someone-else', 'role': 'admin'})

    def test_mcp_and_chat_show_the_pages_to_the_model_without_storing_them(self):
        import asyncio
        import json
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from api import chat
        from api.routers import mcp
        name = self.quotation()
        user = {'name': 'USR-test', 'role': 'viewer'}
        out = mcp._handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'view_pdf_pages', 'arguments': {'doctype': 'quotation', 'name': name}}}, user)['result']
        self.assertFalse(out['isError'])
        self.assertEqual([c['type'] for c in out['content']], ['text', 'image'])
        self.assertEqual(out['content'][1]['mimeType'], 'image/png')
        self.assertNotIn('_images', out['content'][0]['text'])

        sid = chat.create_session()['id']
        seen = []
        replies = iter([
            NS(content='', tool_calls=[NS(id='c1', function=NS(name='view_pdf_pages', arguments=json.dumps(
                {'doctype': 'quotation', 'name': name, 'area': 'top'})))]),
            NS(content='Looks right.', tool_calls=[]),
        ])

        def turn(client, messages, *args, **kwargs):
            seen.append([dict(m) for m in messages])
            return next(replies), None

        async def event(e):
            pass
        with patch.object(chat, '_orchestrator_turn', side_effect=turn), \
             patch.object(chat, 'refresh_auth_principal', side_effect=lambda u: u):
            asyncio.run(chat.run_thinking_loop([], event, session_id=sid, user_info=user, max_iterations=3))
        shown = seen[1][-1]
        self.assertEqual(shown['role'], 'user')
        images = [p for p in shown['content'] if p['type'] == 'image_url']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['image_url']['detail'], 'high')
        self.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
        tool_message = next(m for m in seen[1] if m['role'] == 'tool')
        self.assertNotIn('iVBOR', tool_message['content'])
        stored = get_db().sql('SELECT content FROM "Chat Message" WHERE session_id = ?', [sid])
        self.assertTrue(stored)
        self.assertFalse(any('iVBOR' in (r['content'] or '') for r in stored))
        converted = chat._responses_msg_content(shown['content'])
        self.assertEqual([p['detail'] for p in converted if p['type'] == 'input_image'], ['high'])

    def test_chat_reply_may_link_the_preview_it_rendered(self):
        import asyncio
        import json
        from types import SimpleNamespace as NS
        from unittest.mock import patch
        from api import chat
        tid = self.template(html=layout('Linked'), publish=False)
        self.quotation()
        sid = chat.create_session()['id']
        user = self.client.get('/api/auth/me').json()  # the downloader must be the one who generated it
        fake = '/api/documents/quotation/QTN-9999/pdf?artifact_id=0000'

        def turn(client, messages, *args, **kwargs):
            tool = [m for m in messages if m['role'] == 'tool']
            if not tool:
                return NS(content='', tool_calls=[NS(id='c1', function=NS(name='preview_pdf_template',
                          arguments=json.dumps({'template': tid})))]), None
            url = json.loads(tool[-1]['content'])['download_url']
            return NS(content=f'[Vorschau]({url}) and [made up]({fake})', tool_calls=[]), None

        events = []

        async def event(e):
            events.append(e)
        with patch.object(chat, '_orchestrator_turn', side_effect=turn), \
             patch.object(chat, 'refresh_auth_principal', side_effect=lambda u: u), \
             patch.object(chat, 'generate_title', side_effect=lambda *a, **k: None):
            reply = asyncio.run(chat.run_session_turn(sid, 'Zeig mir die Vorschau', user, event))
        preview = next(e for e in events if e.get('tool') == 'preview_pdf_template' and e['type'] == 'tool_result')
        self.assertIn(preview['document']['download_url'], reply)
        self.assertNotIn(fake, reply)
        self.assertIn('[PDF not generated]', reply)
        # The linked preview downloads.
        r = self.client.get(preview['document']['download_url'])
        self.assertEqual((r.status_code, r.content[:4]), (200, b'%PDF'), r.text[:200])
        self.assertIn('Linked', pdf_text(r.content))

    def test_viewers_choose_but_cannot_edit_and_mcp_exposes_the_tools(self):
        self.client.put('/api/auth/settings', json={'rest_api_enabled': '1'})
        key = self.client.post('/api/auth/api-keys', json={'name': 'viewer', 'role': 'viewer'}).json()['token']
        self.client.cookies.clear()
        headers = {'Authorization': 'Bearer ' + key}
        r = self.client.post('/api/actions/create_pdf_template', json={'title': 'Nope'}, headers=headers)
        self.assertEqual(r.status_code, 403, r.text)
        r = self.client.post('/api/actions/list_pdf_templates', json={}, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        tools = self.client.post('/api/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        names = {t['name'] for t in tools.json()['result']['tools']}
        self.assertIn('list_pdf_templates', names)
        self.assertIn('preview_pdf_template', names)
        self.assertNotIn('update_pdf_template', names)
        # The shared demo account edits layouts but can't remove uploaded files.
        from api.services import registered_action_allowed
        self.assertTrue(registered_action_allowed('update_pdf_template', 'public_manager'))
        self.assertTrue(registered_action_allowed('list_pdf_templates', 'public_manager'))
        self.assertFalse(registered_action_allowed('delete_pdf_template_asset', 'public_manager'))


if __name__ == '__main__':
    unittest.main()
