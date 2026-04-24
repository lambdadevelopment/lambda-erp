# GitHub Actions OIDC federated identity — lets CI authenticate to Azure
# without storing long-lived credentials. The workflow runs `az login` with
# the federated token, then `docker push` to ACR and `az containerapp update`
# to roll out a new image tag.

resource "azuread_application" "github_actions" {
  display_name = "gh-${var.github_org}-${var.github_repo}-oidc"

  tags = [
    "github-oidc",
    "automation",
    local.tags.project,
  ]
}

resource "azuread_service_principal" "github_actions" {
  client_id = azuread_application.github_actions.client_id

  tags = [
    "github-oidc",
    "automation",
    local.tags.project,
  ]
}

resource "azuread_application_federated_identity_credential" "github_actions_branch" {
  for_each = toset(var.github_branches)

  application_id = azuread_application.github_actions.id
  display_name   = "gh-${each.value}"
  description    = "Federated credential for ${var.github_org}/${var.github_repo} @ ${each.value}"

  audiences = ["api://AzureADTokenExchange"]
  issuer    = "https://token.actions.githubusercontent.com"
  subject   = "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${each.value}"
}

# Contributor on the resource group lets CI push container app revisions and
# update secrets. Scoped to the RG only — cannot touch the rest of the sub.
resource "azurerm_role_assignment" "github_rg_contributor" {
  scope                = azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_actions.object_id
}

# AcrPush so `docker push` works.
resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPush"
  principal_id         = azuread_service_principal.github_actions.object_id
}

# Access to the terraform state storage account, so the terraform-apply
# workflow can read/write the remote state blob.
#
# The SA is provisioned by the sibling `terraformstate` module in a
# different resource group (lambda-erp-tfstate). We hardcode the fully-
# qualified resource id rather than using a data source, because a data
# source would need read permission on that RG *before* this role
# assignment exists — the classic bootstrap chicken-and-egg.
#
# Two roles are needed:
#   * Storage Account Contributor — grants `listKeys`, which the azurerm
#     backend uses by default on every `terraform init`.
#   * Reader — grants `Microsoft.Authorization/roleAssignments/read`, so
#     terraform can refresh *these same role assignment resources* on
#     subsequent CI-driven runs. Without this, every CI apply would
#     fail at the refresh step.
locals {
  tfstate_sa_id = "/subscriptions/${data.azurerm_subscription.current.subscription_id}/resourceGroups/lambda-erp-tfstate/providers/Microsoft.Storage/storageAccounts/lambdaerptfstate"
}

resource "azurerm_role_assignment" "github_tfstate_contrib" {
  scope                = local.tfstate_sa_id
  role_definition_name = "Storage Account Contributor"
  principal_id         = azuread_service_principal.github_actions.object_id
}

resource "azurerm_role_assignment" "github_tfstate_reader" {
  scope                = local.tfstate_sa_id
  role_definition_name = "Reader"
  principal_id         = azuread_service_principal.github_actions.object_id
}
