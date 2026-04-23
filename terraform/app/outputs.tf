output "resource_group_name" {
  value = azurerm_resource_group.rg.name
}

output "container_app_name" {
  value = azurerm_container_app.app.name
}

output "container_app_fqdn" {
  description = "Default Azure-assigned FQDN. Open this URL to visit the demo."
  value       = azurerm_container_app.app.ingress[0].fqdn
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "acr_name" {
  value = azurerm_container_registry.acr.name
}

output "aca_pull_identity_client_id" {
  value = azurerm_user_assigned_identity.aca_pull.client_id
}

# Values the GitHub Actions workflow needs as repo secrets / vars.
output "github_oidc_secrets" {
  description = "Set these as GitHub Actions repository secrets for the deploy workflow."
  value = {
    AZURE_CLIENT_ID       = azuread_application.github_actions.client_id
    AZURE_TENANT_ID       = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID = data.azurerm_subscription.current.subscription_id
    AZURE_RESOURCE_GROUP  = azurerm_resource_group.rg.name
    AZURE_CONTAINER_APP   = azurerm_container_app.app.name
    ACR_LOGIN_SERVER      = azurerm_container_registry.acr.login_server
    ACR_REPO              = var.acr_repo
  }
}
