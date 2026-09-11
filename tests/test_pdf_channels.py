"""Authenticated HTTP/MCP and deterministic LLM-stub PDF delivery tests."""
import asyncio
import hashlib
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

os.environ.setdefault('JWT_SECRET_KEY','pdf-channel-test-secret')
os.environ.setdefault('OPENAI_API_KEY','sk-test-unused')
from fastapi.testclient import TestClient
from api.main import app
from api import chat
from lambda_erp.database import get_db
from tests.test_availability_api import _reset_db
from tests.pdf_fixtures import seed_pdf_documents


class Channels(unittest.TestCase):
    def setUp(self):
        os.environ['LAMBDA_ERP_DB']=_reset_db()
        os.environ['LAMBDA_ERP_AUTO_DEMO']='0'
        self.client=TestClient(app);self.client.__enter__()
        r=self.client.post('/api/auth/register',json={'email':'pdf-test@example.com','password':'test-password-123','full_name':'PDF Test'})
        self.assertEqual(r.status_code,200,r.text)
        self.client.put('/api/auth/settings',json={'chat_api_enabled':'1','rest_api_enabled':'1'})
        r=self.client.post('/api/auth/api-keys',json={'name':'pdf-reader','role':'viewer'})
        self.assertEqual(r.status_code,200,r.text)
        self.headers={'Authorization':'Bearer '+r.json()['token']}
        self.docs=seed_pdf_documents()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        get_db().conn.close()

    def test_rest_errors_metadata_and_exact_generated_download_across_auth_transports(self):
        name=self.docs['Reservation']
        self.assertTrue(self.client.get('/api/documents/reservation/fields').json()['pdf']['supported'])
        self.assertTrue(self.client.get('/api/v1/documents/reservation/fields',headers=self.headers).json()['pdf']['supported'])
        r=self.client.post(f'/api/documents/reservation/{name}/pdf')
        self.assertEqual(r.status_code,200,r.text)
        file=r.json()
        # Session and API-key identities use the same real owner, despite chat's
        # api:<name> audit namespace. Every channel can download the same bytes.
        download=self.client.get(file['pdf_url'],headers=self.headers)
        self.assertEqual(download.status_code,200,download.text[:150] if download.status_code!=200 else '')
        self.assertEqual(hashlib.sha256(download.content).hexdigest(),file['sha256'])
        get_db().set_value('Reservation',name,'asset',None)
        for prefix in ('/api','/api/v1'):
            r=self.client.get(f'{prefix}/documents/reservation/{name}/pdf',headers=self.headers)
            self.assertEqual(r.status_code,422,r.text)
            self.assertEqual(r.json()['detail']['code'],'pdf_incomplete')
        self.assertEqual(download.content,self.client.get(file['pdf_url'],headers=self.headers).content)

    def test_mcp_viewer_gets_generated_file_and_actionable_errors(self):
        self.client.put('/api/auth/settings',json={'chat_api_enabled':'0'})
        self.client.cookies.clear()  # ensure role is the viewer key, not admin cookie
        payload={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'generate_document_pdf','arguments':{'doctype':'reservation','name':self.docs['Reservation']}}}
        r=self.client.post('/api/mcp',headers=self.headers,json=payload)
        self.assertEqual(r.status_code,200,r.text)
        import json
        result=r.json()['result'];self.assertFalse(result['isError'],result)
        file=json.loads(result['content'][0]['text'])
        self.assertTrue(file['validated'])
        self.assertEqual(self.client.get(file['pdf_url'],headers=self.headers).status_code,200)
        get_db().set_value('Reservation',self.docs['Reservation'],'asset',None)
        out=self.client.post('/api/mcp',headers=self.headers,json=payload).json()['result']
        self.assertTrue(out['isError'])
        self.assertEqual(json.loads(out['content'][0]['text'])['code'],'pdf_incomplete')

    def test_real_tool_dispatch_creates_attachment_before_chat_success(self):
        self.client.cookies.clear()
        name=self.docs['Reservation']
        calls=iter([
            (NS(content=None,tool_calls=[NS(id='pdf-call',function=NS(name='generate_document_pdf',arguments='{"doctype":"reservation","name":"'+name+'"}'))]),None),
            (NS(content='The requested PDF has been generated.',tool_calls=[]),None),
        ])
        async def no_title(*args,**kwargs):pass
        with patch.object(chat,'_orchestrator_turn',side_effect=lambda *a,**k:next(calls)),patch.object(chat,'generate_title',no_title):
            r=self.client.post('/api/v1/chat',headers=self.headers,json={'message':'Generate the reservation PDF.'})
        self.assertEqual(r.status_code,200,r.text)
        result=r.json();self.assertEqual(result['document_errors'],[])
        self.assertEqual(len(result['documents']),1,result)
        file=result['documents'][0]
        self.assertTrue(file['validated'])
        self.assertIn('artifact_id=',file['pdf_url'])
        body=self.client.get(file['pdf_url'],headers=self.headers)
        self.assertEqual(body.status_code,200)
        self.assertEqual(hashlib.sha256(body.content).hexdigest(),file['sha256'])
        self.assertTrue(any(x['tool']=='generate_document_pdf' and x['success'] for x in result['tool_results']))

    def test_failed_pdf_does_not_become_attachment(self):
        self.client.cookies.clear()
        get_db().set_value('Reservation',self.docs['Reservation'],'asset',None)
        calls=iter([
            (NS(content=None,tool_calls=[NS(id='pdf-call',function=NS(name='generate_document_pdf',arguments='{"doctype":"reservation","name":"'+self.docs['Reservation']+'"}'))]),None),
            (NS(content='The PDF needs an assigned machine.',tool_calls=[]),None),
        ])
        async def no_title(*args,**kwargs):pass
        with patch.object(chat,'_orchestrator_turn',side_effect=lambda *a,**k:next(calls)),patch.object(chat,'generate_title',no_title):
            r=self.client.post('/api/v1/chat',headers=self.headers,json={'message':'Generate the reservation PDF.'})
        self.assertEqual(r.status_code,200,r.text)
        result=r.json();self.assertEqual(result['documents'],[])
        self.assertIn('asset',result['document_errors'][0]['error'])
        self.assertEqual(get_db().sql('SELECT COUNT(*) AS n FROM "Generated PDF"')[0]['n'],0)

    def test_latest_pdf_attempt_controls_delivery(self):
        self.client.cookies.clear()
        name=self.docs['Reservation']
        for fail_last in (False,True):
            with self.subTest(fail_last=fail_last):
                get_db().set_value('Reservation',name,{'asset':'PDF-Asset','notes':None})
                turns=iter(range(3))
                def turn(*args,**kwargs):
                    step=next(turns)
                    if step==2:return NS(content='PDF request processed.',tool_calls=[]),None
                    if step==1:
                        get_db().set_value('Reservation',name,{'asset':None if fail_last else 'PDF-Asset','notes':'Revised rental instructions'})
                    return NS(content=None,tool_calls=[NS(id=f'pdf-{step}',function=NS(name='generate_document_pdf',arguments='{"doctype":"reservation","name":"'+name+'"}'))]),None
                async def no_title(*args,**kwargs):pass
                with patch.object(chat,'_orchestrator_turn',side_effect=turn),patch.object(chat,'generate_title',no_title):
                    result=self.client.post('/api/v1/chat',headers=self.headers,json={'message':'Regenerate the reservation PDF after correcting it.'}).json()
                if fail_last:
                    self.assertEqual(result['documents'],[])
                    self.assertTrue(result['document_errors'])
                else:
                    self.assertEqual(len(result['documents']),1)
                    from tests.test_pdf_contract import text
                    data=self.client.get(result['documents'][0]['pdf_url'],headers=self.headers).content
                    self.assertIn('Revised rental instructions',text(data))


if __name__=='__main__':unittest.main()
