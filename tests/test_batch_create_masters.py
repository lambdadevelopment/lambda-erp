"""Creating many master records in one call, and the chat's step limit.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import os
import unittest
from unittest.mock import patch

from tests import test_document_requirements as fixtures
from api import chat
from api.routers import masters


class BatchCreateMasters(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)

    def create(self, master_type, records):
        return chat.TOOL_HANDLERS['batch_create_masters']({'master_type': master_type, 'records': records})

    def test_a_chart_of_accounts_in_one_call(self):
        group = {'name': '5 Personnel - TC', 'account_name': 'Personnel', 'company': 'Test Co',
                 'root_type': 'Expense', 'is_group': 1}
        accounts = [group] + [
            {'name': f'{5000 + i} Wages {i} - TC', 'account_name': f'{5000 + i} Wages {i}',
             'company': 'Test Co', 'root_type': 'Expense', 'parent_account': '5 Personnel - TC'}
            for i in range(120)]
        got = self.create('account', accounts)
        self.assertEqual((got['created'], got['failed'], len(got['results'])), (121, 0, 121))
        self.assertEqual(got['results'][1], {**got['results'][1], 'index': 1, 'ok': True, 'name': '5000 Wages 0 - TC'})
        self.assertIn('view_url', got['results'][0])
        stored = self.db.sql('SELECT COUNT(*) AS n FROM "Account" WHERE parent_account = ?', ['5 Personnel - TC'])[0]['n']
        self.assertEqual(stored, 120)

    def test_a_refused_record_does_not_undo_the_others(self):
        records = [
            {'customer_name': 'Clinic One'},
            {'no_such_field': 'x'},
            'not an object',
            {'name': 'C', 'customer_name': 'Already there'},   # the fixture's customer
            {'customer_name': 'Clinic Two', 'city': 'Bern'},
        ]
        got = self.create('customer', records)
        self.assertEqual((got['created'], got['failed']), (2, 3))
        self.assertEqual([row['ok'] for row in got['results']], [True, False, False, False, True])
        self.assertTrue(all(row.get('error') for row in got['results'] if not row['ok']))
        names = {row['customer_name'] for row in self.db.sql('SELECT customer_name FROM "Customer"')}
        self.assertTrue({'Clinic One', 'Clinic Two'} <= names)
        self.assertNotIn('Already there', names)
        # Sending the same list again creates nothing twice where the id was given.
        again = self.create('account', [{'name': 'X - TC', 'account_name': 'X', 'company': 'Test Co', 'root_type': 'Expense'}] * 2)
        self.assertEqual((again['created'], again['failed']), (1, 1))

    def test_bad_calls(self):
        self.assertIn('non-empty array', self.create('customer', [])['error'])
        self.assertIn('non-empty array', self.create('customer', None)['error'])
        self.assertIn('max 200', self.create('customer', [{'customer_name': 'x'}] * 201)['error'])
        self.assertIn('error', self.create('no-such-master', [{'x': 1}]))
        self.assertEqual(self.db.sql('SELECT COUNT(*) AS n FROM "Customer"')[0]['n'], 1)
        self.assertEqual(masters.BATCH_CREATE_MAX, 200)

    def test_the_tool_is_offered_and_classified(self):
        from api import tool_permissions
        names = {tool['function']['name'] for tool in chat.build_tools({'role': 'admin'})}
        self.assertIn('batch_create_masters', names)
        self.assertTrue(tool_permissions.tool_allowed('batch_create_masters', 'manager'))
        self.assertFalse(tool_permissions.tool_allowed('batch_create_masters', 'viewer'))

    def test_step_limit(self):
        os.environ.pop('LAMBDA_ERP_CHAT_MAX_STEPS', None)
        self.assertEqual(chat.chat_max_steps(), 30)
        for value, steps in (('50', 50), ('0', 1), ('1000', 100), ('many', 30), ('', 30)):
            with patch.dict(os.environ, {'LAMBDA_ERP_CHAT_MAX_STEPS': value}):
                self.assertEqual(chat.chat_max_steps(), steps)
        self.assertEqual(chat.DEMO_MAX_STEPS, 12)


if __name__ == '__main__':
    unittest.main()
