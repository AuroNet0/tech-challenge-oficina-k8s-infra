variable "cluster_bootstrap_access_enabled" {
  description = "Elevacao temporaria e aprovada para instalar CRDs/controllers/RBAC; remover apos bootstrap."
  type        = bool
  default     = false
  validation {
    condition     = !var.cluster_bootstrap_access_enabled
    error_message = "Privilegio administrativo temporario pertence ao broker com lease; nunca persistir no plano Terraform."
  }
}

variable "automation_roles" {
  description = "Roles persistentes exatas do bootstrap Fase4; null nao cria entradas de deploy/destroy."
  type        = object({ provision = string, deploy = string, destroy = string })
  default     = null
  validation {
    condition = var.automation_roles == null ? true : alltrue([for purpose, arn in var.automation_roles :
      arn == "arn:aws:iam::${var.aws_account_id}:role/tc-oficina-p4-${var.environment_id}-${purpose}"
    ]) && var.eks_admin_role_arn == var.automation_roles.provision
    error_message = "Entradas EKS devem usar roles persistentes exatas; administrador deve ser provision do mesmo ambiente."
  }
}

variable "workload_permissions_boundaries" {
  type    = map(string)
  default = {}
  validation {
    condition = alltrue([for name, arn in var.workload_permissions_boundaries :
      contains(["eks-cluster", "eks-nodes"], name) && arn == "arn:aws:iam::${var.aws_account_id}:policy/tc-oficina-p4-${var.environment_id}-${name}-boundary"
    ])
    error_message = "Boundary deve ser exata do workload, conta e ambiente."
  }
}

resource "aws_eks_access_entry" "executor" {
  for_each          = var.automation_roles == null ? {} : { deploy = var.automation_roles.deploy, destroy = var.automation_roles.destroy }
  cluster_name      = aws_eks_cluster.environment.name
  principal_arn     = each.value
  type              = "STANDARD"
  kubernetes_groups = ["oficina:${var.environment_id}:executor"]
}

output "executor_access_contract" {
  value = {
    group                               = "oficina:${var.environment_id}:executor"
    bootstrap_elevation                 = var.cluster_bootstrap_access_enabled
    bootstrap_authorization             = "broker-lease-AmazonEKSClusterAdminPolicy-provision-only"
    bootstrap_elevation_must_be_revoked = true
    token_source                        = "aws eks get-token com sessao OIDC temporaria; nunca token SA persistente"
    roles                               = var.automation_roles
  }
}
