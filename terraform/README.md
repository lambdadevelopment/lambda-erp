# lambda-erp Terraform

Single-replica Azure Container Apps deployment of the FastAPI + static
frontend, sized for a public demo (~100 concurrent viewers).

```
terraform/
├── terraformstate/   # Bootstrap: creates the storage account that holds
│                     #   remote state for every sibling module. Run once
│                     #   per subscription.
└── app/              # The demo itself: RG, ACR, Log Analytics, Container
                      #   Apps Environment, single Container App, GitHub
                      #   OIDC, optional custom domain.
```

## Why a single replica?

`lambda_erp/database.py` uses SQLite on container-local disk and `api/chat.py`
keeps per-process state (`session_tasks`, `demo_typing_waiters`). Running
`min = max = 1` is intentional — horizontal scaling would split that state
across pods. 1 vCPU / 2 GiB handles ~100 idle WebSockets and a handful of
active chat turns for demo traffic; the first real bottleneck is LLM
rate/cost, not the VM.

## Zero-to-running

### 1. Gather inputs

- **Azure subscription ID** — `az account show --query id -o tsv`
- **OpenAI API key** — required; chat orchestration runs on GPT.
- **Anthropic API key** — required; the code-specialist sub-agent that
  generates custom-analytics JS runs on Claude. The demo still boots
  without it but custom-report code generation will fail.
- **JWT secret** — a stable 64-char hex string used to sign login cookies:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
  Generate once, keep stable across revisions (users' cookies survive
  rollouts that way).

You collect these four items once, then paste them into GitHub — they
never need to sit on a laptop during any terraform run:

- **Subscription ID** goes into `terraform.tfvars` in step 3 (also into
  GitHub Actions Variables for CI).
- **OpenAI key, Anthropic key, JWT secret** go directly into **GitHub
  Actions Secrets** in step 6. From that point on, the
  `terraform-apply` workflow reads them as `TF_VAR_*` env vars on
  every CI-driven apply and pushes them into the Container App's
  secret store.

The bootstrap `terraform apply` in step 4 runs with **no real secrets
at all** — the three secret-backed variables all default to a
placeholder string, which terraform writes into the Container App
alongside the Microsoft quickstart image. The first CI-driven
`terraform-apply` run (step 7) is where real secrets first reach
Azure, and they arrive straight from GitHub, never through a shell.

You'll also paste six **GitHub Actions Variables** (step 5), but none
of them require manual discovery — terraform prints all six via the
`github_oidc_secrets` output after step 4. They're resource coordinates
(Entra app client_id, resource group, container app name, etc.), not
credentials.

Why the split: Variables are readable in CI logs (convenient for
debugging), Secrets are redacted. OIDC federation means both workflows
(`deploy.yml`, `terraform-apply.yml`) need zero long-lived credentials
to talk to Azure — every run mints a fresh token.

### 2. Bootstrap the remote state storage account

The `terraformstate` module provisions the SA that both modules then use as
a remote backend. Classic chicken-and-egg — the first apply runs with
local state, then migrates itself to remote state.

```bash
cd terraform/terraformstate
# 1. Comment out the `backend "azurerm"` block in main.tf
terraform init
terraform apply -var subscription_id=<SUB_ID>

# 2. Uncomment the backend block
terraform init -migrate-state   # answer "yes"
```

After this step, state for both modules lives in the `lambdaerptfstate`
storage account under the `lambdaerptfcontainer` blob container. Nothing
else writes to the local filesystem.

### 3. Create `terraform/app/terraform.tfvars`

Gitignored, but **no secrets go here** — API keys live only in GitHub
Secrets (set in step 7). This file just configures non-sensitive knobs:

```hcl
subscription_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# custom_domain = "erp-demo.lambda.dev"   # optional — see below
```

If you're deploying from a fork or a different org, also set:

```hcl
github_org      = "your-org"
github_repo     = "lambda-erp"
github_branches = ["master"]
```

To tune demo spend caps, see **Demo spend guardrails** below.

### 4. Bootstrap apply (one-time, from any laptop)

The very first `terraform apply` needs elevated permissions to create
the Entra app + federated identity + role assignments — the CI service
principal doesn't exist yet (it's what this step creates), so this one
run has to happen from a machine where you can sign in as an owner of
the subscription + Entra tenant. No further applies will run from a
laptop after this.

**No secrets go in on this run.** The three secret-backed variables
(`openai_api_key`, `anthropic_api_key`, `jwt_secret_key`) all default
to the placeholder string `"placeholder-will-be-set-by-github-actions"`;
terraform writes that string into the Container App's secret store,
which is fine because the app image isn't real yet either (the
container is running Microsoft's quickstart image at this point).
The `terraform-apply` workflow overwrites the placeholders with real
values in step 7.

```bash
cd ../app
terraform init
terraform apply
```

**First apply works on a fresh subscription** — the Container App
starts with Microsoft's public quickstart image
(`mcr.microsoft.com/k8se/quickstart:latest`) as a placeholder because
ACR is empty at that point. `ignore_changes` on the container image
field means terraform sets this once at creation and never fights CI's
later `az containerapp update --image <sha>` calls.

Outputs you care about:

- `container_app_fqdn` — `<app-name>.<hash>.<region>.azurecontainerapps.io`,
  the default URL. Opening this now shows the Microsoft quickstart
  page until you deploy the real image.
- `github_oidc_secrets` — the 6 values you need to paste into GitHub
  Actions Variables in step 5.
- `custom_domain_setup` — CNAME + cert-bind command if you set
  `custom_domain`. Null otherwise.

### 5. Configure GitHub Actions Variables

```bash
terraform output github_oidc_secrets
```

Paste each of the six returned values into
`github.com/<org>/<repo>/settings/variables/actions` under the
**Variables** tab, using these exact names:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINER_APP`
- `ACR_LOGIN_SERVER`

See `.github/DEPLOYMENT.md` for the OIDC trust model.

### 6. Configure GitHub Actions Secrets

Under **Secrets** tab (same page), add:

| Secret              | Value                                         |
|---------------------|-----------------------------------------------|
| `OPENAI_API_KEY`    | your OpenAI key                               |
| `ANTHROPIC_API_KEY` | your Anthropic key                            |
| `JWT_SECRET_KEY`    | the 64-hex-char string from step 1            |

These are what the `terraform-apply` workflow reads as `TF_VAR_*` env
vars. They never touch a laptop — you paste them into the GitHub UI
directly.

### 7. Swap the placeholder secrets for real ones

Go to **Actions → Terraform apply → Run workflow**, pick `apply` from
the dropdown, and run. This first CI-driven apply sees the three
secret-backed variables change from `"placeholder-will-be-set-by-github-actions"`
to the real values from step 6 and updates the Container App's secret
store in place. The plan shows `(sensitive value)` for each — actual
values never appear in logs.

### 8. Trigger the first real image deploy

Push any commit to `master` (or run `deploy.yml` via `workflow_dispatch`
in the Actions UI). CI builds the image, pushes
`<acr>/lambda-erp:<sha>` to ACR, and calls `az containerapp update
--image <sha>`. A new revision replaces the Microsoft quickstart
placeholder, and the FQDN from step 4 now serves the real app — with
the real secrets from step 7 already in place.

## Ongoing updates

From this point on, **no secret ever sits on a laptop**:

| Change you want                              | How to do it                                                              |
|----------------------------------------------|---------------------------------------------------------------------------|
| New app code (Python/React)                  | Push to `master`. `deploy.yml` builds, pushes, rolls the revision.        |
| New infra, env var, or demo cap              | Edit `terraform/app/*.tf`, push, then run `terraform-apply` workflow.     |
| Rotate `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Update the GitHub Secret, then run `terraform-apply` with `action=apply`. |
| Rotate `JWT_SECRET_KEY`                      | Same as above. All existing login cookies will become invalid.            |

The `terraform-apply` workflow has a `plan | apply` dropdown on manual
dispatch — always `plan` first on risky changes so you see the diff
before applying.

## Secrets: who owns what

- **Container App secrets** (`openai-api-key`, `anthropic-api-key`,
  `jwt-secret-key`) live in **GitHub Secrets** and are applied to
  Azure by the `terraform-apply` workflow, which passes them as
  `TF_VAR_*` env vars. Terraform then writes them into the Container
  App's own secret store. No copy on disk anywhere else.
- **OIDC federation** means both workflows (`deploy.yml` and
  `terraform-apply.yml`) have no long-lived Azure credentials. Every
  run mints a fresh token.
- **State storage** in Azure blob has the post-apply values
  (terraform state always does). The blob container is private; only
  principals with Storage Blob Data Owner can read it.
- `JWT_SECRET_KEY` is stored as a GitHub Secret rather than
  auto-generated because Container Apps has no writable volume across
  revisions, so the local-dev file-based fallback in
  `api/auth.py:_resolve_jwt_secret()` wouldn't survive rollouts.

## Demo spend guardrails

`LAMBDA_ERP_DEMO_*` env vars are wired onto the Container App with
defaults targeting ~$240/day total LLM spend ($10/hr global). Tune via
`terraform.tfvars`:

```hcl
demo_global_hourly_usd     = 10.0     # $240/day cap
demo_per_ip_hourly_usd     = 0.5208   # legacy absolute cap; hard-clamped to ≤25% of global
demo_max_completion_tokens = 1024
demo_max_message_chars     = 300
demo_max_attachment_bytes  = 102400   # 100 KiB
```

All six are surfaced in the admin UI under `/admin/settings` with live
spend and a per-window breakdown.

## Custom domain

Set `custom_domain = "erp-demo.example.com"` in `terraform.tfvars`. After
`terraform apply`, the `custom_domain_setup` output prints:

- the CNAME target (point your DNS at it)
- the `az containerapp hostname bind` command to run once the CNAME
  propagates (Azure-managed TLS is bound out-of-band — the provider
  can't reference the cert by id).

## Rough cost

| Resource                                 | Monthly (idle)     |
|------------------------------------------|--------------------|
| Container App (1 vCPU, 2 GiB, always-on) | ~$30               |
| ACR Basic                                | ~$5                |
| Log Analytics (minimal)                  | ~$5                |
| **Total infra**                          | **~$40**           |

LLM usage is billed separately by OpenAI/Anthropic. The demo cap above
targets ~$50/day ceiling for visitor traffic, so the worst-case combined
monthly bill is roughly $40 infra + $1500 LLM if the demo is slammed
24/7.

## Moving to a new repo / org

Azure doesn't care what GitHub repo pushes to it — it only trusts the
OIDC `sub` claim. To hand the deploy off:

1. Update `github_org` / `github_repo` in `terraform.tfvars`.
2. `terraform apply` — the federated credential subject gets rewritten
   in place. Same SPN, same role assignments, same ACR, same Container
   App.
3. Copy `.github/workflows/deploy.yml` to the new repo.
4. Re-run `terraform output github_oidc_secrets` and paste into the new
   repo's Actions Variables.
5. Push to `master` in the new repo → deploy.

No new subscription, no new service principal, no re-provisioning. The
terraform state keeps tracking the same Azure resources; the git repo it
lives beside can move freely.
