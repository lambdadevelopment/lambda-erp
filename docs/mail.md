# Mail through Microsoft 365

Available from 1.4.32 (migration 72).

The ERP sends mail as a mailbox of the customer's own Microsoft 365 tenant,
through Microsoft Graph. Mail comes from their domain, is kept in that
mailbox's Sent Items, and replies reach the people who work there. There is no
SMTP relay and no third-party mail service.

## Setting it up

### 1. The app in Microsoft Entra

Use the Entra app that already serves Microsoft sign-in (multi-tenant), or a
dedicated one.

- Add the **application** permission `Microsoft Graph → Mail.Send`.
- The customer's Microsoft 365 admin grants consent for their tenant
  (admin consent URL:
  `https://login.microsoftonline.com/<tenant-id>/adminconsent?client_id=<client-id>`).
- Recommended: restrict the app to the sending mailbox(es) with an Exchange
  application access policy (or RBAC for Applications), so it cannot send as
  anyone else in the tenant.

### 2. The deployment (secrets)

| Variable | Meaning |
|---|---|
| `MAIL_GRAPH_TENANT_ID` | The customer's tenant ID. Required; mail stays off without it. |
| `MAIL_GRAPH_CLIENT_ID` / `MAIL_GRAPH_CLIENT_SECRET` | The app. Default: `MICROSOFT_OAUTH_CLIENT_ID` / `MICROSOFT_OAUTH_CLIENT_SECRET`. |
| `MAIL_ALLOWED_RECIPIENTS` | Optional, comma-separated addresses and `@domains`. When set, mail goes only to those and everything else is refused. Set it in every development or test environment. |

None of these are visible or editable in the UI; the settings page only says
whether they are configured.

### 3. In the ERP (admins)

**Settings → E-Mail (Microsoft 365)**: the sending mailbox (e.g.
`innendienst@example.ch`), optionally further mailboxes a message may name as
its sender, on/off, a test mail and the last messages sent. Mail can only be
switched on once a sending mailbox is set.

## Sending from code

```python
from api.mail import send_mail, MailError

send_mail(
    "einkauf@spital.example", "Auftragsbestätigung SO-0093", "Danke für Ihren Auftrag …",
    html=False, cc=None, reply_to="innendienst@example.ch",
    attachments=[("SO-0093.pdf", pdf_bytes, "application/pdf")],
    idempotency_key="sales-order:SO-0093:confirmation",
    reference=("Sales Order", "SO-0093"), actor=user_name,
)
```

- Returns `{"name", "status": "Sent", "replayed"}`; raises `MailError` when the
  message is refused or not sent (`retry` says whether trying later may help).
- **Idempotency.** With an `idempotency_key`, the same message sent again
  returns the first result (`replayed=True`) and mails nothing. The same key
  with different content is refused. A failed send can be retried with the
  same key. If the connection broke after Microsoft may have accepted the
  message, the outcome is logged as *Unknown* and is not repeated
  automatically: check the Sent Items first.
- Every message is recorded in `Mail Log` (sender, recipients, subject,
  status, error, the record it belongs to, who sent it). Bodies and
  attachments are not stored.
- Limits: up to 50 recipients, 3 MB of attachments per message.

## Not yet

Reading mail (an orders mailbox into the order intake) is a later step on the
same connection: it needs the additional `Mail.Read` permission for that
mailbox. There is no chat tool that sends mail; deployments decide which
workflow sends what.
