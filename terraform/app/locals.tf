locals {
  rg_name  = "${var.project_name}-rg"
  law_name = "law-${var.project_name}"
  cae_name = "cae-${var.project_name}"
  app_name = var.project_name
  uai_name = "uai-${var.project_name}-pull"

  tags = {
    project   = var.project_name
    terraform = "true"
  }
}
