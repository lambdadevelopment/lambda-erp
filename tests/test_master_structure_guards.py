"""Server-owned billing progress and structural master reference protection."""
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from unittest.mock import patch
from tests import test_document_requirements as requirements
from api import services, chat
from api.routers.masters import create_master_record, update_master_record, master_requirements
from api.routers.reports import _profit_and_loss
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.assets.asset import Asset
from lambda_erp.exceptions import ValidationError


class StructureGuards(unittest.TestCase):
    setUp = requirements.RequirementsTest.setUp
    tearDown = requirements.RequirementsTest.tearDown

    def sub(self, **extra):
        return services.create_document('subscription', dict(company='Test Co', party_type='Customer', party='C',
            start_date='2026-07-01', end_date='2026-09-01', plans=[dict(item_code='M', qty=1, rate=10)], **extra))

    def invoice(self):
        doc = services.create_document('sales-invoice',dict(company='Test Co',customer='C',items=[dict(item_code='M',qty=1,rate=100)]))
        return services.submit_document('sales-invoice',doc['name'])

    def test_create_cannot_seed_billing_progress(self):
        for field in ('current_invoice_start', 'current_invoice_end'):
            with self.assertRaisesRegex(ValidationError, 'server-managed'):
                self.sub(**{field:'2026-09-01'})
        self.assertFalse(self.db.get_all('Subscription'))
        # Direct model writes enforce the same rule.
        with self.assertRaisesRegex(ValidationError, 'server-managed'):
            Subscription(current_invoice_start='2026-09-01').save()

    def test_rewind_skip_and_clear_billing_progress_are_rejected(self):
        with patch('lambda_erp.accounting.subscription.nowdate',return_value='2026-09-11'):
            doc=self.sub()
            Subscription.load(doc['name']).process()
            for values in [dict(current_invoice_start='2026-07-01',current_invoice_end='2026-08-01'),
                           dict(current_invoice_start='2026-09-01',current_invoice_end='2026-09-01'),
                           dict(current_invoice_start=None),dict(current_invoice_end='')]:
                with self.assertRaisesRegex(ValidationError,'server-managed'):
                    services.update_document('subscription',doc['name'],values)
            current=Subscription.load(doc['name'])
            self.assertEqual(current.current_invoice_start,'2026-08-01')
            self.assertIsNotNone(current.process())
            self.assertIsNone(current.process())
            self.assertEqual(len(self.db.get_all('Sales Invoice')),2)

    def test_no_invoice_can_be_skipped_before_first_process(self):
        doc=self.sub()
        with self.assertRaisesRegex(ValidationError,'server-managed'):
            services.update_document('subscription',doc['name'],dict(current_invoice_start='2026-09-01'))
        self.assertEqual(Subscription.load(doc['name']).current_invoice_start,'2026-07-01')

    def test_direct_model_and_batch_cannot_rewind(self):
        doc=self.sub(); model=Subscription.load(doc['name'])
        model.current_invoice_end='2026-09-01'
        with self.assertRaisesRegex(ValidationError,'server-managed'): model.save()
        result=services.batch_update_documents('subscription',[dict(name=doc['name'],data=dict(current_invoice_end='2026-09-01'))])
        self.assertIn('server-managed',str(result))

    def test_round_trip_and_normal_processing_still_work(self):
        doc=self.sub()
        updated=services.update_document('subscription',doc['name'],doc)
        self.assertEqual(updated['current_invoice_start'],'2026-07-01')
        with patch('lambda_erp.accounting.subscription.nowdate',return_value='2026-08-01'):
            self.assertIsNotNone(Subscription.load(doc['name']).process())

    def test_metadata_explains_write_restrictions(self):
        metadata=services.document_field_metadata('subscription')
        self.assertEqual(metadata['read_only_fields'],['current_invoice_end','current_invoice_start'])
        self.assertIn('server_managed_fields',metadata['requirements'])
        self.assertIn('root_type',master_requirements('account')['structural_fields'])
        self.assertIn('Server-managed fields',chat._prompt_validation_context())
        self.assertIn('Once referenced, structural fields',chat._prompt_validation_context())

    def test_used_account_cannot_reclassify_reports(self):
        self.invoice()
        account=self.db.get_value('Company','Test Co','default_income_account')
        before=_profit_and_loss(self.db,company='Test Co')['total_income']
        for values in [dict(root_type='Asset',report_type='Balance Sheet'),dict(account_currency='CHF'),dict(is_group=1)]:
            with self.assertRaisesRegex(ValidationError,'referenced by'):
                update_master_record('account',account,values)
        self.assertEqual(_profit_and_loss(self.db,company='Test Co')['total_income'],before)
        renamed=update_master_record('account',account,dict(account_name='Sales renamed',root_type='Income'))
        self.assertEqual(renamed['account_name'],'Sales renamed')

    def test_used_warehouse_cannot_switch_company(self):
        booking=services.create_document('reservation',{**self.pool,'asset':self.a.name,'company':'Test Co'})
        self.db.insert('Company',dict(name='Other Co',company_name='Other Co',default_currency='USD'))
        with self.assertRaisesRegex(ValidationError,'referenced by'):
            update_master_record('warehouse','A',dict(company='Other Co'))
        self.assertEqual(self.db.get_value('Warehouse','A','company'),'Test Co')
        services.update_document('reservation',booking['name'],dict(status='Cancelled'))

    def test_used_item_cannot_remove_tracking_or_change_stock_semantics(self):
        booking=services.create_document('reservation',{**self.pool,'asset':self.a.name})
        for fields in [dict(is_asset_tracked=0),dict(is_stock_item=1),dict(stock_uom='Kg')]:
            with self.assertRaisesRegex(ValidationError,'referenced by'):
                update_master_record('item','M',fields)
        self.assertEqual(self.db.get_value('Item','M','is_asset_tracked'),1)
        services.update_document('reservation',booking['name'],dict(status='Cancelled'))
        self.assertEqual(update_master_record('item','M',dict(item_name='New label',is_asset_tracked=True))['item_name'],'New label')

    def test_legacy_untracked_reservation_can_still_be_released(self):
        booking=services.create_document('reservation',{**self.pool,'asset':self.a.name})
        # Simulate data from before this guard, not a supported mutation path.
        self.db.set_value('Item','M','is_asset_tracked',0)
        result=services.update_document('reservation',booking['name'],dict(status='Cancelled'))
        self.assertEqual(result['status'],'Cancelled')

    def test_unused_master_structure_can_change(self):
        create_master_record('item',dict(name='UNUSED',item_name='Unused',is_asset_tracked=0))
        self.assertEqual(update_master_record('item','UNUSED',dict(is_asset_tracked=1))['is_asset_tracked'],1)
        create_master_record('account',dict(name='UNUSED-ACC',account_name='Unused account',company='Test Co',root_type='Income',report_type='Profit and Loss',is_group=0))
        self.assertEqual(update_master_record('account','UNUSED-ACC',dict(root_type='Asset',report_type='Balance Sheet'))['root_type'],'Asset')

    def test_company_currency_and_used_cost_center_company_are_protected(self):
        with self.assertRaisesRegex(ValidationError,'referenced by'):
            update_master_record('company','Test Co',dict(default_currency='CHF'))
        center=self.db.get_value('Company','Test Co','default_cost_center')
        self.db.insert('Company',dict(name='Other Co',company_name='Other Co'))
        with self.assertRaisesRegex(ValidationError,'referenced by'):
            update_master_record('cost-center',center,dict(company='Other Co'))

    def test_concurrent_first_reference_prevents_tracking_change(self):
        create_master_record('item',dict(name='FRESH',item_name='Fresh',is_asset_tracked=1))
        checked, release, changing = threading.Event(),threading.Event(),threading.Event()
        original=Asset.validate
        def paused(doc):
            original(doc)
            if doc.item_code=='FRESH':
                checked.set()
                if not release.wait(5): raise RuntimeError('test did not release writer')
        def create():
            try: return Asset(item_code='FRESH',warehouse='A').save().name
            finally: self.db.conn.close()
        def change():
            try:
                changing.set()
                return update_master_record('item','FRESH',dict(is_asset_tracked=0))
            finally: self.db.conn.close()
        with patch.object(Asset,'validate',paused):
            with ThreadPoolExecutor(max_workers=2) as executor:
                created=executor.submit(create)
                try:
                    self.assertTrue(checked.wait(5))
                    changed=executor.submit(change)
                    self.assertTrue(changing.wait(5))
                    # The edit must wait for this first reference to commit.
                    with self.assertRaises(TimeoutError): changed.result(timeout=0.2)
                finally: release.set()
                self.assertTrue(created.result(timeout=10))
                with self.assertRaisesRegex(ValidationError,'referenced by'): changed.result(timeout=10)
        self.assertEqual(self.db.get_value('Item','FRESH','is_asset_tracked'),1)

    def test_structure_edit_before_first_use_is_observed_by_writer(self):
        from api.routers import masters
        create_master_record('item',dict(name='FRESH',item_name='Fresh',is_asset_tracked=1))
        checked, release, writing=threading.Event(),threading.Event(),threading.Event()
        original=masters._structural_reference
        def paused(*args):
            result=original(*args)
            checked.set()
            if not release.wait(5): raise RuntimeError('test did not release edit')
            return result
        def change():
            try: return update_master_record('item','FRESH',dict(is_asset_tracked=0))
            finally: self.db.conn.close()
        def create():
            try:
                writing.set()
                return Asset(item_code='FRESH',warehouse='A').save()
            finally: self.db.conn.close()
        with patch.object(masters,'_structural_reference',paused):
            with ThreadPoolExecutor(max_workers=2) as executor:
                changed=executor.submit(change)
                try:
                    self.assertTrue(checked.wait(5))
                    created=executor.submit(create)
                    self.assertTrue(writing.wait(5))
                    with self.assertRaises(TimeoutError): created.result(timeout=0.2)
                finally: release.set()
                self.assertEqual(changed.result(timeout=10)['is_asset_tracked'],0)
                with self.assertRaisesRegex(ValidationError,'not asset-tracked'): created.result(timeout=10)


if __name__=='__main__': unittest.main()
