output "contract" {
  description = "Contrato local; ready=false bloqueia roots operacionais. Nao consulta AWS."
  value = {
    environment_id = var.environment_id
    aws_account_id = var.aws_account_id
    aws_region     = var.aws_region
    name_prefix    = local.name_prefix
    tags           = local.tags
    ready          = local.ready
    planning_ready = local.planning_ready
    backend = {
      bucket              = local.environment.backend_bucket
      region              = local.environment.aws_region
      allowed_account_ids = [var.aws_account_id]
      encrypt             = true
      use_lockfile        = true
      state_keys          = local.environment.state_keys
    }
  }

  precondition {
    condition     = local.catalog_valid
    error_message = "Catalogo invalido: revise nomes, ownership e mapeamento exclusivo de states."
  }
}
