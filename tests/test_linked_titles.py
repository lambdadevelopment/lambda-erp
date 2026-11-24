"""Lists show the names of linked records, under the reader's access rules.

Synthetic data only; runs on SQLite (temp file) or on Postgres when
LAMBDA_ERP_TEST_DB is set.
"""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp import permissions


class LinkedTitles(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        db = self.db
        db.insert('Customer', {'name': 'C-OUT', 'customer_name': 'Outside Clinic', 'default_currency': 'USD'})
        db.insert('Item', {'name': 'G-1', 'item_name': 'Glove, medium', 'is_stock_item': 1, 'standard_rate': 1})
        db.commit()
        self.selling = services.create_document('price-list', {
            'price_list_name': 'Selling', 'currency': 'USD', 'selling': 1, 'buying': 0})['name']
        for customer, rate in (('C', 3), ('C-OUT', 4), (None, 5)):
            services.create_document('item-price', {'item_code': 'G-1', 'price_list': self.selling,
                                                    'customer': customer, 'rate': rate})

    def rows(self, fields):
        return services.list_documents('item-price', fields=fields, order_by='rate', order='asc')

    def test_names_of_linked_records(self):
        rows = self.rows(['item_code__title', 'customer__title', 'customer', 'rate'])
        self.assertEqual([(r['item_code__title'], r['customer'], r['customer__title']) for r in rows],
                         [('Glove, medium', 'C', 'Customer'), ('Glove, medium', 'C-OUT', 'Outside Clinic'),
                          ('Glove, medium', None, None)])
        # The id a name needs is fetched for it but not returned unless asked.
        self.assertNotIn('item_code', rows[0])
        self.assertIn('customer', rows[0])

    def test_a_name_the_reader_may_not_see_stays_empty(self):
        scoped = {'restrictions': {'profile': None, 'hidden': frozenset(),
                                   'permissions': {'default': None, 'doctypes': {}},
                                   'scope': {'customer': frozenset({'C'})}}}
        with permissions.acting_as(scoped):
            rows = self.rows(['customer', 'customer__title', 'item_code__title'])
        by_customer = {r['customer']: r['customer__title'] for r in rows}
        self.assertNotEqual(by_customer.get('C-OUT'), 'Outside Clinic')
        self.assertEqual(by_customer.get('C'), 'Customer')
        self.assertTrue(all(r['item_code__title'] == 'Glove, medium' for r in rows))

    def test_only_real_links_are_answered(self):
        self.assertEqual(services.linked_title_fields('Item Price', ['rate__title', 'nope__title', 'customer__title']),
                         {'customer__title': ('customer', 'Customer', 'customer_name')})
        rows = self.rows(['rate__title', 'rate'])
        self.assertNotIn('rate__title', rows[0])

    def test_the_list_route_passes_them_through(self):
        from types import SimpleNamespace
        from fastapi import HTTPException
        from starlette.datastructures import QueryParams
        from api.routers.documents import list_docs
        fields = 'name,customer,customer__title,item_code__title,bogus__title'
        request = SimpleNamespace(query_params=QueryParams({'fields': fields, 'order_by': 'rate', 'order': 'asc'}),
                                  headers={}, url=SimpleNamespace(path='/api/documents/item-price'))
        page = list_docs('item-price', request, order_by='rate', order='asc', fields=fields, limit=50, offset=0,
                         _user={'name': 'admin', 'role': 'admin'})
        first = page['rows'][0]
        self.assertEqual((first['customer__title'], first['item_code__title']), ('Customer', 'Glove, medium'))
        self.assertNotIn('bogus__title', first)
        with self.assertRaises(HTTPException) as caught:
            list_docs('item-price', SimpleNamespace(query_params=QueryParams({}), headers={}, url=SimpleNamespace(path='/')),
                      order_by='customer__title', limit=50, offset=0, _user={'name': 'admin', 'role': 'admin'})
        self.assertEqual(caught.exception.status_code, 400)

if __name__ == '__main__':
    unittest.main()
