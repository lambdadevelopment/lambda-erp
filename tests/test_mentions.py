"""Mention REST/MCP identity, atomicity, retry and visibility regression tests."""
import copy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from tests.test_mcp import _reset_db


class MentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        os.environ.setdefault('JWT_SECRET_KEY', 'mention-tests-only')
        os.environ.setdefault('OPENAI_API_KEY', 'not-used')
        from fastapi.testclient import TestClient
        from api.main import app
        from api import mentions
        from lambda_erp.database import get_db
        from api.auth import create_access_token, COOKIE_NAME
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.db, cls.mentions = get_db(), mentions
        cls.tokens = {name: create_access_token(name) for name in ('author', 'recipient', 'other')}
        cls.cookie = COOKIE_NAME
        for name, role in [('author', 'admin'), ('recipient', 'viewer'), ('other', 'manager')]:
            cls.db.insert('User', dict(name=name, email=name+'@example.com', full_name=name.title(),
                                      hashed_password='test-only', role=role, enabled=1))
        cls.db.sql('CREATE TABLE "Mention Test Record" (name TEXT PRIMARY KEY, blocked INTEGER DEFAULT 0)')
        cls.db.sql('CREATE TABLE "Mention Test Note" (name TEXT PRIMARY KEY, record_id TEXT, body TEXT, author_id TEXT, discarded INTEGER DEFAULT 0)')
        def access(record_id, user):
            row = cls.db.get_value('Mention Test Record', record_id, ['blocked'])
            return bool(row and not row['blocked'] and user.get('role') in mentions.REAL_ROLES)
        def create(record_id, body, user):
            name = uuid4().hex
            cls.db.insert('Mention Test Note', dict(name=name, record_id=record_id, body=body, author_id=user['user_id']))
            return name
        def resolve(note_id, user):
            note = cls.db.get_value('Mention Test Note', note_id, ['record_id','body','discarded'])
            if not note or note['discarded'] or not access(note['record_id'], user):
                return None
            return {**note, 'title':'Record '+note['record_id'], 'view_url':'/app/test/'+note['record_id']+'#note-'+note_id}
        mentions.register_mention_source('test', label='Test record', can_access=access, create_note=create, resolve_note=resolve)
        cls.client.cookies.set(cls.cookie, cls.tokens['author'])
        assert cls.client.put('/api/auth/settings', json={'rest_api_enabled':'1'}).status_code == 200
        key = cls.client.post('/api/auth/api-keys', json={'name':'test', 'role':'manager'})
        assert key.status_code == 200, key.text
        cls.key = key.json()['token']

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        for table in ('Mention Notification','Mention Request','Mention Test Note','Mention Test Record'):
            self.db.sql(f'DELETE FROM "{table}"')
        self.db.sql('UPDATE "User" SET enabled = 1')
        self.db.insert('Mention Test Record', {'name':'R-1'})
        self.body = dict(doctype='test', record_id='R-1', body='Please check @Recipient',
                         mention_user_ids=['recipient'], request_id=str(uuid4()))

    def action(self, action, args, user='author'):
        self.client.cookies.clear()
        self.client.cookies.set(self.cookie, self.tokens[user])
        return self.client.post('/api/actions/'+action, json=args)

    def create(self):
        response = self.action('add_record_note', self.body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def inbox(self, user='recipient', **args):
        response = self.action('list_my_mentions', args, user)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_note_and_notification_roundtrip_with_independent_read_done(self):
        saved = self.create()
        inbox = self.inbox()
        self.assertEqual(inbox['total'], 1)
        item = inbox['rows'][0]
        self.assertEqual(item['note_id'], saved['note_id'])
        self.assertEqual(item['view_url'], saved['view_url'])
        self.assertEqual(inbox['unread_count'], 1)
        self.assertEqual(self.inbox('other')['total'], 0)
        for state, unread, total in [('read',0,1),('done',0,0),('reopen',0,1),('unread',1,1)]:
            r = self.action('set_mention_state', {'notification_id':item['id'], 'state':state}, 'recipient')
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.inbox()['unread_count'], unread)
            self.assertEqual(self.inbox()['total'], total)
        self.assertEqual(len(self.db.get_all('Mention Test Note')), 1)

    def test_retry_and_concurrent_retries_create_one_note(self):
        actor = {'name':'author','role':'admin'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.mentions.add_record_note(self.body, actor), range(4)))
        self.assertEqual(len({r['note_id'] for r in results}), 1)
        self.assertEqual(sum(not r['replayed'] for r in results), 1)
        self.assertEqual(self.inbox()['total'], 1)
        self.body['body'] = 'Different content'
        self.assertEqual(self.action('add_record_note', self.body).status_code, 422)

    def test_failure_rolls_back_note_request_and_notifications(self):
        original = self.db.insert
        def fail(table, data):
            if table == 'Mention Notification':
                raise RuntimeError('simulated delivery failure')
            return original(table, data)
        with patch.object(self.db, 'insert', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.mentions.add_record_note(self.body, {'name':'author','role':'admin'})
        self.assertEqual(len(self.db.get_all('Mention Test Note')), 0)
        self.assertEqual(self.db.sql('SELECT COUNT(*) AS n FROM "Mention Request"')[0]['n'], 0)
        self.create()
        self.assertEqual(self.inbox()['total'], 1)

    def test_cannot_choose_another_inbox_or_actor_and_viewer_cannot_write(self):
        self.assertEqual(self.action('add_record_note', self.body, 'recipient').status_code, 403)
        for field in ('actor_id','user_id','author_id'):
            self.assertEqual(self.action('add_record_note', {**self.body, field:'other'}).status_code, 422)
        self.create()
        self.assertEqual(self.action('list_my_mentions', {'user_id':'recipient'}, 'other').status_code, 422)
        item = self.inbox()['rows'][0]
        self.assertEqual(self.action('set_mention_state', {'notification_id':item['id'],'state':'done'}, 'other').status_code, 404)

    def test_self_and_duplicate_recipients_do_not_spam(self):
        self.body['mention_user_ids'] = ['recipient','recipient','author']
        self.assertEqual(self.create()['notified_count'], 1)
        self.assertEqual(self.inbox()['total'], 1)
        self.assertEqual(self.inbox('author')['total'], 0)

    def test_retry_reports_saved_note_even_if_recipient_was_later_disabled(self):
        original = self.create()
        self.db.set_value('User', 'recipient', 'enabled', 0)
        replay = self.create()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['note_id'], original['note_id'])
        self.assertEqual(replay['notified_count'], 1)

    def test_recipient_and_record_visibility_rechecked(self):
        self.db.sql('UPDATE "User" SET enabled = 0 WHERE name = ?', ['recipient'])
        self.db.conn.commit()
        self.assertEqual(self.action('add_record_note', self.body).status_code, 422)
        self.assertEqual(len(self.db.get_all('Mention Test Note')), 0)
        search = self.action('search_mention_users', {'query':'recipient'}).json()
        self.assertEqual(search['users'], [])
        self.db.sql('UPDATE "User" SET enabled = 1 WHERE name = ?', ['recipient'])
        self.db.conn.commit()
        self.create()
        self.db.sql('UPDATE "Mention Test Record" SET blocked = 1')
        self.db.conn.commit()
        self.assertEqual(self.inbox()['total'], 0)
        self.assertEqual(self.inbox()['unread_count'], 0)
        self.db.sql('UPDATE "Mention Test Record" SET blocked = 0')
        self.db.sql('UPDATE "Mention Test Note" SET discarded = 1')
        self.db.conn.commit()
        self.assertEqual(self.inbox()['total'], 0)

    def test_pagination_and_completed_history(self):
        for _ in range(3):
            self.body['request_id'] = str(uuid4()); self.create()
        page = self.inbox(limit=1, offset=1)
        self.assertEqual(len(page['rows']), 1)
        self.assertEqual(page['total'], 3)
        self.assertTrue(page['has_more'])
        self.action('set_mention_state', {'notification_id':page['rows'][0]['id'], 'state':'done'}, 'recipient')
        self.assertEqual(self.inbox(view='done')['total'], 1)
        self.assertEqual(self.inbox(view='all')['total'], 3)

    def test_mcp_uses_authenticated_identity_and_same_actions(self):
        self.client.cookies.clear()
        headers = {'Authorization':'Bearer '+self.key}
        def rpc(method, params):
            response = self.client.post('/api/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['result']
        tools = {t['name']:t for t in rpc('tools/list', {})['tools']}
        self.assertTrue(tools['list_my_mentions']['annotations']['readOnlyHint'])
        saved = rpc('tools/call', {'name':'add_record_note','arguments':self.body})
        self.assertFalse(saved.get('isError'), saved)
        data = json.loads(saved['content'][0]['text'])
        self.assertEqual(data['acting_user']['user_id'], 'author')
        self.assertEqual(self.inbox()['total'], 1)


if __name__ == '__main__':
    unittest.main()
