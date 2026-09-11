"""Regression tests for the five remaining audited workflow gaps."""
import unittest
from unittest.mock import patch
from tests import test_document_requirements as requirements
from api import services, chat
from api.routers.masters import update_master_record
from lambda_erp.exceptions import ValidationError
from lambda_erp.accounting.sales_invoice import SalesInvoice, make_sales_return
from lambda_erp.accounting.subscription import Subscription
from lambda_erp.accounting.bank_transaction import BankTransaction


class BusinessGuards(unittest.TestCase):
    setUp = requirements.RequirementsTest.setUp
    tearDown = requirements.RequirementsTest.tearDown

    def bank_account(self):
        return self.db.get_all('Account',filters={'account_type':'Bank','is_group':0})[0]['name']

    def sales(self, kind='sales-invoice', **extra):
        return services.create_document(kind,dict(company='Test Co',customer='C',items=[dict(item_code='M',qty=1,rate=100)],**extra))

    def booking(self, **extra):
        return services.create_document('reservation',{**self.pool,'asset':self.a.name,**extra})

    def sub(self):
        return services.create_document('subscription',dict(company='Test Co',party_type='Customer',party='C',start_date='2026-07-01',plans=[dict(item_code='M',qty=1,rate=10)]))

    def rule(self, **extra):
        return services.create_document('pricing-rule',dict(title='Discount',item_code='M',selling=1,rate_or_discount='Discount Percentage',discount_percentage=25,**extra))

    def test_bank_create_cannot_fabricate_reconciliation(self):
        base=dict(bank_account=self.bank_account(),deposit=100)
        for data in [dict(allocated_amount=100),dict(status='Reconciled'),dict(unallocated_amount=100),
                     dict(reference_name='FAKE'),dict(reconciled_by='admin'),dict(bank_statement_import='FAKE')]:
            with self.assertRaisesRegex(ValidationError,'server-managed'):
                services.create_document('bank-transaction',{**base,**data})
        self.assertFalse(self.db.get_all('Bank Transaction'))
        self.assertFalse(self.db.get_all('Bank Reconciliation'))
        self.assertFalse(self.db.get_all('GL Entry'))

    def test_manual_bank_updates_stay_unreconciled(self):
        doc=services.create_document('bank-transaction',dict(bank_account=self.bank_account(),deposit=100,allocated_amount=0,status='Unreconciled'))
        self.assertEqual(doc['unallocated_amount'],100)
        doc['deposit']=200
        updated=services.update_document('bank-transaction',doc['name'],doc)
        self.assertEqual(updated['unallocated_amount'],200)
        self.assertEqual(updated['status'],'Unreconciled')
        model=BankTransaction.load(doc['name']);model.allocated_amount=200
        with self.assertRaisesRegex(ValidationError,'server-managed'): model.save()
        with self.assertRaisesRegex(ValidationError,'server-managed'):
            services.update_document('bank-transaction',doc['name'],dict(status='Reconciled'))

    def test_invalid_manual_bank_amounts_rejected(self):
        for amount in (-10,float('inf'),float('nan')):
            with self.assertRaisesRegex(ValidationError,'finite and non-negative'):
                services.create_document('bank-transaction',dict(bank_account=self.bank_account(),deposit=amount))

    def test_legacy_fake_bank_allocation_cannot_be_resaved(self):
        doc=services.create_document('bank-transaction',dict(bank_account=self.bank_account(),deposit=100))
        self.db.set_value('Bank Transaction',doc['name'],dict(allocated_amount=100,status='Reconciled'))
        with self.assertRaisesRegex(ValidationError,'cannot be marked reconciled'):
            services.update_document('bank-transaction',doc['name'],dict(description='changed'))

    def test_foreign_company_rule_does_not_affect_order(self):
        self.db.insert('Company',dict(name='Other Co',company_name='Other Co',default_currency='USD'))
        self.rule(company='Other Co',priority=100)
        self.assertEqual(self.sales('sales-order')['grand_total'],100)
        self.rule(company='Test Co',priority=1)
        self.assertEqual(self.sales('sales-order')['grand_total'],75)

    def test_global_rules_remain_available(self):
        self.rule()
        self.assertEqual(self.sales('sales-order')['grand_total'],75)

    def test_wrong_direction_rule_cannot_mask_applicable_rule(self):
        self.rule(company='Test Co',priority=1)
        services.create_document('pricing-rule',dict(title='Purchasing only',item_code='M',company='Test Co',buying=1,selling=0,rate_or_discount='Rate',rate=50,priority=100))
        self.assertEqual(self.sales('sales-order')['grand_total'],75)
        self.db.insert('Supplier',dict(name='SUP',supplier_name='Supplier',default_currency='USD'))
        purchase=services.create_document('purchase-order',dict(company='Test Co',supplier='SUP',items=[dict(item_code='M',qty=1,rate=100)]))
        self.assertEqual(purchase['grand_total'],50)

    def budget(self, **extra):
        return services.create_document('budget',dict(company='Test Co',account=self.db.get_value('Company','Test Co','default_expense_account'),fiscal_year='2026',budget_amount=10,**extra))

    def purchase(self):
        self.db.insert('Supplier',dict(name='SUP',supplier_name='Supplier',default_currency='USD'))
        return services.create_document('purchase-invoice',dict(company='Test Co',supplier='SUP',posting_date='2026-09-11',items=[dict(item_code='M',qty=1,rate=100)]))

    def test_budget_action_is_validated_on_create_and_update(self):
        for action in ('Stopp','Ignore',True,False):
            with self.assertRaisesRegex(ValidationError,'Stop or Warn'): self.budget(action_if_exceeded=action)
        budget=self.budget()
        self.assertEqual(budget['action_if_exceeded'],'Warn')
        with self.assertRaisesRegex(ValidationError,'Stop or Warn'):
            services.update_document('budget',budget['name'],dict(action_if_exceeded='Stopp'))

    def test_invalid_legacy_budget_blocks_posting_atomically(self):
        budget=self.budget(action_if_exceeded='Stop')
        self.db.set_value('Budget',budget['name'],'action_if_exceeded','Stopp')
        invoice=self.purchase()
        with self.assertRaisesRegex(ValidationError,'Stop or Warn'):
            services.submit_document('purchase-invoice',invoice['name'])
        self.assertFalse(self.db.get_all('GL Entry'))
        self.assertEqual(services.load_document('purchase-invoice',invoice['name'])['docstatus'],0)
        services.update_document('budget',budget['name'],dict(action_if_exceeded='Stop'))
        with self.assertRaisesRegex(ValidationError,'Budget exceeded'):
            services.submit_document('purchase-invoice',invoice['name'])
        services.update_document('budget',budget['name'],dict(budget_amount=200))
        self.assertEqual(services.submit_document('purchase-invoice',invoice['name'])['docstatus'],1)

    def test_budget_warn_still_allows_posting(self):
        self.budget(action_if_exceeded='Warn');invoice=self.purchase()
        with self.assertWarnsRegex(UserWarning,'Budget exceeded'):
            services.submit_document('purchase-invoice',invoice['name'])

    def test_reservation_party_and_company_must_match(self):
        self.db.insert('Customer',dict(name='OTHER',customer_name='Other'))
        self.db.insert('Company',dict(name='Other Co',company_name='Other Co'))
        order=self.sales('sales-order')
        link=dict(voucher_type='Sales Order',voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError,'party must match'): self.booking(**link,party='OTHER')
        with self.assertRaisesRegex(ValidationError,'Company must match'): self.booking(**link,company='Other Co')
        booking=self.booking(**link)
        self.assertEqual(booking['company'],'Test Co')
        with self.assertRaisesRegex(ValidationError,'party must match'):
            services.update_document('reservation',booking['name'],dict(party='OTHER'))

    def test_purchase_reservation_uses_supplier(self):
        self.db.insert('Supplier',dict(name='SUP',supplier_name='Supplier'))
        order=services.create_document('purchase-order',dict(company='Test Co',supplier='SUP',items=[dict(item_code='M',qty=1,rate=100)]))
        link=dict(voucher_type='Purchase Order',voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError,'party must match'): self.booking(**link)
        self.assertEqual(self.booking(**link,party_type='Supplier',party='SUP')['party'],'SUP')

    def test_draft_voucher_cannot_change_party_after_booking(self):
        self.db.insert('Customer',dict(name='OTHER',customer_name='Other'))
        order=self.sales('sales-order')
        booking=self.booking(voucher_type='Sales Order',voucher_no=order['name'])
        with self.assertRaisesRegex(ValidationError,'Resolve active reservation'):
            services.update_document('sales-order',order['name'],dict(customer='OTHER'))
        self.assertEqual(services.load_document('sales-order',order['name'])['customer'],'C')
        services.update_document('reservation',booking['name'],dict(status='Cancelled'))
        services.update_document('sales-order',order['name'],dict(customer='OTHER'))

    def test_mismatched_legacy_booking_can_be_released(self):
        order=self.sales('sales-order')
        booking=self.booking(voucher_type='Sales Order',voucher_no=order['name'])
        self.db.set_value('Sales Order',order['name'],'customer','LEGACY-MISMATCH')
        self.assertEqual(services.update_document('reservation',booking['name'],dict(status='Cancelled'))['status'],'Cancelled')

    def disable_customer(self):
        update_master_record('customer','C',dict(disabled=1))

    def test_disabled_customer_blocks_creation_and_draft_submission(self):
        invoice=self.sales();self.disable_customer()
        for kind in ('sales-invoice','sales-order','quotation','delivery-note'):
            with self.assertRaisesRegex(ValidationError,'Customer C is disabled'): self.sales(kind)
        with self.assertRaisesRegex(ValidationError,'Customer C is disabled'):
            services.submit_document('sales-invoice',invoice['name'])
        self.assertEqual(services.load_document('sales-invoice',invoice['name'])['docstatus'],0)
        self.assertFalse(self.db.get_all('GL Entry'))
        update_master_record('customer','C',dict(disabled=0))
        services.submit_document('sales-invoice',invoice['name'])

    def test_disabled_customer_still_allows_returns_and_cancel(self):
        invoice=self.sales();services.submit_document('sales-invoice',invoice['name'])
        self.disable_customer()
        returned=make_sales_return(invoice['name']).save().submit()
        returned.cancel()
        services.cancel_document('sales-invoice',invoice['name'])
        self.assertEqual(services.load_document('sales-invoice',invoice['name'])['docstatus'],2)

    def test_disabled_customer_still_allows_settling_existing_invoice(self):
        invoice=self.sales();invoice=services.submit_document('sales-invoice',invoice['name'])
        self.disable_customer()
        payment=services.create_document('payment-entry',dict(company='Test Co',party_type='Customer',party='C',payment_type='Receive',paid_from=invoice['debit_to'],paid_to=self.bank_account(),paid_amount=100,received_amount=100,currency='USD',references=[dict(reference_doctype='Sales Invoice',reference_name=invoice['name'],allocated_amount=100)]))
        services.submit_document('payment-entry',payment['name'])
        self.assertEqual(services.load_document('sales-invoice',invoice['name'])['outstanding_amount'],0)

    def test_disabled_customer_blocks_new_booking_but_allows_release(self):
        booking=self.booking();self.disable_customer()
        self.assertEqual(services.update_document('reservation',booking['name'],dict(status='Returned'))['status'],'Returned')
        with self.assertRaisesRegex(ValidationError,'Customer C is disabled'): self.booking()

    def test_disabled_customer_stops_billing_but_can_cancel_subscription(self):
        sub=self.sub();self.disable_customer()
        with patch('lambda_erp.accounting.subscription.nowdate',return_value='2026-09-11'):
            with self.assertRaisesRegex(ValidationError,'Customer C is disabled'): Subscription.load(sub['name']).process()
        self.assertEqual(Subscription.load(sub['name']).current_invoice_start,'2026-07-01')
        services.update_document('subscription',sub['name'],dict(status='Cancelled'))
        self.assertIsNone(Subscription.load(sub['name']).process())

    def test_rules_are_visible_to_llm(self):
        prompt=chat._prompt_validation_context()
        for fragment in ('Disabled customers','Manual bank transactions','Stop or Warn','limits the rule','customer/supplier and Company'):
            self.assertIn(fragment,prompt)
        self.assertIn('allocated_amount',services.document_field_metadata('bank-transaction')['read_only_fields'])


if __name__=='__main__': unittest.main()
