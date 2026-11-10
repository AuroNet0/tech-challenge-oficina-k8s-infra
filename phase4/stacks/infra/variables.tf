variable "planning_only" {
  description = "Permite somente gerar plano de demo-a desabilitado; executor deve impedir apply deste plano."
  type        = bool
  default     = false
  nullable    = false
}

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

variable "availability_zones" {
  description = "Duas AZs distintas e previamente verificadas na regiao cadastrada; nenhuma descoberta AWS nesta etapa."
  type        = list(string)
  nullable    = false

  validation {
    condition = length(var.availability_zones) == 2 && length(distinct(var.availability_zones)) == 2 && alltrue([
      for zone in var.availability_zones : can(regex("^${var.aws_region}[a-z]$", zone))
    ])
    error_message = "Forneca exatamente duas AZs distintas da regiao esperada."
  }
}

variable "eks_admin_role_arn" {
  description = "Role IAM existente, aprovada e da conta esperada para administracao deste cluster; nao cria automacao GitHub."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^arn:aws:iam::${var.aws_account_id}:role/[A-Za-z0-9+=,.@_/-]+$", var.eks_admin_role_arn))
    error_message = "Informe ARN de role IAM da conta esperada, nunca usuario ou sessao STS."
  }
}

variable "kubernetes_version" {
  description = "Versao EKS explicita; confirmar suporte, addons e disponibilidade antes do plan autorizado."
  type        = string
  default     = "1.35"
  nullable    = false

  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.kubernetes_version))
    error_message = "Informe versao minor Kubernetes explicita, como 1.35."
  }
}

variable "eks_public_access_cidrs" {
  description = "Lista opcional de IPv4 /32 administrativos; vazia mantem endpoint somente privado. Runner deve ter caminho de rede aprovado."
  type        = list(string)
  default     = []
  nullable    = false

  validation {
    condition = alltrue([
      for cidr in var.eks_public_access_cidrs : can(cidrnetmask(cidr)) && can(regex("/32$", cidr)) && !startswith(cidr, "0.0.0.0/")
    ])
    error_message = "Acesso publico administrativo aceita somente IPv4 /32 explicitos; nunca 0.0.0.0."
  }
}

variable "node_count" {
  description = "Quantidade fixa de nodes m7i-flex.large. Padrao experimental de um node, sem autoscaler; aumento exige revisao de capacidade/custo."
  type        = number
  default     = 1
  nullable    = false

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 3 && floor(var.node_count) == var.node_count
    error_message = "Use de um a tres nodes, apos revisao de capacidade."
  }
}

variable "postgres_version" {
  description = "Minor PostgreSQL 17 explicito, alinhado aos composes OS/Billing; validar Auth e disponibilidade regional."
  type        = string
  default     = "17.10"
  nullable    = false

  validation {
    condition     = can(regex("^17\\.[0-9]+$", var.postgres_version))
    error_message = "Use uma versao minor explicita do PostgreSQL 17."
  }
}

variable "db_instance_class" {
  description = "Classe RDS de demonstracao; db.t4g.micro e hipotese de capacidade, nao garantia para tres servicos."
  type        = string
  default     = "db.t4g.micro"
  nullable    = false

  validation {
    condition     = contains(["db.t4g.micro", "db.t4g.small", "db.t4g.medium"], var.db_instance_class)
    error_message = "Use uma classe RDS t4g de demonstracao revisada."
  }
}

variable "db_allocated_storage" {
  description = "Armazenamento gp3 em GiB; sem autoscaling para limitar custo."
  type        = number
  default     = 20
  nullable    = false

  validation {
    condition     = var.db_allocated_storage >= 20 && var.db_allocated_storage <= 100 && floor(var.db_allocated_storage) == var.db_allocated_storage
    error_message = "Use de 20 a 100 GiB inteiros para demonstracao."
  }
}
