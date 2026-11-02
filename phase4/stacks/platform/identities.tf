variable "alb_controller_identity_enabled" {
  type    = bool
  default = false
}

variable "alb_identity_inputs" {
  description = "SG Edge e certificado ACM observados; nao cria ALB. Null bloqueia instalacao da identidade."
  type        = object({ security_group_id = string, certificate_arn = string })
  default     = null
  validation {
    condition = var.alb_identity_inputs == null ? true : (
      can(regex("^sg-[0-9a-f]{17}$", var.alb_identity_inputs.security_group_id)) &&
      can(regex("^arn:aws:acm:${var.aws_region}:${var.aws_account_id}:certificate/[0-9a-f-]{36}$", var.alb_identity_inputs.certificate_arn))
    )
    error_message = "SG e certificado devem pertencer a conta/regiao e ser observados antes de instalar identidade ALB."
  }
}

variable "workload_permissions_boundaries" {
  description = "Boundaries persistentes aprovadas; automacao exige ARNs antes de criar roles efemeras."
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([for name, arn in var.workload_permissions_boundaries :
      contains(["ebs-csi", "load-balancer-controller"], name) && arn == "arn:aws:iam::${var.aws_account_id}:policy/tc-oficina-p4-${var.environment_id}-${name}-boundary"
    ])
    error_message = "Boundary deve ser exata do workload, conta e ambiente."
  }
}

module "alb_policy" {
  count                 = var.alb_controller_identity_enabled && var.alb_identity_inputs != null ? 1 : 0
  source                = "../../modules/alb-controller-policy"
  environment_id        = var.environment_id
  aws_account_id        = var.aws_account_id
  aws_region            = var.aws_region
  cluster_name          = local.namespace
  private_subnet_ids    = [for subnet in var.infra_contract.private_subnets : subnet.id]
  alb_security_group_id = var.alb_identity_inputs.security_group_id
  certificate_arn       = var.alb_identity_inputs.certificate_arn
}

data "aws_security_group" "alb" {
  count = length(module.alb_policy)
  id    = var.alb_identity_inputs.security_group_id
}

resource "terraform_data" "alb_identity_guard" {
  lifecycle {
    precondition {
      condition = !var.alb_controller_identity_enabled || try(
        var.alb_identity_inputs != null && data.aws_security_group.alb[0].vpc_id == var.infra_contract.vpc_id &&
        data.aws_security_group.alb[0].name == "${local.namespace}-alb" && data.aws_security_group.alb[0].tags.EnvironmentId == var.environment_id,
        false
      )
      error_message = "Identidade ALB bloqueada: SG Edge observado e ownership/VPC exatos obrigatorios."
    }
  }
}

resource "aws_iam_role" "alb_controller" {
  count                = length(module.alb_policy)
  name                 = "${local.namespace}-load-balancer-controller"
  permissions_boundary = lookup(var.workload_permissions_boundaries, "load-balancer-controller", null)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" }, Action = ["sts:AssumeRole", "sts:TagSession"]
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-arn"            = var.infra_contract.eks.arn
        "aws:RequestTag/kubernetes-namespace"       = "kube-system"
        "aws:RequestTag/kubernetes-service-account" = "aws-load-balancer-controller"
      } }
    }]
  })
  depends_on = [terraform_data.platform_guard, terraform_data.alb_identity_guard]
}

resource "aws_iam_role_policy" "alb_controller" {
  count  = length(module.alb_policy)
  name   = "${local.namespace}-private-alb"
  role   = aws_iam_role.alb_controller[0].id
  policy = module.alb_policy[0].json
  lifecycle {
    precondition {
      condition     = length(module.alb_policy[0].json) <= 10240
      error_message = "Policy inline ALB excede quota IAM da role; revisar antes de instalar."
    }
  }
}

resource "aws_eks_pod_identity_association" "alb_controller" {
  count           = length(module.alb_policy)
  cluster_name    = var.infra_contract.eks.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.alb_controller[0].arn
  depends_on      = [aws_eks_addon.pod_identity, aws_iam_role_policy.alb_controller]
}

output "alb_identity_contract" {
  value = {
    declared               = true, enabled = var.alb_controller_identity_enabled
    role_arn               = try(aws_iam_role.alb_controller[0].arn, null)
    policy_json            = try(module.alb_policy[0].json, null)
    chart_version          = "1.14.0", controller_version = "v2.14.0", installed = false
    upstream_policy_source = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.14.0/docs/install/iam_policy.json"
    helm_values_overlay    = { enableShield = false, enableWaf = false, enableWafv2 = false, enableBackendSecurityGroup = false }
  }
}
