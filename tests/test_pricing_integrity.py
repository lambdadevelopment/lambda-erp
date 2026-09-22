"""Pricing, provenance and GL outcomes on synthetic SQLite/PostgreSQL data."""
import unittest
from tests import test_document_requirements as fixtures
from api import services
from lambda_erp.exceptions import ValidationError
from lambda_erp.controllers.pricing_rule import PricingRule
from lambda_erp.controllers.item_price import PriceList, ItemPrice
from lambda_erp.accounting.sales_invoice import SalesInvoice, make_sales_return
from lambda_erp.accounting.purchase_invoice import PurchaseInvoice, make_purchase_return
from lambda_erp.accounting.pos_invoice import POSInvoice, make_pos_return
from lambda_erp.selling.quotation import Quotation, make_sales_order, make_sales_invoice_from_quotation
from lambda_erp.selling.sales_order import make_sales_invoice
from lambda_erp.buying.purchase_order import PurchaseOrder, make_purchase_invoice


class PricingIntegrity(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.db.insert('Supplier', {'name': 'SUP', 'supplier_name': 'Supplier', 'default_currency': 'USD'})
        self.db.set_value('Item', 'M', 'standard_rate', 200)
        self.rule = PricingRule(title='Campaign', item_code='M', selling=1, buying=1,
                                rate_or_discount='Rate', rate=120).save()

    def invoice(self, cls=SalesInvoice, **overrides):
        party = {'supplier': 'SUP'} if cls is PurchaseInvoice else {'customer': 'C'}
        if cls is POSInvoice:
            items = overrides.get('items', [dict(qty=1, rate=145)])
            amount = sum(row['qty'] * row['rate'] for row in items)
            party['payments'] = [dict(mode_of_payment='Cash', amount=amount,
                account=self.db.get_value('Account', {'company':'Test Co','account_type':'Cash'}, 'name'))]
        return cls(company='Test Co', **party, **{
            'external_source': 'findmee/org-1', 'external_reference': 'export-1',
            'items': [dict(item_code='M', qty=1, rate=145, external_line_reference='line-1')],
            **overrides,
        })

    def test_returns_keep_historical_rate_and_get_new_identity(self):
        for cls, convert in [(SalesInvoice, make_sales_return), (PurchaseInvoice, make_purchase_return),
                             (POSInvoice, make_pos_return)]:
            with self.subTest(doctype=cls.DOCTYPE):
                original = self.invoice(cls).save()
                original.submit()
                credit = convert(original.name).save()
                credit.save()  # repeat saves cannot reprice the credit
                self.assertEqual(credit.items[0]['rate'], 145)
                self.assertEqual(credit.grand_total, -145)
                self.assertEqual(credit.external_source, original.external_source)
                self.assertIsNone(credit.external_reference)
                self.assertEqual(credit.items[0]['external_line_reference'], 'line-1')
                credit.external_reference = 'correction-1'
                credit.save().submit()
                self.assertEqual(sum(r['debit']-r['credit'] for r in self.db.get_all(
                    'GL Entry', filters={'voucher_no': credit.name}, fields=['debit','credit'])), 0)
                credit.cancel()
                original.cancel()

    def test_conversions_keep_document_and_line_protection(self):
        for flag in [dict(external_source='findmee/org-1'), dict(ignore_pricing_rule=1), {}]:
            lines = [dict(item_code='M', qty=1, rate=145, ignore_pricing_rule=1,
                          external_line_reference='quote-line')]
            quote = Quotation(company='Test Co', customer='C', items=lines, **flag).save()
            quote.submit()
            order = make_sales_order(quote.name).save()
            order.submit()
            for invoice in [make_sales_invoice(order.name), make_sales_invoice_from_quotation(quote.name)]:
                invoice.save()
                self.assertEqual(invoice.items[0]['rate'], 145)
                self.assertEqual(invoice.items[0]['external_line_reference'], 'quote-line')
                self.assertIsNone(invoice.external_reference)
                invoice.discard()
            order.cancel()
            quote.cancel()
        po = PurchaseOrder(company='Test Co', supplier='SUP', external_source='findmee/org-1',
                           items=[dict(item_code='M', qty=1, rate=145)]).save()
        self.assertEqual(po.items[0]['rate'],145)
        po.submit()
        pi = make_purchase_invoice(po.name).save()
        self.assertEqual(pi.items[0]['rate'],145)
        self.assertEqual(pi.external_source,po.external_source)
        pi.discard()
        po.cancel()

    def test_income_cost_centres_survive_posting_return_and_cancel(self):
        self.db.insert('Cost Center', dict(name='East', cost_center_name='East', company='Test Co'))
        self.db.insert('Cost Center', dict(name='West', cost_center_name='West', company='Test Co'))
        for cls, convert in [(SalesInvoice, make_sales_return), (POSInvoice, make_pos_return)]:
            doc=self.invoice(cls, items=[dict(item_code='M', qty=1, rate=100, cost_center='East'),
                                       dict(item_code='M', qty=1, rate=200, cost_center='West'),
                                       dict(item_code='M', qty=1, rate=50, cost_center='East')]).save()
            doc.submit()
            income=doc.items[0]['income_account']
            def totals(name):
                rows=self.db.get_all('GL Entry',filters={'voucher_no':name,'account':income},
                                     fields=['cost_center','debit','credit'])
                result={}
                for row in rows:
                    cc=row['cost_center']; result[cc]=result.get(cc,0)+row['credit']-row['debit']
                return result
            self.assertEqual(totals(doc.name),{'East':150,'West':200})
            credit=convert(doc.name).save(); credit.submit()
            self.assertEqual(totals(credit.name),{'East':-150,'West':-200})
            credit.cancel(); doc.cancel()
            self.assertEqual(totals(doc.name),{'East':0,'West':0})
            self.assertEqual(totals(credit.name),{'East':0,'West':0})

    def test_external_identity_duplicate_recovery_and_immutability(self):
        doc=self.invoice().save()
        with self.assertRaises(Exception) as error:
            self.invoice().save()
        self.assertIn('unique',str(error.exception).lower())
        found=services.list_documents('sales-invoice',filters={
            'external_source':'findmee/org-1','external_reference':'export-1'})
        self.assertEqual([d['name'] for d in found],[doc.name])
        self.assertEqual(len(self.db.get_all('Sales Invoice Item',filters={'parent':doc.name})),1)
        doc.external_reference='changed'
        with self.assertRaisesRegex(ValidationError,'Cannot change external_reference'):
            doc.save()
        for changes in [dict(external_reference=' '),dict(external_source=None),dict(items=[
            dict(item_code='M',qty=1,rate=10,external_line_reference='same'),
            dict(item_code='M',qty=1,rate=20,external_line_reference='same')])]:
            with self.assertRaises(ValidationError): self.invoice(**changes).save()
        # Retrying after discard must still find the old identity, not recreate it.
        doc=SalesInvoice.load(doc.name); doc.discard()
        with self.assertRaises(Exception): self.invoice().save()
        other=self.invoice(external_source='findmee/org-2').save()
        self.assertNotEqual(other.name,doc.name)

    def test_pricing_master_api_and_repeated_discount_save(self):
        from api.routers.masters import update_master_record
        self.rule.enabled=0; self.rule.save()
        pl=services.create_document('price-list',dict(price_list_name='CHF prices',currency='USD',selling='1',buying='0',enabled='1'))
        services.create_document('item-price',dict(item_code='M',price_list=pl['name'],rate=180,customer='C'))
        update_master_record('customer','C',{'default_price_list':pl['name']})
        with self.assertRaises(ValidationError): update_master_record('customer','C',{'default_price_list':'missing'})
        for bad in [dict(selling='0',buying='0'),dict(currency='')]:
            with self.assertRaises(ValidationError): PriceList(price_list_name='Invalid',**{'currency':'USD','selling':1,**bad}).save()
        rule=PricingRule(title='Discount',item_code='M',selling=1,rate_or_discount='Discount Percentage',discount_percentage=10).save()
        doc=SalesInvoice(company='Test Co',customer='C',items=[dict(item_code='M',qty=1,rate=None)]).save()
        self.assertEqual(doc.items[0]['rate'],162)
        doc.save(); self.assertEqual(doc.items[0]['rate'],162)
        # An explicit rate without a list also has a stable discount base.
        update_master_record('customer','C',{'default_price_list':None})
        doc=SalesInvoice(company='Test Co',customer='C',items=[dict(item_code='M',qty=1,rate=100)]).save()
        doc.save(); self.assertEqual(doc.items[0]['rate'],90)
        free=SalesInvoice(company='Test Co',customer='C',items=[dict(item_code='M',qty=1,rate=0)]).save()
        self.assertEqual(free.grand_total,0)

    def test_external_stock_guard_survives_conversion(self):
        original=self.invoice().save(); original.submit()
        credit=make_sales_return(original.name); credit.update_stock=1
        with self.assertRaisesRegex(ValidationError,'update_stock=1'): credit.save()

    def test_upgrade_from_pre_pricing_schema_preserves_existing_data(self):
        from lambda_erp.database import _PRICED_DOCUMENTS
        legacy=self.invoice(external_source=None,external_reference=None,
                            items=[dict(item_code='M',qty=1,rate=120)]).save()
        for table, child in _PRICED_DOCUMENTS:
            self.db.sql(f'DROP INDEX "ux_{table.replace(" ", "_").lower()}_external"')
            self.db.sql(f'DROP INDEX "ux_{child.replace(" ", "_").lower()}_external_line"')
            for field in ['ignore_pricing_rule','external_source','external_reference']:
                self.db.drop_column(table,field)
            for field in ['ignore_pricing_rule','external_line_reference','pricing_rule']:
                self.db.drop_column(child,field)
        for table in ['Item Price','Price List']:
            self.db.sql(f'DROP TABLE "{table}"')
        self.db.drop_column('Company','default_price_list')
        for field in ['apply_on','item_group','applicable_for','customer','customer_group','territory','supplier','max_qty','min_amt','max_amt']:
            self.db.drop_column('Pricing Rule',field)
        self.db.sql('DELETE FROM "_SchemaMigrations" WHERE version >= 32')
        self.db._setup_schema()
        self.assertEqual(self.db.get_value('Pricing Rule',self.rule.name,'apply_on'),'Item Code')
        restored=SalesInvoice.load(legacy.name)
        self.assertEqual(restored.items[0]['rate'],120)
        self.assertEqual(restored.ignore_pricing_rule,0)
        self.assertIsNone(restored.external_reference)
        restored.save()
        self.assertEqual(restored.grand_total,120)
        self.assertEqual(len(self.db.sql('SELECT version FROM "_SchemaMigrations" WHERE version >= 32')),5)
        self.invoice().save()
        with self.assertRaises(Exception): self.invoice().save()

    def test_migration_failure_is_not_recorded_as_success(self):
        self.db.sql('DELETE FROM "_SchemaMigrations" WHERE version = 36')
        index='ux_sales_invoice_external'
        self.db.sql(f'DROP INDEX "{index}"')
        # A prior release could claim m033 applied while missing the index.
        self.db.insert('Sales Invoice',dict(name='old-a',external_source='legacy',external_reference='duplicate'))
        self.db.insert('Sales Invoice',dict(name='old-b',external_source='legacy',external_reference='duplicate'))
        with self.assertRaisesRegex(RuntimeError,'Required migration 36'):
            self.db._migrate()
        self.assertEqual(self.db.sql('SELECT version FROM "_SchemaMigrations" WHERE version = 36'),[])
        self.db.sql('UPDATE "Sales Invoice" SET external_reference = ? WHERE name = ?', ['corrected','old-b'])
        self.db._migrate()
        self.assertTrue(self.db.sql('SELECT version FROM "_SchemaMigrations" WHERE version = 36'))
        with self.assertRaises(Exception):
            with self.db.atomic():
                self.db.insert('Sales Invoice',dict(name='old-c',external_source='legacy',external_reference='duplicate'))


if __name__=='__main__':
    unittest.main()
