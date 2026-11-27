"""Where a stock line goes when nobody named a warehouse: the item's default
warehouse, else the company's only warehouse. Synthetic data only; runs on
SQLite (temp file) or on Postgres when LAMBDA_ERP_TEST_DB is set."""
import unittest

from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.controllers.item_price import default_warehouse


class DefaultWarehouse(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Company', {'name': 'Solo Co', 'company_name': 'Solo Co', 'default_currency': 'USD'})
        self.db.insert('Warehouse', {'name': 'SOLO', 'warehouse_name': 'Solo main', 'company': 'Solo Co'})
        self.db.insert('Warehouse', {'name': 'SOLO-GRP', 'warehouse_name': 'Solo group', 'company': 'Solo Co', 'is_group': 1})
        self.db.insert('Item', {'name': 'N', 'item_name': 'Service', 'is_stock_item': 0, 'standard_rate': 5})
        self.db.commit()

    def order(self, company='Test Co', slug='sales-order', **line):
        date = 'posting_date' if slug in ('sales-invoice', 'delivery-note') else 'transaction_date'
        return services.create_document(slug, {
            'company': company, 'customer': 'C', date: '2026-11-27',
            'items': [{'item_code': 'S', 'qty': 1, 'rate': 10, **line}]})

    def test_item_default_then_only_warehouse(self):
        # Several warehouses and no default: left empty, nothing is guessed.
        self.assertFalse(self.order()['items'][0].get('warehouse'))
        self.db.set_value('Item', 'S', 'default_warehouse', 'A')
        self.db.commit()
        self.assertEqual(self.order()['items'][0]['warehouse'], 'A')
        self.assertEqual(self.order(slug='quotation')['items'][0]['warehouse'], 'A')
        # A named warehouse stays.
        other = [w['name'] for w in self.db.get_all('Warehouse', filters={'company': 'Test Co'}, fields=['name'])
                 if w['name'] != 'A'][0]
        self.assertEqual(self.order(warehouse=other)['items'][0]['warehouse'], other)
        # The item's default belongs to another company: the company's only warehouse.
        self.assertEqual(self.order(company='Solo Co')['items'][0]['warehouse'], 'SOLO')
        self.assertEqual(default_warehouse('S', 'Solo Co'), 'SOLO')

    def test_not_for_services_or_invoices_that_move_no_stock(self):
        self.db.set_value('Item', 'S', 'default_warehouse', 'A')
        self.db.commit()
        service = services.create_document('sales-order', {
            'company': 'Test Co', 'customer': 'C', 'transaction_date': '2026-11-27',
            'items': [{'item_code': 'N', 'qty': 1, 'rate': 5}]})
        self.assertFalse(service['items'][0].get('warehouse'))
        invoice = self.order(slug='sales-invoice')
        self.assertFalse(invoice['items'][0].get('warehouse'))

    def test_consignment_warehouse_is_never_the_default(self):
        self.db.insert('Customer', {'name': 'K', 'customer_name': 'Consignee', 'default_currency': 'USD'})
        self.db.insert('Warehouse', {'name': 'SOLO-KONSI', 'warehouse_name': 'At the customer', 'company': 'Solo Co',
                                     'consignment_customer': 'K'})
        self.db.commit()
        self.assertEqual(default_warehouse('S', 'Solo Co'), 'SOLO')


if __name__ == '__main__':
    unittest.main()
