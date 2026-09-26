# App connections (planned for 1.1.3)

Settings → API access now starts with **Connect an app**. Choose ChatGPT, Claude
Desktop, Claude Code CLI, Codex CLI, or another integration. The setup only shows
instructions for that client. Existing keys keep working.

## Claude Desktop and ChatGPT connectors

Enable **REST API access** in the ERP first (this switch also controls MCP).
Add a custom remote connector in the client using `https://<erp-host>/api/mcp`.
Choose OAuth if asked, and leave optional client ID / secret fields empty: the
client registers automatically. Sign in to the ERP and approve an access level.
ChatGPT custom app availability depends on developer mode and workspace policy.

There is no API key to copy into these connectors. A consent screen identifies
the app and callback origin and defaults to read access. The granted role cannot
exceed the requested scopes or the user's current ERP role. Existing social
login (Google / Apple) returns to the pending consent screen too.

## CLI clients and other integrations

Choose the client, name the connection, select access, and create the key. Copy
both the key and the client-specific setup before dismissing it. Keys are shown
once. Claude Code gets a command; Codex gets a TOML configuration entry. Other
integrations can choose MCP, REST, or the separate Chat API. The setup indicates
which service must be enabled rather than offering a key that cannot connect.

The same normal API key can access the enabled REST / MCP and Chat API surfaces;
selecting a client or interface customizes instructions, **not** credential scope.
The role cap is enforced server-side. OAuth tokens, by contrast, are bound to
MCP and are not accepted as REST API keys or browser sessions.

## Deployment and protocol

- Set `MCP_PUBLIC_URL=https://<erp-host>` to the canonical **origin** when behind
  a reverse proxy. If unset, the incoming request's base URL is used. Use HTTPS
  for hosted connectors. Do not include `/api/mcp` in this variable.
- Metadata lives at `/.well-known/oauth-authorization-server` and
  `/.well-known/oauth-protected-resource/api/mcp` (also available without the
  resource path suffix). Unauthenticated MCP requests include a discovery
  challenge in `WWW-Authenticate`.
- Dynamic client registration: `/api/mcp-oauth/register`. Public clients and
  `client_secret_basic` / `client_secret_post` clients are supported. Only HTTPS
  callbacks and HTTP loopback callbacks are accepted. Native clients may vary
  the loopback port. Redirects are matched against registered callbacks; the
  server does not fetch arbitrary client metadata URLs.
- Authorization uses `/api/mcp-oauth/authorize`, followed by explicit, CSRF-bound
  approval. Codes expire after two minutes and are consumed atomically once.
  S256 PKCE is required even for confidential clients.
- `/api/mcp-oauth/token` issues one-hour access tokens and rotating 30-day refresh
  tokens. Only hashes are persisted. Both are bound to the client, grant and MCP
  resource. Refresh cannot expand scopes; refresh replay revokes the grant.
- `/api/mcp-oauth/revoke` or the ERP connections list revokes a grant. Disabled
  accounts, role demotion, and revoked grants take effect on subsequent calls.
- The transport stays stateless JSON over Streamable HTTP. GET returns 405
  because no server-initiated event stream is offered. MCP POST validates the
  Origin header; additional trusted browser origins can be configured with
  comma-separated `MCP_ALLOWED_ORIGINS` (normally not needed by remote clients).
- Scope names: `erp:read`, `erp:write`, `erp:admin`. MCP tool metadata describes
  permissions and read/write behavior; the existing ERP authorization still
  enforces every operation.

Migration 39 adds the app label to existing API keys. OAuth grants use the same
owner/role/revocation records, without an API-key secret. OAuth clients, pending
requests, codes and hashed tokens use separate tables created on startup.

## Verification

`python -m tests.test_mcp_oauth` covers discovery, registration, consent, code
exchange, PKCE, scope and resource binding, refresh rotation, expiry, revocation,
legacy key compatibility, and social-login return navigation. Set
`LAMBDA_ERP_TEST_DB` to a **disposable** PostgreSQL database to test that backend;
the suite clears its public schema. CI runs both database variants.

After deploying, verify real Claude and ChatGPT account linking, a read call,
a write within granted permissions, and revoke/reconnect. Local protocol tests
cannot establish client account eligibility or replace that live smoke test.
