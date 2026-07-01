"""Social login (Google / Apple) for Lambda ERP.

OAuth/OIDC is layered on top of the existing password auth without changing what
a session *is*: once a provider has proven the user's identity, we mint the very
same `lambda_erp_token` JWT cookie that the password flow issues (see
`api/auth.py`). Everything downstream — `get_current_user`, roles, the frontend
auth context — is untouched, and the password flow keeps working unchanged.

Design notes:
- Providers are OIDC. We read each provider's discovery document for its
  authorization/token/JWKS endpoints, so the same code path serves Google and
  Apple. Only Apple's client-secret differs (a short-lived ES256 JWT signed with
  the developer key, built in `_client_secret`).
- No extra dependency: httpx (already a dep) does discovery + token exchange,
  python-jose (already a dep) validates the ID token against the provider JWKS
  and signs Apple's client secret.
- CSRF / flow state travels in the OAuth `state` parameter as a JWT signed with
  our own secret and a 10-minute expiry (carrying mode/uid/invite/nonce). We do
  not use a state cookie: Apple posts the callback cross-site (form_post), which
  a SameSite=Lax cookie would drop, and a signed+short-lived state avoids that
  minefield while still being unforgeable. The OIDC `nonce` is echoed in the ID
  token and checked, binding the token to this flow.
- A provider is simply *disabled* (its buttons hidden) whenever its env vars are
  absent, so local/dev runs need no OAuth setup.
"""

import os
import json
import time
import uuid
import secrets as _secrets

import httpx
from jose import jwt, JWTError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from lambda_erp.database import get_db
from lambda_erp.utils import now
from api.auth import (
    SECRET_KEY,
    ALGORITHM,
    create_access_token,
    set_auth_cookie,
    get_current_user,
    validate_assignable_role,
    _setting_enabled,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# A non-matchable password placeholder for OAuth-created users. It does not start
# with the bcrypt prefix, so `verify_password` can never accept it — the User row
# simply has no usable password until/unless one is set. Keeps `hashed_password`
# NOT NULL without a table-rebuild migration.
OAUTH_PASSWORD_SENTINEL = "oauth$no-password"

STATE_TTL_SECONDS = 600

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "google": {
        "label": "Google",
        "issuer": "https://accounts.google.com",
        "discovery": "https://accounts.google.com/.well-known/openid-configuration",
        "scope": "openid email profile",
        "response_mode": None,  # default `query` (GET callback)
    },
    "apple": {
        "label": "Apple",
        "issuer": "https://appleid.apple.com",
        "discovery": "https://appleid.apple.com/.well-known/openid-configuration",
        # Requesting name/email requires Apple's form_post response mode, which
        # POSTs the callback back to us.
        "scope": "openid email name",
        "response_mode": "form_post",
    },
}


def _client_id(provider: str) -> str | None:
    if provider == "google":
        return os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if provider == "apple":
        return os.environ.get("APPLE_OAUTH_CLIENT_ID")
    return None


def _client_secret(provider: str) -> str | None:
    """The client_secret to present at the token endpoint.

    Google: a static string from env. Apple: there is no static secret — it is a
    JWT we sign with the Sign-in-with-Apple key (ES256, <=6-month expiry), built
    fresh on each exchange so nothing needs manual rotation.
    """
    if provider == "google":
        return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if provider == "apple":
        team_id = os.environ.get("APPLE_TEAM_ID")
        key_id = os.environ.get("APPLE_KEY_ID")
        private_key = os.environ.get("APPLE_PRIVATE_KEY")
        client_id = os.environ.get("APPLE_OAUTH_CLIENT_ID")
        if not (team_id and key_id and private_key and client_id):
            return None
        issued = int(time.time())
        claims = {
            "iss": team_id,
            "iat": issued,
            "exp": issued + 15777000,  # ~6 months, Apple's max
            "aud": "https://appleid.apple.com",
            "sub": client_id,
        }
        return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": key_id})
    return None


def is_configured(provider: str) -> bool:
    return bool(_client_id(provider) and _client_secret(provider))


def configured_providers() -> list[str]:
    """Providers that have complete credentials — drives which buttons show."""
    return [p for p in PROVIDERS if is_configured(p)]


# ---------------------------------------------------------------------------
# OIDC discovery + JWKS (cached in-process)
# ---------------------------------------------------------------------------

_discovery_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}


def _discovery(provider: str) -> dict:
    if provider not in _discovery_cache:
        url = PROVIDERS[provider]["discovery"]
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        _discovery_cache[provider] = resp.json()
    return _discovery_cache[provider]


def _jwks(provider: str) -> dict:
    if provider not in _jwks_cache:
        url = _discovery(provider)["jwks_uri"]
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        _jwks_cache[provider] = resp.json()
    return _jwks_cache[provider]


def _jwk_for(provider: str, kid: str) -> dict:
    for _ in range(2):
        for key in _jwks(provider).get("keys", []):
            if key.get("kid") == kid:
                return key
        # Unknown kid — provider may have rotated keys; drop cache and retry once.
        _jwks_cache.pop(provider, None)
    raise HTTPException(status_code=502, detail="Unknown signing key from identity provider")


# ---------------------------------------------------------------------------
# Redirect URI + flow state
# ---------------------------------------------------------------------------


def _redirect_uri(request: Request, provider: str) -> str:
    """The registered callback URL. Must match byte-for-byte between the auth
    request and the token exchange, and match what's registered with the
    provider. Prefer an explicit base (OAUTH_REDIRECT_BASE, e.g.
    https://erp.lambda.dev) so proxies can't perturb it; else derive it from the
    request, honoring the TLS-terminating proxy's forwarded headers."""
    base = os.environ.get("OAUTH_REDIRECT_BASE")
    if not base:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        base = f"{proto}://{host}"
    return f"{base.rstrip('/')}/api/auth/{provider}/callback"


def _encode_state(**claims) -> str:
    claims = {**claims, "exp": int(time.time()) + STATE_TTL_SECONDS}
    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)


def _decode_state(state: str) -> dict:
    try:
        return jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in state")


# ---------------------------------------------------------------------------
# ID-token verification
# ---------------------------------------------------------------------------


def _verify_id_token(provider: str, id_token: str, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError:
        raise HTTPException(status_code=400, detail="Malformed identity token")
    key = _jwk_for(provider, header.get("kid"))
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[header.get("alg", "RS256")],
            audience=_client_id(provider),
            issuer=PROVIDERS[provider]["issuer"],
            # Apple omits at_hash; we don't pass the access token, so disable it.
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=400, detail=f"Identity token rejected: {exc}")
    if nonce and claims.get("nonce") != nonce:
        raise HTTPException(status_code=400, detail="Sign-in nonce mismatch")
    return claims


def _email_is_verified(claims: dict) -> bool:
    val = claims.get("email_verified")
    return val is True or str(val).lower() == "true"


# ---------------------------------------------------------------------------
# User lookup / creation
# ---------------------------------------------------------------------------


def _find_by_identity(db, provider: str, subject: str) -> dict | None:
    rows = db.sql(
        'SELECT user_name FROM "User OAuth Identity" WHERE provider = ? AND subject = ?',
        [provider, subject],
    )
    if not rows:
        return None
    user_name = rows[0]["user_name"]
    users = db.sql(
        'SELECT name, email, full_name, role, enabled FROM "User" WHERE name = ?',
        [user_name],
    )
    return dict(users[0]) if users else None


def _link_identity(db, user_name: str, provider: str, subject: str, email: str | None):
    db.insert("User OAuth Identity", {
        "name": f"OAI-{uuid.uuid4().hex[:8]}",
        "user_name": user_name,
        "provider": provider,
        "subject": subject,
        "email": (email or "").lower() or None,
        "creation": now(),
    })


def _consume_invite(db, invite_token: str, email: str) -> str:
    rows = db.sql('SELECT token, email, role, used FROM "Invite" WHERE token = ?', [invite_token])
    invite = rows[0] if rows else None
    if not invite:
        raise HTTPException(status_code=404, detail="Invalid invite token")
    if invite["used"]:
        raise HTTPException(status_code=410, detail="Invite already used")
    if invite["email"].lower() != email.lower():
        raise HTTPException(status_code=403, detail="This invite was issued for a different email")
    role = validate_assignable_role(invite["role"])
    db.sql('UPDATE "Invite" SET used = 1 WHERE token = ?', [invite_token])
    db.conn.commit()
    return role


def _create_user(db, email: str, full_name: str, invite_token: str | None) -> dict:
    """Create a User for a first-time OAuth sign-in, honoring the same signup
    gating as the password `register` flow (first-user-admin / invite / public
    signup). Returns the new user dict or raises the appropriate HTTP error."""
    user_count = db.sql('SELECT COUNT(*) as cnt FROM "User"')[0]["cnt"]
    if user_count == 0:
        role = "admin"
    elif invite_token:
        role = _consume_invite(db, invite_token, email)
    elif _setting_enabled(db, "allow_public_signup"):
        role = "viewer"
    else:
        raise HTTPException(status_code=403, detail="Registration requires an invite")

    user_name = f"USR-{uuid.uuid4().hex[:8]}"
    db.insert("User", {
        "name": user_name,
        "email": email.lower(),
        "full_name": full_name or email.split("@")[0],
        "hashed_password": OAUTH_PASSWORD_SENTINEL,
        "role": role,
        "enabled": 1,
        "creation": now(),
        "modified": now(),
    })
    return {"name": user_name, "email": email.lower(), "full_name": full_name, "role": role}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _require_provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    if not is_configured(provider):
        raise HTTPException(status_code=404, detail=f"{PROVIDERS[provider]['label']} sign-in is not configured")


@router.get("/oauth/providers")
def oauth_providers():
    """Public: which social-login buttons the frontend should show."""
    return {"providers": [
        {"id": p, "label": PROVIDERS[p]["label"]} for p in configured_providers()
    ]}


@router.get("/oauth/identities")
def list_identities(user: dict = Depends(get_current_user)):
    """The signed-in user's linked providers (for a 'Linked accounts' UI)."""
    db = get_db()
    rows = db.sql(
        'SELECT provider, email, creation FROM "User OAuth Identity" WHERE user_name = ? ORDER BY creation',
        [user["name"]],
    )
    return [dict(r) for r in rows]


@router.get("/{provider}/login")
def oauth_login(provider: str, request: Request, link: int = 0, invite: str | None = None):
    """Kick off the OAuth flow. With ?link=1 (while authenticated) the callback
    attaches the provider to the current account instead of logging in."""
    _require_provider(provider)
    cfg = PROVIDERS[provider]

    mode = "login"
    uid = None
    if link:
        # Linking requires a real active session; capture who is linking. The
        # shared public_manager demo account must never be a link target.
        current = get_current_user(request)
        if current["role"] == "public_manager":
            raise HTTPException(status_code=403, detail="Demo accounts cannot link a provider")
        mode = "link"
        uid = current["name"]

    nonce = _secrets.token_urlsafe(16)
    state = _encode_state(
        provider=provider, mode=mode, uid=uid, invite=invite,
        nonce=nonce, sid=_secrets.token_urlsafe(8),
    )
    redirect_uri = _redirect_uri(request, provider)
    params = {
        "client_id": _client_id(provider),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "nonce": nonce,
    }
    if cfg["response_mode"]:
        params["response_mode"] = cfg["response_mode"]

    authorization_endpoint = _discovery(provider)["authorization_endpoint"]
    url = str(httpx.URL(authorization_endpoint, params=params))
    return RedirectResponse(url=url, status_code=303)


async def _read_callback_params(request: Request) -> dict:
    """Callback args arrive in the query string (Google, GET) or as form fields
    (Apple form_post, POST)."""
    if request.method == "POST":
        form = await request.form()
        return dict(form)
    return dict(request.query_params)


def _login_redirect(request: Request, user_name: str) -> RedirectResponse:
    token = create_access_token(user_name)
    resp = RedirectResponse(url="/", status_code=303)
    set_auth_cookie(request, resp, token)
    return resp


def _apple_full_name(params: dict) -> str | None:
    """Apple returns the name only on the *first* authorization, as a JSON blob
    in the `user` form field."""
    raw = params.get("user")
    if not raw:
        return None
    try:
        name = json.loads(raw).get("name", {})
        parts = [name.get("firstName"), name.get("lastName")]
        return " ".join(p for p in parts if p) or None
    except (ValueError, AttributeError):
        return None


@router.api_route("/{provider}/callback", methods=["GET", "POST"])
async def oauth_callback(provider: str, request: Request):
    _require_provider(provider)
    params = await _read_callback_params(request)

    if params.get("error"):
        return RedirectResponse(url=f"/login?oauth_error={params.get('error')}", status_code=303)

    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return RedirectResponse(url="/login?oauth_error=missing_code", status_code=303)

    flow = _decode_state(state)
    if flow.get("provider") != provider:
        raise HTTPException(status_code=400, detail="State/provider mismatch")

    # Exchange the code for tokens.
    token_endpoint = _discovery(provider)["token_endpoint"]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(request, provider),
        "client_id": _client_id(provider),
        "client_secret": _client_secret(provider),
    }
    token_resp = httpx.post(token_endpoint, data=data, timeout=10)
    if token_resp.status_code != 200:
        return RedirectResponse(url="/login?oauth_error=token_exchange_failed", status_code=303)
    id_token = token_resp.json().get("id_token")
    if not id_token:
        return RedirectResponse(url="/login?oauth_error=no_id_token", status_code=303)

    claims = _verify_id_token(provider, id_token, flow.get("nonce", ""))
    subject = claims.get("sub")
    email = (claims.get("email") or "").lower()
    if not subject:
        raise HTTPException(status_code=400, detail="Identity token missing subject")

    db = get_db()

    # --- Link mode: attach this provider identity to the current account ------
    if flow.get("mode") == "link":
        uid = flow.get("uid")
        existing = _find_by_identity(db, provider, subject)
        if existing and existing["name"] != uid:
            return RedirectResponse(url="/login?oauth_error=identity_taken", status_code=303)
        if not existing:
            _link_identity(db, uid, provider, subject, email)
        return RedirectResponse(url="/?linked=" + provider, status_code=303)

    # --- Login mode -----------------------------------------------------------
    user = _find_by_identity(db, provider, subject)
    if user:
        if not user.get("enabled"):
            return RedirectResponse(url="/login?oauth_error=account_disabled", status_code=303)
        return _login_redirect(request, user["name"])

    # No identity yet. We need a provider-verified email to go further.
    if not email or not _email_is_verified(claims):
        return RedirectResponse(url="/login?oauth_error=email_unverified", status_code=303)

    # An email match to an existing account is an intentional conflict, not an
    # auto-link (that would be an account-takeover vector). The user must sign in
    # and link from settings instead.
    clash = db.sql('SELECT name FROM "User" WHERE email = ?', [email])
    if clash:
        return RedirectResponse(url="/login?oauth_error=email_exists", status_code=303)

    full_name = claims.get("name") or _apple_full_name(params) or ""
    try:
        new_user = _create_user(db, email, full_name, flow.get("invite"))
    except HTTPException as exc:
        reason = "registration_closed" if exc.status_code == 403 else "invite_invalid"
        return RedirectResponse(url=f"/login?oauth_error={reason}", status_code=303)

    _link_identity(db, new_user["name"], provider, subject, email)
    return _login_redirect(request, new_user["name"])
