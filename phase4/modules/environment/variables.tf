variable "environment_id" {
  description = "Identificador cadastrado no catalogo revisado."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,11}$", var.environment_id)) && contains(keys(local.catalog.environments), var.environment_id)
    error_message = "Ambiente desconhecido ou identificador invalido."
  }
}

variable "aws_region" {
  description = "Regiao solicitada, obrigatoriamente igual ao catalogo."
  type        = string
  nullable    = false

  validation {
    condition     = var.aws_region == local.environment.aws_region
    error_message = "Regiao diferente da regiao cadastrada."
  }
}

variable "aws_account_id" {
  description = "Conta esperada; a identidade real devera ser verificada antes do init remoto."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "A conta AWS deve conter exatamente 12 digitos."
  }
}

variable "git_revision" {
  description = "SHA Git completo da revisao auditada."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.git_revision))
    error_message = "Informe um SHA Git completo de 40 caracteres hexadecimais minusculos."
  }
}
