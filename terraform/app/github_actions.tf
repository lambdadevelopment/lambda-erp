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
