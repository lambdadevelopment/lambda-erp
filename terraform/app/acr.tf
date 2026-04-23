resource "random_string" "acr_suffix" {
  length  = 6
  upper   = false
  special = false
}

# ACR name must be globally unique, 5-50 lowercase alphanumeric.
resource "azurerm_container_registry" "acr" {
  name                = "lambdaerpacr${random_string.acr_suffix.result}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false # use managed identity for pulls
  tags                = local.tags
}
