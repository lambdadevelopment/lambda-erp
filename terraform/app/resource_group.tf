resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = var.location
  tags     = local.tags
}

data "azurerm_subscription" "current" {}
data "azurerm_client_config" "current" {}
