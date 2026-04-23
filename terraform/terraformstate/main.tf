#################################################################################
# terraformstate/main.tf — Azure storage for Terraform remote state (lambda-erp)
#
# Bootstrap flow (run once, when the storage account does not yet exist):
#   1. Comment out the `backend "azurerm"` block below.
#   2. `terraform init && terraform apply`
#   3. Uncomment the backend block.
#   4. `terraform init -migrate-state` (moves local state into the SA).
#
# Every sibling module (app/) references this SA via its own backend block.
#################################################################################

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.33.0"
    }
  }

  backend "azurerm" {
    resource_group_name  = "lambda-erp-tfstate"
    storage_account_name = "lambdaerptfstate"
    container_name       = "lambdaerptfcontainer"
    # Key must match the project sub-path — one per module.
    key = "lambda-erp/terraformstate.tfstate"
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "location" {
  description = "Azure region for the tfstate storage. Co-located with the app in northeurope."
  type        = string
  default     = "northeurope"
}

resource "azurerm_resource_group" "tfstate" {
  name     = "lambda-erp-tfstate"
  location = var.location
}

resource "azurerm_storage_account" "tfstate" {
  name                     = "lambdaerptfstate"
  resource_group_name      = azurerm_resource_group.tfstate.name
  location                 = azurerm_resource_group.tfstate.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  blob_properties {
    versioning_enabled = true
  }

  tags = {
    project   = "lambda-erp"
    purpose   = "terraform-state"
    terraform = "true"
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "lambdaerptfcontainer"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}

output "backend_config" {
  description = "Copy this into backend blocks of sibling modules."
  value = {
    resource_group_name  = azurerm_resource_group.tfstate.name
    storage_account_name = azurerm_storage_account.tfstate.name
    container_name       = azurerm_storage_container.tfstate.name
  }
}
