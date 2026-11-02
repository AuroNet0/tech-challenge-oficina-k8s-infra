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

variable "infra_contract" {
  description = "Output platform_contract publico da infra selecionada; nenhum acesso a state remoto."
  type = object({
    environment_id  = string, aws_account_id = string, aws_region = string, name_prefix = string
    tags            = map(string), vpc_id = string
    private_subnets = map(object({ id = string, availability_zone = string }))
    security_groups = object({ nodes = string, database = string })
    rds             = object({ address = string, port = number, application_databases = map(object({ database = string, username = string })) })
  })
  nullable = false
  validation {
    condition = (
      var.infra_contract.rds.port == 5432 &&
      startswith(var.infra_contract.rds.address, "${var.infra_contract.name_prefix}-postgres.") &&
      endswith(var.infra_contract.rds.address, ".${var.aws_region}.rds.amazonaws.com") &&
      var.infra_contract.rds.application_databases.auth.database == "oficina_auth" &&
      var.infra_contract.rds.application_databases.auth.username == "oficina_auth"
    )
    error_message = "Contrato Auth/RDS incompativel com bootstrap aprovado."
  }
}

variable "platform_contract" {
  description = "Output edge_contract da Platform do mesmo ambiente; Services continuam pertencendo a Platform."
  type = object({
    environment_id = string, aws_account_id = string, aws_region = string, namespace = string
    vpc_id         = string, private_subnet_ids = list(string)
    services       = map(object({ name = string, port = number, protocol = string }))
  })
  nullable = false
}

variable "publish_auth" {
  description = "Criar Lambda e suas duas rotas somente apos evidencias de readiness."
  type        = bool
  default     = false
  nullable    = false
}

variable "prepare_alb" {
  description = "Renderizar Ingress privado; nao aplica manifestos nem cria ALB via Terraform."
  type        = bool
  default     = false
  nullable    = false
}

variable "publish_business" {
  description = "Publicar allowlist de negocio somente apos listener/targets verificados."
  type        = bool
  default     = false
  nullable    = false
}

variable "publish_webhook" {
  description = "Opt-in adicional do webhook real Mercado Pago; nao usa JWT, valida assinatura na aplicacao."
  type        = bool
  default     = false
  nullable    = false
}

variable "readiness" {
  description = "Atestacoes futuras com evidencias por release, nunca comprovadas apenas por Terraform/mocks."
  type = object({
    artifact_verified        = optional(bool, false), bootstrap_layer_verified = optional(bool, false)
    secrets_verified         = optional(bool, false), bootstrap_completed = optional(bool, false)
    auth_migration_completed = optional(bool, false), controller_ready = optional(bool, false)
    workloads_ready          = optional(bool, false), network_policy_enforced = optional(bool, false)
    pki_valid_for_operation  = optional(bool, false), jwks_ready = optional(bool, false)
    targets_healthy          = optional(bool, false), listener_verified = optional(bool, false)
    mercado_pago_verified    = optional(bool, false)
  })
  default  = {}
  nullable = false
}

variable "auth_release" {
  description = "Publicacao futura: JAR shaded Java21 versionado em S3 e layer bootstrap imutavel; nunca caminho local ficticio."
  type = object({
    bucket              = string, key = string, version_id = string, sha256_base64 = string, revision = string
    bootstrap_layer_arn = string, bootstrap_revision = string
  })
  default = null
  validation {
    condition = var.auth_release == null ? true : (
      var.auth_release.bucket == "${var.infra_contract.name_prefix}-artifacts" &&
      can(regex("^[0-9a-f]{40}$", var.auth_release.revision)) &&
      var.auth_release.key == "auth/${var.auth_release.revision}/auth.jar" &&
      length(var.auth_release.version_id) > 0 && var.auth_release.version_id != "null" &&
      can(regex("^[A-Za-z0-9+/]{43}=$", var.auth_release.sha256_base64)) &&
      can(regex("^[0-9a-f]{40}$", var.auth_release.bootstrap_revision)) &&
      can(regex("^arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:layer:${var.infra_contract.name_prefix}-auth-bootstrap:[1-9][0-9]*$", var.auth_release.bootstrap_layer_arn))
    )
    error_message = "Artefato/layer exige origem exata do ambiente, revisoes completas, S3 versionId e checksum SHA256 base64."
  }
}

variable "alb_tls" {
  description = "Certificado ACM publico confiavel e nome SAN para TLS API Gateway-ALB. CA tecnica nao serve neste salto."
  type        = object({ certificate_arn = string, server_name = string })
  default     = null
  validation {
    condition = var.alb_tls == null ? true : (
      can(regex("^arn:aws:acm:${var.aws_region}:${var.aws_account_id}:certificate/[0-9a-f-]{36}$", var.alb_tls.certificate_arn)) &&
      can(regex("^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\\.[a-z]{2,}$", var.alb_tls.server_name)) &&
      !endswith(var.alb_tls.server_name, ".amazonaws.com")
    )
    error_message = "TLS exige ARN ACM da conta/regiao e hostname controlado, sem URL ou hostname AWS nao pertencente ao projeto."
  }
}

variable "alb_listener_arn" {
  description = "Listener observado apos reconciliacao do Ingress; null ate existir. Nao usar ARN inventado."
  type        = string
  default     = null
  validation {
    condition = var.alb_listener_arn == null ? true : can(regex(
      "^arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:listener/app/${var.infra_contract.name_prefix}-edge/[0-9a-f]{16}/[0-9a-f]{16}$", var.alb_listener_arn
    ))
    error_message = "Listener deve ser ALB app exato do ambiente, conta e regiao."
  }
}
