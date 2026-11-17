"""What a tool hands to an assistant stays readable however much data there is.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import json
import unittest
from unittest.mock import patch

from tests import test_document_requirements as fixtures
from api import chat, services, tool_results
from api.routers import mcp


def size(value):
    return len(json.dumps(value, default=str))


class ToolResultLimits(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        for i in range(1200):
            db.insert('Customer', {'name': f'CUST-{i:05d}', 'customer_name': f'Praxis Muster {i} AG',
                                   'default_currency': 'CHF', 'city': 'Winterthur' if i % 2 else 'Bern',
                                   'email': f'info{i}@example.ch'})
        db.commit()

    def search(self, **args):
        return chat._handle_search_masters({'master_type': 'customer', **args})

    # -- master search ----------------------------------------------------------
    def test_a_lookup_with_few_matches_is_the_plain_list_of_full_records(self):
        rows = self.search(query='Muster 42 AG')
        self.assertIsInstance(rows, list)
        self.assertEqual([row['name'] for row in rows], ['CUST-00042'])
        self.assertIn('email', rows[0])
        rows = self.search(query='Muster 42')  # 42, 420-429: eleven records
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 11)

    def test_a_broad_search_is_a_page_with_compact_rows(self):
        page = self.search()
        self.assertEqual((page['total'], page['returned'], page['offset'], page['has_more']), (1201, 50, 0, True))
        self.assertIn('customer_name', page['rows'][0])
        self.assertNotIn('email', page['rows'][0])
        self.assertLess(size(page), 15000)  # 2.9 million characters for 5000 customers before
        self.assertIn('offset=50', page['note'])
        # Reading on, and the last page.
        page = self.search(offset=50)
        self.assertEqual((page['offset'], page['rows'][0]['name']), (50, 'CUST-00049'))
        last = self.search(offset=1200)
        self.assertEqual((last['returned'], last['has_more']), (1, False))
        # The same for a query that matches many.
        page = self.search(query='Praxis')
        self.assertEqual((page['total'], page['returned']), (1200, 50))
        # Filters narrow it and count on.
        page = self.search(filters={'city': 'Bern'})
        self.assertEqual(page['total'], 600)

    def test_columns_and_page_size_can_be_chosen(self):
        page = self.search(result_fields=['email'], offset=10)
        self.assertEqual((page['returned'], sorted(page['rows'][0])), (50, ['email', 'name', 'view_url']))
        self.assertNotIn('identifying columns only', page['note'])
        # A caller that sets the limit gets a plain array of full records, as before; never more than 500.
        rows = self.search(limit=5)
        self.assertEqual((len(rows), 'email' in rows[0]), (5, True))
        self.assertEqual(len(self.search(limit=100000)), tool_results.MAX_PAGE)
        # The paged form with a total is unchanged.
        meta = self.search(include_meta=True)
        self.assertEqual((len(meta['rows']), meta['total']), (50, 1201))

    def test_the_chart_of_accounts_of_a_small_company_is_still_one_list(self):
        rows = chat._handle_search_masters({'master_type': 'account'})
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 20)
        self.assertEqual(len(chat._handle_search_masters({'master_type': 'account', 'limit': 10})), 10)
        self.assertTrue({'name', 'account_name', 'root_type'} <= set(chat._compact_master_columns(self.db, 'Account')))
        expense = chat._handle_search_masters({'master_type': 'account', 'filters': {'root_type': 'Expense'}})
        self.assertTrue(expense and all(row['root_type'] == 'Expense' for row in expense))

    # -- reports and documents ---------------------------------------------------
    def test_a_list_report_is_a_window_with_its_total(self):
        db = self.db
        for i in range(260):
            db.insert('Item', {'name': f'IT-{i:04d}', 'item_name': f'Item {i}', 'is_stock_item': 1, 'standard_rate': 1})
        db.commit()
        entry = services.create_document('stock-entry', {
            'company': 'Test Co', 'stock_entry_type': 'Material Receipt',
            'items': [{'item_code': f'IT-{i:04d}', 'qty': 1, 'basic_rate': 1, 't_warehouse': 'A'} for i in range(260)]})
        services.submit_document('stock-entry', entry['name'])
        report = chat._handle_get_report({'report_type': 'stock-balance'})
        self.assertEqual((report['total_rows'], report['returned'], report['has_more']), (260, 200, True))
        rest = chat._handle_get_report({'report_type': 'stock-balance', 'offset': 200})
        self.assertEqual((rest['returned'], rest['has_more']), (60, False))
        small = chat._handle_get_report({'report_type': 'stock-balance', 'filters': {'item_code': 'IT-0007'}})
        self.assertNotIn('total_rows', small)
        self.assertIn('error', chat._handle_get_report({'report_type': 'stock-balance', 'limit': 0}))
        # The document with 260 lines comes as a window too.
        doc = chat._handle_get_document({'doctype': 'stock-entry', 'name': entry['name']})
        self.assertEqual((len(doc['items']), doc['_lines']['items']['total'], doc['_lines']['items']['has_more']),
                         (200, 260, True))
        doc = chat._handle_get_document({'doctype': 'stock-entry', 'name': entry['name'], 'offset': 200})
        self.assertEqual((len(doc['items']), doc['_lines']['items']['has_more']), (60, False))
        short = chat._handle_get_document({'doctype': 'stock-entry', 'name': entry['name'], 'limit': 500})
        self.assertEqual(len(short['items']), 260)
        self.assertNotIn('_lines', short)

    # -- the net below every tool ------------------------------------------------
    def test_the_guard_cuts_what_no_tool_bounded(self):
        rows = [{'name': f'R-{i}', 'text': 'x' * 400} for i in range(1000)]
        cut = tool_results.bound_tool_result('some_tool', rows)
        self.assertTrue(cut['truncated'])
        self.assertEqual(cut['total'], 1000)
        self.assertLessEqual(size(cut), tool_results.max_chars())
        self.assertGreater(cut['returned'], 100)
        cut = tool_results.bound_tool_result('some_tool', {'summary': {'n': 1000}, 'rows': rows})
        self.assertEqual((cut['summary'], cut['_truncated']['field'], cut['_truncated']['total']), ({'n': 1000}, 'rows', 1000))
        self.assertLessEqual(size(cut), tool_results.max_chars())
        # Nothing to cut: an error that says why, not megabytes.
        blob = tool_results.bound_tool_result('some_tool', {'text': 'x' * 500000})
        self.assertIn('too large', blob['error'])
        # Small results and pictures for the model are left alone.
        self.assertEqual(tool_results.bound_tool_result('t', {'ok': True}), {'ok': True})
        kept = tool_results.bound_tool_result('t', {'page': 1, '_images': ['i' * 300000]})
        self.assertEqual((kept['page'], len(kept['_images'][0])), (1, 300000))
        with patch.dict('os.environ', {'LAMBDA_ERP_TOOL_RESULT_MAX_CHARS': '5000'}):
            self.assertLessEqual(size(tool_results.bound_tool_result('t', rows)), 5000)

    def test_mcp_answers_go_through_the_guard(self):
        user = {'name': 'admin', 'role': 'admin', 'user_id': 'admin'}
        with patch.object(mcp, '_call', return_value=[{'text': 'x' * 400} for _ in range(1000)]):
            answer = mcp._handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': 'search_masters', 'arguments': {}}}, user)
        text = json.dumps(answer, default=str)
        self.assertLess(len(text), tool_results.max_chars() * 1.2)
        self.assertIn('truncated', text)


if __name__ == '__main__':
    unittest.main()
