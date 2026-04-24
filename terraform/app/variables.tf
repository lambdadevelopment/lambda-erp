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
# Demo spend guardrails
#
# The public demo is backed by our OpenAI + Anthropic keys, so visitor
# traffic costs us real money. Two sliding 1-hour windows inside the app
# cap spend: a global bucket across all visitors, and a per-IP bucket.
# Defaults target ~$50/day ($50 / 24h ≈ $2.08/hr global, with per-IP set
# to 25% of global so one actor can't monopolize the budget).
# --------------------------------------------------------------------------

variable "demo_global_hourly_usd" {
  description = "Global demo spend cap per hour in USD (sliding window). Default = $50/day ÷ 24h."
  type        = number
  default     = 2.0833
}

variable "demo_per_ip_hourly_usd" {
  description = "Per-IP demo spend cap per hour in USD (sliding window)."
  type        = number
  default     = 0.5208
}

variable "demo_max_completion_tokens" {
  description = "Cap on `max_completion_tokens` for each LLM call made by a public_manager (demo) session. Lower = tighter upper bound on per-turn cost."
  type        = number
  default     = 1024
}

variable "demo_max_message_chars" {
  description = "Reject chat messages from public_manager sessions longer than this many characters. Guards against a pasted wall of text exhausting the hourly budget in a single call."
  type        = number
  default     = 300
}

variable "demo_max_attachment_bytes" {
  description = "Reject chat-attachment uploads from public_manager sessions larger than this many bytes. Defaults to 100 KiB; attachments are base64-expanded into prompt tokens, so even a few MB can blow the hourly budget on a single message."
  type        = number
  default     = 102400
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
