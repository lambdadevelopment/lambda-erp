# Social login: Google & Apple sign-in

Status: **implemented in core** (`api/oauth.py`, unreleased). Both Google and
Apple are code-complete and covered by flow tests; each provider stays disabled
until its credentials are configured (login buttons appear only when a provider's
env vars are present). Remaining work is ops: register the OAuth apps and add the
secrets/redirect URIs per deployment (see Rollout). Owner: TBD.

## Goal
Let users authenticate with Google or Apple instead of creating an
email + password account, without changing what a session *is*. OAuth becomes
just another way to prove identity; once proven, the backend mints the **same**
`lambda_erp_token` JWT cookie the password flow already issues. Everything
downstream — `get_current_user`, roles, the frontend auth context — is
untouched. The existing email/password flow keeps working exactly as today.

**This is a core change** (`lambda-erp`). All auth lives in core
(`api/auth.py`, `frontend/src/pages/login.tsx`). The internal deployment is a
pure consumer — its only involvement is supplying provider credentials as
Container App secrets and registering the production redirect URIs.

## Locked decisions
- **Account linking = error on conflict (first login wins).** When an
  *unauthenticated* OAuth callback returns an email that already belongs to a
  password (or other-provider) account, we do **not** auto-link — we reject with
  a clear message. Auto-linking an unauthenticated third party to an existing
  account is an account-takeover vector, so first-registration wins.
- **Switching methods = an authenticated link action, not delete + re-signup.**
  The sanctioned way to move from password → Google (or add Apple) is a "Link
  Google/Apple to my account" action performed **while already logged in**. This
  keeps the same `USR-xxxx` primary key and therefore all `created_by` document
  history. (See "Why not delete + re-signup" below — it does not work cleanly
  with the current schema.)
- **OAuth email must be provider-verified to link or auto-create.** Google
  always asserts a verified email. Apple asserts verified for real addresses and
  for its private relay. We only trust the email when the provider marks it
  verified; otherwise reject.
- **New OAuth users are auto-created** on first sign-in (subject to the same
  `allow_public_signup` / invite gating the password `register` flow already
  enforces — an OAuth sign-in is a registration when no account exists).
- **Password stays optional, not removed.** An OAuth-only user carries a
  non-matchable sentinel hash (no table change), so they simply have no usable
  password. The password endpoints are unchanged.
- **Providers ship independently.** Google first (trivial setup), Apple second
  (real setup + secret-rotation cost). Nothing forces them into one release.
- **Invites are honorable via OAuth.** An admin invite link can be accepted with
  "Continue with Google/Apple" instead of setting a password — the invite token
  is carried through the OAuth `state` and consumed on the callback, applying the
  invited role. No separate invite-acceptance path for OAuth.
- **Unlinking is out of scope for now.** No unlink action in the first cut; a
  user keeps whatever methods they have linked. Can be added later (with a
  "≥1 method must remain" guard).

## Current state (what already exists)
`api/auth.py` — FastAPI auth router, stateless:
- **Sessions:** HS256 JWT (`{sub, exp}`) in an HTTP-only cookie
  `lambda_erp_token`, 30-day, SameSite=Lax, Secure when HTTPS. Minted by
  `create_access_token(user_name)` (auth.py:104), set via `response.set_cookie`
  (auth.py:133).
- **User table** (`lambda_erp/database.py`): PK `name` = `USR-{uuid[:8]}`,
  `email` (UNIQUE), `full_name`, `hashed_password` (`bcrypt_sha256$…`), `role`,
  `enabled`, timestamps.
- **Endpoints:** `register`, `login`, `logout`, `me`, `change-password`,
  `invite`, admin user management.
- **Secret resolution:** `JWT_SECRET_KEY` env → `.jwt_secret` file → in-memory.
  Internal passes `JWT_SECRET_KEY` via `terraform/app/container_app.tf`.

Frontend: `frontend/src/contexts/auth-context.tsx` (context),
`frontend/src/pages/login.tsx` (form), `frontend/src/api/client.ts` (fetch with
`credentials: "include"`). No token in JS — the cookie is the whole session.

**No OAuth/OIDC/SSO code exists** in either repo today.

## Why not "delete + re-signup" to switch methods
The current `DELETE /users/{name}` (auth.py:428) is admin-only and only
**soft-disables** (`enabled = 0`) — there is no hard delete and no self-service
deletion. So the "just delete and sign up again" path fails three ways:
1. A user cannot delete themselves; only an admin can, and only to disabled.
2. Soft-disable leaves the row and its `UNIQUE` email in place, so the same
   address cannot be re-registered.
3. Re-registering would mint a new `USR-xxxx`, orphaning every document whose
   `created_by` points at the old name.

Hence the authenticated **link** action is the switch mechanism. (A separate,
optional hard-delete-with-reassignment feature could be added later, but it is
out of scope here and not required for social login.)

## Design

### Identity storage
Add provider identities as a small side table rather than columns on User, so a
user can hold several (password + Google + Apple) and we never widen the hot
User row. Created via `CREATE TABLE IF NOT EXISTS` in `_setup_schema` (runs every
boot — the established pattern for brand-new tables), so no migration is needed:

```
Table: "User OAuth Identity"
  name        TEXT PK      -- OAI-{uuid[:8]}
  user_name   TEXT         -- FK → User.name  (NOT `user` — reserved in Postgres)
  provider    TEXT         -- 'google' | 'apple'
  subject     TEXT         -- provider 'sub' (stable per-provider user id)
  email       TEXT         -- provider-asserted email at link time (audit)
  creation    TEXT
  UNIQUE(provider, subject)
```

`hashed_password` stays NOT NULL: rather than a table-rebuild migration to make
it nullable, an OAuth-only user carries a sentinel value (`oauth$no-password`)
that can never match `verify_password` (it lacks the bcrypt prefix). So there is
**no schema migration at all** — one new table, no column changes.

Lookup on callback: match `(provider, subject)` first (stable even if the user
changes their provider email); fall back to matching a **verified** email to an
existing account only to *report the conflict*, never to silently link.

### Backend endpoints (`api/oauth.py`)
No new dependency: httpx (already a dep) does OIDC discovery + token exchange,
and python-jose (already a dep) validates the ID token against the provider JWKS
and signs Apple's ES256 client secret. Per provider, two routes:

- `GET /api/auth/{provider}/login`
  - Generates state + (for OIDC) nonce, stores them in a short-lived signed
    cookie, redirects to the provider's authorization URL.
  - Optional `?link=1` when called by an authenticated user → carry the current
    `user_name` in signed state so the callback links instead of logging in.
- `GET /api/auth/{provider}/callback`
  - Verify state/nonce; exchange code for tokens; validate the ID token
    signature via the provider JWKS; require `email_verified`.
  - **Link mode:** attach `(provider, subject)` to the current user (reject if
    that identity already belongs to someone else).
  - **Login mode:** find user by `(provider, subject)` → log in. Else if a
    verified email matches an existing account → **409 conflict** ("This email
    already has an account; sign in and link Google from settings"). Else →
    create a new User (respecting signup gating) + identity row.
  - **Invite mode:** when an invite token is carried in `state`, validate + consume
    it and create the User with the invited role (bypassing public-signup gating),
    then link the identity. Reuses the existing invite-token logic.
  - On success: `create_access_token` + `set_cookie` (reuse existing helpers),
    then redirect to the SPA.

Everything after cookie-set is identical to the password flow.

### Apple specifics (the expensive part)
- Requires the paid Apple Developer account (available — App Store Connect) with
  a **Services ID** (the OAuth client_id), an **App ID**, and a **Sign in with
  Apple key** (.p8).
- Apple's `client_secret` is **not** a static string — it is a JWT you sign with
  the .p8 key (ES256), max ~6-month expiry. We generate it at runtime from the
  key + team/key IDs, so there is no static secret to rotate manually, but the
  key material must be present as secrets.
- Apple returns the user's name **only on first authorization** — capture
  `full_name` from the first callback or fall back to the email local-part.
- Apple may return a **private relay** address (`…@privaterelay.appleid.com`);
  treat it as the verified email like any other.

### Frontend (`frontend/src/pages/login.tsx`)
- Add "Continue with Google" / "Continue with Apple" buttons that navigate
  (full redirect, not fetch) to `/api/auth/{provider}/login`. No OAuth JS SDK
  needed — the redirect dance is server-side; the user returns already
  authenticated with the cookie set.
- Show which providers are enabled via the existing `authSetupStatus` payload
  (extend it with `providers: ["google", "apple"]`), so buttons appear only when
  configured.
- Account settings: a "Linked accounts" section with "Link Google/Apple"
  buttons that hit `/api/auth/{provider}/login?link=1`. No unlink action in the
  first cut (see locked decisions).

### Domains & callback URLs
The live ERP is `erp.lambda.dev`; a separate, unrelated app runs on
`lambda.dev`. This is **not** a problem — neither provider restricts you to a
single domain, and different subdomains are first-class.
- **Google:** exact-match authorized redirect URI
  `https://erp.lambda.dev/api/auth/google/callback`. The consent screen's
  authorized domain is the registrable `lambda.dev`, which covers the subdomain
  automatically. Give the ERP its **own OAuth client**, separate from any
  `lambda.dev` client.
- **Apple:** register `erp.lambda.dev` under the Services ID's "Domains and
  Subdomains" and `https://erp.lambda.dev/api/auth/apple/callback` as a Return
  URL. Apple requires **domain verification**: the ERP must serve
  `https://erp.lambda.dev/.well-known/apple-developer-domain-association.txt`
  (Apple gives you the file contents). Give the ERP its **own Services ID**.
- **Cookie scoping:** the session cookie is set host-only (no `Domain=`
  attribute), so `lambda_erp_token` is scoped to `erp.lambda.dev` and never
  leaks to `lambda.dev`. Never add `Domain=lambda.dev` — that would broaden the
  session across both apps.

### Config / secrets (internal, via Terraform)
Store as Container App secrets and pass as env (pattern already at
`terraform/app/container_app.tf`):
- Google: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`.
- Apple: `APPLE_OAUTH_CLIENT_ID` (Services ID), `APPLE_TEAM_ID`,
  `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (.p8 contents).
- A base URL / allowed redirect for building callback URLs.
Register the production callback URLs (`https://<domain>/api/auth/google/callback`,
`…/apple/callback`) in Google Cloud Console and the Apple Services ID config.
Provider config is read from env; when a provider's vars are absent it is simply
disabled (buttons hidden) — so local/dev runs need no OAuth setup.

## Accounting / data impact
None. Auth touches no ledger, no documents. The only schema change is the new
`User OAuth Identity` table (created by `CREATE TABLE IF NOT EXISTS` at boot); no
column changes, no migration.

## Rollout
1. **Core, Google-first:** identity table + migration, `authlib`, Google
   login/callback, nullable password, login-page buttons, `authSetupStatus`
   providers list, linked-accounts settings UI. Release as a normal core bump.
2. **Internal:** register the Google OAuth client, add the two secrets in
   Terraform, bump the core pin. Verify end-to-end on the deployed domain.
3. **Core, Apple:** Services ID + key handling, runtime client-secret JWT,
   first-callback name capture, relay-email handling, Apple button. Release.
4. **Internal:** Apple secrets in Terraform, register callback, bump pin.

## Open questions
None outstanding — see locked decisions.
