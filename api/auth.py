"""Authentication & team management for Lambda ERP.

JWT cookie-based auth with role hierarchy: admin > manager > viewer.
First user to register becomes admin. Subsequent users need an invite.
"""

import os
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt, JWTError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from lambda_erp.database import get_db
from lambda_erp.utils import now

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _resolve_jwt_secret() -> str:
    """Get a stable JWT signing secret.

    Priority:
      1. JWT_SECRET_KEY env var (ops override, e.g. for multi-container
         deployments where all instances must share the secret).
      2. A `.jwt_secret` file next to the SQLite DB (auto-generated on
         first boot, persists via the same volume as the DB so restarts
         don't invalidate existing cookies).
      3. Newly-generated random secret (only used if the filesystem is
         read-only or inaccessible — cookies will be invalidated on
         restart in that degenerate case).
    """
    env_secret = os.environ.get("JWT_SECRET_KEY")
    if env_secret:
        return env_secret

    db_path = os.environ.get("LAMBDA_ERP_DB", "lambda_erp.db")
    data_dir = os.path.dirname(os.path.abspath(db_path)) or "."
    secret_file = os.path.join(data_dir, ".jwt_secret")

    if os.path.isfile(secret_file):
        try:
            with open(secret_file, "r", encoding="utf-8") as f:
                existing = f.read().strip()
            if existing:
                return existing
        except OSError:
            pass

    new_secret = secrets.token_hex(32)
    try:
        os.makedirs(data_dir, exist_ok=True)
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(new_secret)
    except OSError:
        # Filesystem is read-only or permission denied. Fall through and
        # use the in-memory secret; it just won't survive this restart.
        pass

    return new_secret


SECRET_KEY = _resolve_jwt_secret()
ALGORITHM = "HS256"
COOKIE_NAME = "lambda_erp_token"
TOKEN_EXPIRE_DAYS = 30
PASSWORD_HASH_PREFIX = "bcrypt_sha256$"
router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt()).decode("utf-8")
    return f"{PASSWORD_HASH_PREFIX}{hashed}"


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed.startswith(PASSWORD_HASH_PREFIX):
        return False

    digest = hashlib.sha256(plain.encode("utf-8")).hexdigest().encode("ascii")
    encoded_hash = hashed.removeprefix(PASSWORD_HASH_PREFIX).encode("utf-8")
    try:
        return bcrypt.checkpw(digest, encoded_hash)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user_name: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": user_name, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    """Returns user name (sub) or None if invalid/expired."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _is_https_request(request: Request) -> bool:
    """True if the request reached us over HTTPS, including via a TLS-
    terminating proxy (Azure Container Apps, Cloudflare, etc.) that sets
    X-Forwarded-Proto."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def set_auth_cookie(request: Request, response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        # Only mark Secure when the request actually came in over HTTPS,
        # so local http://localhost dev still gets the cookie.
        secure=_is_https_request(request),
        samesite="lax",
        max_age=TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/",
    )


def clear_auth_cookie(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str
    full_name: str
    password: str
    invite_token: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class InviteRequest(BaseModel):
    email: str
    role: str = "viewer"


class ChangeRoleRequest(BaseModel):
    role: str


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

ROLE_HIERARCHY = {"admin": 3, "manager": 2, "public_manager": 2, "viewer": 1}
ASSIGNABLE_ROLES = {"admin", "manager", "viewer"}


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: extract and validate JWT from cookie.
    Falls back to public_manager if one exists and no cookie is set."""
    token = request.cookies.get(COOKIE_NAME)

    if token:
        user_name = decode_token(token)
        if user_name:
            db = get_db()
            user = db.get_value("User", user_name, ["name", "email", "full_name", "role", "enabled"])
            if user and user.get("enabled"):
                return dict(user)

    # Fall back to public manager (demo mode)
    db = get_db()
    pub = db.sql('SELECT name, email, full_name, role, enabled FROM "User" WHERE role = "public_manager" AND enabled = 1')
    if pub:
        return dict(pub[0])

    raise HTTPException(status_code=401, detail="Not authenticated")


def validate_assignable_role(role: str) -> str:
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {role}")
    return role


def require_role(minimum_role: str):
    """Returns a dependency that checks the user has at least the specified role."""
    min_level = ROLE_HIERARCHY[minimum_role]

    def checker(user: dict = Depends(get_current_user)) -> dict:
        user_level = ROLE_HIERARCHY.get(user["role"], 0)
        if user_level < min_level:
            raise HTTPException(status_code=403, detail=f"Requires {minimum_role} role or higher")
        return user

    return checker


# Pre-built for convenience
require_admin = require_role("admin")
require_manager = require_role("manager")
require_viewer = require_role("viewer")


def require_non_public_manager(user: dict = Depends(get_current_user)) -> dict:
    user_level = ROLE_HIERARCHY.get(user["role"], 0)
    if user_level < ROLE_HIERARCHY["manager"]:
        raise HTTPException(status_code=403, detail="Requires manager role or higher")
    if user["role"] == "public_manager":
        raise HTTPException(status_code=403, detail="Demo mode cannot modify master data")
    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/setup-status")
def auth_setup_status():
    """Public: check if any users exist (for first-run detection)."""
    db = get_db()
    count = db.sql('SELECT COUNT(*) as cnt FROM "User"')[0]["cnt"]
    return {"has_users": count > 0, "registration_open": count == 0}


@router.post("/register")
def register(data: RegisterRequest, request: Request, response: Response):
    db = get_db()

    # Check if email already taken
    existing = db.sql('SELECT name FROM "User" WHERE email = ?', [data.email.lower()])
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Determine role
    user_count = db.sql('SELECT COUNT(*) as cnt FROM "User"')[0]["cnt"]

    if user_count == 0:
        role = "admin"
    else:
        if not data.invite_token:
            raise HTTPException(status_code=403, detail="Registration requires an invite")

        rows = db.sql('SELECT token, email, role, used FROM "Invite" WHERE token = ?', [data.invite_token])
        invite = rows[0] if rows else None
        if not invite:
            raise HTTPException(status_code=404, detail="Invalid invite token")
        if invite["used"]:
            raise HTTPException(status_code=410, detail="Invite already used")
        if invite["email"].lower() != data.email.lower():
            raise HTTPException(status_code=403, detail="This invite was issued for a different email")

        role = validate_assignable_role(invite["role"])
        db.sql('UPDATE "Invite" SET used = 1 WHERE token = ?', [data.invite_token])
        db.conn.commit()

    user_name = f"USR-{uuid.uuid4().hex[:8]}"
    db.insert("User", {
        "name": user_name,
        "email": data.email.lower(),
        "full_name": data.full_name,
        "hashed_password": hash_password(data.password),
        "role": role,
        "enabled": 1,
        "creation": now(),
        "modified": now(),
    })

    token = create_access_token(user_name)
    set_auth_cookie(request, response, token)

    return {"name": user_name, "email": data.email.lower(), "full_name": data.full_name, "role": role}


@router.post("/login")
def login(data: LoginRequest, request: Request, response: Response):
    db = get_db()
    rows = db.sql(
        'SELECT name, email, full_name, hashed_password, role, enabled FROM "User" WHERE email = ?',
        [data.email.lower()],
    )
    if not rows:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = rows[0]
    if not verify_password(data.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.get("enabled", 1):
        raise HTTPException(status_code=403, detail="Account disabled")
    if user["role"] == "public_manager":
        raise HTTPException(status_code=403, detail="Demo mode account cannot sign in directly")

    token = create_access_token(user["name"])
    set_auth_cookie(request, response, token)

    return {"name": user["name"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]}


@router.post("/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"name": user["name"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]}


@router.post("/invite")
def create_invite(data: InviteRequest, user: dict = Depends(require_admin)):
    db = get_db()

    validate_assignable_role(data.role)

    existing = db.sql('SELECT name FROM "User" WHERE email = ?', [data.email.lower()])
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    token = secrets.token_hex(16)
    db.insert("Invite", {
        "token": token,
        "email": data.email.lower(),
        "role": data.role,
        "created_by": user["name"],
        "used": 0,
        "creation": now(),
    })

    return {"token": token, "email": data.email.lower(), "role": data.role, "link": f"/login?invite={token}"}


@router.get("/users")
def list_users(user: dict = Depends(require_admin)):
    db = get_db()
    rows = db.sql('SELECT name, email, full_name, role, enabled, creation FROM "User" ORDER BY creation')
    return [dict(r) for r in rows]


@router.put("/users/{user_name}/role")
def change_role(user_name: str, data: ChangeRoleRequest, user: dict = Depends(require_admin)):
    validate_assignable_role(data.role)

    db = get_db()
    if not db.exists("User", user_name):
        raise HTTPException(status_code=404, detail="User not found")

    if user_name == user["name"] and data.role != "admin":
        admin_count = db.sql('SELECT COUNT(*) as cnt FROM "User" WHERE role = "admin" AND enabled = 1')[0]["cnt"]
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="Cannot demote the only admin")

    db.set_value("User", user_name, {"role": data.role, "modified": now()})
    return {"ok": True}


@router.delete("/users/{user_name}")
def disable_user(user_name: str, user: dict = Depends(require_admin)):
    if user_name == user["name"]:
        raise HTTPException(status_code=409, detail="Cannot disable yourself")

    db = get_db()
    if not db.exists("User", user_name):
        raise HTTPException(status_code=404, detail="User not found")

    db.set_value("User", user_name, {"enabled": 0, "modified": now()})
    return {"ok": True}


@router.get("/invites")
def list_invites(user: dict = Depends(require_admin)):
    db = get_db()
    rows = db.sql('SELECT token, email, role, used, creation FROM "Invite" ORDER BY creation DESC')
    return [dict(r) for r in rows]


@router.post("/public-manager")
def create_public_manager(user: dict = Depends(require_admin)):
    """Create (or re-enable) the public manager account for demo mode.

    Also seeds the chat-replay artefacts the "Enter Live Demo" script
    narrates (quotation, purchase order, custom analytics draft, the
    Redstone sales invoice, top-customer snapshots). Without this the
    admin-UI toggle would produce a public_manager account but a blank
    replay session, while booting with LAMBDA_ERP_ENABLE_PUBLIC_DEMO=1
    would produce both — same end state no matter how demo mode gets
    turned on.
    """
    db = get_db()
    existing = db.sql('SELECT name, enabled FROM "User" WHERE role = "public_manager"')
    if existing:
        if not existing[0]["enabled"]:
            db.set_value("User", existing[0]["name"], {"enabled": 1, "modified": now()})
        user_name = existing[0]["name"]
        status = "enabled"
    else:
        user_name = f"USR-{uuid.uuid4().hex[:8]}"
        db.insert("User", {
            "name": user_name,
            "email": "demo@lambda-erp.local",
            "full_name": "Demo User",
            "hashed_password": hash_password(secrets.token_hex(32)),
            "role": "public_manager",
            "enabled": 1,
            "creation": now(),
            "modified": now(),
        })
        status = "created"

    # Seed replay records idempotently. The helper is safe to call on
    # every invocation — it checks existing settings before inserting.
    # Wrapped so a seed edge case (e.g. simulator hasn't run yet in an
    # unusual deploy) never blocks the public_manager toggle.
    try:
        companies = db.get_all("Company", fields=["name"])
        if companies:
            from api.bootstrap import ensure_demo_chat_records
            ensure_demo_chat_records(companies[0]["name"])
    except Exception:
        pass

    return {"ok": True, "name": user_name, "status": status}


@router.delete("/public-manager")
def remove_public_manager(user: dict = Depends(require_admin)):
    """Disable the public manager account."""
    db = get_db()
    existing = db.sql('SELECT name FROM "User" WHERE role = "public_manager"')
    if not existing:
        return {"ok": True, "status": "not_found"}
    db.set_value("User", existing[0]["name"], {"enabled": 0, "modified": now()})
    return {"ok": True, "status": "disabled"}


@router.get("/public-manager")
def get_public_manager_status():
    """Public: check if a public manager exists and is enabled."""
    db = get_db()
    existing = db.sql('SELECT name, full_name, role, enabled FROM "User" WHERE role = "public_manager" AND enabled = 1')
    if existing:
        return {"active": True, "user": dict(existing[0])}
    return {"active": False}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

DEFAULTS = {
    "pdf_page_size": "A4",
    "opening_balances_enabled": "1",
}


@router.get("/settings")
def get_settings(user: dict = Depends(get_current_user)):
    db = get_db()
    rows = db.sql('SELECT key, value FROM "Settings"')
    settings = {r["key"]: r["value"] for r in rows}
    # Merge defaults for missing keys
    for k, v in DEFAULTS.items():
        if k not in settings:
            settings[k] = v
    return settings


@router.put("/settings")
def update_settings(data: dict, user: dict = Depends(require_admin)):
    db = get_db()
    for key, value in data.items():
        existing = db.sql('SELECT key FROM "Settings" WHERE key = ?', [key])
        if existing:
            db.sql('UPDATE "Settings" SET value = ? WHERE key = ?', [str(value), key])
        else:
            db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', [key, str(value)])
    db.conn.commit()
    # Return updated settings
    rows = db.sql('SELECT key, value FROM "Settings"')
    settings = {r["key"]: r["value"] for r in rows}
    for k, v in DEFAULTS.items():
        if k not in settings:
            settings[k] = v
    return settings
