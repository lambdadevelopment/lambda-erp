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

Gitignored — holds the secrets that become Container App secrets on
apply. Minimum content:

```hcl
subscription_id   = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
openai_api_key    = "sk-..."
anthropic_api_key = "sk-ant-..."
jwt_secret_key    = "<64 hex chars from step 1>"

# custom_domain = "erp-demo.lambda.dev"   # optional — see below
```

If you're deploying from a fork or a different org, also set:

```hcl
github_org      = "your-org"
github_repo     = "lambda-erp"
github_branches = ["master"]
```

### 4. Apply the app module

```bash
cd ../app
terraform init
terraform apply
```

**First apply works on a fresh subscription** — the Container App starts
with Microsoft's public quickstart image (`mcr.microsoft.com/k8se/quickstart:latest`)
as a placeholder because ACR is empty at that point. `ignore_changes` on
the container image field means terraform sets this once at creation and
never fights CI's later `az containerapp update --image <sha>` calls.

Outputs you care about:

- `container_app_fqdn` — `<app-name>.<hash>.<region>.azurecontainerapps.io`,
  the default URL. Opening this now shows the Microsoft quickstart page
  until you deploy the real image.
- `github_oidc_secrets` — the 6 values you need to paste into GitHub
  Actions Variables in step 5.
- `custom_domain_setup` — CNAME + cert-bind command if you set
  `custom_domain`. Null otherwise.

### 5. Configure GitHub Actions Variables

```bash
terraform output github_oidc_secrets
```

Copy the 6 values into `github.com/<org>/<repo>/settings/variables/actions`
as **repository Variables** (not Secrets — they're resource coordinates,
not credentials). See `.github/DEPLOYMENT.md` for the table and the OIDC
trust model.

### 6. Trigger the first real deploy

Push any commit to `master` (or trigger `deploy.yml` via
`workflow_dispatch` in the Actions UI). CI builds the image, pushes
`<acr>/lambda-erp:<sha>` to ACR, and calls `az containerapp update
--image <sha>`. A new revision replaces the Microsoft quickstart
placeholder, and the FQDN from step 4 now serves the real app.

## Secrets: who owns what

- **Container App secrets** (`openai-api-key`, `anthropic-api-key`,
  `jwt-secret-key`) are set by **terraform** from your local
  `terraform.tfvars`. The `ignore_changes = [secret]` lifecycle rule is
  defence-in-depth — it means an out-of-band `az containerapp secret
  set` call (e.g. from an incident-response runbook) wouldn't be reverted
  on the next `terraform apply`. Current CI does not rotate them.
- **OIDC federation** means CI has no long-lived Azure credentials.
  There are **zero GitHub Secrets** required for `deploy.yml`.
- `JWT_SECRET_KEY` is supplied via terraform rather than auto-generated
  because Container Apps has no writable volume across revisions, so the
  local-dev file-based fallback in `api/auth.py:_resolve_jwt_secret()`
  wouldn't survive rollouts.

## Demo spend guardrails

`LAMBDA_ERP_DEMO_*` env vars are wired onto the Container App with
conservative defaults targeting ~$50/day total LLM spend. Tune via
`terraform.tfvars`:

```hcl
demo_global_hourly_usd     = 2.0833   # $50 / 24h
demo_per_ip_hourly_usd     = 0.5208   # 25% of global — hard-clamped
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
