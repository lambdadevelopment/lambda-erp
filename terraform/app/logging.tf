resource "azurerm_log_analytics_workspace" "law" {
  name                = local.law_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

# Route platform logs through Azure Monitor instead of the legacy Data Collector
# API. Keep the existing workspace and categories; app-level duplicates or
# allLogs would collect additional data and can increase ingestion costs.
resource "azurerm_monitor_diagnostic_setting" "container_logs" {
  name                       = "container-logs-to-workspace"
  target_resource_id         = azurerm_container_app_environment.cae.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id

  # Container Apps uses resource-specific tables and does not persist
  # logAnalyticsDestinationType, so leave that optional field unset.
  enabled_log {
    category = "ContainerAppConsoleLogs"
  }

  enabled_log {
    category = "ContainerAppSystemLogs"
  }
}
