# REST API

Drive the ERP over its REST API — the same endpoints the web app uses
(`/api/documents`, `/api/masters`, `/api/reports`, …) — from a connector or
script, authenticated by Bearer API keys. **Off by default**: an admin turns it
on and issues keys. This is how you wire an external system (a webshop, a
fiduciary tool, a sync job) into an ERP instance without a browser session.

It reuses the **same per-user API keys** as the [Chat API](chat-api.md); the two
surfaces are enabled by independent flags. A key created for one works for the
other the moment its flag is on.

## How it works

Every REST endpoint already authenticates the web app through a session cookie.
With REST-key access enabled, those same endpoints **also** accept
`Authorization: Bearer sk_erp_…`. A key **acts as its owner**, with the owner's
live role capped at the key's role:

- A document the key creates is attributed to the real user.
- Every role check behaves exactly as if that user had logged in — a `viewer`
  key can read but not write; a `manager` key can create/cancel documents; only
  an `admin` key can touch admin-only endpoints (settings, user management).
- Lowering the owner's role, disabling them, or revoking the key constrains or
  kills access **immediately** — the role is resolved on every request.

There is no separate write API to learn: the key opens the front door of the
REST API the frontend already speaks.

## Enable it

1. Sign in as an **admin**.
2. **Settings → Programmatic API** → toggle **Enable** on the **REST API** row.
3. **Create key** — name it and pick a role (`viewer` / `manager` / `admin`,
   default is your own role). The full token (`sk_erp_…`) is shown **once** —
   copy it now. Keys are managed in the same place as chat keys.

Or over the API (with an admin cookie):

```bash
# enable REST-key access
curl -X PUT https://erp.example.com/api/auth/settings \
  -H 'Content-Type: application/json' --cookie "$ADMIN_COOKIE" \
  -d '{"rest_api_enabled": "1"}'

# issue a manager-capped key (returns {"token": "sk_erp_…", ...} once)
curl -X POST https://erp.example.com/api/auth/api-keys \
  -H 'Content-Type: application/json' --cookie "$ADMIN_COOKIE" \
  -d '{"name": "webshop-sync", "role": "manager"}'
```

> **Use a dedicated user, not your own login.** Create a user (e.g.
> `sync@your-co.example`) with the least role the job needs (`manager` to write
> documents), and issue the key from that account. Attribution stays clean, and
> you can rotate or disable it without touching your own access. A key can never
> out-rank its owner, so a manager-owned key can never reach admin endpoints.

## Use it

The endpoints are the ERP's ordinary REST surface — send the key on every
request:

```bash
# list documents of a type
curl https://erp.example.com/api/documents/sales-invoice \
  -H "Authorization: Bearer sk_erp_…"

# read one document (structured JSON)
curl https://erp.example.com/api/documents/sales-invoice/SINV-0001 \
  -H "Authorization: Bearer sk_erp_…"

# create a master (needs manager+)
curl -X POST https://erp.example.com/api/masters/customer \
  -H "Authorization: Bearer sk_erp_…" -H 'Content-Type: application/json' \
  -d '{"customer_name": "Acme AG"}'

# create a document, then submit it
curl -X POST https://erp.example.com/api/documents/quotation \
  -H "Authorization: Bearer sk_erp_…" -H 'Content-Type: application/json' \
  -d '{ "customer": "CUST-0001", "items": [ {"item_code": "SVC-A", "qty": 1, "rate": 100} ] }'
curl -X POST https://erp.example.com/api/documents/quotation/QTN-0001/submit \
  -H "Authorization: Bearer sk_erp_…"

# render a document PDF
curl https://erp.example.com/api/documents/sales-invoice/SINV-0001/pdf \
  -H "Authorization: Bearer sk_erp_…" -o invoice.pdf
```

The verbs mirror the document lifecycle: `POST /api/documents/{type}` (create),
`PUT /api/documents/{type}/{name}` (update a draft), and
`.../submit`, `.../cancel`, `.../discard`, `.../convert`. Masters live under
`/api/masters/{type}`, reports under `/api/reports/…`. Whatever the web app can
call, a suitably-roled key can call.

## Responses

| Status | Meaning |
|---|---|
| `200` | success |
| `401` | REST-key access is disabled, or the key is missing / malformed / invalid / revoked, or its owner is disabled |
| `403` | the key's role is too low for this endpoint (e.g. a `viewer` key writing) |
| `404` | unknown document/record |
| `409` / `422` | validation error (see the message) |

A malformed or invalid `Authorization: Bearer` header is a **failed** auth
attempt (`401`), never a silent downgrade to demo access.

## Scope & limits (v1)

- **Role is the only scope.** A key is capped at `viewer` / `manager` / `admin`;
  there are no per-endpoint or per-doctype scopes yet. Give a sync job the
  lowest role that does the work.
- **Independent of the Chat API.** Enabling REST keys does not enable
  `POST /api/v1/chat`, and vice versa — they are separate Settings flags over
  one shared set of keys.
- **Same key hygiene as chat keys.** Hashed at rest, shown once, revoke then
  delete (`POST /api/auth/api-keys/{id}/revoke`). Owner or admin manages them.

See [`chat-api.md`](chat-api.md) for the conversational surface that shares these
keys.
