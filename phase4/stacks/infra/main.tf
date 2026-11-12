module "environment" {
  source = "../../modules/environment"

  environment_id  = var.environment_id
  aws_region      = var.aws_region
  aws_account_id  = var.aws_account_id
  git_revision    = var.git_revision
  approval_source = var.approval_source
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = module.environment.contract.tags
  }
}

locals {
  environment = module.environment.contract
  name_prefix = local.environment.name_prefix
  vpc_cidr    = var.environment_id == "demo-a" ? "10.42.0.0/16" : "10.43.0.0/16"
  zones       = { for index, zone in var.availability_zones : tostring(index) => zone }
  planning_allowed = (
    var.planning_only && var.environment_id == "demo-a" &&
    var.aws_account_id == "446517826146" && var.aws_region == "us-east-1" &&
    try(local.environment.planning_ready, false)
  )
  databases = {
    auth    = { database = "oficina_auth", username = "oficina_auth" }
    os      = { database = "oficina_os", username = "oficina_os" }
    billing = { database = "oficina_billing", username = "billing" }
  }
}

resource "terraform_data" "environment_guard" {
  input = local.environment

  lifecycle {
    precondition {
      condition = (
        local.environment.planning_ready &&
        local.environment.tags.Phase == "4" &&
        local.environment.tags.Lifecycle == "ephemeral" &&
        local.environment.tags.Shared == "false"
      )
      error_message = "Infra bloqueada: somente ambiente efemero aprovado, com conta e backend revisados."
    }
  }
}

output "environment_contract" {
  description = "Identidade e backend controlado da stack infra efemera."
  value       = local.environment

  precondition {
    condition     = module.environment.contract.planning_ready
    error_message = "Operacao bloqueada: identidade ou backend invalidos no catalogo."
  }
}
