variable "environment_id" {
  description = "Identificador cadastrado no catalogo."
  type        = string
  nullable    = false
}

variable "aws_region" {
  description = "Regiao cadastrada."
  type        = string
  nullable    = false
}

variable "aws_account_id" {
  description = "Conta cadastrada e verificada futuramente via STS."
  type        = string
  nullable    = false
}

variable "git_revision" {
  description = "SHA Git completo."
  type        = string
  nullable    = false
}
