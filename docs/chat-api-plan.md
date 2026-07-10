# Plan — Programmatic Chat API (core `lambda-erp`)

**Goal.** Expose the ERP's chat agent over a simple authenticated HTTP API so an
external application (via Lambda's own `lambda-web` infra, then the iPhone app)
can hold a conversation with an ERP instance — the same way a connector script
talks to Microsoft Dynamics NAV today. This is a **core capability** (new
interface + auth model + data model), so it lands **upstream in `lambda-erp`**;
`lambda-erp-internal` then just bumps the version, flips the enable flag, and
issues a key.

**Scope for v1: chat only.** No document CRUD over this API beyond what the chat
agent itself already does with its tools.

## Decisions (locked with the user)

1. **Synchronous REST.** `POST /api/v1/chat { message, session_id? }` runs the
   agent to completion and returns the final reply. (SSE streaming is a possible
   fast-follow, not v1.)
2. **Named API keys in a new table** — admin creates/revokes multiple keys,
   hashed at rest, shown once. `Authorization: Bearer sk_erp_…`.
3. **Per-key role, default `manager`** (∈ `viewer|manager|admin`). The agent's
   existing role gates enforce what tools the key may use.
4. **Stateless-first, history opt-in (revised).** The Lambda ERP is *one of
   several* backends the caller's assistant (lambda-web / iOS) consults, so the
   conversation-of-record lives on the caller side, not here. Therefore:
   - **Default (no `session_id`): stateless reasoning.** The agent answers using
     only the current message (+ system prompt); prior turns are **not** replayed
     as LLM context. This avoids the "lossy shadow conversation" problem — the ERP
     never tries to resolve "as I said earlier" against a partial history it only
     half-saw. The caller resolves references and sends self-contained prompts.
   - **Still persisted for audit.** Each turn is written to the caller's rolling
     session so it's inspectable in the ERP chat UI — persisted for visibility,
     **not** replayed as context.
   - **Opt-in continuity (`session_id` given): stateful.** That session's history
     *is* replayed — caller-owned, ephemeral working memory for a single
     multi-step ERP task ("create an offer" → "add 3 widgets" → "convert to
     invoice"). The caller controls when to start fresh (delete → new).
   The single knob is **"replay history or not,"** gated on whether the caller
   passed an explicit `session_id`.
5. **Disabled by default.** A `chat_api_enabled` Settings flag gates everything;
   endpoints behave as if absent (404) when off.

## How the code sits today (verified)

- **Agent loop** `api/chat.py::run_thinking_loop(messages, on_event, session_id,
  user_info, client_ip)` is already transport-agnostic: it takes a generic async
  `on_event` callback and writes the final assistant turn back into `messages`.
- **WS driver** `process_session_message` (a closure inside `chat_websocket`)
  builds the system prompt + conversation, defines an `on_event` that forwards to
  the WebSocket, calls `run_thinking_loop`, then persists the assistant message
  and kicks off title generation. **A REST path is a second, thinner driver over
  the same loop.**
- **Auth** is cookie-JWT only (`api/auth.py`), roles `admin > manager > viewer`
  (+ demo `public_manager`). No API-key/Bearer path exists yet — net new.
- **Settings** is a key/value table with an established pattern: `DEFAULTS`,
  `_setting_enabled(db, key)`, `GET/PUT /api/settings` (PUT is admin-gated).
- **Sessions** — `create_session(user_id)`, `list_sessions(user_id, role)`,
  `get_session`, `can_access_session(session, user)`, `save_chat_message`,
  `load_serialized_chat_history`. Session rows carry a `user_id` owner.

## Design

### 1. Data model

**New table `Api Key`** (quoted-name convention, like `"Chat Session"`):

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | uuid |
| `name` | TEXT | human label ("lambda-web connector") |
| `key_hash` | TEXT | `sha256(token)` — tokens are high-entropy, so sha256 is sufficient and fast (unlike user passwords) |
| `key_prefix` | TEXT | first ~11 chars (`sk_erp_ab12`) for display/identification only |
| `role` | TEXT | `viewer\|manager\|admin`, default `manager` |
| `session_owner` | TEXT | the `user_id` value its chat sessions are owned under (e.g. `apikey:<id>`) — keeps API conversations isolated from human users |
| `created_by` | TEXT | admin User.name that issued it |
| `created_at` | TEXT | |
| `last_used_at` | TEXT | touched on each authenticated call (audit) |
| `revoked` | INTEGER | 0/1, soft-disable without deleting history |

Created idempotently in `database.py` alongside the other `CREATE TABLE IF NOT
EXISTS` DDL (and a `_migrate` add for existing DBs). Dual-backend safe (SQLite +
Postgres) — plain TEXT/INTEGER only.

**Settings flag.** Add to `api/auth.py::DEFAULTS`:
`"chat_api_enabled": "0"`.

### 2. Auth — Bearer API key

New dependency in `api/auth.py` (mirrors `get_current_user`):

```
def get_api_caller(request) -> dict:   # returns a user_info-shaped dict
    - if not _setting_enabled(db, "chat_api_enabled"): raise 404  (feature hidden)
    - token = bearer from Authorization header; missing -> 401
    - row = SELECT * FROM "Api Key" WHERE key_hash = sha256(token) AND revoked = 0
    - none -> 401
    - UPDATE last_used_at = now()
    - return {"name": row.session_owner, "role": row.role, "api_key_id": row.id}
```

- Token format `sk_erp_<48 hex>`; on issue store `key_hash = sha256`, `key_prefix
  = token[:11]`. Returned in full exactly once.
- 404-when-disabled avoids advertising the surface on instances that never turn
  it on.
- The returned dict is the same shape `run_thinking_loop`/`build_system_prompt`
  already consume, so role gating "just works."

### 3. REST endpoints — `api/routers/chat_api.py` (mounted at `/api/v1`)

- **`POST /api/v1/chat`** — body `{ message: str, session_id?: str }`, dep
  `get_api_caller`.
  1. Resolve the target session + the replay mode:
     - `session_id` given → load it; must be owned by the caller's
       `session_owner` (else 404). **`replay_history = True`** (stateful
       continuity).
     - omitted → the caller's most-recent non-deleted session, or
       `create_session(user_id=session_owner)` if none — the rolling **audit**
       session. **`replay_history = False`** (stateless reasoning; the turn is
       still persisted here for visibility).
  2. `save_chat_message(session, "user", message)`.
  3. Run the shared headless driver (below) with a no-op `on_event` and the
     resolved `replay_history`.
  4. Respond `{ reply, session_id, title }`. Blocking; generous server timeout.
- **`GET /api/v1/chat/sessions`** — list the caller's sessions (id, title,
  updated_at) so a connector can show/choose conversations.
- **`DELETE /api/v1/chat/sessions/{id}`** — delete (the "then a new one will be
  created" reset). Owner-scoped.
- (Optional) **`GET /api/v1/chat/sessions/{id}`** — paginated history, reusing
  `load_serialized_chat_history`, if the connector wants to render past turns.

All gated by `chat_api_enabled` via the shared dependency.

### 4. Refactor — one shared agent driver (small, low-risk)

Hoist the body of `process_session_message` into a module-level:

```
async def run_session_turn(session_id, user_content, user_info, on_event,
                           *, attachment_ids=None, client_ip=None,
                           replay_history=True) -> str | None:
    # build system prompt; conversation = full history (replay_history=True)
    #   OR just the current user message (replay_history=False, stateless);
    # (attachments), run_thinking_loop, extract + persist the assistant reply,
    # fire title-gen on first reply, return the assistant content.
```

- `replay_history` is the single statefulness knob. `True` → `build_conversation`
  (full session history, the normal WS chat). `False` → context is only the
  current user turn (stateless REST default); the turn is still persisted, just
  not replayed.
- The WS closure becomes a thin wrapper: build its forwarding `on_event`, call
  `run_session_turn(..., replay_history=True)`, done. Behaviour identical
  (guarded by the validation suite + manual chat smoke test).
- REST calls it with `on_event = _noop` and `replay_history` = whether a
  `session_id` was supplied; uses the return value.
- **The agent loop `run_thinking_loop` itself is not touched.**

### 5. Admin management — API + UI (core frontend)

Backend (admin-gated, `api/auth.py` or a small `api/routers/api_keys.py`):
- `GET /api/api-keys` → list metadata (id, name, prefix, role, last_used,
  revoked). Never returns a token.
- `POST /api/api-keys` `{name, role}` → create; returns the **full token once**.
- `POST /api/api-keys/{id}/revoke` → soft-revoke.
- The enable flag rides the existing `PUT /api/settings`.

Frontend (**must land in the core repo** so both core and `-internal`'s build
get it): admin Settings page gains a **"Chat API"** section — the enable toggle,
a key list, a Create dialog (name + role) that surfaces the token once with a
copy button, and Revoke.

### 6. Security & guardrails

- Keys hashed at rest, shown once, prefix-identified; soft-revoke keeps audit.
- Off by default; disabled instances 404 the whole surface.
- Per-key role bounds tool access through the **existing** agent gates — a
  `manager` key can create/cancel documents via chat; the Create-key UI must say
  so plainly.
- `last_used_at` for audit; consider a lightweight per-key rate limit (reuse the
  demo limiter shape) — can be deferred but noted.
- Sessions are owned under `apikey:<id>`, isolated from human users' chat lists.

### 7. Testing (`tests/`, in-memory SQLite)

- Key lifecycle: create → hash stored (not the token) → lookup by hash → revoke
  denies.
- Gating: endpoint 404 when `chat_api_enabled=0`; 401 on missing/bad/revoked key.
- Session semantics: two `POST /chat` without `session_id` land in the **same**
  rolling audit session; `DELETE` then a new one is created; a `session_id` from
  another owner → 404.
- Statefulness: without `session_id`, `run_session_turn` is called with
  `replay_history=False` (assert prior turns are not in the LLM message list);
  with `session_id`, `replay_history=True` (history replayed).
- (Agent reply itself is exercised by the existing suite; API tests can stub the
  LLM call or assert structure rather than content.)

### 8. Docs

- `docs/chat-api.md` — enable it, create a key, `curl` example, session
  semantics, security note. Add a line to the README feature list.

### 9. lambda-web side (out of scope here — noted for continuity)

A connector script (the NAV pattern) reads the ERP base URL + Bearer key from
config and POSTs to `/api/v1/chat`, surfacing the ERP chat to the iPhone app.
Built in `lambda-web`, not in this repo.

### 10. Rollout

Land in core `lambda-erp` → cut a release (PyPI + npm in lockstep) → `-internal`
bumps the pin, sets `chat_api_enabled=1`, and issues a key → lambda-web connects.

## Residual choices (proceeding with these defaults unless you say otherwise)

- **Path** `/api/v1/chat` (versioned, distinct from the cookie WS chat). ✔ default
- **Session visibility:** owned under `apikey:<id>`, *not* shown in the human web
  chat list for v1 (can surface to admins later). ✔ default
- **History as context:** stateless by default (persist for audit, don't
  replay); replay only when the caller passes an explicit `session_id`. ✔ default
- **Rate limiting:** ship without a per-key limit in v1, add if needed. ✔ default
