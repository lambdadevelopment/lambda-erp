"""Personal sidebar menu: hide/show/reorder via REST, chat actions and MCP."""
import json
import os
import unittest

from tests.test_mcp import _reset_db

CATALOG = [
    {"key": "Chats", "label": "Chats", "title": "Chats", "items": []},
    {"key": "Selling", "label": "Selling", "title": "Verkauf", "items": [
        {"key": "/app/quotation", "label": "Quotation", "title": "Angebot"},
        {"key": "/app/sales-invoice", "label": "Sales Invoice", "title": "Ausgangsrechnung"}]},
    {"key": "Rentals", "label": "Rentals", "title": "Vermietung", "items": [
        {"key": "/rentals/calendar", "label": "Fleet Calendar", "title": "Flottenkalender"},
        {"key": "/app/asset", "label": "Fleet (Assets)", "title": "Flotte (Anlagen)"},
        {"key": "/app/reservation", "label": "Reservations", "title": "Reservierungen"}]},
    {"key": "Settings", "label": "Settings", "title": "Einstellungen", "items": [
        {"key": "/admin/settings", "label": "General", "title": "Allgemein"},
        {"key": "/settings/menu", "label": "Menu", "title": "Menü"}]},
]


class MenuLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        os.environ.setdefault('JWT_SECRET_KEY', 'menu-layout-tests-only')
        os.environ.setdefault('OPENAI_API_KEY', 'not-used')
        from fastapi.testclient import TestClient
        from api.main import app
        from lambda_erp.database import get_db
        from api.auth import create_access_token, COOKIE_NAME
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.db = get_db()
        cls.cookie = COOKIE_NAME
        cls.tokens = {name: create_access_token(name) for name in ('anna', 'ben')}
        for name, role in [('anna', 'admin'), ('ben', 'viewer')]:
            cls.db.insert('User', dict(name=name, email=name+'@example.com', full_name=name.title(),
                                      hashed_password='test-only', role=role, enabled=1))
        cls.login('anna')
        assert cls.client.put('/api/auth/settings', json={'rest_api_enabled': '1'}).status_code == 200
        key = cls.client.post('/api/auth/api-keys', json={'name': 'test', 'role': 'manager'})
        assert key.status_code == 200, key.text
        cls.key = key.json()['token']

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    @classmethod
    def login(cls, user):
        cls.client.cookies.clear()
        cls.client.cookies.set(cls.cookie, cls.tokens[user])

    def setUp(self):
        self.db.sql('DELETE FROM "User Preference"')
        self.db.commit()
        for user in ('anna', 'ben'):
            self.publish(user)

    def publish(self, user):
        self.login(user)
        response = self.client.put('/api/auth/my-settings', json={'nav_catalog': json.dumps(CATALOG)})
        self.assertEqual(response.status_code, 200, response.text)

    def action(self, name, args, user='anna'):
        self.login(user)
        return self.client.post('/api/actions/'+name, json=args)

    def ok(self, name, args, user='anna'):
        response = self.action(name, args, user)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def hidden(self, result):
        return {g['key'] for g in result['menu'] if g['hidden']} | {
            i['key'] for g in result['menu'] for i in g['items'] if i['hidden']}

    def test_hide_a_whole_group_or_one_item_by_key_or_label(self):
        result = self.ok('update_menu_layout', {'hide': ['Rentals']})
        self.assertEqual(self.hidden(result), {'Rentals'})
        result = self.ok('update_menu_layout', {'show': ['rentals'], 'hide': ['Flotte (Anlagen)']})
        self.assertEqual(self.hidden(result), {'/app/asset'})
        stored = json.loads(self.client.get('/api/auth/my-settings').json()['nav_layout'])
        self.assertEqual(stored['hidden'], ['/app/asset'])

    def test_reorder_groups_and_items(self):
        result = self.ok('update_menu_layout', {
            'group_order': ['Rentals'],
            'item_order': {'Rentals': ['/app/reservation', 'Fleet Calendar']}})
        self.assertEqual([g['key'] for g in result['menu']], ['Rentals', 'Chats', 'Selling', 'Settings'])
        self.assertEqual([i['key'] for i in result['menu'][0]['items']],
                         ['/app/reservation', '/rentals/calendar', '/app/asset'])

    def test_layout_is_per_user(self):
        self.ok('update_menu_layout', {'hide': ['Selling']})
        self.assertEqual(self.hidden(self.ok('get_menu_layout', {}, 'ben')), set())
        self.assertEqual(self.hidden(self.ok('get_menu_layout', {})), {'Selling'})

    def test_viewers_may_change_their_own_menu(self):
        result = self.ok('update_menu_layout', {'hide': ['/app/quotation']}, 'ben')
        self.assertEqual(self.hidden(result), {'/app/quotation'})

    def test_menu_settings_page_and_its_group_cannot_be_hidden(self):
        for ref in ('/settings/menu', 'Settings', 'Menü'):
            response = self.action('update_menu_layout', {'hide': [ref]})
            self.assertEqual(response.status_code, 422, response.text)
        self.ok('update_menu_layout', {'hide': ['General']})

    def test_invalid_references_are_rejected_without_saving(self):
        self.ok('update_menu_layout', {'hide': ['/app/asset']})
        bad = [{'hide': ['Spaceships']}, {'group_order': ['/app/asset']},
               {'item_order': {'Rentals': ['/app/quotation']}}, {'item_order': {'/app/asset': []}}]
        for args in bad:
            response = self.action('update_menu_layout', args)
            self.assertEqual(response.status_code, 422, (args, response.text))
        self.assertEqual(self.hidden(self.ok('get_menu_layout', {})), {'/app/asset'})

    def test_reset_restores_the_default(self):
        self.ok('update_menu_layout', {'hide': ['Rentals'], 'group_order': ['Settings']})
        result = self.ok('update_menu_layout', {'reset': True})
        self.assertEqual(result['layout'], {'order': [], 'items': {}, 'hidden': []})
        self.assertEqual([g['key'] for g in result['menu']], [g['key'] for g in CATALOG])

    def test_unknown_catalog_asks_to_open_the_web_app(self):
        self.db.sql('DELETE FROM "User Preference"')
        self.db.commit()
        self.assertIn('open the ERP web app', self.ok('get_menu_layout', {})['note'])
        response = self.action('update_menu_layout', {'hide': ['Rentals']})
        self.assertEqual(response.status_code, 422, response.text)

    def test_mcp_lists_and_runs_the_menu_tools(self):
        self.client.cookies.clear()
        headers = {'Authorization': 'Bearer '+self.key}

        def rpc(method, params):
            response = self.client.post('/api/mcp', headers=headers,
                                        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['result']
        tools = {t['name']: t for t in rpc('tools/list', {})['tools']}
        self.assertTrue(tools['get_menu_layout']['annotations']['readOnlyHint'])
        self.assertFalse(tools['update_menu_layout']['annotations']['readOnlyHint'])
        result = rpc('tools/call', {'name': 'update_menu_layout', 'arguments': {'hide': ['Rentals']}})
        self.assertFalse(result.get('isError'), result)
        self.assertEqual(self.hidden(json.loads(result['content'][0]['text'])), {'Rentals'})


if __name__ == '__main__':
    unittest.main()
