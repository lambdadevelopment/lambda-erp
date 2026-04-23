variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "location" {
  description = "Azure region. northeurope (Dublin) balances EU + US-East latency well."
  type        = string
  default     = "northeurope"
}

variable "project_name" {
  description = "Base name/prefix for resources"
  type        = string
  default     = "lambda-erp-demo"
}

# --------------------------------------------------------------------------
# Container Apps
# --------------------------------------------------------------------------

variable "acr_repo" {
  description = "ACR repository (image name) for the FastAPI + frontend container"
  type        = string
  default     = "lambda-erp"
}

variable "image_tag" {
  description = "Image tag to deploy. GitHub Actions overrides this on each release."
  type        = string
  default     = "latest"
}

variable "cpu" {
  description = "vCPU per replica (ACA Consumption supports 0.25 / 0.5 / 0.75 / 1.0 / ...)"
  type        = number
  default     = 1.0
}

variable "memory" {
  description = "Memory per replica (must match CPU ratio — e.g. 1.0 CPU → 2Gi)"
  type        = string
  default     = "2Gi"
}

variable "target_port" {
  description = "Port that uvicorn/FastAPI listens on inside the container"
  type        = number
  default     = 8000
}

variable "health_probe_path" {
  description = "HTTP path used by startup/liveness probes. /api/auth/setup-status is public and cheap."
  type        = string
  default     = "/api/auth/setup-status"
}

# --------------------------------------------------------------------------
# App secrets — GitHub Actions rewrites the placeholder values after apply.
# --------------------------------------------------------------------------

variable "openai_api_key" {
  description = "OpenAI API key used by the chat tool-calling loop"
  type        = string
  sensitive   = true
  default     = "placeholder-will-be-set-by-github-actions"
}

variable "jwt_secret_key" {
  description = "Secret key used to sign JWT cookies"
  type        = string
  sensitive   = true
  default     = "placeholder-will-be-set-by-github-actions"
}

# --------------------------------------------------------------------------
# Custom domain (optional — set to null to skip)
# --------------------------------------------------------------------------

variable "custom_domain" {
  description = "Custom domain for the demo (e.g. erp-demo.lambda.dev). Set null to skip binding."
  type        = string
  default     = null
}

# --------------------------------------------------------------------------
# GitHub OIDC — lets CI deploy without long-lived secrets
# --------------------------------------------------------------------------

variable "github_org" {
  description = "GitHub organization or username that owns the lambda-erp repo"
  type        = string
  default     = "torusinvestments"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "lambda-erp"
}

variable "github_branches" {
  description = "Branches that may deploy via OIDC federation"
  type        = list(string)
  default     = ["master"]
}
