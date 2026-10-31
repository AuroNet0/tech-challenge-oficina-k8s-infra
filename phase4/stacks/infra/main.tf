module "environment" {
  source = "../../modules/environment"

  environment_id = var.environment_id
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
  git_revision   = var.git_revision
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = module.environment.contract.tags
  }
}

output "environment_contract" {
  description = "Contrato da stack infra; sem recursos nesta etapa."
  value       = module.environment.contract

  precondition {
    condition     = module.environment.contract.ready
    error_message = "Operacao bloqueada: ambiente desabilitado ou conta/backend nao aprovados no catalogo."
  }
}
