# Chat API

Talk to the ERP's chat agent over a small HTTP API, authenticated by Bearer API
keys. **Off by default** — an admin enables it and issues keys. This is how an
external orchestrator (e.g. Lambda's own infra, then a mobile app) drives an ERP
instance the way a connector script talks to a legacy ERP.

## Enable it

1. Sign in as an **admin**.
2. **Settings → Chat API** → toggle **Enable**.
3. **Create key** — give it a name and a role (`viewer` / `manager` / `admin`,
   default `manager`). The full token (`sk_erp_…`) is shown **once** — copy it now.

Or via the API:

```bash
# enable
curl -X PUT https://erp.example.com/api/auth/settings \
  -H 'Content-Type: application/json' --cookie "$ADMIN_COOKIE" \
  -d '{"chat_api_enabled": "1"}'

# issue a key (returns {"token": "sk_erp_…", ...} once)
curl -X POST https://erp.example.com/api/auth/api-keys \
  -H 'Content-Type: application/json' --cookie "$ADMIN_COOKIE" \
  -d '{"name": "lambda-web", "role": "manager"}'
```

> A key **acts with its role**. A `manager` key can create and cancel documents
> through chat — treat the token like a password. Revoke from the same screen (or
> `POST /api/auth/api-keys/{id}/revoke`).

## Talk to the agent

```bash
curl -X POST https://erp.example.com/api/v1/chat \
  -H "Authorization: Bearer sk_erp_…" \
  -H 'Content-Type: application/json' \
  -d '{"message": "How many open sales orders are there?"}'
```

```json
{ "reply": "There are 3 open sales orders totalling …",
  "session_id": "a1b2c3d4",
  "title": "Open sales orders" }
```

The call **blocks** until the agent finishes (it may run several tool calls
internally).

### Sessions & history

The Lambda ERP is meant to be **one backend among several** that the caller's
assistant consults, so the caller owns the conversation-of-record. Statefulness
is therefore a single knob — whether you pass `session_id`:

- **No `session_id` (default): stateless.** The agent answers using only the
  message you send; prior turns are **not** replayed. The turn is still persisted
  to a rolling session (visible in the ERP chat UI) for audit — just not fed back
  to the model. Send **self-contained** prompts (resolve "that offer" / "like
  earlier" on your side before calling).
- **With `session_id`: stateful.** That session's history *is* replayed —
  caller-owned, ephemeral working memory for a single multi-step ERP task. You
  control when to start fresh.

```bash
# list this key's sessions
curl https://erp.example.com/api/v1/chat/sessions -H "Authorization: Bearer sk_erp_…"

# continue a specific conversation (replays its history)
curl -X POST https://erp.example.com/api/v1/chat -H "Authorization: Bearer sk_erp_…" \
  -H 'Content-Type: application/json' \
  -d '{"message": "now convert it to an invoice", "session_id": "a1b2c3d4"}'

# delete a session; the next stateless call opens a fresh one
curl -X DELETE https://erp.example.com/api/v1/chat/sessions/a1b2c3d4 \
  -H "Authorization: Bearer sk_erp_…"
```

Sessions are isolated per key — a key can only see/continue its own.

## Responses

| Status | Meaning |
|---|---|
| `200` | reply returned |
| `401` | missing / bad / revoked key |
| `404` | the API is disabled, or the `session_id` isn't the caller's |
| `422` | empty `message` |

See [`chat-api-plan.md`](chat-api-plan.md) for the design and rationale.
