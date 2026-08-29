# Social login

Lambda ERP supports Google and Apple sign-in alongside email and password.
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
  verified addresses.
- Invitations can be accepted through Google or Apple; the invitation token
  and role are carried through the signed OAuth state.
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
| `GET /api/auth/{provider}/login` | Start a Google or Apple login |
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

`OAUTH_REDIRECT_BASE` optionally fixes the public origin used to construct
callback URLs. This is useful behind a reverse proxy. Otherwise the request
origin is used.

Register these exact callback URLs with the providers:

```text
https://<erp-domain>/api/auth/google/callback
https://<erp-domain>/api/auth/apple/callback
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
