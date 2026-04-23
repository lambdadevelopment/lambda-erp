# Consumption-only Container Apps Environment.
# No VNet integration: SQLite is local to the replica, so there is no private
# database to shield, and skipping the VNet removes NAT Gateway cost (~$65/mo)
# and a lot of config. Ingress is public with Azure-managed TLS.
resource "azurerm_container_app_environment" "cae" {
  name                       = local.cae_name
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
  tags                       = local.tags

  lifecycle {
    ignore_changes = [
      infrastructure_resource_group_name,
      workload_profile,
    ]
  }
}
