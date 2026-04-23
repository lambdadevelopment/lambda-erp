terraform {
  required_version = ">= 1.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.33.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "azurerm" {
    resource_group_name  = "lambda-erp-tfstate"
    storage_account_name = "lambdaerptfstate"
    container_name       = "lambdaerptfcontainer"
    key                  = "lambda-erp/app.tfstate"
  }
}
