"""Internal notes on documents and masters: REST, mentions, permissions, MCP."""
import json
import os
import unittest
from uuid import uuid4

from tests.test_mcp import _reset_db


class RecordNoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        os.environ.setdefault('JWT_SECRET_KEY', 'record-note-tests-only')
        os.environ.setdefault('OPENAI_API_KEY', 'not-used')
        from fastapi.testclient import TestClient
        from api.main import app
        from lambda_erp.database import get_db
        from api.auth import create_access_token, COOKIE_NAME
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.db = get_db()
        cls.tokens = {name: create_access_token(name) for name in ('author', 'recipient', 'other')}
        cls.cookie = COOKIE_NAME
        for name, role in [('author', 'admin'), ('recipient', 'viewer'), ('other', 'manager')]:
            cls.db.insert('User', dict(name=name, email=name+'@example.com', full_name=name.title(),
                                      hashed_password='test-only', role=role, enabled=1))
        cls.client.cookies.set(cls.cookie, cls.tokens['author'])
        assert cls.client.put('/api/auth/settings', json={'rest_api_enabled': '1'}).status_code == 200
        key = cls.client.post('/api/auth/api-keys', json={'name': 'test', 'role': 'manager'})
        assert key.status_code == 200, key.text
        cls.key = key.json()['token']

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        for table in ('Record Note', 'Mention Notification', 'Mention Request'):
            self.db.sql(f'DELETE FROM "{table}"')
        self.db.sql('DELETE FROM "Customer"')
        self.db.sql('DELETE FROM "Sales Invoice"')
        self.db.insert('Customer', {'name': 'CUST-001', 'customer_name': 'Acme AG'})
        self.db.insert('Sales Invoice', {'name': 'SINV-0001', 'customer': 'CUST-001',
                                         'customer_name': 'Acme AG', 'docstatus': 1,
                                         'modified': '2026-01-01 00:00:00'})

    def action(self, action, args, user='author'):
        self.client.cookies.clear()
        self.client.cookies.set(self.cookie, self.tokens[user])
        return self.client.post('/api/actions/'+action, json=args)

    def ok(self, action, args, user='author'):
        response = self.action(action, args, user)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add(self, record_type, record_id, body, user='author'):
        return self.ok('add_record_note', dict(doctype=record_type, record_id=record_id, body=body,
                                               request_id=str(uuid4())), user)

    def notes(self, record_type='customer', record_id='CUST-001', user='author'):
        return self.ok('list_record_notes', dict(record_type=record_type, record_id=record_id), user)

    def inbox(self, user='recipient'):
        return self.ok('list_my_mentions', {'view': 'all'}, user)

    def test_master_note_reaches_the_inbox_with_a_link_to_it(self):
        saved = self.add('customer', 'CUST-001', 'Disputes the March invoice @Recipient')
        self.assertEqual(saved['notified_count'], 1)
        self.assertEqual(saved['view_url'], f"/masters/customer/CUST-001#note-{saved['note_id']}")
        [mention] = self.inbox()['rows']
        self.assertEqual(mention['title'], 'Customer CUST-001 · Acme AG')
        self.assertEqual(mention['view_url'], saved['view_url'])
        listed = self.notes(user='recipient')
        self.assertEqual(listed['total'], 1)
        self.assertEqual(listed['notes'][0]['author_name'], 'Author')
        self.assertFalse(listed['notes'][0]['can_edit'])

    def test_document_note_leaves_the_document_untouched(self):
        saved = self.add('sales-invoice', 'SINV-0001', 'Paid by phone, receipt follows')
        self.assertEqual(saved['notified_count'], 0)
        self.assertEqual(saved['view_url'], f"/app/sales-invoice/SINV-0001#note-{saved['note_id']}")
        row = self.db.get_value('Sales Invoice', 'SINV-0001', ['modified', 'remarks'])
        self.assertEqual(row['modified'], '2026-01-01 00:00:00')
        self.assertFalse(row['remarks'])
        self.assertEqual(self.notes('sales-invoice', 'SINV-0001')['notes'][0]['body'],
                         'Paid by phone, receipt follows')

    def test_newest_first_with_pagination(self):
        for i in range(3):
            self.add('customer', 'CUST-001', f'note {i}')
        page = self.ok('list_record_notes', dict(record_type='customer', record_id='CUST-001', limit=2))
        self.assertEqual([n['body'] for n in page['notes']], ['note 2', 'note 1'])
        self.assertTrue(page['has_more'])
        self.assertEqual(page['total'], 3)

    def test_editing_resyncs_mentions_and_only_the_author_may_edit(self):
        note = self.add('customer', 'CUST-001', 'Check this @Recipient')['note_id']
        self.assertEqual(self.inbox()['total'], 1)
        denied = self.action('update_record_note', {'note_id': note, 'body': 'hijack'}, 'other')
        self.assertEqual(denied.status_code, 403, denied.text)
        edited = self.ok('update_record_note', {'note_id': note, 'body': 'Check this @Other'})
        self.assertEqual(edited['notified_count'], 1)
        self.assertEqual(self.inbox()['total'], 0)
        self.assertEqual(self.inbox('other')['total'], 1)
        listed = self.notes()['notes'][0]
        self.assertEqual(listed['body'], 'Check this @Other')
        self.assertTrue(listed['edited'])

    def test_failed_edit_keeps_the_old_text(self):
        note = self.add('customer', 'CUST-001', 'original')['note_id']
        self.db.insert('User', dict(name='twin', email='twin@example.com', full_name='Recipient',
                                    hashed_password='x', role='viewer', enabled=1))
        try:
            response = self.action('update_record_note', {'note_id': note, 'body': 'now @Recipient'})
            self.assertEqual(response.status_code, 422, response.text)
        finally:
            self.db.sql('DELETE FROM "User" WHERE name = ?', ['twin'])
        self.assertEqual(self.notes()['notes'][0]['body'], 'original')

    def test_delete_by_author_or_admin_removes_notifications(self):
        note = self.add('customer', 'CUST-001', 'hello @Recipient', 'other')['note_id']
        denied = self.action('delete_record_note', {'note_id': note}, 'recipient')
        self.assertEqual(denied.status_code, 403, denied.text)
        self.ok('delete_record_note', {'note_id': note}, 'author')  # admin
        self.assertEqual(self.notes()['total'], 0)
        self.assertEqual(self.inbox()['total'], 0)

    def test_viewers_read_but_cannot_write(self):
        response = self.action('add_record_note', dict(doctype='customer', record_id='CUST-001',
                                                       body='x', request_id=str(uuid4())), 'recipient')
        self.assertEqual(response.status_code, 403, response.text)

    def test_unknown_records_and_types_are_rejected(self):
        response = self.action('list_record_notes', {'record_type': 'customer', 'record_id': 'NOPE'})
        self.assertEqual(response.status_code, 404, response.text)
        response = self.action('list_record_notes', {'record_type': 'spaceship', 'record_id': 'X'})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('sales-invoice', response.json()['detail'])
        response = self.action('add_record_note', dict(doctype='customer', record_id='NOPE',
                                                       body='x', request_id=str(uuid4())))
        self.assertEqual(response.status_code, 404, response.text)

    def test_deleting_a_master_drops_its_notes_and_mentions(self):
        self.db.insert('Customer', {'name': 'CUST-002', 'customer_name': 'Gone GmbH'})
        self.add('customer', 'CUST-002', 'bye @Recipient')
        self.add('customer', 'CUST-001', 'stays @Recipient')
        self.client.cookies.set(self.cookie, self.tokens['author'])
        response = self.client.delete('/api/masters/customer/CUST-002')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'deleted')
        self.assertEqual(self.db.sql('SELECT COUNT(*) AS n FROM "Record Note"')[0]['n'], 1)
        self.assertEqual(self.inbox()['total'], 1)

    def test_every_document_and_master_type_is_a_mention_source(self):
        sources = {s['doctype'] for s in self.ok('search_mention_users', {'query': ''})['sources']}
        self.assertTrue({'customer', 'supplier', 'item', 'sales-invoice', 'purchase-order'} <= sources)

    def test_mcp_lists_and_runs_the_note_tools(self):
        self.client.cookies.clear()
        headers = {'Authorization': 'Bearer '+self.key}
        def rpc(method, params):
            response = self.client.post('/api/mcp', headers=headers,
                                        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['result']
        tools = {t['name']: t for t in rpc('tools/list', {})['tools']}
        self.assertTrue(tools['list_record_notes']['annotations']['readOnlyHint'])
        self.assertFalse(tools['update_record_note']['annotations']['readOnlyHint'])
        self.assertIn('never printed', tools['list_record_notes']['description'])
        saved = rpc('tools/call', {'name': 'add_record_note', 'arguments': dict(
            doctype='sales-invoice', record_id='SINV-0001', body='via MCP', request_id=str(uuid4()))})
        self.assertFalse(saved.get('isError'), saved)
        listed = rpc('tools/call', {'name': 'list_record_notes', 'arguments': dict(
            record_type='sales-invoice', record_id='SINV-0001')})
        notes = json.loads(listed['content'][0]['text'])['notes']
        self.assertEqual([(n['body'], n['author_id']) for n in notes], [('via MCP', 'author')])
        context = json.loads(rpc('tools/call', {'name': 'get_erp_context', 'arguments': {}})['content'][0]['text'])
        self.assertIn('add_record_note', context['instructions'])


if __name__ == '__main__':
    unittest.main()
