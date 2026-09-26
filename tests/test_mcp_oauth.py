"""OAuth connector flow, credential isolation and replay protection on both DBs.

Run: python -m tests.test_mcp_oauth
Optional: LAMBDA_ERP_TEST_DB=postgresql://... (disposable database only).
"""
import base64
import hashlib
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('JWT_SECRET_KEY', 'mcp-oauth-tests-only-secret')
os.environ.setdefault('OPENAI_API_KEY', 'test-unused')

from fastapi.testclient import TestClient
from starlette.requests import Request
from api.main import app
from api.auth import refresh_auth_principal
from api.oauth import _login_redirect
from api import mcp_oauth
from lambda_erp.database import get_db
from tests.test_mcp import _reset_db


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.path = _reset_db()
        os.environ['LAMBDA_ERP_DB'] = self.path
        os.environ['LAMBDA_ERP_AUTO_DEMO'] = '0'
        self.client = TestClient(app)
        self.client.__enter__()
        r = self.client.post('/api/auth/register', json={'email':'owner@example.com', 'full_name':'Owner', 'password':'test-password-123'})
        self.assertEqual(r.status_code, 200, r.text)
        self.user = r.json()
        self.client.put('/api/auth/settings', json={'rest_api_enabled':'1'})
        self.redirect = 'https://client.example/callback'
        self.verifier = 'a' * 64
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.resource = 'http://testserver/api/mcp'
        self.reg = self.register()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if not self.path.startswith('postgres'):
            os.unlink(self.path)

    def register(self, **overrides):
        body = {'client_name':'Claude test connector', 'redirect_uris':[self.redirect], 'token_endpoint_auth_method':'none'}
        body.update(overrides)
        r = self.client.post('/api/mcp-oauth/register', json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def begin(self, **overrides):
        params = dict(client_id=self.reg['client_id'], redirect_uri=self.redirect, response_type='code',
                      code_challenge=self.challenge, code_challenge_method='S256', scope='erp:read erp:write erp:admin',
                      state='opaque-state', resource=self.resource)
        params.update(overrides)
        return self.client.get('/api/mcp-oauth/authorize', params=params, follow_redirects=False)

    def approve(self, role='viewer', allow=True):
        r = self.begin()
        self.assertEqual(r.status_code, 303, r.text)
        request_id = parse_qs(urlsplit(r.headers['location']).query)['request'][0]
        r = self.client.get('/api/mcp-oauth/consent', params={'request_id':request_id})
        self.assertEqual(r.status_code, 200, r.text)
        self.decision = dict(request_id=request_id, csrf_token=r.json()['csrf_token'], allow=allow, role=role)
        r = self.client.post('/api/mcp-oauth/consent', json=self.decision)
        self.assertEqual(r.status_code, 200, r.text)
        return parse_qs(urlsplit(r.json()['redirect_uri']).query)

    def exchange(self, code, **overrides):
        data = dict(grant_type='authorization_code', code=code, client_id=self.reg['client_id'],
                    redirect_uri=self.redirect, code_verifier=self.verifier, resource=self.resource)
        data.update(overrides)
        return self.client.post('/api/mcp-oauth/token', data=data)

    def connect(self, role='viewer'):
        query = self.approve(role)
        self.assertEqual(query['state'], ['opaque-state'])
        r = self.exchange(query['code'][0])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers['cache-control'], 'no-store')
        return r.json()

    def rpc(self, access, method='tools/list', params=None):
        return self.client.post('/api/mcp', headers={'Authorization':f'Bearer {access}'},
                                json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})

    def test_discovery_and_transport(self):
        for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/api/mcp'):
            r = self.client.get(path)
            self.assertEqual(r.json()['resource'], self.resource)
        metadata = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(metadata['code_challenge_methods_supported'], ['S256'])
        self.assertIn('refresh_token', metadata['grant_types_supported'])
        r = self.client.post('/api/mcp', json={'jsonrpc':'2.0','id':1,'method':'initialize'})
        self.assertEqual(r.status_code, 401)
        self.assertIn('resource_metadata=', r.headers['www-authenticate'])
        self.assertEqual(self.client.get('/api/mcp').status_code, 405)

    def test_complete_flow_caps_permissions_and_lists_connection(self):
        tokens = self.connect()
        tools = self.rpc(tokens['access_token']).json()['result']['tools']
        self.assertIn('get_document', {t['name'] for t in tools})
        self.assertNotIn('create_document', {t['name'] for t in tools})
        self.assertTrue(next(t for t in tools if t['name'] == 'get_document')['annotations']['readOnlyHint'])
        denied = self.rpc(tokens['access_token'], 'tools/call', {'name':'create_master','arguments':{}})
        self.assertTrue(denied.json()['result']['isError'])
        keys = self.client.get('/api/auth/api-keys').json()
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]['app_type'], 'oauth')
        self.assertEqual(keys[0]['role'], 'viewer')
        self.assertTrue(keys[0]['last_used_at'])
        self.assertNotIn('token', keys[0])
        stored = get_db().sql('SELECT hash FROM "MCP OAuth Token"')
        self.assertNotIn(tokens['access_token'], str(stored))
        self.assertNotIn(tokens['refresh_token'], str(stored))

    def test_version_probe_still_discovers_sign_in(self):
        # Remote connectors can send their preferred revision before sign-in.
        # Session cookies must not suppress the OAuth discovery challenge.
        for version in ('2025-03-26', '2025-06-18', '2025-11-25', '2099-01-01'):
            for authorization in ('', 'Bearer invalid-key'):
                with self.subTest(version=version, authorization=authorization):
                    r = self.client.post('/api/mcp', headers={
                        'MCP-Protocol-Version': version,
                        'Authorization': authorization,
                        'Accept': 'application/json, text/event-stream',
                    }, json={'jsonrpc':'2.0', 'id':1, 'method':'initialize',
                             'params':{'protocolVersion':version, 'capabilities':{},
                                       'clientInfo':{'name':'connector-test', 'version':'1'}}})
                    self.assertEqual(r.status_code, 401, r.text)
                    self.assertIn('resource_metadata="http://testserver/.well-known/oauth-protected-resource/api/mcp"',
                                  r.headers['www-authenticate'])

    def test_initialize_negotiates_before_enforcing_version_header(self):
        tokens = self.connect()
        headers = {'Authorization': f"Bearer {tokens['access_token']}"}
        for requested, expected in (('2025-03-26', '2025-03-26'),
                                    ('2025-06-18', '2025-06-18'),
                                    ('2025-11-25', '2025-06-18'),
                                    ('2099-01-01', '2025-06-18')):
            with self.subTest(requested=requested):
                r = self.client.post('/api/mcp', headers={**headers, 'MCP-Protocol-Version':requested},
                                     json={'jsonrpc':'2.0', 'id':1, 'method':'initialize',
                                           'params':{'protocolVersion':requested, 'capabilities':{},
                                                     'clientInfo':{'name':'connector-test', 'version':'1'}}})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['result']['protocolVersion'], expected)
                r = self.client.post('/api/mcp', headers={**headers, 'MCP-Protocol-Version':expected},
                                     json={'jsonrpc':'2.0', 'id':2, 'method':'tools/list'})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertIn('tools', r.json()['result'])
        # After initialization, unsupported revisions must still be rejected.
        for body in ({'jsonrpc':'2.0', 'id':3, 'method':'tools/list'},
                     [{'jsonrpc':'2.0', 'id':4, 'method':'initialize'},
                      {'jsonrpc':'2.0', 'id':5, 'method':'tools/list'}]):
            r = self.client.post('/api/mcp', headers={**headers, 'MCP-Protocol-Version':'2099-01-01'}, json=body)
            self.assertEqual(r.status_code, 400, r.text)

    def test_initialize_rejects_malformed_protocol_version(self):
        tokens = self.connect()
        for value in ([], {}, 123):
            r = self.rpc(tokens['access_token'], 'initialize', {'protocolVersion':value})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['error']['code'], -32602)

    def test_code_single_use_and_pkce(self):
        code = self.approve()['code'][0]
        self.assertEqual(self.exchange(code, code_verifier='b' * 64).status_code, 400)
        self.assertEqual(self.exchange(code, redirect_uri='https://evil.example/callback').status_code, 400)
        self.assertEqual(self.exchange(code, resource='https://evil.example/mcp').status_code, 400)
        self.assertEqual(self.exchange(code).status_code, 200)
        self.assertEqual(self.exchange(code).status_code, 400)

    def test_consent_single_use_and_denial(self):
        query = self.approve(allow=False)
        self.assertEqual(query['error'], ['access_denied'])
        self.assertEqual(self.client.get('/api/auth/api-keys').json(), [])
        self.assertEqual(self.client.post('/api/mcp-oauth/consent', json=self.decision).status_code, 400)

    def test_consent_csrf_and_origin(self):
        r = self.begin()
        rid = parse_qs(urlsplit(r.headers['location']).query)['request'][0]
        csrf = self.client.get('/api/mcp-oauth/consent', params={'request_id':rid}).json()['csrf_token']
        body = dict(request_id=rid, csrf_token=csrf, allow=True, role='viewer')
        self.assertEqual(self.client.post('/api/mcp-oauth/consent', json={**body,'csrf_token':'forged'}).status_code, 403)
        self.assertEqual(self.client.post('/api/mcp-oauth/consent', json=body, headers={'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/mcp-oauth/consent', json=body).status_code, 200)

    def test_granted_role_cannot_exceed_requested_scope(self):
        r = self.begin(scope='erp:read')
        rid = parse_qs(urlsplit(r.headers['location']).query)['request'][0]
        consent = self.client.get('/api/mcp-oauth/consent', params={'request_id':rid}).json()
        self.assertEqual(consent['roles'], ['viewer'])
        r = self.client.post('/api/mcp-oauth/consent', json=dict(request_id=rid, csrf_token=consent['csrf_token'], allow=True, role='admin'))
        self.assertEqual(r.status_code, 400)

    def test_refresh_rotation_and_replay_revokes_grant(self):
        tokens = self.connect()
        data = dict(grant_type='refresh_token', refresh_token=tokens['refresh_token'], client_id=self.reg['client_id'], resource=self.resource)
        r = self.client.post('/api/mcp-oauth/token', data=data)
        self.assertEqual(r.status_code, 200, r.text)
        rotated = r.json()
        self.assertNotEqual(tokens['refresh_token'], rotated['refresh_token'])
        self.assertEqual(self.rpc(rotated['access_token']).status_code, 200)
        self.assertEqual(self.client.post('/api/mcp-oauth/token', data=data).status_code, 400)
        self.assertEqual(self.rpc(rotated['access_token']).status_code, 401)

    def test_refresh_cannot_expand_and_narrowing_survives_principal_refresh(self):
        tokens = self.connect('manager')
        data = dict(grant_type='refresh_token', refresh_token=tokens['refresh_token'], client_id=self.reg['client_id'])
        self.assertEqual(self.client.post('/api/mcp-oauth/token', data={**data,'scope':'erp:admin'}).status_code, 400)
        r = self.client.post('/api/mcp-oauth/token', data={**data,'scope':'erp:read'})
        self.assertEqual(r.status_code, 200, r.text)
        names = {t['name'] for t in self.rpc(r.json()['access_token']).json()['result']['tools']}
        self.assertNotIn('create_document', names)
        key_id = self.client.get('/api/auth/api-keys').json()[0]['id']
        refreshed = refresh_auth_principal({'credential_type':'api_key','api_key_id':key_id,'oauth_scope_role':'viewer'})
        self.assertEqual(refreshed['role'], 'viewer')

    def test_revocation_and_owner_demotion_disable_existing_credentials(self):
        tokens = self.connect('admin')
        db = get_db()
        db.set_value('User', self.user['name'], 'role', 'viewer')
        names = {t['name'] for t in self.rpc(tokens['access_token']).json()['result']['tools']}
        self.assertNotIn('create_document', names)
        key_id = self.client.get('/api/auth/api-keys').json()[0]['id']
        self.assertEqual(self.client.post(f'/api/auth/api-keys/{key_id}/revoke').status_code, 200)
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
        self.assertEqual(self.client.post('/api/mcp-oauth/token', data=dict(grant_type='refresh_token',refresh_token=tokens['refresh_token'],client_id=self.reg['client_id'])).status_code, 400)

    def test_access_expiry_resource_binding_and_rest_isolation(self):
        tokens = self.connect()
        # A valid admin cookie must never let an invalid MCP bearer through.
        self.assertEqual(self.rpc('bad-key').status_code, 401)
        self.assertEqual(self.rpc(tokens['refresh_token']).status_code, 401)
        db = get_db()
        with db.atomic():
            db.sql('UPDATE "MCP OAuth Token" SET resource = ? WHERE kind = ?', ['https://other.example/api/mcp','access'])
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
        with db.atomic():
            db.sql('UPDATE "MCP OAuth Token" SET resource = ?, expires_at = ? WHERE kind = ?', [self.resource,0,'access'])
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/auth/api-keys', headers={'Authorization':f"Bearer {tokens['access_token']}"}).status_code, 401)

    def test_disabled_rest_gate(self):
        tokens = self.connect()
        self.client.put('/api/auth/settings', json={'rest_api_enabled':'0'})
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 403)
        self.assertEqual(self.begin().status_code, 403)

    def test_confidential_client_secret_post(self):
        self.reg = self.register(token_endpoint_auth_method='client_secret_post')
        code = self.approve()['code'][0]
        self.assertEqual(self.exchange(code).status_code, 401)
        self.assertEqual(self.exchange(code, client_secret=self.reg['client_secret']).status_code, 200)

    def test_confidential_client_secret_basic(self):
        self.reg = self.register(token_endpoint_auth_method='client_secret_basic')
        code = self.approve()['code'][0]
        auth = base64.b64encode(f"{self.reg['client_id']}:{self.reg['client_secret']}".encode()).decode()
        r = self.client.post('/api/mcp-oauth/token', headers={'Authorization':f'Basic {auth}'}, data=dict(grant_type='authorization_code',code=code,redirect_uri=self.redirect,code_verifier=self.verifier))
        self.assertEqual(r.status_code, 200, r.text)

    def test_bad_registration_and_authorization_inputs(self):
        for body in ([], {'redirect_uris':[self.redirect], 'token_endpoint_auth_method':[]},
                     {'redirect_uris':[self.redirect], 'grant_types':[{}]}):
            self.assertEqual(self.client.post('/api/mcp-oauth/register', json=body).status_code, 400)
        for uri in ('http://evil.example/cb','https://evil.example/#fragment','javascript:alert(1)','https://user:pass@example.com/cb','https://*.example.com/cb'):
            r = self.client.post('/api/mcp-oauth/register',json={'redirect_uris':[uri]})
            self.assertEqual(r.status_code, 400, (uri,r.text))
        for overrides in ({'redirect_uri':'https://evil.example/cb'}, {'code_challenge_method':'plain'}, {'response_type':'token'}, {'scope':'unknown'}, {'resource':'https://other.example/api/mcp'}):
            self.assertEqual(self.begin(**overrides).status_code, 400)

    def test_cross_client_code_and_refresh_rejected(self):
        code = self.approve()['code'][0]
        other = self.register()['client_id']
        self.assertEqual(self.exchange(code, client_id=other).status_code, 400)
        tokens = self.exchange(code).json()
        self.assertEqual(self.client.post('/api/mcp-oauth/token', data=dict(grant_type='refresh_token',refresh_token=tokens['refresh_token'],client_id=other)).status_code, 400)

    def test_loopback_native_client(self):
        self.redirect = 'http://127.0.0.1/callback'
        self.reg = self.register()
        self.redirect = 'http://127.0.0.1:49152/callback'
        self.assertIn('access_token', self.connect())

    def test_legacy_keys_and_app_metadata(self):
        r = self.client.post('/api/auth/api-keys',json={'name':'Codex','role':'viewer','app_type':'codex'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.rpc(r.json()['token']).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/api-keys').json()[0]['app_type'], 'codex')
        self.assertEqual(self.client.post('/api/auth/api-keys',json={'name':'fake','app_type':'oauth'}).status_code, 422)

    def test_social_login_resumes_only_safe_request_id(self):
        r = self.begin()
        rid = parse_qs(urlsplit(r.headers['location']).query)['request'][0]
        request = Request({'type':'http','scheme':'https','path':'/api/auth/google/callback','headers':[(b'cookie',f'lambda_erp_mcp_return={rid}'.encode())]})
        self.assertEqual(_login_redirect(request, self.user['name']).headers['location'], '/connect/authorize?request='+rid)
        request = Request({'type':'http','scheme':'https','path':'/api/auth/google/callback','headers':[(b'cookie',b'lambda_erp_mcp_return=//evil.example')]})
        self.assertEqual(_login_redirect(request, self.user['name']).headers['location'], '/')

    def test_https_return_cookie_supports_apple_post_callback(self):
        with patch.dict(os.environ, {'MCP_PUBLIC_URL':'https://erp.example'}):
            r = self.begin(resource='https://erp.example/api/mcp')
            self.assertEqual(r.status_code, 303)
            self.assertIn('SameSite=none', r.headers['set-cookie'])
            self.assertIn('Secure', r.headers['set-cookie'])
            self.assertEqual(self.client.get('/.well-known/oauth-authorization-server').json()['issuer'], 'https://erp.example')

    def test_expired_code_and_disabled_owner(self):
        code = self.approve()['code'][0]
        db = get_db()
        with db.atomic():
            db.sql('UPDATE "MCP OAuth Code" SET expires_at = 0')
        self.assertEqual(self.exchange(code).status_code, 400)
        tokens = self.connect()
        db.set_value('User', self.user['name'], 'enabled', 0)
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)

    def test_oauth_revoke_endpoint_and_unknown_token(self):
        tokens = self.connect()
        for value in ('unknown', tokens['refresh_token']):
            r = self.client.post('/api/mcp-oauth/revoke', data={'client_id':self.reg['client_id'],'token':value})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rpc(tokens['access_token']).status_code, 401)

    def test_concurrent_code_exchange_issues_only_one_token_pair(self):
        code = self.approve()['code'][0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.exchange(code).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(len(get_db().sql('SELECT hash FROM "MCP OAuth Token"')), 2)

    def test_consent_is_bound_to_signed_in_user(self):
        r = self.begin()
        rid = parse_qs(urlsplit(r.headers['location']).query)['request'][0]
        csrf = self.client.get('/api/mcp-oauth/consent', params={'request_id':rid}).json()['csrf_token']
        forged = mcp_oauth.jwt.encode({'sub':'different-user','rid':rid,'aud':'mcp-consent','exp':int(time.time())+60}, mcp_oauth.SECRET_KEY, algorithm=mcp_oauth.ALGORITHM)
        r = self.client.post('/api/mcp-oauth/consent', json={'request_id':rid,'csrf_token':forged,'allow':True,'role':'viewer'})
        self.assertEqual(r.status_code, 403)
        # API credentials cannot substitute for the browser's interactive login.
        key = self.client.post('/api/auth/api-keys', json={'name':'test','role':'admin'}).json()['token']
        self.client.cookies.clear()
        r = self.client.post('/api/mcp-oauth/consent', headers={'Authorization':'Bearer '+key}, json={'request_id':rid,'csrf_token':csrf,'allow':True,'role':'viewer'})
        self.assertEqual(r.status_code, 403)

    def test_existing_keys_survive_app_label_migration(self):
        key = self.client.post('/api/auth/api-keys', json={'name':'legacy','role':'viewer'}).json()
        db = get_db()
        with db.atomic():
            db.sql('ALTER TABLE "Api Key" DROP COLUMN app_type')
            db.sql('DELETE FROM "_SchemaMigrations" WHERE version = 39')
        db._col_cache.pop('Api Key', None)
        db._text_col_cache.pop('Api Key', None)
        # Re-run the actual versioned migration on a pre-1.1.3 key table.
        migration = next(fn for version, _, fn in db.MIGRATIONS if version == 39)
        migration(db)
        self.assertEqual(self.rpc(key['token']).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/api-keys').json()[0]['app_type'], 'other')


if __name__ == '__main__':
    unittest.main()
