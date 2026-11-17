# Social login

Lambda ERP supports Google, Apple and Microsoft sign-in alongside email and
password.
Each provider remains disabled until all of its environment variables are
configured; the login page only shows configured providers.

OAuth proves the user's identity and then creates the same
`lambda_erp_token` session cookie as the password flow. Roles, authorization
and the rest of the application therefore behave identically after sign-in.

## Account behavior

- A new verified provider identity creates a user when public signup is
  enabled or a valid invitation is present.
- An unauthenticated provider login never automatically links to an existing
  account with the same email address. The user must first sign in normally
  and link the provider from Settings. This prevents account takeover through
  an email match alone.
- Provider emails must be verified. Apple private-relay addresses are valid
  verified addresses. For Microsoft see below.
- Invitations can be accepted through any of the providers; the invitation
  token and role are carried through the signed OAuth state.
- OAuth-only users have no usable password. They can add one later as a
  fallback.
- Provider unlinking is not currently supported.

Provider identities live in the `User OAuth Identity` table and are keyed by
the provider's stable subject identifier. A user may have more than one login
method without changing their `User.name` or document attribution.

## Endpoints

The OAuth router is mounted below `/api/auth`:

| Endpoint | Purpose |
|---|---|
| `GET /api/auth/oauth/providers` | List configured providers for the login UI |
| `GET /api/auth/oauth/identities` | List the signed-in user's linked providers |
| `GET /api/auth/{provider}/login` | Start a login (`google`, `apple`, `microsoft`) |
| `GET /api/auth/{provider}/login?link=1` | Link a provider while signed in |
| `GET or POST /api/auth/{provider}/callback` | Provider callback |

Google returns through a GET callback. Apple uses `form_post`, so its callback
is accepted via POST as well.

## Configuration

Google requires:

- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`

Apple requires:

- `APPLE_OAUTH_CLIENT_ID` — the Services ID
- `APPLE_TEAM_ID`
- `APPLE_KEY_ID`
- `APPLE_PRIVATE_KEY` — the `.p8` key contents

Microsoft requires (since 1.4.24):

- `MICROSOFT_OAUTH_CLIENT_ID` — the application (client) ID of the app
  registration
- `MICROSOFT_OAUTH_CLIENT_SECRET` — a client secret of it
- `MICROSOFT_OAUTH_TENANT` — optional, who may sign in: `common` (default:
  any Microsoft account, personal or of any organisation), `organizations`
  (work and school accounts only), `consumers` (personal accounts only), or
  one tenant ID for a single organisation

`OAUTH_REDIRECT_BASE` optionally fixes the public origin used to construct
callback URLs. This is useful behind a reverse proxy. Otherwise the request
origin is used.

Register these exact callback URLs with the providers:

```text
https://<erp-domain>/api/auth/google/callback
https://<erp-domain>/api/auth/apple/callback
https://<erp-domain>/api/auth/microsoft/callback
```

Google should use a dedicated OAuth client for the ERP deployment. For Apple,
register the ERP hostname on the Services ID and configure the return URL.
Apple's client secret is generated at runtime as an ES256 JWT from the key,
team and client identifiers.

The ERP auth cookie is host-only. Do not add a parent-domain `Domain`
attribute, because that would expose the session to sibling applications.

## Security invariants

- OAuth state and nonce are signed and short-lived.
- ID-token signatures and claims are validated against the provider's JWKS.
- Only provider-verified email addresses may create accounts.
- Linking requires an existing authenticated session.
- A provider identity already owned by another user cannot be linked.

The implementation lives in `api/oauth.py`.

## Microsoft

Register an app in Microsoft Entra (App registrations → New registration):

- Supported account types to match `MICROSOFT_OAUTH_TENANT`. For the default
  `common`: "Accounts in any organizational directory and personal Microsoft
  accounts".
- Redirect URI of type **Web**: the callback URL above.
- A client secret (Certificates & secrets). It expires; renew it before it
  does.
- Optional: add the `email` optional claim to the ID token (Token
  configuration). Without it the sign-in name is used as the address.

**Which accounts to admit.** Leave `MICROSOFT_OAUTH_TENANT` at `common`
unless there is a concrete reason not to. Restricting it to work accounts or
to one organisation rarely adds safety and often locks out the people who are
meant to sign in: companies use several e-mail domains and Microsoft tenants
side by side, and some people only have a personal Microsoft account. What
decides who gets in is not the kind of Microsoft account but the ERP user it
is linked to (next paragraph). The app registration's account type and this
setting must agree: an app registered for organisations only makes Microsoft
refuse personal accounts ("You can't sign in here with a personal account")
before the ERP is asked. Changing the app to include personal accounts
requires access token version 2 on it (`api.requestedAccessTokenVersion`).

Signing in with a Microsoft account does not let a user in by itself. As with
the other providers, the account must already be linked to an ERP user, or a
new user is created only under the usual rules (first user, invitation, or
public signup).

Two things differ from Google and Apple, and both are checked by the ERP:

- **Issuer.** With `common`, each token is issued by the user's own tenant.
  The ERP accepts a token only if its issuer is exactly the Microsoft issuer
  of the tenant ID inside the token, and that tenant is admitted by
  `MICROSOFT_OAUTH_TENANT`.
- **E-mail address.** In an organisation's directory the administrator can
  write any address into a user's e-mail field, so that field alone proves
  nothing. The ERP takes it only when its domain equals the domain of the
  user's sign-in name, which the organisation has verified with Microsoft;
  otherwise it uses the sign-in name. A guest of a tenant gets no address and
  cannot register with it. Personal Microsoft accounts use the address
  Microsoft holds for them.

The identity is stored as tenant ID and object ID, so it stays the same if
the app registration is replaced.
