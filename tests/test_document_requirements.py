"""Regression coverage for incomplete writes across model, REST, chat and MCP.

Synthetic in-memory data; no live services or LLM calls. Optional local test PG
uses the same reset helper as the existing REST suite.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

os.environ.setdefault('JWT_SECRET_KEY', 'document-validation-test-secret')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test-unused')

from lambda_erp.database import setup
from lambda_erp.exceptions import ValidationError
from lambda_erp.assets.asset import Asset
from lambda_erp.assets.reservation import Reservation
from lambda_erp.accounting.chart_of_accounts import setup_chart_of_accounts, setup_cost_center
from lambda_erp.stock.delivery_note import DeliveryNote
from lambda_erp.accounting.sales_invoice import SalesInvoice
from lambda_erp.accounting.payment_entry import PaymentEntry
from lambda_erp.selling.quotation import Quotation
from api import services, chat
from api.routers import mcp


class RequirementsTest(unittest.TestCase):
    def setUp(self):
        from tests.test_rest_api import _reset_db
        self.db = setup(_reset_db())
        db = self.db
        db.insert('Company', {'name':'Test Co', 'company_name':'Test Co', 'default_currency':'USD'})
        setup_chart_of_accounts('Test Co', 'USD')
        setup_cost_center('Test Co')
        db.insert('Customer', {'name':'C', 'customer_name':'Customer', 'default_currency':'USD'})
        for yard in ['A', 'B']:
            db.insert('Warehouse', {'name':yard,'warehouse_name':yard,'company':'Test Co'})
        db.insert('Item', {'name':'M','item_name':'Machine','is_asset_tracked':1,'is_stock_item':0})
        db.insert('Item', {'name':'S','item_name':'Stock item','is_stock_item':1,'standard_rate':10})
        self.a = Asset(item_code='M', warehouse='A', asset_tag='A-1').save()
        self.b = Asset(item_code='M', warehouse='B', asset_tag='B-1').save()
        self.pool = dict(item_code='M', warehouse='A', qty=1, from_datetime='2026-09-14',
                         to_datetime='2026-09-16', allocation_mode='Pool', party_type='Customer', party='C')

    def tearDown(self):
        self.db.conn.close()

    def test_audit_failure_does_not_mask_saved_document(self):
        result = services.create_document('reservation', self.pool)
        with self.assertLogs('chat', level='WARNING'):
            chat._save_tool_trace('deleted-session', 'tool_result', {'result': result})
        self.assertEqual(services.load_document('reservation', result['name'])['allocation_mode'], 'Pool')

    def test_pool_requires_intent_and_warns(self):
        data = {k:v for k,v in self.pool.items() if k != 'allocation_mode'}
        with self.assertRaisesRegex(ValidationError, 'explicitly choose'):
            Reservation(data).save()
        result = services.create_document('reservation', self.pool)
        self.assertIsNone(result['asset'])
        self.assertEqual(result['_validation']['warnings'][0]['code'], 'asset_unassigned')
        # Even a single matching unit isn't silently selected.
        self.assertEqual(result['allocation_mode'], 'Pool')
        with self.assertRaisesRegex(ValidationError, 'before marking'):
            services.update_document('reservation', result['name'], {'status':'Out'})
        updated = services.update_document('reservation', result['name'], {'asset':self.a.name, 'status':'Out'})
        self.assertEqual(updated['allocation_mode'], 'Unit')
        self.assertEqual(updated['_validation']['warnings'], [])
        with self.assertRaisesRegex(ValidationError, 'explicitly choose'):
            services.update_document('reservation', result['name'], {'asset':None, 'status':'Reserved'})

    def test_party_or_internal_purpose(self):
        data = {k:v for k,v in self.pool.items() if k not in {'party','party_type'}}
        with self.assertRaisesRegex(ValidationError, 'Purpose'):
            Reservation(data).save()
        Reservation({**data, 'purpose':'Maintenance'}).save()

    def test_location_and_discarded_asset(self):
        with self.assertRaisesRegex(ValidationError, 'Warehouse must match'):
            Reservation({**self.pool, 'asset':self.b.name}).save()
        with self.assertRaisesRegex(ValidationError, 'Warehouse is required'):
            Asset(item_code='M').save()
        Asset(item_code='M', warehouse='A', asset_tag='A-2').save()
        self.a.discard()
        with self.assertRaisesRegex(ValidationError, 'discarded'):
            Reservation({**self.pool, 'asset':self.a.name}).save()

    def test_release_legacy_incomplete_booking(self):
        # It must remain possible to release an old hold, not trap capacity.
        self.db.insert('Reservation', {**self.pool, 'name':'LEGACY', 'party':None,
                                      'purpose':None, 'asset':self.b.name})
        result=services.update_document('reservation','LEGACY',{'status':'Cancelled'})
        self.assertEqual(result['status'],'Cancelled')

    def test_voucher_reference(self):
        for extra in [{'voucher_type':'Quotation'}, {'voucher_type':'Quotation','voucher_no':'FAKE'}]:
            with self.assertRaises(ValidationError):
                Reservation({**self.pool, **extra}).save()

    def test_unknown_parent_and_child_fields_no_phantom_success(self):
        with self.assertRaisesRegex(ValidationError,'asset_id'):
            services.create_document('reservation',{**self.pool,'asset_id':self.a.name})
        self.assertEqual(self.db.get_all('Reservation'), [])
        with self.assertRaisesRegex(ValidationError,'quantitty'):
            services.create_document('quotation',{'customer':'C','company':'Test Co',
                'items':[{'item_code':'S','qty':1,'rate':10,'quantitty':1}]})
        r=services.create_document('reservation',self.pool)
        result=services.batch_update_documents('reservation',[{'name':r['name'],'data':{'asset_id':self.a.name}}])
        self.assertEqual(result['failed'],1)
        self.assertIsNone(services.load_document('reservation',r['name'])['asset'])
        # Read/save round trips accept only the explicitly designated response annotation.
        services.update_document('reservation',r['name'],r)

    def test_missing_transaction_fields(self):
        for data in [
            {'customer':'C','items':[{'item_code':'S','qty':1,'rate':10}]},
            {'customer':'C','company':'Test Co','items':[{'item_code':'S','rate':10}]},
            {'customer':'C','company':'Test Co','items':[{'qty':1,'rate':10}]},
        ]:
            with self.assertRaises(ValidationError):
                Quotation(data).save()
        # Intentional free-text service lines and explicit zero-price lines remain valid.
        Quotation(customer='C',company='Test Co',items=[{'description':'Free service','qty':1,'rate':0}]).save()

    def test_stock_writes_fail_before_posting(self):
        for cls in [DeliveryNote, SalesInvoice]:
            doc=cls(customer='C',company='Test Co',update_stock=1,
                    items=[{'item_code':'S','qty':1,'rate':10}])
            with self.assertRaisesRegex(ValidationError,'Warehouse is required'):
                doc.save()
            self.assertFalse(self.db.exists(doc.DOCTYPE,doc.name))
        # Old incomplete draft must also fail on submit, without leaving transaction mode set.
        self.db.insert('Delivery Note',{'name':'LEGACY-DN','company':'Test Co','customer':'C','docstatus':0})
        self.db.insert('Delivery Note Item',{'name':'LEGACY-DNI','parent':'LEGACY-DN','item_code':'S','qty':1,'rate':10})
        with self.assertRaisesRegex(ValidationError,'Warehouse is required'):
            DeliveryNote.load('LEGACY-DN').submit()
        self.assertFalse(self.db._in_transaction)
        self.assertEqual(self.db.get_value('Delivery Note','LEGACY-DN','docstatus'),0)
        self.assertEqual(self.db.get_all('Stock Ledger Entry'),[])
        self.assertEqual(self.db.get_all('GL Entry'),[])

    def test_payment_partial_reference_vs_on_account(self):
        data=dict(payment_type='Receive',party_type='Customer',party='C',company='Test Co',paid_amount=10)
        with self.assertRaisesRegex(ValidationError,'Reference Name'):
            PaymentEntry(**data,references=[{'reference_doctype':'Sales Invoice','allocated_amount':10}]).save()
        PaymentEntry(**data,references=[]).save().submit()
        self.assertEqual(len(self.db.get_all('GL Entry')),2)

    def test_mcp_shares_input_rules_and_metadata(self):
        result=mcp._handle({'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'create_document','arguments':{'doctype':'reservation','data':{**self.pool,'asset_id':self.a.name}}}}, {'role':'manager'})
        self.assertTrue(result['result']['isError'])
        fields=chat._handle_get_document_fields({'doctype':'reservation'})
        self.assertIn('from_datetime',fields['requirements']['required'])
        self.assertIn('voucher_no',fields['dynamic_link_fields'])
        self.assertEqual(fields,services.document_field_metadata('reservation'))
        prompt=chat.build_system_prompt({'role':'manager'})
        for rule in fields['requirements']['conditional']:
            self.assertIn(rule,prompt)

    def test_chat_reports_errors_and_preserves_trace_without_polluting_history(self):
        sid=chat.create_session()['id']
        chat._save_tool_trace(sid, 'tool_call', {'tool': 'prior-audit-record'})
        chat.save_chat_message(sid,'user','Book a machine')
        responses=iter([
            (NS(content='', tool_calls=[NS(id='c1',function=NS(name='create_document',arguments=json.dumps({'doctype':'reservation'})))]),None),
            (NS(content='Missing data',tool_calls=[]),None),
        ])
        events=[]
        async def event(e): events.append(e)
        with patch.object(chat,'_orchestrator_turn',side_effect=lambda *args: next(responses)), \
             patch.object(chat,'refresh_auth_principal',side_effect=lambda user:user):
            asyncio.run(chat.run_thinking_loop([],event,session_id=sid,user_info={'role':'manager'},max_iterations=2))
        result=next(e for e in events if e['type']=='tool_result')
        self.assertFalse(result['success'])
        self.assertIn('data',result['error'])
        trace=self.db.sql('SELECT role,message_type FROM "Chat Message" WHERE session_id=?',[sid])
        self.assertEqual(sum(r['role']=='tool' for r in trace),3)
        self.assertEqual(chat.build_conversation(sid),[{'role':'user','content':'Book a machine'}])
        self.assertFalse(chat.load_serialized_chat_history(sid, limit=1)['has_more'])

    def test_transient_plugin_input_must_be_declared(self):
        with patch.object(Reservation,'INPUT_FIELDS',{'_reason'}):
            result=services.create_document('reservation',{**self.pool,'_reason':'intentional plugin command'})
            self.assertNotIn('_reason',result)
        with self.assertRaisesRegex(ValidationError,'_reason'):
            services.update_document('reservation',result['name'],{'_reason':'not declared'})

    def test_migration_preserves_legacy_pool_and_unit(self):
        from lambda_erp.database import _m027_reservation_allocation_mode
        self.db.insert('Reservation',{'name':'OLD-P','asset':None})
        self.db.insert('Reservation',{'name':'OLD-U','asset':self.a.name})
        _m027_reservation_allocation_mode(self.db)
        _m027_reservation_allocation_mode(self.db)
        self.assertEqual(self.db.get_value('Reservation','OLD-P','allocation_mode'),'Pool')
        self.assertEqual(self.db.get_value('Reservation','OLD-U','allocation_mode'),'Unit')


if __name__=='__main__':
    unittest.main()
