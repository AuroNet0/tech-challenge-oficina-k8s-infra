variable "workload_boundary_documents" {
  description = "Policies publicas revisadas pelo bootstrap privilegiado, nunca payloads de Secrets. Nao copiar AdministratorAccess. Vazio bloqueia criacao automatizada de roles."
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([for name, document in var.workload_boundary_documents :
      contains(["eks-cluster", "eks-nodes", "ebs-csi", "load-balancer-controller", "auth-lambda"], name) &&
      can(jsondecode(document).Statement) && length(document) <= 6144 &&
      try(alltrue([for statement in jsondecode(document).Statement :
        !can(statement.NotAction) && alltrue([for action in flatten([statement.Action]) :
          !strcontains(action, "*") && !startswith(lower(action), "sts:") &&
          (!startswith(lower(action), "iam:") || action == "iam:CreateServiceLinkedRole")
        ])
      ]), false)
    ])
    error_message = "Boundary exige JSON revisado, tamanho permitido, workload conhecido e nenhuma delegacao IAM/STS administrativa."
  }
}

resource "aws_iam_policy" "workload_boundary" {
  for_each    = var.workload_boundary_documents
  name        = "${local.prefix}-${each.key}-boundary"
  description = "Limite persistente ${each.key} do ambiente ${var.environment_id}; nao remover no destroy efemero"
  policy      = each.value
  depends_on  = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

output "boundary_contract" {
  value = {
    owner                           = "phase4/bootstrap/identities"
    declared                        = { for name, policy in aws_iam_policy.workload_boundary : name => policy.arn }
    required                        = { for name in keys(local.workload_roles) : name => "arn:aws:iam::${local.account}:policy/${local.prefix}-${name}-boundary" }
    creation_permissions_prepared   = local.boundary_ready
    ready_for_aws                   = false
    review_required_before_creation = true
  }
}
