module "environment" {
  source = "../../modules/environment"

  environment_id = var.environment_id
  aws_region     = var.aws_region
  aws_account_id = var.aws_account_id
  git_revision   = var.git_revision
}

output "environment_contract" {
  description = "Contrato da stack platform; sem recursos nesta etapa."
  value       = module.environment.contract

  precondition {
    condition     = module.environment.contract.ready
    error_message = "Operacao bloqueada: ambiente desabilitado ou conta/backend nao aprovados no catalogo."
  }
}
