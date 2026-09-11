"""Regression coverage for lifecycle, rental dependencies and fresh connections."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests import test_document_requirements as requirements
from api import services, chat
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.assets.asset import Asset
from lambda_erp.assets.reservation import Reservation, overlapping_reservations
from lambda_erp.exceptions import ValidationError, DocumentStatusError


class LifecycleGuards(unittest.TestCase):
    setUp = requirements.RequirementsTest.setUp
    tearDown = requirements.RequirementsTest.tearDown

    def booking(self, **extra):
        return services.create_document('reservation', {**self.pool, **extra})

    def subscription(self, **extra):
        return services.create_document('subscription', dict(company='Test Co', party_type='Customer', party='C',
            start_date='2026-07-01', billing_interval='Monthly',
            plans=[dict(item_code='M', qty=1, rate=10)], **extra))

    def order(self, submit=True):
        doc = services.create_document('sales-order', dict(company='Test Co', customer='C', items=[dict(item_code='M', qty=1, rate=10)]))
        return services.submit_document('sales-order', doc['name']) if submit else doc

    def test_fresh_connection_rolls_back_and_preserves_original_exception(self):
        def worker():
            try:
                with self.db.atomic():
                    self.db.insert('Customer', {'name': 'ROLLBACK', 'customer_name': 'Rollback'})
                    with self.db.atomic():
                        self.db.insert('Customer', {'name': 'NESTED', 'customer_name': 'Nested'})
                    raise ValueError('intentional failure')
            finally:
                self.db.conn.close()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaisesRegex(ValueError, 'intentional failure'):
                executor.submit(worker).result(timeout=10)
        self.assertFalse(self.db.exists('Customer', 'ROLLBACK'))
        self.assertFalse(self.db.exists('Customer', 'NESTED'))

    def test_fresh_connection_commits_successfully(self):
        def worker():
            try:
                with self.db.atomic():
                    self.db.insert('Customer', {'name': 'COMMIT', 'customer_name': 'Commit'})
            finally:
                self.db.conn.close()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(worker).result(timeout=10)
        self.assertTrue(self.db.exists('Customer', 'COMMIT'))

    def test_reservation_rejects_generic_submit_and_keeps_blocking(self):
        doc = self.booking(asset=self.a.name)
        with self.assertRaisesRegex(DocumentStatusError, 'does not support'):
            services.submit_document('reservation', doc['name'])
        self.assertEqual(services.load_document('reservation', doc['name'])['status'], 'Reserved')
        with self.assertRaisesRegex(ValidationError, 'already committed'):
            self.booking(asset=self.a.name)
        with self.assertRaisesRegex(DocumentStatusError, 'does not support'):
            services.submit_document('asset', self.a.name)

    def test_lifecycle_metadata_reaches_chat(self):
        for slug in ('asset', 'reservation', 'subscription', 'budget', 'pricing-rule', 'bank-account'):
            self.assertFalse(services.document_field_metadata(slug)['requirements']['lifecycle']['submit'])
        self.assertTrue(services.document_field_metadata('sales-order')['requirements']['lifecycle']['submit'])
        self.assertIn('Does not support generic submit/cancel', chat._prompt_validation_context())
        self.assertFalse(services.document_field_metadata('budget')['requirements']['lifecycle']['discard'])

    def test_discard_requires_persisted_flag(self):
        from lambda_erp.accounting.budget import Budget
        with self.assertRaisesRegex(DocumentStatusError, 'does not support discard'):
            Budget(name='not-needed').discard()

    def test_discarded_subscription_never_bills(self):
        doc = self.subscription()
        stale = Subscription.load(doc['name'])
        services.discard_document('subscription', doc['name'])
        self.assertEqual(Subscription.load(doc['name']).discarded, 1)
        self.assertIsNone(stale.process())
        self.assertFalse(self.db.get_all('Sales Invoice', filters={'subscription': doc['name']}))
        with self.assertRaises(DocumentStatusError):
            services.update_document('subscription', doc['name'], {'status': 'Active'})

    def test_legacy_discarded_subscription_migration(self):
        from lambda_erp.database import _m029_subscription_discarded
        doc = self.subscription()
        self.db.set_value('Subscription', doc['name'], {'status': 'Discarded', 'discarded': 0})
        _m029_subscription_discarded(self.db)
        self.db.commit()
        self.assertEqual(Subscription.load(doc['name']).discarded, 1)
        self.assertIsNone(Subscription.load(doc['name']).process())

    def test_voucher_cannot_cancel_with_active_booking(self):
        order = self.order()
        booking = self.booking(asset=self.a.name, voucher_type='Sales Order', voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError, 'Resolve active reservation'):
            services.cancel_document('sales-order', order['name'])
        self.assertEqual(services.load_document('sales-order', order['name'])['docstatus'], 1)
        services.update_document('reservation', booking['name'], {'status': 'Cancelled'})
        services.cancel_document('sales-order', order['name'])
        with self.assertRaisesRegex(ValidationError, 'non-cancelled'):
            self.booking(asset=self.a.name, voucher_type='Sales Order', voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError, 'non-cancelled'):
            services.update_document('reservation', booking['name'], {'status': 'Reserved'})

    def test_draft_voucher_cannot_discard_with_active_booking(self):
        order = self.order(submit=False)
        booking = self.booking(voucher_type='Sales Order', voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError, 'Resolve active reservation'):
            services.discard_document('sales-order', order['name'])
        services.discard_document('reservation', booking['name'])
        services.discard_document('sales-order', order['name'])
        with self.assertRaisesRegex(ValidationError, 'non-discarded'):
            self.booking(voucher_type='Sales Order', voucher_no=order['name'])

    def test_reserved_asset_changes_require_resolution(self):
        booking = self.booking(asset=self.a.name, status='Out')
        for values in ({'warehouse': 'B'}, {'status': 'Retired'}, {'disabled': 1}):
            with self.assertRaisesRegex(ValidationError, 'Resolve active reservation'):
                services.update_document('asset', self.a.name, values)
        with self.assertRaisesRegex(ValidationError, 'Resolve active reservation'):
            services.discard_document('asset', self.a.name)
        with self.assertRaisesRegex(DocumentStatusError, 'Use discard'):
            services.update_document('asset', self.a.name, {'discarded': 1})
        self.assertEqual(Asset.load(self.a.name).warehouse, 'A')
        services.update_document('reservation', booking['name'], {'status': 'Returned'})
        services.update_document('asset', self.a.name, {'warehouse': 'B', 'status': 'Retired'})

    def test_pool_capacity_cannot_be_removed_while_booked(self):
        booking = self.booking()
        with self.assertRaisesRegex(ValidationError, 'pooled bookings'):
            services.update_document('asset', self.a.name, {'warehouse': 'B'})
        services.update_document('reservation', booking['name'], {'status': 'Cancelled'})
        services.discard_document('asset', self.a.name)

    def test_expired_subscription_catches_up_before_completion(self):
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-09-11'):
            doc = self.subscription(end_date='2026-09-01')
            sub = Subscription.load(doc['name'])
            self.assertNotEqual(sub.status, 'Completed')
            self.assertIsNotNone(sub.process())
            self.assertEqual(sub.current_invoice_start, '2026-08-01')
            self.assertNotEqual(sub.status, 'Completed')
            self.assertIsNotNone(sub.process())
            self.assertEqual(sub.current_invoice_start, '2026-09-01')
            self.assertEqual(sub.status, 'Completed')
            self.assertIsNone(sub.process())
            self.assertEqual(len(self.db.get_all('Sales Invoice', filters={'subscription': doc['name']})), 2)

    def test_legacy_completed_but_unbilled_subscription_is_rechecked(self):
        doc = self.subscription(end_date='2026-09-01')
        self.db.set_value('Subscription', doc['name'], 'status', 'Completed')
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-09-11'):
            self.assertIsNotNone(Subscription.load(doc['name']).process())

    def test_stale_subscription_cannot_rewind_billed_period(self):
        doc = self.subscription()
        stale = Subscription.load(doc['name'])
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-09-11'):
            Subscription.load(doc['name']).process()
        with self.assertRaisesRegex(DocumentStatusError, 'changed since'):
            stale.save()
        self.assertEqual(Subscription.load(doc['name']).current_invoice_start, '2026-08-01')

    def test_extending_completed_subscription_starts_next_period(self):
        doc = self.subscription(end_date='2026-08-01')
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-08-01'):
            sub = Subscription.load(doc['name'])
            sub.process()
            self.assertEqual(sub.status, 'Completed')
            services.update_document('subscription', doc['name'], {'end_date': '2026-09-01'})
            self.assertIsNone(Subscription.load(doc['name']).process())
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-09-01'):
            self.assertIsNotNone(Subscription.load(doc['name']).process())

    def test_subscription_end_boundary_and_cancel(self):
        doc = self.subscription(end_date='2026-07-15')
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-07-14'):
            self.assertIsNone(Subscription.load(doc['name']).process())
        with patch('lambda_erp.accounting.subscription.nowdate', return_value='2026-07-15'):
            sub = Subscription.load(doc['name'])
            self.assertEqual(sub.process()['grand_total'], 10)
            self.assertEqual(sub.status, 'Completed')
            self.assertIsNone(sub.process())
        cancelled = self.subscription(status='Cancelled')
        self.assertIsNone(Subscription.load(cancelled['name']).process())
        empty = self.subscription(end_date='2026-07-01')
        self.assertIsNone(Subscription.load(empty['name']).process())

    def test_invoice_subscription_link_must_match_party(self):
        sub = self.subscription()
        self.db.insert('Customer', {'name': 'OTHER', 'customer_name': 'Other'})
        with self.assertRaisesRegex(ValidationError, 'match invoice company and party'):
            services.create_document('sales-invoice', dict(company='Test Co', customer='OTHER',
                subscription=sub['name'], items=[dict(item_code='M', qty=1, rate=10)]))

    def _booking_against_dependency_change(self, cancel_order=False):
        order = self.order() if cancel_order else None
        checked, release, changing = threading.Event(), threading.Event(), threading.Event()
        original = Reservation._check_availability
        def paused(doc, *args, **kwargs):
            original(doc, *args, **kwargs)
            checked.set()
            if not release.wait(5): raise RuntimeError('test did not release booking')
        def book():
            try:
                extra = dict(voucher_type='Sales Order', voucher_no=order['name']) if order else {}
                return self.booking(asset=self.a.name, **extra)
            finally: self.db.conn.close()
        def change():
            try:
                changing.set()
                if order: return services.cancel_document('sales-order', order['name'])
                return services.update_document('asset', self.a.name, {'status': 'Retired'})
            finally: self.db.conn.close()
        with patch.object(Reservation, '_check_availability', paused):
            with ThreadPoolExecutor(max_workers=2) as executor:
                booking = executor.submit(book)
                try:
                    self.assertTrue(checked.wait(5))
                    mutation = executor.submit(change)
                    self.assertTrue(changing.wait(5))
                finally: release.set()
                self.assertEqual(booking.result(timeout=10)['status'], 'Reserved')
                with self.assertRaisesRegex(ValidationError, 'Resolve active reservation'):
                    mutation.result(timeout=10)

    def test_reserving_blocks_concurrent_asset_retirement(self):
        self._booking_against_dependency_change()

    def test_reserving_blocks_concurrent_order_cancellation(self):
        self._booking_against_dependency_change(cancel_order=True)

    def _concurrent_reservations(self, first_asset, second_asset):
        first_checked, release = threading.Event(), threading.Event()
        second_checked, second_started = threading.Event(), threading.Event()
        original = Reservation._check_availability
        def checked(doc, *args, **kwargs):
            original(doc, *args, **kwargs)
            if doc.name == 'FIRST':
                first_checked.set()
                if not release.wait(5):
                    raise RuntimeError('test did not release first booking')
            else:
                second_checked.set()
        def worker(name, asset):
            try:
                if name == 'SECOND': second_started.set()
                return self.booking(name=name, **({'asset': asset} if asset else {}))
            finally:
                self.db.conn.close()
        with patch.object(Reservation, '_check_availability', checked):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(worker, 'FIRST', first_asset)
                try:
                    self.assertTrue(first_checked.wait(5))
                    second = executor.submit(worker, 'SECOND', second_asset)
                    self.assertTrue(second_started.wait(5))
                    self.assertFalse(second_checked.wait(0.2), 'second booking validated against an uncommitted availability snapshot')
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=10)['status'], 'Reserved')
                with self.assertRaises(ValidationError):
                    second.result(timeout=10)
        active = overlapping_reservations(self.db, item_code='M', warehouse='A', from_dt=self.pool['from_datetime'], to_dt=self.pool['to_datetime'])
        self.assertEqual(len(active), 1)

    def test_concurrent_unit_reservations(self):
        self._concurrent_reservations(self.a.name, self.a.name)

    def test_concurrent_pool_reservations(self):
        self._concurrent_reservations(None, None)

    def test_concurrent_mixed_reservations(self):
        self._concurrent_reservations(None, self.a.name)


if __name__ == '__main__':
    unittest.main()
