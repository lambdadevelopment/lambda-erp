# GitHub Actions deploy pipeline

`.github/workflows/deploy.yml` builds the Docker image on every push to
`master`, pushes it to Azure Container Registry, and rolls a new revision
onto Azure Container Apps. This README covers what the workflow does, how
it authenticates, what you need to configure in a new repo, and how to
verify it's wired up correctly.

## What the workflow does

**Triggers:**
- `push` to `master` (the branch name matches `github_branches` default in
  `terraform/app/variables.tf`). Runs are skipped when only `.md` files,
  `docs/`, `.gitignore`, or `LICENSE` changed.
- `workflow_dispatch` — manual "Run workflow" button in the Actions UI.
- **Never** `pull_request_target`. That trigger runs with the base branch's
  secrets on fork PRs, which would let any forker push arbitrary images
  into our ACR. Fork PRs go through the normal `pull_request` trigger with
  no secret access, so the repo is safe to open publicly.

**Concurrency guard** prevents two simultaneous merges to `master` from
racing and deploying each other's stale image.

**Steps:**
1. Checkout
2. Azure login via OIDC (no long-lived credentials stored anywhere)
3. `az acr login`
4. Build Docker image tagged with both `<git-sha>` (immutable, the thing
   deployed) and `latest` (convenience for local pulls / debugging)
5. Push both tags
6. `az containerapp update --image <sha-tag>` creates a new revision
   pinned to this commit
7. Write a summary to the GitHub Actions run page with the URL + active
   revision name

## How authentication works

No long-lived credentials are stored in GitHub. Instead, the workflow uses
**OpenID Connect federated identity**:

1. GitHub Actions signs a JWT for each workflow run. The token's `sub`
   claim is `repo:<org>/<repo>:ref:refs/heads/<branch>`.
2. The workflow presents that JWT to Azure AD via `azure/login@v2`.
3. Azure AD checks it against the `azuread_application_federated_identity_credential`
   resource in `terraform/app/github_actions.tf`. Only tokens matching a
   known `(org, repo, branch)` triple are accepted.
4. Azure issues a short-lived access token scoped to the service
   principal's role assignments (Contributor on the resource group,
   AcrPush on the registry).

This means:
- **Zero secrets in GitHub.** Forks and PRs can never deploy — their JWT
  `sub` claim doesn't match the trust.
- **Rotating credentials is free** — nothing to rotate, every run gets a
  fresh token.
- **Adding a new branch to deploy** = update `github_branches` in
  `terraform.tfvars` and `terraform apply`.

## What you need to configure in a new repo

### Actions Variables

Settings → Secrets and variables → Actions → **Variables** tab. Add the
six values that `terraform output github_oidc_secrets` prints:

| Name | Value |
|---|---|
| `AZURE_CLIENT_ID` | Entra app's client_id |
| `AZURE_TENANT_ID` | Azure tenant id |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription id |
| `AZURE_RESOURCE_GROUP` | e.g. `lambda-erp-demo-rg` |
| `AZURE_CONTAINER_APP` | e.g. `lambda-erp-demo` |
| `ACR_LOGIN_SERVER` | e.g. `lambdaerpacrxxxxxx.azurecr.io` |

None of these are sensitive — they're just resource coordinates. Storing
them as Variables (not Secrets) means they show up in the run logs so
debugging is easier.

### Actions Secrets

**None required for the deploy workflow.** The Container App's own env
vars (`OPENAI_API_KEY`, `JWT_SECRET_KEY`, etc.) are managed by Terraform
directly on the app — the workflow only swaps the image, it never touches
secrets.

## First-deploy checklist

1. In `terraform.tfvars`, set `github_org`, `github_repo`, and
   `github_branches` for the new repo.
2. `cd terraform/app && terraform apply`. This updates the federated
   credential in Azure AD to trust the new repo.
3. Run `terraform output github_oidc_secrets` → copy the six values into
   the new repo's Actions Variables (above).
4. Push `.github/workflows/deploy.yml` to `master`. The workflow fires
   automatically, builds, pushes, and rolls out. Watch the run summary
   for the deployed URL.

## Subsequent deploys

Every push to `master` builds + deploys. The workflow tags with the commit
SHA, so every deploy is traceable to an exact commit; `docker pull
lambdaerpacr....azurecr.io/lambda-erp:<sha>` reproduces the exact image.

Rollback = `az containerapp update --image <older-sha-tag>` or set
`latest_revision = false` + pick a prior revision in Terraform.

## Moving to a new repo / org

The Azure side doesn't care what GitHub repo pushes to it — it only trusts
the OIDC subject string. To move the code to a new repo:

1. Update `github_org` / `github_repo` in `terraform.tfvars`.
2. `terraform apply` — the federated credential subject gets rewritten in
   place. Same SPN, same role assignments, same ACR, same Container App.
3. Copy `.github/workflows/deploy.yml` to the new repo.
4. Configure the six Actions Variables in the new repo (same values).
5. Push to `master` in the new repo → deploy.

No new Azure subscription, no new service principal, no re-provisioning
of ACR or Container Apps. The Terraform state remains the source of
truth for Azure infra; it happens to live wherever you're running
Terraform from, not in the GitHub repo.

## Known gaps

- **`ANTHROPIC_API_KEY` isn't wired in `terraform/app/container_app.tf`.**
  The Container App has `OPENAI_API_KEY` and `JWT_SECRET_KEY` declared
  as secret env vars, but not Anthropic. Until that's added, the deployed
  instance can't use the code-specialist sub-agent (custom analytics code
  generation). Fix: add a matching `env {}` block and `secret {}` entry
  plus a `var.anthropic_api_key` variable.
- **`JWT_SECRET_KEY` is supplied via Terraform** (`var.jwt_secret_key`)
  rather than auto-generated-and-persisted like in local dev. That's
  correct for Container Apps — there's no reliable writable `/data`
  volume across revisions, so the file-based fallback from
  `api/auth.py:_resolve_jwt_secret()` wouldn't survive rollouts. Generate
  a proper secret once (`python -c "import secrets; print(secrets.token_hex(32))"`),
  store in tfvars, `terraform apply`. It stays stable across revisions.

## Safety considerations for public repos

- **Never add `pull_request_target`** as a trigger to any deploy
  workflow. Fork PRs with that trigger run with the base branch's
  secrets.
- **Branch protection on `master`**: require PR reviews before merge so
  no one can push straight to the deploy branch.
- **Never commit `.env`**. `.gitignore` excludes `.env` and `.env.*` —
  only `.env.example` (with placeholder values) is tracked.
- **Fork deploys are blocked by OIDC trust scope.** Even if someone
  forks the repo and copies the workflow, their GitHub OIDC `sub` claim
  is `repo:<their-fork-org>/<their-fork-repo>:...`, which doesn't match
  the federated credential. Azure rejects their token. No deploy.

## Files

- `.github/workflows/deploy.yml` — the actual workflow
- `terraform/app/github_actions.tf` — Entra app + federated credential +
  role assignments
- `terraform/app/variables.tf` — `github_org` / `github_repo` /
  `github_branches`
- `terraform/app/outputs.tf` — the `github_oidc_secrets` output that
  lists everything you need to put into Actions Variables
