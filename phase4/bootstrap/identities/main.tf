provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "oficina", Owner = local.catalog.environments[var.environment_id].owner, EnvironmentId = var.environment_id, Phase = "4", Lifecycle = "persistent", ManagedBy = "phase4-identities" }
  }
}

locals {
  catalog        = jsondecode(file("${path.module}/../../environments/catalog.json"))
  prefix         = "tc-oficina-p4-${var.environment_id}"
  account        = var.aws_account_id
  region         = var.aws_region
  cluster        = "arn:aws:eks:${local.region}:${local.account}:cluster/${local.prefix}"
  oidc_provider  = "arn:aws:iam::${local.account}:oidc-provider/token.actions.githubusercontent.com"
  repository     = var.subject_format == "immutable" ? "AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946" : "AuroNet0/tech-challenge-oficina-k8s-infra"
  environments   = { for purpose in ["provision", "deploy", "destroy"] : purpose => "phase4-${var.environment_id}-${purpose}" }
  subjects       = { for purpose, environment in local.environments : purpose => "repo:${local.repository}:environment:${environment}" }
  workload_roles = { for name in ["eks-cluster", "eks-nodes", "ebs-csi", "load-balancer-controller", "auth-lambda"] : name => "arn:aws:iam::${local.account}:role/${local.prefix}-${name}" }
  owned          = { StringEquals = { "aws:ResourceTag/EnvironmentId" = var.environment_id } }
  requested      = { StringEquals = { "aws:RequestTag/EnvironmentId" = var.environment_id, "aws:RequestTag/Phase" = "4", "aws:RequestTag/Lifecycle" = "ephemeral" } }
}

data "aws_caller_identity" "current" {}
data "aws_iam_openid_connect_provider" "github" {
  arn = local.oidc_provider
}

resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition = (
        var.installation_approved && var.oidc_claims_verified &&
        local.catalog.schema_version == 1 &&
        local.catalog.environments[var.environment_id].resource_prefix == "tc" &&
        local.catalog.environments[var.environment_id].project_name == "oficina" &&
        data.aws_caller_identity.current.account_id == local.account &&
        local.catalog.environments[var.environment_id].aws_region == local.region &&
        (local.catalog.environments[var.environment_id].aws_account_id == null || local.catalog.environments[var.environment_id].aws_account_id == local.account) &&
        contains(data.aws_iam_openid_connect_provider.github.client_id_list, "sts.amazonaws.com") &&
        trimsuffix(trimprefix(data.aws_iam_openid_connect_provider.github.url, "https://"), "/") == "token.actions.githubusercontent.com"
      )
      error_message = "Bootstrap bloqueado: conta/regiao, OIDC existente, subject e instalacao precisam de aprovacao independente."
    }
    precondition {
      condition = (
        alltrue([for name in keys(var.inventory.secrets) : contains(local.canonical_names, name)]) &&
        alltrue([for arn in var.inventory.api_resources : can(regex("^arn:aws:apigateway:${local.region}::(/(apis|vpclinks)/[a-z0-9]+(/[A-Za-z0-9/_$-]+)?|/tags/arn:aws:apigateway:${local.region}::/(apis|vpclinks)/[a-z0-9]+)$", arn))]) &&
        alltrue([for arn in var.inventory.workload_roles : contains(values(local.workload_roles), arn)]) &&
        alltrue([for arn in var.inventory.ecr : contains(local.ecr_arns, arn)]) &&
        alltrue([for arn in var.inventory.lambda : can(regex("^arn:aws:lambda:${local.region}:${local.account}:(function:${local.prefix}-auth|layer:${local.prefix}-auth-bootstrap:[1-9][0-9]*)$", arn))]) &&
        alltrue([for arn in var.inventory.rds : contains(local.rds_arns, arn)]) &&
        alltrue([for arn in var.inventory.log_groups : contains([for known in local.log_arns : trimsuffix(known, ":*")], arn)]) &&
        alltrue([for arn in var.inventory.eks_children : arn == local.cluster || can(regex("^arn:aws:eks:${local.region}:${local.account}:(nodegroup|addon|access-entry|podidentityassociation)/${local.prefix}/[A-Za-z0-9/_-]+$", arn))]) &&
        alltrue([for arn in var.inventory.ec2 : can(regex("^arn:aws:ec2:${local.region}:${local.account}:(vpc|subnet|security-group|security-group-rule|internet-gateway|route-table|route-table-association|launch-template|vpc-endpoint)/[a-z0-9-]+$", arn))])
      )
      error_message = "Inventario contem recurso fora das finalidades/ARNs autorizados; IDs opacos exigem aprovacao de ownership independente."
    }
  }
}

resource "aws_iam_role" "executor" {
  for_each             = local.environments
  name                 = "${local.prefix}-${each.key}"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Effect = "Allow", Principal = { Federated = local.oidc_provider }, Action = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = local.subjects[each.key]
      } }
      }], var.complete_workflow_delegation_enabled && each.key != "destroy" ? [{
      Sid    = "CompleteWorkflowDelegation"
      Effect = "Allow", Principal = { Federated = local.oidc_provider }, Action = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = concat(
          ["repo:${local.repository}:environment:phase4-${var.environment_id}-bootstrap"],
          each.key == "provision" ? [local.subjects.destroy] : []
        )
      } }
    }] : [])
  })
  depends_on = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "recovery" {
  name = "${local.prefix}-endpoint-recovery"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  depends_on = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "recovery" {
  role = aws_iam_role.recovery.id
  name = "close-selected-endpoint"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "ObserveSelectedCluster", Effect = "Allow", Action = ["eks:DescribeCluster", "eks:DescribeUpdate", "eks:ListUpdates"], Resource = local.cluster },
    { Sid = "ObserveBootstrapAccess", Effect = "Allow", Action = "eks:ListAssociatedAccessPolicies", Resource = "arn:aws:eks:${local.region}:${local.account}:access-entry/${local.prefix}/role/${local.account}/${local.prefix}-provision/*" },
    { Sid = "RevokeTemporaryBootstrap", Effect = "Allow", Action = "eks:DisassociateAccessPolicy", Resource = "arn:aws:eks:${local.region}:${local.account}:access-entry/${local.prefix}/role/${local.account}/${local.prefix}-provision/*", Condition = { ArnEquals = { "eks:principalArn" = "arn:aws:iam::${local.account}:role/${local.prefix}-provision", "eks:policyArn" = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy" } } },
    {
      Sid       = "CloseOnly", Effect = "Allow", Action = "eks:UpdateClusterConfig", Resource = local.cluster
      Condition = { Bool = { "eks:endpointPublicAccess" = "false", "eks:endpointPrivateAccess" = "true" } }
    }
  ] })
}

locals {
  policy_parts = {
    for part in flatten([for purpose, statements in local.effective_policies : [
      for index, chunk in chunklist([for statement in statements : jsonencode(statement)], 8) : {
        key      = "${purpose}-${index}", purpose = purpose
        document = jsonencode({ Version = "2012-10-17", Statement = [for encoded in chunk : jsondecode(encoded)] })
      }
    ]]) : part.key => part
  }
}

resource "aws_iam_policy" "executor" {
  for_each   = local.policy_parts
  name       = "${local.prefix}-${each.key}"
  policy     = each.value.document
  depends_on = [terraform_data.guard]
  lifecycle {
    precondition {
      condition     = length(each.value.document) <= 6144 && length([for part in local.policy_parts : part if part.purpose == each.value.purpose]) <= 10
      error_message = "Policy excede quota IAM de 6144 caracteres ou dez policies por role; revisar particionamento antes de instalar."
    }
  }
}

resource "aws_iam_role_policy_attachment" "executor" {
  for_each   = local.policy_parts
  role       = aws_iam_role.executor[each.value.purpose].name
  policy_arn = aws_iam_policy.executor[each.key].arn
}

output "identity_contract" {
  value = {
    environment_id              = var.environment_id, aws_account_id = local.account, aws_region = local.region
    name_prefix                 = local.prefix
    roles                       = { for purpose, role in aws_iam_role.executor : purpose => role.arn }
    recovery_role_arn           = aws_iam_role.recovery.arn
    github_environments         = local.environments, subjects = local.subjects
    state_key                   = "phase4/bootstrap/identities/${var.environment_id}.tfstate"
    backend_bucket              = var.backend_bucket
    persistent                  = true, recovery_operational = false, endpoint_open_allowed = false
    issuer_automation_delegated = false
    workload_boundaries         = var.inventory.workload_boundaries
  }
}

output "policy_documents" {
  value = merge({ for purpose, statements in local.effective_policies : purpose => jsonencode({ Version = "2012-10-17", Statement = statements }) }, { recovery = aws_iam_role_policy.recovery.policy })
}

output "endpoint_broker_candidate" {
  description = "Nao anexada a nenhuma role. Abertura bloqueada ate broker/lease/recuperacao independente operacionais."
  value = {
    implemented  = true
    open_allowed = false
    cluster_arn  = local.cluster
    policy_json = jsonencode({ Version = "2012-10-17", Statement = [
      { Effect = "Allow", Action = ["eks:DescribeCluster", "eks:DescribeUpdate", "eks:ListUpdates"], Resource = local.cluster },
      { Effect = "Allow", Action = "eks:UpdateClusterConfig", Resource = local.cluster, Condition = { Bool = { "eks:endpointPrivateAccess" = "true" } } }
    ] })
    reason = "Broker valida IP /32 observado, lease duravel e payload fixo. Ativacao exige bootstrap aprovado e prova recente no runtime; este output nao atesta readiness. Nenhuma role GitHub recebe abertura direta."
  }
}
