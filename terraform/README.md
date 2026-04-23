# lambda-erp Terraform

Single-replica Azure Container Apps deployment of the FastAPI + static
frontend, sized for a public demo (~100 concurrent viewers).

```
terraform/
├── terraformstate/   # Bootstrap: creates the storage account that holds
│                     #   remote state for every sibling module. Run once.
└── app/              # The demo itself: RG, ACR, Log Analytics, Container
                      #   Apps Environment, single Container App, GitHub
                      #   OIDC, optional custom domain.
```

## Why a single replica?

`lambda_erp/database.py` uses SQLite on container-local disk and `api/chat.py`
keeps per-process state (`session_tasks`, `demo_typing_waiters`). Running
`min = max = 1` is intentional — horizontal scaling would split that state
across pods. 1 vCPU / 2 GiB handles ~100 idle WebSockets and a handful of
active chat turns for demo traffic; the first real bottleneck is OpenAI
rate/cost, not the VM.

## First-time bootstrap

The `terraformstate` module provisions the SA that both modules then use as
a remote backend. Classic chicken-and-egg, so the first apply is local-state:

```bash
cd terraform/terraformstate
# 1. Comment out the `backend "azurerm"` block in main.tf
terraform init
terraform apply -var subscription_id=<SUB_ID>

# 2. Uncomment the backend block
terraform init -migrate-state   # answer "yes"
```

Then apply the app module:

```bash
cd ../app
terraform init
terraform apply -var subscription_id=<SUB_ID>
```

The outputs print the default FQDN (open that URL to hit the demo), the
GitHub Actions OIDC secret set, and — if `custom_domain` is set — the CNAME
+ cert-bind command.

## Deploy a new image

Terraform provisions the infra once. Ongoing deploys run from GitHub Actions
using the OIDC app that Terraform created. Typical CI workflow:

1. `az login --service-principal --federated-token ${{ env.ACTIONS_ID_TOKEN }} ...`
2. `az acr login --name <ACR_NAME>`
3. `docker build -t $ACR_LOGIN_SERVER/$ACR_REPO:$GITHUB_SHA .`
4. `docker push $ACR_LOGIN_SERVER/$ACR_REPO:$GITHUB_SHA`
5. `az containerapp update -g $AZURE_RESOURCE_GROUP -n $AZURE_CONTAINER_APP --image $ACR_LOGIN_SERVER/$ACR_REPO:$GITHUB_SHA`
6. Secrets (`OPENAI_API_KEY`, `JWT_SECRET_KEY`) are set with
   `az containerapp secret set` from repo secrets. Terraform intentionally
   ignores these so the two systems don't fight.

## Custom domain

Set `-var custom_domain=erp-demo.example.com`. After `apply`, the
`custom_domain_setup` output prints the CNAME target and the
`az containerapp hostname bind` command to run once the DNS record
propagates.

## Rough cost

| Resource                     | Monthly (idle)     |
|------------------------------|--------------------|
| Container App (1 vCPU, 2 GiB, always-on) | ~$30 |
| ACR Basic                    | ~$5                |
| Log Analytics (minimal)      | ~$5                |
| **Total**                    | **~$40**           |

OpenAI usage is billed separately by OpenAI.
