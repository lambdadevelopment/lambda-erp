"""Number ranges: read them, set the next number, and follow hand-set numbers.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from api.number_series import get_number_series, set_next_number
from lambda_erp import permissions


def _price_list(name=None, title='List'):
    data = {'price_list_name': title, 'currency': 'USD', 'selling': 1, 'buying': 0}
    if name:
        data['name'] = name
    return services.create_document('price-list', data)['name']


class NumberSeries(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.prefix = services.DOCUMENT_CLASSES['Price List'].PREFIX

    def num(self, name):
        return int(name.rsplit('-', 1)[1])

    def test_reading_a_range_says_what_comes_next(self):
        first = _price_list(title='A')
        row = get_number_series({'doctype': 'price-list'})['series'][0]
        self.assertEqual(row['prefix'], self.prefix)
        self.assertEqual(row['last_number'], self.num(first))
        self.assertEqual(row['next_name'], f'{self.prefix}-{self.num(first) + 1:04d}')
        self.assertEqual(_price_list(title='B'), row['next_name'])
        everything = {r['doctype'] for r in get_number_series({})['series']}
        self.assertIn('sales-invoice', everything)

    def test_set_the_next_number_up_and_back_down_but_never_onto_a_used_one(self):
        first = _price_list(title='A')
        row = set_next_number({'doctype': 'price-list', 'next_number': 320})
        self.assertEqual(row['next_name'], f'{self.prefix}-0320')
        self.assertEqual(_price_list(title='B'), f'{self.prefix}-0320')
        with self.assertRaisesRegex(ValueError, 'at least 321'):
            set_next_number({'doctype': 'price-list', 'next_number': 320})
        with self.assertRaisesRegex(ValueError, 'at least 321'):
            set_next_number({'doctype': 'price-list', 'next_number': self.num(first)})
        # Moved too far by mistake: back down as far as the highest used number.
        set_next_number({'doctype': 'price-list', 'next_number': 5000})
        set_next_number({'doctype': 'price-list', 'next_number': 321})
        self.assertEqual(_price_list(title='C'), f'{self.prefix}-0321')

    def test_a_hand_set_number_ahead_of_the_range_moves_the_range_past_it(self):
        _price_list(title='A')
        self.assertEqual(_price_list(f'{self.prefix}-0321', 'B'), f'{self.prefix}-0321')
        self.assertEqual(_price_list(title='C'), f'{self.prefix}-0322')
        # A hand-set number behind the range leaves it alone.
        _price_list(f'{self.prefix}-0100', 'D')
        self.assertEqual(_price_list(title='E'), f'{self.prefix}-0323')
        # Names that are not "PREFIX-<digits>" do not count.
        _price_list(f'{self.prefix}-SPECIAL', 'F')
        _price_list(f'{self.prefix}-9000-X', 'G')
        self.assertEqual(_price_list(title='H'), f'{self.prefix}-0324')

    def test_a_series_never_used_yet_starts_after_the_highest_stored_number(self):
        self.db.sql('DELETE FROM "_NamingSeries" WHERE prefix = ?', [self.prefix])
        self.db.commit()
        _price_list(f'{self.prefix}-0040', 'A')  # first save also seeds the range
        self.assertEqual(get_number_series({'doctype': 'price-list'})['series'][0]['next_number'], 41)

    def test_only_admins_set_ranges_and_readers_see_only_their_types(self):
        from api.number_series import initialize_number_series
        initialize_number_series()
        meta = services.REGISTERED_ACTIONS
        self.assertEqual(meta['set_next_number']['minimum_role'], 'admin')
        self.assertEqual(meta['get_number_series']['minimum_role'], 'viewer')
        self.assertFalse(services.registered_action_allowed('set_next_number', 'manager'))
        self.assertTrue(services.registered_action_allowed('set_next_number', 'admin'))
        with self.assertRaisesRegex(ValueError, 'Unknown document type'):
            get_number_series({'doctype': 'nope'})
        with self.assertRaises(Exception):
            set_next_number({'doctype': 'price-list', 'next_number': 0})


if __name__ == '__main__':
    unittest.main(verbosity=2)
