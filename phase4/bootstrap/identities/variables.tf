variable "environment_id" {
  type = string
  validation {
    condition     = contains(["demo-a", "demo-b"], var.environment_id)
    error_message = "Ambiente deve ser demo-a ou demo-b."
  }
}

variable "complete_workflow_delegation_enabled" {
  type    = bool
  default = false
  validation {
    condition     = !var.complete_workflow_delegation_enabled || (var.subject_format == "immutable" && var.environment_id == "demo-a")
    error_message = "Delegacao completa exige subjects immutable e escopo exclusivo demo-a."
  }
}

variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Conta AWS exata obrigatoria."
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "backend_bucket" {
  description = "Bucket persistente aprovado existente; esta root nao cria nem migra backend."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.backend_bucket))
    error_message = "Nome de bucket aprovado obrigatorio."
  }
}

variable "installation_approved" {
  type    = bool
  default = false
}

variable "oidc_claims_verified" {
  description = "Verificacao independente de configuracao OIDC e subject real, sem armazenar token."
  type        = bool
  default     = false
}

variable "subject_format" {
  description = "Repositorio criado em setembro/2026 usa immutable IDs por padrao; customizacao deve ser verificada antes de instalar."
  type        = string
  default     = "immutable"
  validation {
    condition     = contains(["immutable", "classic"], var.subject_format)
    error_message = "Somente formatos GitHub documentados e verificados; custom subjects exigem revisao de codigo."
  }
}

variable "inventory" {
  description = "ARNs publicos completos observados e aprovados. Vazio nao concede mutacao/destruicao de IDs ainda desconhecidos."
  type = object({
    ec2                   = optional(set(string), []), eks_children = optional(set(string), [])
    api_resources         = optional(set(string), []), secrets = optional(map(string), {})
    rds_master_secret_arn = optional(string), rds = optional(set(string), [])
    workload_boundaries   = optional(map(string), {})
    workload_roles        = optional(set(string), []), lambda = optional(set(string), []), ecr = optional(set(string), [])
    log_groups            = optional(set(string), [])
  })
  default = {}
  validation {
    condition = alltrue([for arn in concat(tolist(var.inventory.ec2), tolist(var.inventory.eks_children), tolist(var.inventory.rds), values(var.inventory.secrets)) :
      can(regex("^arn:aws:[a-z0-9-]+:${var.aws_region}:${var.aws_account_id}:[A-Za-z0-9!_./:=+-]+$", arn)) && !strcontains(arn, "*")
      ]) && alltrue([for name, arn in var.inventory.secrets :
      name != "auth-signing" && can(regex("^arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:/oficina/phase4/${var.environment_id}/${name}-[A-Za-z0-9]{6}$", arn))
      ]) && alltrue([for key, arn in var.inventory.workload_boundaries :
      contains(["eks-cluster", "eks-nodes", "ebs-csi", "load-balancer-controller", "auth-lambda"], key) &&
      arn == "arn:aws:iam::${var.aws_account_id}:policy/tc-oficina-p4-${var.environment_id}-${key}-boundary"
      ]) && (var.inventory.rds_master_secret_arn == null ? true : can(regex(
        "^arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:rds!db-[A-Za-z0-9-]+-[A-Za-z0-9]{6}$", var.inventory.rds_master_secret_arn
    )))
    error_message = "Inventario exige ARNs completos da conta/regiao, Secrets canonicos sem auth-signing e boundaries exatas."
  }
}

variable "inventory_approved" {
  description = "Ownership exato revisado externamente; nao inferir autorizacao de IDs opacos por prefixo/tag."
  type        = bool
  default     = false
}

variable "destroy_confirmation" {
  type    = string
  default = ""
}

variable "rotate_secret_names" {
  description = "Aprovacao separada para PutSecretValue de fontes ja inventariadas; vazio por padrao."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for name in var.rotate_secret_names : contains(keys(var.inventory.secrets), name)])
    error_message = "Rotacao exige fonte canonica presente no inventario aprovado."
  }
}
