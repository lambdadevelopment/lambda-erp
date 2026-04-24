# GitHub Actions deploy pipeline

Two workflows deploy lambda-erp:

- **`.github/workflows/deploy.yml`** — builds the Docker image on every
  push to `master`, pushes it to Azure Container Registry, and rolls a
  new revision onto Azure Container Apps. Fires automatically; the
  hot path for app code changes.
- **`.github/workflows/terraform-apply.yml`** — runs `terraform plan`
  or `terraform apply` in `terraform/app/` against Azure. Manual
  dispatch only. This is where Container App env vars, demo caps,
  and secret rotations happen. Secrets live in GitHub Secrets and
  flow in as `TF_VAR_*` env — nothing on anyone's laptop.

**Setting up the Azure infra itself is covered in
[`terraform/README.md`](../terraform/README.md).** Come back here once
terraform has printed `github_oidc_secrets`.

## What the workflow does

**Triggers:**
- `push` to `master` (the branch name matches `github_branches` default
  in `terraform/app/variables.tf`). Runs are skipped when only `.md`
  files, `docs/`, `.gitignore`, or `LICENSE` changed.
- `workflow_dispatch` — manual "Run workflow" button in the Actions UI.
- **Never** `pull_request_target`. That trigger runs with the base
  branch's secrets on fork PRs, which would let any forker push
  arbitrary images into our ACR. Fork PRs go through the normal
  `pull_request` trigger with no secret access, so the repo is safe to
  open publicly.

**Concurrency guard** prevents two simultaneous merges to `master` from
racing and deploying each other's stale image.

**Steps:**
1. Checkout
2. Azure login via OIDC (no long-lived credentials stored anywhere)
3. `az acr login`
4. Build Docker image tagged with both `<git-sha>` (immutable, the
   thing deployed) and `latest` (convenience for local pulls /
   debugging)
5. Push both tags
6. `az containerapp update --image <sha-tag>` creates a new revision
   pinned to this commit
7. Write a summary to the GitHub Actions run page with the URL +
   active revision name

## How authentication works

No long-lived credentials are stored in GitHub. Instead, the workflow
uses **OpenID Connect federated identity**:

1. GitHub Actions signs a JWT for each workflow run. The token's `sub`
   claim is `repo:<org>/<repo>:ref:refs/heads/<branch>`.
2. The workflow presents that JWT to Azure AD via `azure/login@v2`.
3. Azure AD checks it against the
   `azuread_application_federated_identity_credential` resource in
   `terraform/app/github_actions.tf`. Only tokens matching a known
   `(org, repo, branch)` triple are accepted.
4. Azure issues a short-lived access token scoped to the service
   principal's role assignments (Contributor on the resource group,
   AcrPush on the registry).

This means:
- **Zero GitHub Secrets.** Forks and PRs can never deploy — their JWT
  `sub` claim doesn't match the trust.
- **Rotating credentials is free** — nothing to rotate, every run gets
  a fresh token.
- **Adding a new branch to deploy** = update `github_branches` in
  `terraform.tfvars` and `terraform apply`.

## What you configure in GitHub

### Actions Variables

Settings → Secrets and variables → Actions → **Variables** tab. Add the
six values that `terraform output github_oidc_secrets` prints after the
first app-module apply:

| Name                    | Value                                  |
|-------------------------|----------------------------------------|
| `AZURE_CLIENT_ID`       | Entra app's client_id                  |
| `AZURE_TENANT_ID`       | Azure tenant id                        |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription id                  |
| `AZURE_RESOURCE_GROUP`  | e.g. `lambda-erp-demo-rg`              |
| `AZURE_CONTAINER_APP`   | e.g. `lambda-erp-demo`                 |
| `ACR_LOGIN_SERVER`      | e.g. `lambdaerpacrxxxxxx.azurecr.io`   |

None of these are sensitive — they're just resource coordinates. Storing
them as Variables (not Secrets) means they show up in run logs so
debugging is easier.

### Actions Secrets

Add three secrets (Secrets tab, same page). These are read by
`terraform-apply.yml` as `TF_VAR_*` env vars on every CI-driven apply
and written into the Container App's own secret store. `deploy.yml`
itself doesn't touch them — it just swaps images.

| Secret              | Source                                                   |
|---------------------|----------------------------------------------------------|
| `OPENAI_API_KEY`    | OpenAI dashboard                                         |
| `ANTHROPIC_API_KEY` | Anthropic console                                        |
| `JWT_SECRET_KEY`    | `python -c "import secrets; print(secrets.token_hex(32))"` |

**Do not put these in a local `terraform.tfvars`** once the bootstrap
apply is done — `terraform/README.md` step 5 covers the handoff.
Rotating a key is one GitHub UI edit + a `terraform-apply` run, no
laptop involved.

## First deploy

Assuming `terraform/README.md` steps 1–5 are done:

1. Push any commit to `master` (or use the "Run workflow" button in the
   Actions UI).
2. The workflow builds, pushes `<acr>/lambda-erp:<sha>`, and replaces
   the Microsoft quickstart placeholder that terraform installed.
3. Open the FQDN from `terraform output container_app_fqdn`. First boot
   takes ~3 minutes while the simulator seeds the demo data; you'll see
   progress in the Container App logs.

## Subsequent deploys

Every push to `master` builds + deploys. The workflow tags with the
commit SHA, so every deploy is traceable to an exact commit:

```bash
docker pull lambdaerpacrXXXXXX.azurecr.io/lambda-erp:<sha>
```

reproduces the exact image.

**Rollback:** `az containerapp update --image <older-sha-tag>`. Or, in
terraform, pick a prior revision and set `latest_revision = false`
alongside an explicit revision name in the traffic block.

## Moving to a new repo / org

The Azure side doesn't care what GitHub repo pushes to it — it only
trusts the OIDC subject string. Full step-by-step in
[`terraform/README.md`](../terraform/README.md) under "Moving to a new
repo / org".

## Safety considerations for public repos

- **Never add `pull_request_target`** as a trigger to any deploy
  workflow. Fork PRs with that trigger run with the base branch's
  secrets.
- **Branch protection on `master`**: require PR reviews before merge
  so no one can push straight to the deploy branch.
- **Never commit `.env`** or `terraform.tfvars`. `.gitignore` excludes
  both — only `.env.example` / `*.tfvars.example` templates are tracked.
- **Fork deploys are blocked by OIDC trust scope.** Even if someone
  forks the repo and copies the workflow, their GitHub OIDC `sub` claim
  is `repo:<their-fork-org>/<their-fork-repo>:...`, which doesn't match
  the federated credential. Azure rejects their token. No deploy.

## Files

- `.github/workflows/deploy.yml` — app-image build + roll workflow
- `.github/workflows/terraform-apply.yml` — CI-driven terraform apply
  (plan/apply dropdown on manual dispatch)
- `terraform/app/github_actions.tf` — Entra app + federated credential
  + role assignments
- `terraform/app/variables.tf` — `github_org` / `github_repo` /
  `github_branches`
- `terraform/app/outputs.tf` — the `github_oidc_secrets` output that
  lists everything you need to put into Actions Variables
