# Custom domain binding is conditional — set var.custom_domain to null to skip.
#
# Azure-managed certificates cannot be referenced by id via the azurerm
# provider (path segment mismatch), so the certificate bind step is done
# out-of-band with `az containerapp hostname bind` after the CNAME resolves.
resource "azurerm_container_app_custom_domain" "app" {
  count            = var.custom_domain != null ? 1 : 0
  name             = trimsuffix(var.custom_domain, ".")
  container_app_id = azurerm_container_app.app.id

  lifecycle {
    ignore_changes = [
      certificate_binding_type,
      container_app_environment_certificate_id,
    ]
  }

  depends_on = [azurerm_container_app.app]
}

output "custom_domain_setup" {
  description = "DNS + cert-bind steps for the custom domain"
  value = var.custom_domain == null ? null : {
    cname_host   = var.custom_domain
    cname_value  = azurerm_container_app.app.ingress[0].fqdn
    verification = azurerm_container_app_environment.cae.custom_domain_verification_id
    bind_command = "az containerapp hostname bind -g ${azurerm_resource_group.rg.name} -n ${azurerm_container_app.app.name} --hostname ${var.custom_domain} --environment ${azurerm_container_app_environment.cae.name}"
  }
}
