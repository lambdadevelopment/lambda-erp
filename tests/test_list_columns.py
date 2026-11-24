"""Personal list columns, read and changed through the chat / action tools.

Synthetic data only. The columns are the same preferences the column menu of
the web app writes (`columns.<doctype>`, `sort.<doctype>`).
"""
import os
import unittest

from tests.test_mcp import _reset_db


class ListColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['LAMBDA_ERP_DB'] = _reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        os.environ['LAMBDA_ERP_PLUGINS'] = ''
        os.environ.setdefault('JWT_SECRET_KEY', 'list-columns-tests-only')
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
            cls.db.insert('User', dict(name=name, email=name + '@example.com', full_name=name.title(),
                                      hashed_password='test-only', role=role, enabled=1))
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.db.sql('DELETE FROM "User Preference"')
        self.db.commit()

    def action(self, name, args, user='anna'):
        self.client.cookies.clear()
        self.client.cookies.set(self.cookie, self.tokens[user])
        return self.client.post('/api/actions/' + name, json=args)

    def ok(self, name, args, user='anna'):
        response = self.action(name, args, user)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def settings(self, user='anna'):
        self.client.cookies.clear()
        self.client.cookies.set(self.cookie, self.tokens[user])
        return self.client.get('/api/auth/my-settings').json()

    def test_what_a_list_can_show(self):
        got = self.ok('get_list_columns', {'doctype': 'sales-invoice'})
        self.assertIsNone(got['columns'])
        self.assertIn('default', got['columns_note'])
        for column in ('name', 'customer', 'customer_name', 'grand_total', '__pdf'):
            self.assertIn(column, got['available'])
        self.assertNotIn('docstatus', got['available'])
        prices = self.ok('get_list_columns', {'doctype': 'item-price'})['available']
        self.assertIn('item_code__title', prices)
        self.assertIn('customer__title', prices)

    def test_set_add_remove_sort_and_reset(self):
        got = self.ok('update_list_columns', {'doctype': 'sales-invoice',
                                              'columns': ['customer_name', 'grand_total', '__pdf']})
        self.assertEqual(got['columns'], ['name', 'customer_name', 'grand_total', '__pdf'])
        self.assertEqual(self.settings()['columns.sales-invoice'], 'name,customer_name,grand_total,__pdf')
        got = self.ok('update_list_columns', {'doctype': 'sales-invoice', 'add': ['status'], 'remove': ['__pdf']})
        self.assertEqual(got['columns'], ['name', 'customer_name', 'grand_total', 'status'])
        got = self.ok('update_list_columns', {'doctype': 'sales-invoice', 'sort_by': 'grand_total', 'sort_order': 'desc'})
        self.assertEqual((got['sort_by'], got['sort_order']), ('grand_total', 'desc'))
        self.assertEqual(self.settings()['sort.sales-invoice'], 'grand_total:desc')
        got = self.ok('update_list_columns', {'doctype': 'sales-invoice', 'reset': True})
        self.assertEqual((got['columns'], got['sort_by']), (None, None))
        self.assertNotIn('columns.sales-invoice', self.settings())

    def test_refusals(self):
        for args, message in (
                ({'doctype': 'sales-invoice', 'columns': ['nope']}, 'Unknown column'),
                ({'doctype': 'no-such-type', 'columns': ['name']}, 'Unknown document type'),
                ({'doctype': 'sales-invoice', 'add': ['status']}, 'default columns'),
                ({'doctype': 'sales-invoice', 'sort_by': '__pdf'}, 'cannot be sorted'),
                ({'doctype': 'item-price', 'sort_by': 'customer__title'}, 'cannot be sorted'),
                ({'doctype': 'sales-invoice', 'columns': []}, 'at least one column')):
            response = self.action('update_list_columns', args)
            self.assertNotEqual(response.status_code, 200, args)
            self.assertIn(message, response.text, args)

    def test_columns_are_per_user(self):
        self.ok('update_list_columns', {'doctype': 'sales-invoice', 'columns': ['customer_name']})
        self.assertIsNone(self.ok('get_list_columns', {'doctype': 'sales-invoice'}, 'ben')['columns'])
        self.ok('update_list_columns', {'doctype': 'sales-invoice', 'columns': ['status']}, 'ben')
        self.assertEqual(self.ok('get_list_columns', {'doctype': 'sales-invoice'})['columns'], ['name', 'customer_name'])

    def test_offered_to_chat_and_mcp(self):
        from api import chat
        from api.routers import mcp
        names = {tool['function']['name'] for tool in chat.build_tools({'role': 'viewer'})}
        self.assertTrue({'get_list_columns', 'update_list_columns'} <= names)
        self.assertIn('get_list_columns', mcp._READ_ONLY)
        self.assertNotIn('update_list_columns', mcp._READ_ONLY)


if __name__ == '__main__':
    unittest.main()
