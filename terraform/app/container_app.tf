# Single-replica Container App.
#
# min_replicas = max_replicas = 1 is deliberate. lambda-erp uses SQLite on
# local container disk and keeps in-memory state (chat session tasks, demo
# typing waiters) inside the process. Horizontal scaling would split those
# across pods. 1 CPU / 2 GiB easily handles ~100 idle WebSockets plus a
# handful of active chat turns for a demo.
#
# Scale-to-zero is off (min_replicas = 1) so first-time visitors don't wait
# ~15s on a cold start — demo UX matters more than the ~$30/mo idle cost.
resource "azurerm_container_app" "app" {
  name                         = local.app_name
  resource_group_name          = azurerm_resource_group.rg.name
  container_app_environment_id = azurerm_container_app_environment.cae.id
  revision_mode                = "Single"

  ingress {
    external_enabled = true
    target_port      = var.target_port
    transport        = "auto" # enables http2 / websocket upgrade

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aca_pull.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.aca_pull.id
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "api"
      image  = var.initial_bootstrap_image
      cpu    = var.cpu
      memory = var.memory

      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
      }

      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }

      env {
        name  = "ANTHROPIC_CODE_MODEL"
        value = var.anthropic_code_model
      }

      env {
        name        = "JWT_SECRET_KEY"
        secret_name = "jwt-secret-key"
      }

      env {
        name  = "PORT"
        value = tostring(var.target_port)
      }

      # Public demo is opt-in via the admin UI (`/admin/settings` →
      # "Enable Public Access") *after* registering as admin. That
      # keeps the first visitor to a fresh deploy on the
      # register-as-admin path; only once an admin clicks the toggle
      # does the shared public_manager + "Enter Live Demo" button
      # appear. The admin endpoint handles both the user creation and
      # the chat-replay record seeding — same end state as setting
      # this env var to "1" at boot.
      env {
        name  = "LAMBDA_ERP_ENABLE_PUBLIC_DEMO"
        value = "0"
      }

      # Demo spend guardrails — see variables.tf for rationale.
      env {
        name  = "LAMBDA_ERP_DEMO_GLOBAL_HOURLY_USD"
        value = tostring(var.demo_global_hourly_usd)
      }

      env {
        name  = "LAMBDA_ERP_DEMO_PER_IP_HOURLY_USD"
        value = tostring(var.demo_per_ip_hourly_usd)
      }

      env {
        name  = "LAMBDA_ERP_DEMO_MAX_COMPLETION_TOKENS"
        value = tostring(var.demo_max_completion_tokens)
      }

      env {
        name  = "LAMBDA_ERP_DEMO_MAX_MESSAGE_CHARS"
        value = tostring(var.demo_max_message_chars)
      }

      env {
        name  = "LAMBDA_ERP_DEMO_MAX_ATTACHMENT_BYTES"
        value = tostring(var.demo_max_attachment_bytes)
      }

      startup_probe {
        transport               = "HTTP"
        path                    = var.health_probe_path
        port                    = var.target_port
        failure_count_threshold = 10
        initial_delay           = 20
        interval_seconds        = 10
        timeout                 = 5
      }

      liveness_probe {
        transport               = "HTTP"
        path                    = var.health_probe_path
        port                    = var.target_port
        failure_count_threshold = 3
        initial_delay           = 30
        interval_seconds        = 30
        timeout                 = 5
      }
    }
  }

  secret {
    name  = "openai-api-key"
    value = var.openai_api_key
  }

  secret {
    name  = "anthropic-api-key"
    value = var.anthropic_api_key
  }

  secret {
    name  = "jwt-secret-key"
    value = var.jwt_secret_key
  }

  lifecycle {
    # terraform owns the `secret` blocks (values come from TF_VAR_* env
    # populated by GitHub Secrets in the terraform-apply workflow); a
    # fresh apply is the supported path for rotation.
    #
    # The container image is managed separately by deploy.yml via
    # `az containerapp update --image <sha>`, so ignore drift there to
    # avoid fighting CI.
    ignore_changes = [
      template[0].container[0].image,
      workload_profile_name,
    ]
  }

  tags = local.tags

  depends_on = [azurerm_role_assignment.acr_pull]
}
