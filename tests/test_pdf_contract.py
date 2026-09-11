"""Actual rendered PDFs, export errors, immutable files and capability coverage."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
from fastapi import HTTPException
from lambda_erp.database import setup
from api import pdf, services, chat
from api.pdf_contract import PDFError
from api.pdf_profiles import PROFILES, PDFProfile, DisabledPDF, validate_pdf_registry
from api.pdf_exports import create_pdf_export, get_pdf_export
from tests.pdf_fixtures import seed_pdf_documents
from tests.test_availability_api import _reset_db


def text(data):
    return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)


class PDFTests(unittest.TestCase):
    def setUp(self):
        self.db=setup(_reset_db())
        self.docs=seed_pdf_documents()
        self.user={'name':'pdf-viewer','role':'viewer'}
        if os.environ.get('PDF_TEST_INTERNAL'):
            from internal.plugin import register
            if "Lead" not in services.DOCUMENT_CLASSES:
                register()
            services.apply_plugin_schema()

    def tearDown(self):
        self.db.conn.close()

    def test_every_registered_core_profile_prints_actual_content(self):
        validate_pdf_registry()
        self.assertEqual(set(self.docs),set(PROFILES)-{'Lead','Contact','Activity'})
        for dt,name in self.docs.items():
            with self.subTest(doctype=dt):
                before=json.dumps(services.load_document(dt.lower().replace(' ','-'),name),sort_keys=True,default=str)
                data=pdf.generate_pdf(dt.lower().replace(' ','-'),name)
                output=text(data)
                self.assertIn(name,output)
                self.assertGreater(len(output),70)
                self.assertNotIn('USD',output)
                after=json.dumps(services.load_document(dt.lower().replace(' ','-'),name),sort_keys=True,default=str)
                self.assertEqual(before,after,'Export must not change saved documents')
                if dt in {'Quotation','Sales Order','Sales Invoice','POS Invoice','Purchase Order','Purchase Invoice','Proposal'}:
                    self.assertIn('Excavator Example',output)
                    self.assertIn('20.00',output)
                if dt=='Journal Entry':
                    self.assertIn('BANK-PDF',''.join(output.split()));self.assertIn('AR-PDF',''.join(output.split()))
                if dt=='Stock Entry':
                    self.assertIn('St. Gallen Yard',output);self.assertIn('Chur Yard',output)

    def test_reservation_regression_contains_customer_machine_dates_no_financial_defaults(self):
        output=text(pdf.generate_pdf('reservation',self.docs['Reservation']))
        for token in ('Ada Example','Machine unit','PDF-Asset','Excavator Example','St. Gallen Yard','2026-09-16 08:00:00','2026-09-18 08:00:00','Reserved'):
            self.assertIn(token,output)
        self.assertNotIn('0.00',output)
        self.assertNotIn('USD',output)

    def test_pool_and_internal_blocks_are_explicit(self):
        name=self.docs['Reservation']
        self.db.set_value('Reservation',name,{'allocation_mode':'Pool','asset':None})
        output=text(pdf.generate_pdf('reservation',name))
        self.assertIn('Pool booking: no individual machine assigned.',output)
        self.db.set_value('Reservation',name,{'party':None,'party_type':None,'purpose':'Workshop maintenance'})
        self.assertIn('Workshop maintenance',text(pdf.generate_pdf('reservation',name)))
        self.db.set_value('Reservation',name,'purpose',None)
        with self.assertRaisesRegex(PDFError,'purpose'):
            pdf.generate_pdf('reservation',name)

    def test_missing_values_and_bad_references_fail_before_success(self):
        for field,value in [('from_datetime',None),('asset',None),('warehouse','NO-YARD'),('qty',0)]:
            original=self.db.get_value('Reservation',self.docs['Reservation'],field)
            self.db.set_value('Reservation',self.docs['Reservation'],field,value)
            with self.subTest(field=field),self.assertRaises(PDFError):pdf.generate_pdf('reservation',self.docs['Reservation'])
            self.db.set_value('Reservation',self.docs['Reservation'],field,original)
        self.db.set_value('Quotation',self.docs['Quotation'],'currency',None)
        with self.assertRaisesRegex(PDFError,'currency'):pdf.generate_pdf('quotation',self.docs['Quotation'])
        self.db.set_value('Quotation',self.docs['Quotation'],'currency','CHF')
        self.db.sql('DELETE FROM "Quotation Item" WHERE parent = ?',[self.docs['Quotation']]);self.db.conn.commit()
        with self.assertRaisesRegex(PDFError,'items'):pdf.generate_pdf('quotation',self.docs['Quotation'])

    def test_valid_zero_returns_and_historical_states_remain_printable(self):
        name=self.docs['Sales Invoice']
        self.db.set_value('Sales Invoice',name,{'net_total':0,'grand_total':0,'docstatus':2})
        self.db.sql('UPDATE "Sales Invoice Item" SET rate=0,amount=0 WHERE parent=?',[name]);self.db.conn.commit()
        self.db.set_value('Customer','C-PDF','disabled',1)
        output=text(pdf.generate_pdf('sales-invoice',name))
        self.assertIn('Cancelled',output);self.assertIn('0.00',output)
        self.db.set_value('Sales Invoice',name,{'is_return':1,'net_total':-20,'grand_total':-20})
        self.db.sql('UPDATE "Sales Invoice Item" SET qty=-2,rate=10,amount=-20 WHERE parent=?',[name]);self.db.conn.commit()
        self.assertIn('-20.00',text(pdf.generate_pdf('sales-invoice',name)))

    def test_broken_custom_template_and_required_provider_fail(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'document.html').write_text('<html><body>Empty letterhead</body></html>')
            old=list(pdf._plugin_template_dirs)
            try:
                pdf.register_pdf_template_dir(d)
                with self.assertRaises(PDFError) as err:pdf.generate_pdf('quotation',self.docs['Quotation'])
                self.assertEqual(err.exception.code,'pdf_render_incomplete')
            finally:
                pdf._plugin_template_dirs[:]=old;pdf._jinja_env.loader=pdf._build_loader()
        with patch.object(pdf,'_pdf_context_providers',[lambda *a: (_ for _ in ()).throw(RuntimeError('Broken required context'))]):
            with self.assertRaises(PDFError):pdf.generate_pdf('quotation',self.docs['Quotation'])

    def test_bad_proposal_appendix_is_not_silently_omitted(self):
        self.db.insert('Proposal Appendix',{'parent':self.docs['Proposal'],'data':b'not a pdf','filename':'broken.pdf'})
        with self.assertRaises(PDFError) as err:pdf.generate_pdf('proposal',self.docs['Proposal'])
        self.assertEqual(err.exception.code,'pdf_invalid_appendix')

    def test_taxed_recurring_proposal_and_quotation(self):
        name=self.docs['Quotation']
        self.db.set_value('Quotation',name,{'net_total':20,'grand_total':21.62,'total_taxes_and_charges':1.62})
        self.db.insert('Quotation Item',{'name':'MONTHLY-PDF','parent':name,'idx':2,'description':'Monthly service','qty':1,'rate':37,'amount':37,'frequency':'Monthly'})
        self.db.insert('Sales Taxes and Charges',{'name':'VAT-PDF','parent':name,'idx':1,'charge_type':'On Net Total','description':'VAT 8.1%','rate':8.1,'tax_amount':1.62})
        self.db.conn.commit()
        for slug,doctype in [('quotation','Quotation'),('proposal','Proposal')]:
            output=text(pdf.generate_pdf(slug,self.docs[doctype]))
            for token in ('21.62','40.00','37.00','Monthly service'):
                self.assertIn(token,output)

    def test_historical_reservation_survives_asset_move(self):
        self.db.set_value('Asset','PDF-Asset',{'warehouse':'W-PDF-2','disabled':1})
        self.assertIn('St. Gallen Yard',text(pdf.generate_pdf('reservation',self.docs['Reservation'])))

    @unittest.skipUnless(os.environ.get('PDF_TEST_INTERNAL'), 'Internal Swiss payment part')
    def test_internal_qr_invoice_and_nonpayable_credit_record(self):
        self.db.set_value('Company','PDF Co',{'iban':'CH9300762011623852957','address':'Teststrasse 1','city':'St. Gallen','zip_code':'9000','country':'CH'})
        name=self.docs['Sales Invoice']
        self.db.set_value('Sales Invoice',name,'outstanding_amount',20)
        pages=PdfReader(io.BytesIO(pdf.generate_pdf('sales-invoice',name))).pages
        # Logo plus rendered payment-part image must be embedded.
        self.assertGreaterEqual(sum(len(page.images) for page in pages),2)
        self.db.set_value('Sales Invoice',name,{'is_return':1,'net_total':-20,'grand_total':-20,'outstanding_amount':-20})
        self.db.sql('UPDATE "Sales Invoice Item" SET qty=-2,rate=10,amount=-20 WHERE parent=?',[name]);self.db.conn.commit()
        credit=PdfReader(io.BytesIO(pdf.generate_pdf('sales-invoice',name)))
        self.assertIn('-20.00','\n'.join(page.extract_text() or '' for page in credit.pages))
        self.assertEqual(sum(len(page.images) for page in credit.pages),1)

    def test_multpage_item_content_is_not_truncated(self):
        for i in range(45):
            self.db.insert('Quotation Item',{'name':f'LONG-{i}','parent':self.docs['Quotation'],'idx':i+2,'description':f'Service marker {i:03d} '+('Long description. '*8),'qty':1,'rate':1,'amount':1})
        for slug,doctype in [('quotation','Quotation'),('proposal','Proposal')]:
            data=pdf.generate_pdf(slug,self.docs[doctype])
            self.assertGreater(len(PdfReader(io.BytesIO(data)).pages),1)
            self.assertIn('Service marker 044',text(data))

    def test_explicit_registration_and_metadata(self):
        from lambda_erp.model import Document
        class Unspecified(Document):
            DOCTYPE='Unspecified PDF'
        with self.assertRaisesRegex(ValueError,'PDFProfile'):
            services.register_doctype(Unspecified.DOCTYPE,Unspecified)
        for dt in self.docs:
            slug=dt.lower().replace(' ','-')
            self.assertTrue(services.document_field_metadata(slug)['pdf']['supported'])
        with patch.dict(PROFILES,{'Reservation':DisabledPDF('Temporarily unavailable')}):
            self.assertFalse(services.document_field_metadata('reservation')['pdf']['supported'])
            with self.assertRaises(PDFError) as err:pdf.generate_pdf('reservation',self.docs['Reservation'])
            self.assertEqual(err.exception.code,'pdf_unsupported')
        prompt=chat.build_system_prompt(self.user,channel='api')
        self.assertIn('generate_document_pdf',prompt)
        self.assertIn('PDF output contracts',prompt)
        self.assertIn('reservation',prompt)

    def test_generated_snapshot_is_immutable_and_owned(self):
        file=create_pdf_export('reservation',self.docs['Reservation'],self.user)
        self.assertTrue(file['validated'])
        original=get_pdf_export(file['artifact_id'],'reservation',self.docs['Reservation'],self.user)
        self.db.set_value('Reservation',self.docs['Reservation'],'notes','Changed after export')
        self.assertEqual(original,get_pdf_export(file['artifact_id'],'reservation',self.docs['Reservation'],self.user))
        self.assertNotIn('Changed after export',text(original))
        for slug,name,user in [('reservation',self.docs['Reservation'],{'name':'other','role':'admin'}),('quotation',self.docs['Reservation'],self.user),('reservation','OTHER',self.user)]:
            with self.assertRaises(HTTPException) as err:get_pdf_export(file['artifact_id'],slug,name,user)
            self.assertEqual(err.exception.status_code,404)
        self.db.sql('UPDATE "Generated PDF" SET expires_at=? WHERE id=?',['2000-01-01',file['artifact_id']]);self.db.conn.commit()
        with self.assertRaises(HTTPException) as err:get_pdf_export(file['artifact_id'],'reservation',self.docs['Reservation'],self.user)
        self.assertEqual(err.exception.status_code,410)


if __name__=='__main__':unittest.main()
