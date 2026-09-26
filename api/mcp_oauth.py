"""OAuth authorization-code + S256 PKCE for remote MCP connectors.

Dynamic registration supports public and confidential clients; no client URLs
are fetched. Grants reuse the per-user Api Key role/revocation model, but have
no API-key secret. Opaque, hashed OAuth tokens are valid only at this MCP resource.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jose import jwt, JWTError
from pydantic import BaseModel

from api.auth import (ALGORITHM, SECRET_KEY, API_KEY_CREDENTIAL, ROLE_HIERARCHY,
                      _setting_enabled, require_interactive_user,
                      refresh_auth_principal)
from lambda_erp.database import get_db
from lambda_erp.utils import now

router = APIRouter(tags=['mcp-oauth'])
SCOPES = {'erp:read': 'viewer', 'erp:write': 'manager', 'erp:admin': 'admin'}
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
RETURN_COOKIE = 'lambda_erp_mcp_return'
NO_STORE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}


class OAuthError(Exception):
    def __init__(self, error, description, status=400):
        self.error, self.description, self.status = error, description, status


async def oauth_error_handler(request, exc):
    return JSONResponse({'error': exc.error, 'error_description': exc.description},
                        status_code=exc.status, headers=NO_STORE)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def issuer(request):
    # Set on reverse-proxied deployments if the external origin differs from
    # Request.base_url. Never derive OAuth metadata from forwarded host headers.
    return os.environ.get('MCP_PUBLIC_URL', str(request.base_url)).rstrip('/')


def resource_url(request):
    return issuer(request) + '/api/mcp'


def challenge_header(request):
    return {'WWW-Authenticate': f'Bearer resource_metadata="{issuer(request)}/.well-known/oauth-protected-resource/api/mcp"'}


def _enabled():
    if not _setting_enabled(get_db(), 'rest_api_enabled'):
        raise OAuthError('access_denied', 'An administrator must enable REST API / MCP access first.', 403)


def _client(client_id):
    rows = get_db().sql('SELECT * FROM "MCP OAuth Client" WHERE id = ?', [client_id])
    if not rows:
        raise OAuthError('invalid_client', 'Unknown client. Add the connector again.', 401)
    return rows[0]


def _validate_redirect(uri):
    if not isinstance(uri, str) or len(uri) > 2048 or re.search(r'[\s\\*]', uri):
        raise OAuthError('invalid_redirect_uri', 'Invalid redirect URI.')
    try:
        parts = urlsplit(uri)
        parts.port  # Validate the port as well as the host.
    except ValueError:
        raise OAuthError('invalid_redirect_uri', 'Invalid redirect URI.')
    if (not parts.hostname or parts.username or parts.password or parts.fragment
            or parts.scheme not in {'https', 'http'}
            or (parts.scheme == 'http' and parts.hostname not in {'127.0.0.1', '[::1]', '::1', 'localhost'})):
        raise OAuthError('invalid_redirect_uri', 'Use HTTPS, or HTTP on a loopback address.')
    return parts


def _redirect_matches(uri, registered):
    candidate = _validate_redirect(uri)
    for allowed in registered:
        if uri == allowed:
            return True
        parts = urlsplit(allowed)
        # RFC 8252 native apps choose an ephemeral loopback listener port.
        if (parts.scheme == candidate.scheme == 'http' and parts.hostname == candidate.hostname
                and parts.hostname in {'127.0.0.1', '::1', 'localhost'}
                and parts.path == candidate.path and parts.query == candidate.query):
            return True
    return False


def _scope(value):
    scopes = set((value or 'erp:read').split())
    if not scopes or not scopes <= SCOPES.keys():
        raise OAuthError('invalid_scope', 'Supported permissions: erp:read erp:write erp:admin.')
    return ' '.join(sorted(scopes))


def _role(scope):
    return max((SCOPES[s] for s in scope.split()), key=lambda r: ROLE_HIERARCHY[r])


@router.get('/.well-known/oauth-protected-resource')
@router.get('/.well-known/oauth-protected-resource/api/mcp')
def protected_resource(request: Request):
    return JSONResponse({'resource': resource_url(request), 'resource_name': 'Lambda ERP',
                         'authorization_servers': [issuer(request)], 'scopes_supported': list(SCOPES),
                         'bearer_methods_supported': ['header']}, headers=NO_STORE)


@router.get('/.well-known/oauth-authorization-server')
def authorization_server(request: Request):
    base = issuer(request) + '/api/mcp-oauth'
    return JSONResponse({'issuer': issuer(request), 'authorization_endpoint': base + '/authorize',
                         'token_endpoint': base + '/token', 'registration_endpoint': base + '/register',
                         'revocation_endpoint': base + '/revoke', 'scopes_supported': list(SCOPES),
                         'response_types_supported': ['code'],
                         'grant_types_supported': ['authorization_code', 'refresh_token'],
                         'code_challenge_methods_supported': ['S256'],
                         'token_endpoint_auth_methods_supported': ['none', 'client_secret_post', 'client_secret_basic']},
                        headers=NO_STORE)


@router.get('/api/mcp-oauth/status')
def connection_status(request: Request, user: dict = Depends(require_interactive_user)):
    db = get_db()
    return {'mcp_url': resource_url(request), 'rest_enabled': _setting_enabled(db, 'rest_api_enabled'),
            'chat_enabled': _setting_enabled(db, 'chat_api_enabled')}


@router.post('/api/mcp-oauth/register')
async def register(request: Request):
    _enabled()
    raw = await request.body()
    if len(raw) > 16384:
        raise OAuthError('invalid_client_metadata', 'Registration is too large.')
    try:
        data = json.loads(raw)
    except ValueError:
        raise OAuthError('invalid_client_metadata', 'Expected JSON.')
    if not isinstance(data, dict):
        raise OAuthError('invalid_client_metadata', 'Expected an object.')
    uris = data.get('redirect_uris')
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10:
        raise OAuthError('invalid_redirect_uri', 'Supply 1–10 redirect URIs.')
    for uri in uris:
        _validate_redirect(uri)
    method = data.get('token_endpoint_auth_method', 'client_secret_basic')
    if not isinstance(method, str) or method not in {'none', 'client_secret_post', 'client_secret_basic'}:
        raise OAuthError('invalid_client_metadata', 'Unsupported client authentication method.')
    if (data.get('response_types', ['code']) != ['code']
            or not isinstance(data.get('grant_types', []), list)
            or any(not isinstance(g, str) for g in data.get('grant_types', []))
            or not set(data.get('grant_types', ['authorization_code'])) <= {'authorization_code', 'refresh_token'}):
        raise OAuthError('invalid_client_metadata', 'Only authorization code and refresh grants are supported.')
    name = data.get('client_name') or 'MCP connector'
    if not isinstance(name, str) or len(name) > 100:
        raise OAuthError('invalid_client_metadata', 'Client name must be at most 100 characters.')
    db = get_db()
    timestamp = int(time.time())
    # Bound unauthenticated registration growth without relying on process-local
    # counters (the same service can run on several replicas).
    if db.sql('SELECT COUNT(*) AS n FROM "MCP OAuth Client" WHERE created_at > ?', [timestamp - 3600])[0]['n'] >= 1000:
        raise OAuthError('temporarily_unavailable', 'Registration limit reached. Try again later.', 429)
    client_id, secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    with db.atomic():
        db.sql('INSERT INTO "MCP OAuth Client" (id,name,redirect_uris,auth_method,secret_hash,created_at) VALUES (?,?,?,?,?,?)',
               [client_id, name, json.dumps(uris), method, digest(secret) if method != 'none' else None, timestamp])
    result = {'client_id': client_id, 'client_id_issued_at': timestamp, 'client_name': name,
              'redirect_uris': uris, 'token_endpoint_auth_method': method,
              'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']}
    if method != 'none':
        result.update(client_secret=secret, client_secret_expires_at=0)
    return JSONResponse(result, status_code=201, headers=NO_STORE)


@router.get('/api/mcp-oauth/authorize')
def authorize(request: Request):
    _enabled()
    p = dict(request.query_params)
    if any(len(request.query_params.getlist(k)) != 1 for k in p) or len(str(request.url)) > 10000:
        raise OAuthError('invalid_request', 'Invalid authorization parameters.')
    client = _client(p.get('client_id'))
    if not _redirect_matches(p.get('redirect_uri'), json.loads(client['redirect_uris'])):
        raise OAuthError('invalid_request', 'Redirect URI does not match the registered client.')
    if p.get('response_type') != 'code':
        raise OAuthError('unsupported_response_type', 'Use response_type=code.')
    if p.get('code_challenge_method') != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}', p.get('code_challenge', '')):
        raise OAuthError('invalid_request', 'S256 PKCE is required.')
    if p.get('resource', resource_url(request)) != resource_url(request):
        raise OAuthError('invalid_target', 'Unknown MCP resource.')
    p['resource'] = resource_url(request)
    p['scope'] = _scope(p.get('scope'))
    request_id = secrets.token_hex(32)
    db = get_db()
    with db.atomic():
        db.sql('DELETE FROM "MCP OAuth Request" WHERE expires_at < ?', [int(time.time())])
        db.sql('INSERT INTO "MCP OAuth Request" (id,params,expires_at) VALUES (?,?,?)',
               [request_id, json.dumps(p), int(time.time()) + 600])
    response = RedirectResponse('/connect/authorize?request=' + request_id, status_code=303, headers=NO_STORE)
    https = issuer(request).startswith('https://')
    # Apple's social sign-in returns with a cross-site form POST.
    response.set_cookie(RETURN_COOKIE, request_id, max_age=600, httponly=True,
                        secure=https, samesite='none' if https else 'lax', path='/api/auth')
    return response


def _pending(request_id):
    rows = get_db().sql('SELECT * FROM "MCP OAuth Request" WHERE id = ? AND consumed = 0 AND expires_at > ?',
                        [request_id, int(time.time())])
    if not rows:
        raise OAuthError('invalid_request', 'This connection request has expired. Start again in your app.')
    return json.loads(rows[0]['params'])


@router.get('/api/mcp-oauth/consent')
def consent(request: Request, request_id: str, user: dict = Depends(require_interactive_user)):
    _enabled()
    p = _pending(request_id)
    client = _client(p['client_id'])
    # This signed CSRF token binds approval to the logged-in user and request.
    csrf = jwt.encode({'sub': user['name'], 'rid': request_id, 'aud': 'mcp-consent',
                       'exp': int(time.time()) + 600}, SECRET_KEY, algorithm=ALGORITHM)
    maximum = min(ROLE_HIERARCHY[user['role']], ROLE_HIERARCHY[_role(p['scope'])])
    return JSONResponse({'client_name': client['name'], 'redirect_uri': p['redirect_uri'],
                         'roles': [r for r in ('viewer', 'manager', 'admin') if ROLE_HIERARCHY[r] <= maximum],
                         'csrf_token': csrf}, headers=NO_STORE)


class ConsentDecision(BaseModel):
    request_id: str
    csrf_token: str
    allow: bool
    role: str = 'viewer'


@router.post('/api/mcp-oauth/consent')
def approve(data: ConsentDecision, request: Request, user: dict = Depends(require_interactive_user)):
    _enabled()
    origin = request.headers.get('origin')
    if origin and origin != issuer(request):
        raise OAuthError('access_denied', 'Invalid request origin.', 403)
    try:
        claims = jwt.decode(data.csrf_token, SECRET_KEY, algorithms=[ALGORITHM], audience='mcp-consent')
        if claims['sub'] != user['name'] or claims['rid'] != data.request_id:
            raise ValueError()
    except (JWTError, KeyError, ValueError):
        raise OAuthError('access_denied', 'Approval expired. Reload this page.', 403)
    p = _pending(data.request_id)
    client = _client(p['client_id'])
    if data.allow and (data.role not in SCOPES.values() or ROLE_HIERARCHY[data.role] > min(ROLE_HIERARCHY[user['role']], ROLE_HIERARCHY[_role(p['scope'])])):
        raise OAuthError('invalid_scope', 'The app cannot exceed your role or its requested access.')
    query = {'state': p['state']} if 'state' in p else {}
    db = get_db()
    with db.atomic():
        claimed = db.sql('UPDATE "MCP OAuth Request" SET consumed = 1 WHERE id = ? AND consumed = 0 AND expires_at > ? RETURNING id',
                         [data.request_id, int(time.time())])
        if not claimed:
            raise OAuthError('invalid_request', 'This approval has already been used or expired.')
        if data.allow:
            key_id, code = str(uuid.uuid4()), secrets.token_urlsafe(32)
            scope = ' '.join(s for s in p['scope'].split() if ROLE_HIERARCHY[SCOPES[s]] <= ROLE_HIERARCHY[data.role]) or 'erp:read'
            db.sql('INSERT INTO "Api Key" (id,name,owner,key_prefix,role,session_owner,created_at,app_type) VALUES (?,?,?,?,?,?,?,?)',
                   [key_id, client['name'], user['name'], 'OAuth', data.role, f"api:{user['name']}", now(), 'oauth'])
            db.sql('INSERT INTO "MCP OAuth Code" (hash,client_id,key_id,redirect_uri,challenge,resource,scope,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                   [digest(code), client['id'], key_id, p['redirect_uri'], p['code_challenge'], p['resource'], scope, int(time.time()) + 120])
            query['code'] = code
        else:
            query['error'] = 'access_denied'
    parts = urlsplit(p['redirect_uri'])
    redirect = urlunsplit(parts._replace(query=urlencode(parse_qsl(parts.query) + list(query.items()))))
    response = JSONResponse({'redirect_uri': redirect}, headers=NO_STORE)
    response.delete_cookie(RETURN_COOKIE, path='/api/auth')
    return response


def _authenticate_client(request, form):
    client_id, secret = form.get('client_id'), form.get('client_secret', '')
    header = request.headers.get('authorization', '')
    basic = header.lower().startswith('basic ')
    if basic:
        try:
            cid, secret = base64.b64decode(header[6:], validate=True).decode().split(':', 1)
            cid, secret = unquote(cid), unquote(secret)
        except (ValueError, UnicodeError):
            raise OAuthError('invalid_client', 'Invalid client authentication.', 401)
        if client_id and client_id != cid:
            raise OAuthError('invalid_client', 'Conflicting client IDs.', 401)
        client_id = cid
    client = _client(client_id)
    method = client['auth_method']
    if (method == 'client_secret_basic' and not basic) or (method == 'client_secret_post' and basic):
        raise OAuthError('invalid_client', 'Use the registered authentication method.', 401)
    if method != 'none' and not hmac.compare_digest(digest(secret), client['secret_hash']):
        raise OAuthError('invalid_client', 'Invalid client authentication.', 401)
    return client


async def _token_form(request):
    if len(await request.body()) > 16384:
        raise OAuthError('invalid_request', 'Token request is too large.')
    form = await request.form()
    if any(len(form.getlist(k)) != 1 for k in form) or any(not isinstance(v, str) or len(v) > 4096 for v in form.values()):
        raise OAuthError('invalid_request', 'Invalid token parameters.')
    return form


def _grant_user(key_id):
    principal = refresh_auth_principal({'credential_type': API_KEY_CREDENTIAL, 'api_key_id': key_id})
    if not principal or principal['role'] == 'public_manager':
        raise OAuthError('invalid_grant', 'Connection revoked or account no longer available.')
    return principal


def _issue_tokens(db, grant):
    access, refresh = 'erp_oauth_' + secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    for token, kind, ttl in ((access, 'access', ACCESS_TTL), (refresh, 'refresh', REFRESH_TTL)):
        db.sql('INSERT INTO "MCP OAuth Token" (hash,kind,client_id,key_id,resource,scope,expires_at) VALUES (?,?,?,?,?,?,?)',
               [digest(token), kind, grant['client_id'], grant['key_id'], grant['resource'], grant['scope'], int(time.time()) + ttl])
    return {'access_token': access, 'token_type': 'Bearer', 'expires_in': ACCESS_TTL,
            'refresh_token': refresh, 'scope': grant['scope']}


@router.post('/api/mcp-oauth/token')
async def token_endpoint(request: Request):
    _enabled()
    form = await _token_form(request)
    client = _authenticate_client(request, form)
    kind = form.get('grant_type')
    if kind not in {'authorization_code', 'refresh_token'}:
        raise OAuthError('unsupported_grant_type', 'Use authorization_code or refresh_token.')
    db = get_db()
    table = 'MCP OAuth Code' if kind == 'authorization_code' else 'MCP OAuth Token'
    token_hash = digest(form.get('code' if kind == 'authorization_code' else 'refresh_token', ''))
    rows = db.sql(f'SELECT * FROM "{table}" WHERE hash = ? AND client_id = ?', [token_hash, client['id']])
    if not rows or rows[0]['expires_at'] <= time.time():
        raise OAuthError('invalid_grant', 'Invalid or expired credential.')
    grant = rows[0]
    if grant['resource'] != resource_url(request) or form.get('resource', grant['resource']) != grant['resource']:
        raise OAuthError('invalid_target', 'Token is bound to a different MCP resource.')
    if kind == 'authorization_code':
        verifier = form.get('code_verifier', '')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        if (not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier)
                or not hmac.compare_digest(challenge, grant['challenge'])
                or form.get('redirect_uri') != grant['redirect_uri']):
            raise OAuthError('invalid_grant', 'Invalid PKCE verifier or redirect URI.')
    elif grant['kind'] != 'refresh':
        raise OAuthError('invalid_grant', 'Expected a refresh token.')
    if grant['consumed']:
        # A replayed refresh token invalidates the entire grant/token family.
        if kind == 'refresh_token':
            with db.atomic():
                db.sql('UPDATE "Api Key" SET revoked = 1 WHERE id = ?', [grant['key_id']])
        raise OAuthError('invalid_grant', 'Credential already used. Connect again.')
    _grant_user(grant['key_id'])
    if form.get('scope'):
        narrowed = _scope(form['scope'])
        if not set(narrowed.split()) <= set(grant['scope'].split()):
            raise OAuthError('invalid_scope', 'Refresh cannot expand permissions.')
        grant['scope'] = narrowed
    with db.atomic():
        claimed = db.sql(f'UPDATE "{table}" SET consumed = 1 WHERE hash = ? AND consumed = 0 AND expires_at > ? RETURNING hash',
                         [token_hash, int(time.time())])
        if claimed:
            result = _issue_tokens(db, grant)
        else:
            # A second worker may have consumed the same refresh token after
            # our initial read. Commit family revocation before returning error.
            if kind == 'refresh_token':
                db.sql('UPDATE "Api Key" SET revoked = 1 WHERE id = ?', [grant['key_id']])
            result = None
    if result is None:
        raise OAuthError('invalid_grant', 'Credential already used or expired.')
    return JSONResponse(result, headers=NO_STORE)


def authenticate_access_token(request, token):
    _enabled()
    rows = get_db().sql('SELECT * FROM "MCP OAuth Token" WHERE hash = ? AND kind = ? AND expires_at > ?',
                        [digest(token), 'access', int(time.time())])
    if not rows or rows[0]['resource'] != resource_url(request):
        raise HTTPException(401, 'Invalid or expired OAuth access token', headers=challenge_header(request))
    grant = rows[0]
    try:
        user = _grant_user(grant['key_id'])
    except OAuthError:
        raise HTTPException(401, 'OAuth connection revoked', headers=challenge_header(request))
    user['role'] = min((user['role'], _role(grant['scope'])), key=lambda r: ROLE_HIERARCHY[r])
    user['oauth_scope_role'] = _role(grant['scope'])
    # Return a user principal, not the isolated programmatic chat identity.
    user['name'] = user['user_id']
    db = get_db()
    with db.atomic():
        db.sql('UPDATE "Api Key" SET last_used_at = ? WHERE id = ?', [now(), grant['key_id']])
    return user


@router.post('/api/mcp-oauth/revoke')
async def revoke_token(request: Request):
    form = await _token_form(request)
    client = _authenticate_client(request, form)
    rows = get_db().sql('SELECT key_id FROM "MCP OAuth Token" WHERE hash = ? AND client_id = ?',
                        [digest(form.get('token', '')), client['id']])
    if rows:
        db = get_db()
        with db.atomic():
            db.sql('UPDATE "Api Key" SET revoked = 1 WHERE id = ?', [rows[0]['key_id']])
    return JSONResponse({}, headers=NO_STORE)
