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
  description = "Objeto platform_contract da mesma infra; nunca state legado ou descoberta por tags genericas."
  type = object({
    environment_id         = string
    aws_account_id         = string
    aws_region             = string
    name_prefix            = string
    tags                   = map(string)
    vpc_id                 = string
    vpc_cidr               = string
    node_availability_zone = string
    private_subnets        = map(object({ id = string, availability_zone = string }))
    security_groups        = object({ nodes = string, database = string, control_plane = string })
    eks                    = object({ name = string, arn = string, endpoint = string, certificate_authority_data = string, version = string })
    rds = object({
      address               = string, port = number, master_secret_arn = string, bootstrap_database = string
      application_databases = map(object({ database = string, username = string }))
    })
    ecr = map(object({ arn = string, name = string, url = string }))
  })
  nullable = false

  validation {
    condition = (
      toset(keys(var.infra_contract.ecr)) == toset(["os", "execution", "billing", "auth"]) &&
      alltrue([for service, repository in var.infra_contract.ecr :
        repository.url == "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${var.infra_contract.name_prefix}-${service}" &&
        repository.arn == "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.infra_contract.name_prefix}-${service}"
      ]) &&
      var.infra_contract.rds.port == 5432 &&
      startswith(var.infra_contract.rds.address, "${var.infra_contract.name_prefix}-postgres.") &&
      endswith(var.infra_contract.rds.address, ".${var.aws_region}.rds.amazonaws.com") &&
      startswith(var.infra_contract.rds.master_secret_arn, "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:") &&
      toset(keys(var.infra_contract.rds.application_databases)) == toset(["auth", "os", "billing"]) &&
      alltrue([for database in var.infra_contract.rds.application_databases : can(regex("^[a-z][a-z0-9_]{0,62}$", database.database)) && can(regex("^[a-z][a-z0-9_]{0,62}$", database.username))]) &&
      length(distinct([for database in var.infra_contract.rds.application_databases : database.database])) == 3 &&
      length(distinct([for database in var.infra_contract.rds.application_databases : database.username])) == 3
      && length(var.infra_contract.rds.application_databases.auth.username) <= 54
      && !contains([for database in var.infra_contract.rds.application_databases : database.username], "${var.infra_contract.rds.application_databases.auth.username}_migrator")
    )
    error_message = "Contrato RDS/ECR invalido, bancos/usuarios nao exclusivos ou identificador inseguro."
  }
}

variable "images" {
  description = "Imagens imutaveis por digest; quatro ECR do ambiente e imagens verificadas de MongoDB, RabbitMQ e PostgreSQL 17. Auth null omite Deployment/migracao. Fixtures nao comprovam imagem real."
  type        = object({ os = string, execution = string, billing = string, auth = optional(string), mongodb = string, rabbitmq = string, postgres = string })
  nullable    = false

  validation {
    condition = alltrue([for service, image in var.images : image == null ? service == "auth" : can(regex("^[a-z0-9][a-z0-9./:_-]+@sha256:[0-9a-f]{64}$", image))]) && alltrue([
      for service in ["os", "execution", "billing"] :
      startswith(var.images[service], "${var.aws_account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${var.infra_contract.name_prefix}-${service}@sha256:")
    ])
    error_message = "Todas as imagens exigem digest SHA256; servicos devem usar o ECR exato deste ambiente."
  }

  validation {
    condition = var.images.auth == null ? true : can(regex(
      "^${var.aws_account_id}\\.dkr\\.ecr\\.${var.aws_region}\\.amazonaws\\.com/${var.infra_contract.name_prefix}-auth@sha256:[0-9a-f]{64}$", var.images.auth
    ))
    error_message = "Auth exige digest SHA256 no ECR auth exato do ambiente; nao use tags ou imagens de outro ambiente."
  }
}

variable "jwt" {
  description = "Issuer/audience e JWKS HTTPS reais do Auth Lambda. Null permite somente fundacao/dados, sem renderizar aplicacoes."
  type        = object({ issuer = string, audience = string, jwks_uri = string })
  default     = null

  validation {
    condition = var.jwt == null ? true : alltrue([for url in [var.jwt.issuer, var.jwt.jwks_uri] :
      can(regex("^https://[a-zA-Z0-9.-]+(?::443)?/[^?#@ ]*$", "${trimsuffix(url, "/")}/")) &&
      !can(regex("\\.(invalid|localhost)(/|:|$)", url))
    ]) && can(regex("^[A-Za-z0-9:/_.-]{1,200}$", var.jwt.audience))
    error_message = "Issuer/JWKS devem ser HTTPS sem credenciais/query/fragment; audience deve ser explicita."
  }
}

variable "addon_versions" {
  description = "Versoes exatas verificadas futuramente para a versao EKS: sem consulta de latest no plan."
  type        = object({ pod_identity = string, ebs_csi = string, vpc_cni = string })
  nullable    = false

  validation {
    condition = alltrue([for version in values(var.addon_versions) : can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+-eksbuild\\.[0-9]+$", version))]) && try(
      split(".", var.addon_versions.ebs_csi)[0] == "v1" && tonumber(split(".", var.addon_versions.ebs_csi)[1]) >= 58, false
    )
    error_message = "Fixe versoes completas dos addons. CSI exige v1.58+ para tagging automatico da policy cluster-scoped."
  }
}

variable "application_replicas" {
  description = "Zero por padrao. Uma replica exige imagem Auth verificada, migracao da release e todos os preflight acknowledgements."
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.application_replicas)
    error_message = "Esta demonstracao admite somente zero ou uma replica por servico."
  }
}

variable "auth_internal_runtime_approved" {
  type    = bool
  default = false
}

variable "bootstrap_completed" {
  type    = bool
  default = false
}

variable "secrets_delivered" {
  type    = bool
  default = false
}

variable "network_policy_enforced" {
  type    = bool
  default = false
}

variable "auth_image_verified" {
  description = "Confirmacao externa de digest existente, proveniencia dd10bbd e testes da imagem; formato nao comprova existencia."
  type        = bool
  default     = false
}

variable "auth_migration_completed" {
  description = "Confirmar Job desta release concluido antes de disponibilizar Lambda e Auth interno."
  type        = bool
  default     = false
}

variable "auth_migration_enabled" {
  description = "Somente renderiza o Job controlado, apos imagem verificada, bootstrap, CA e Secrets entregues."
  type        = bool
  default     = false
}

variable "new_relic_chart_version" {
  description = "Versao exata do nri-bundle para o instalador futuro; nao instala Helm nesta root."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.new_relic_chart_version))
    error_message = "Fixe uma versao semver do chart apos revisar compatibilidade e recursos."
  }
}

variable "mercado_pago" {
  description = "Configuracao publica do provedor; desligado ate edge fornecer URLs HTTPS reais. Tokens permanecem em Secret."
  type = object({
    enabled          = optional(bool, false)
    notification_url = optional(string, "")
    success_url      = optional(string, "")
    failure_url      = optional(string, "")
    pending_url      = optional(string, "")
  })
  default  = {}
  nullable = false
  validation {
    condition = !var.mercado_pago.enabled || (
      alltrue([for url in [var.mercado_pago.notification_url, var.mercado_pago.success_url, var.mercado_pago.failure_url, var.mercado_pago.pending_url] : can(regex("^https://[a-zA-Z0-9.-]+/[^@?# ]*$", url)) && !can(regex("\\.invalid(/|$)", url))]) &&
      endswith(var.mercado_pago.notification_url, "/api/v1/webhooks/mercado-pago")
    )
    error_message = "Mercado Pago exige callbacks HTTPS reais e notification_url na rota exata do webhook."
  }
}
