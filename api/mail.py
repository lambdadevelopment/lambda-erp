"""Mail from the ERP through the customer's own Microsoft 365 (Microsoft Graph).

The ERP sends as a mailbox of the customer's tenant (for example a shared
`innendienst@...` mailbox): the mail comes from their domain and lands in
that mailbox's Sent Items, so replies reach the people who work there.

Where things are set:

- Deployment (secrets, never in the UI): MAIL_GRAPH_TENANT_ID is the
  customer's tenant. The app is MAIL_GRAPH_CLIENT_ID / MAIL_GRAPH_CLIENT_SECRET,
  by default the same Entra app as Microsoft sign-in
  (MICROSOFT_OAUTH_CLIENT_ID / MICROSOFT_OAUTH_CLIENT_SECRET). It needs the
  application permission Mail.Send, consented by the tenant's admin; limit it
  to the sending mailbox(es) with Exchange's application access policy.
- MAIL_ALLOWED_RECIPIENTS (deployment, optional): comma-separated addresses
  and @domains. When set, mail goes only to those; everything else is
  refused. Development environments set it so real customers are never
  mailed.
- Settings → E-Mail (admins): on/off, the default sending mailbox and further
  mailboxes a message may name as its sender.

send_mail() is the Python API for deployments and plugins. Every message is
logged ("Mail Log"). With an idempotency key, sending the same message again
returns the first result instead of mailing twice; a send whose outcome is
unknown (the connection broke after Graph got it) is not repeated
automatically. See docs/mail.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.auth import require_role
from lambda_erp.database import get_db
from lambda_erp.utils import now

router = APIRouter(prefix="/mail", tags=["mail"])

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
# Graph takes attachments inline up to a 4 MB request; base64 adds a third.
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024
MAX_RECIPIENTS = 50
ADDRESS = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"']+$")

SETTING_ENABLED = "mail.enabled"
SETTING_SENDER = "mail.sender"
SETTING_SENDERS = "mail.senders"

SENDING, SENT, FAILED, UNKNOWN = "Sending", "Sent", "Failed", "Unknown"

_token_lock = threading.Lock()
_token_cache: dict[tuple, tuple[str, float]] = {}


class MailError(Exception):
    """A message was refused or could not be sent. `retry` says whether
    trying again later may help (throttling, a service outage)."""

    def __init__(self, message: str, *, retry: bool = False, log: str | None = None):
        super().__init__(message)
        self.retry = retry
        self.log = log


# -- configuration ------------------------------------------------------------

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _credentials() -> tuple[str, str, str]:
    tenant = _env("MAIL_GRAPH_TENANT_ID")
    client = _env("MAIL_GRAPH_CLIENT_ID") or _env("MICROSOFT_OAUTH_CLIENT_ID")
    secret = _env("MAIL_GRAPH_CLIENT_SECRET") or _env("MICROSOFT_OAUTH_CLIENT_SECRET")
    return tenant, client, secret


def allowed_recipients() -> list[str]:
    return [part.strip().lower() for part in _env("MAIL_ALLOWED_RECIPIENTS").split(",") if part.strip()]


def _recipient_allowed(address: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    address = address.lower()
    domain = "@" + address.rsplit("@", 1)[-1]
    return address in allowed or domain in allowed


def _get_setting(key: str) -> str:
    rows = get_db().sql('SELECT value FROM "Settings" WHERE key = ?', [key])
    return (rows[0]["value"] or "") if rows else ""


def _set_setting(key: str, value: str) -> None:
    db = get_db()
    with db.atomic():
        if db.sql('SELECT key FROM "Settings" WHERE key = ?', [key]):
            db.sql('UPDATE "Settings" SET value = ? WHERE key = ?', [value, key])
        else:
            db.sql('INSERT INTO "Settings" (key, value) VALUES (?, ?)', [key, value])


def settings() -> dict:
    try:
        senders = json.loads(_get_setting(SETTING_SENDERS) or "[]")
    except ValueError:
        senders = []
    return {"enabled": _get_setting(SETTING_ENABLED) == "1",
            "sender": _get_setting(SETTING_SENDER) or None,
            "senders": [str(s) for s in senders if s]}


def status() -> dict:
    """What an admin sees: whether the deployment is configured (never the
    secrets themselves), the settings, and the recipient allowlist."""
    tenant, client, secret = _credentials()
    current = settings()
    configured = bool(tenant and client and secret)
    return {**current, "tenant_configured": bool(tenant), "client_configured": bool(client),
            "secret_configured": bool(secret), "configured": configured,
            "ready": configured and current["enabled"] and bool(current["sender"]),
            "allowed_recipients": allowed_recipients()}


def _clean_address(value: str, label: str) -> str:
    address = str(value or "").strip()
    if not ADDRESS.match(address) or len(address) > 254:
        raise MailError(f"{label}: {address!r} is not an email address")
    return address


def update_settings(*, enabled: bool | None = None, sender: str | None = None,
                    senders: list[str] | None = None) -> dict:
    if sender is not None:
        _set_setting(SETTING_SENDER, _clean_address(sender, "Sending mailbox") if sender.strip() else "")
    if senders is not None:
        cleaned = sorted({_clean_address(s, "Further mailbox").lower() for s in senders if str(s).strip()})
        _set_setting(SETTING_SENDERS, json.dumps(cleaned))
    if enabled is not None:
        if enabled and not settings()["sender"]:
            raise MailError("Set the sending mailbox before switching mail on")
        _set_setting(SETTING_ENABLED, "1" if enabled else "0")
    return status()


# -- Microsoft Graph ----------------------------------------------------------

def _token() -> str:
    tenant, client, secret = _credentials()
    if not (tenant and client and secret):
        raise MailError("Mail is not configured for this deployment (MAIL_GRAPH_TENANT_ID and the app credentials)")
    key = (tenant, client)
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        try:
            response = httpx.post(f"{LOGIN}/{tenant}/oauth2/v2.0/token", timeout=15, data={
                "client_id": client, "client_secret": secret, "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default"})
        except httpx.HTTPError as exc:
            raise MailError(f"Microsoft sign-in for mail could not be reached: {exc.__class__.__name__}", retry=True)
        if response.status_code != 200:
            detail = _graph_error(response)
            raise MailError(f"Microsoft refused the mail app's sign-in ({response.status_code}): {detail}",
                            retry=response.status_code >= 500)
        body = response.json()
        _token_cache[key] = (body["access_token"], time.time() + float(body.get("expires_in") or 3000))
        return body["access_token"]


def _graph_error(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error")
    if isinstance(error, dict):
        return f"{error.get('code')}: {error.get('message')}"[:300]
    return str(body.get("error_description") or error or body)[:300]


def _post_send_mail(sender: str, message: dict) -> None:
    """One sendMail call. Raises MailError; `UNKNOWN` outcomes raise with
    log=UNKNOWN so the caller records that Graph may have accepted it."""
    token = _token()
    try:
        response = httpx.post(f"{GRAPH}/users/{sender}/sendMail", timeout=30,
                              headers={"Authorization": f"Bearer {token}"},
                              json={"message": message, "saveToSentItems": True})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise MailError(f"Microsoft Graph could not be reached: {exc.__class__.__name__}", retry=True)
    except httpx.HTTPError as exc:
        # The request may have arrived: do not send it again automatically.
        raise MailError(f"Connection to Microsoft Graph broke off ({exc.__class__.__name__}); "
                        "check the mailbox's Sent Items before sending again", log=UNKNOWN)
    if response.status_code == 202:
        return
    if response.status_code == 401:
        with _token_lock:
            _token_cache.clear()
    detail = _graph_error(response)
    hint = ""
    if response.status_code == 403:
        hint = " (the app may not send from this mailbox: check Mail.Send consent and the application access policy)"
    elif response.status_code == 404:
        hint = " (no such mailbox in the tenant)"
    raise MailError(f"Microsoft Graph refused the message ({response.status_code}): {detail}{hint}",
                    retry=response.status_code in (429, 500, 502, 503, 504))


# -- sending ------------------------------------------------------------------

def _recipients(values, label: str) -> list[str]:
    if isinstance(values, str):
        values = [values]
    seen, out = set(), []
    for value in values or []:
        address = _clean_address(value, label)
        if address.lower() not in seen:
            seen.add(address.lower())
            out.append(address)
    return out


def send_mail(to, subject: str, body: str, *, html: bool = False, cc=None, reply_to=None,
              attachments: list[tuple[str, bytes, str]] | None = None, sender: str | None = None,
              idempotency_key: str | None = None, reference: tuple[str, str] | None = None,
              actor: str | None = None) -> dict:
    """Send one message through Microsoft 365 and log it.

    to / cc / reply_to: addresses (a string or a list). attachments:
    (filename, bytes, content type). sender: the default mailbox, or one of the
    further mailboxes in the settings. idempotency_key: the same key with the
    same content returns the first result (replayed=True); with different
    content it is refused. reference: (doctype, name) the message belongs to.

    Returns {"name", "status", "replayed"}. Raises MailError when the message
    is refused or not sent; a failed send may be tried again with the same key.
    """
    config = settings()
    if not config["enabled"]:
        raise MailError("Mail is switched off (Settings → E-Mail)")
    sender = (sender or config["sender"] or "").strip()
    if not sender:
        raise MailError("No sending mailbox is set (Settings → E-Mail)")
    permitted = {s.lower() for s in [config["sender"] or "", *config["senders"]] if s}
    if sender.lower() not in permitted:
        raise MailError(f"{sender} is not one of the sending mailboxes in Settings → E-Mail")
    to_list, cc_list = _recipients(to, "To"), _recipients(cc, "Cc")
    reply_list = _recipients(reply_to, "Reply-To")
    if not to_list:
        raise MailError("A message needs at least one recipient")
    if len(to_list) + len(cc_list) > MAX_RECIPIENTS:
        raise MailError(f"At most {MAX_RECIPIENTS} recipients per message")
    allowed = allowed_recipients()
    blocked = [a for a in to_list + cc_list if not _recipient_allowed(a, allowed)]
    if blocked:
        raise MailError(f"This environment only mails {', '.join(allowed)}; refused: {', '.join(blocked)}")
    subject = str(subject or "").strip()
    if not subject or len(subject) > 255:
        raise MailError("A subject of 1 to 255 characters is required")
    files = []
    total = 0
    for filename, data, content_type in attachments or []:
        name = str(filename or "").strip()
        if not name or "/" in name or "\\" in name:
            raise MailError(f"Attachment name {filename!r} is not valid")
        total += len(data)
        files.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                      "contentType": content_type or "application/octet-stream",
                      "contentBytes": base64.b64encode(data).decode("ascii")})
    if total > MAX_ATTACHMENT_BYTES:
        raise MailError(f"Attachments are {total / 1048576:.1f} MB; at most {MAX_ATTACHMENT_BYTES // 1048576} MB per message")

    digest = hashlib.sha256(json.dumps({
        "sender": sender.lower(), "to": to_list, "cc": cc_list, "reply_to": reply_list, "subject": subject,
        "body": body, "html": bool(html),
        "attachments": [[f["name"], hashlib.sha256(base64.b64decode(f["contentBytes"])).hexdigest()] for f in files],
    }, sort_keys=True).encode()).hexdigest()

    db = get_db()
    stamp = now()
    with db.atomic():
        if idempotency_key:
            if db.dialect == "postgres":
                db.sql("SELECT pg_advisory_xact_lock(hashtext(?))", ["mail:" + idempotency_key])
            earlier = db.sql('SELECT name, digest, status FROM "Mail Log" WHERE idempotency_key = ?', [idempotency_key])
            if earlier:
                row = earlier[0]
                if row["digest"] != digest:
                    raise MailError(f"Idempotency key {idempotency_key!r} was used for a different message")
                if row["status"] == SENT:
                    return {"name": row["name"], "status": SENT, "replayed": True}
                if row["status"] in (SENDING, UNKNOWN):
                    raise MailError("This message may already have been sent (outcome unknown); check the "
                                    "mailbox's Sent Items before sending it again", log=row["name"])
                # Failed before: try again under the same log row.
                db.sql('UPDATE "Mail Log" SET status = ?, error = NULL, modified = ? WHERE name = ?',
                       [SENDING, stamp, row["name"]])
                log = row["name"]
            else:
                log = None
        else:
            log = None
        if not log:
            log = "MAIL-" + uuid.uuid4().hex[:12]
            db.insert("Mail Log", {
                "name": log, "creation": stamp, "modified": stamp, "idempotency_key": idempotency_key,
                "digest": digest, "sender": sender, "recipients": json.dumps({"to": to_list, "cc": cc_list}),
                "subject": subject, "status": SENDING,
                "reference_doctype": reference[0] if reference else None,
                "reference_name": reference[1] if reference else None, "actor": actor})

    message = {"subject": subject, "body": {"contentType": "HTML" if html else "Text", "content": body or ""},
               "toRecipients": [{"emailAddress": {"address": a}} for a in to_list]}
    if cc_list:
        message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc_list]
    if reply_list:
        message["replyTo"] = [{"emailAddress": {"address": a}} for a in reply_list]
    if files:
        message["attachments"] = files
    try:
        _post_send_mail(sender, message)
    except MailError as exc:
        outcome = UNKNOWN if exc.log == UNKNOWN else FAILED
        with db.atomic():
            db.sql('UPDATE "Mail Log" SET status = ?, error = ?, modified = ? WHERE name = ?',
                   [outcome, str(exc)[:1000], now(), log])
        exc.log = log
        raise
    with db.atomic():
        db.sql('UPDATE "Mail Log" SET status = ?, error = NULL, modified = ? WHERE name = ?', [SENT, now(), log])
    return {"name": log, "status": SENT, "replayed": False}


def mail_log(limit: int = 50, reference: tuple[str, str] | None = None) -> list[dict]:
    db = get_db()
    where, params = "", []
    if reference:
        where, params = "WHERE reference_doctype = ? AND reference_name = ?", list(reference)
    rows = db.sql(f'SELECT name, creation, modified, sender, recipients, subject, status, error, '
                  f'reference_doctype, reference_name, actor FROM "Mail Log" {where} '
                  f'ORDER BY creation DESC LIMIT {max(1, min(int(limit), 500))}', params)
    return [{**dict(row), "recipients": json.loads(row["recipients"] or "{}")} for row in rows]


# -- admin endpoints ----------------------------------------------------------

_admin = Depends(require_role("admin"))


class MailSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    sender: str | None = Field(default=None, max_length=254)
    senders: list[str] | None = Field(default=None, max_length=20)


class TestMailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=3, max_length=254)
    sender: str | None = Field(default=None, max_length=254)


@router.get("/settings")
def get_mail_settings(user: dict = _admin):
    return status()


@router.put("/settings")
def put_mail_settings(data: MailSettingsInput, user: dict = _admin):
    try:
        return update_settings(enabled=data.enabled, sender=data.sender, senders=data.senders)
    except MailError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/test")
def send_test_mail(data: TestMailInput, user: dict = _admin):
    try:
        return send_mail(data.to, "Test mail from the ERP",
                         "This test message was sent by the ERP through Microsoft 365. "
                         "If you received it, mail from the ERP works.",
                         sender=data.sender, reference=("Mail Test", user.get("name") or ""),
                         actor=user.get("name"))
    except MailError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/log")
def get_mail_log(limit: int = 50, user: dict = _admin):
    return {"messages": mail_log(limit)}
