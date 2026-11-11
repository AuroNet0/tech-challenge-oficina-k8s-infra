module "environment" {
  source = "../../modules/environment"

  environment_id  = var.environment_id
  aws_region      = var.aws_region
  aws_account_id  = var.aws_account_id
  git_revision    = var.git_revision
  approval_source = var.approval_source
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = module.environment.contract.tags
  }
}

locals {
  namespace = module.environment.contract.name_prefix
  labels = {
    "app.kubernetes.io/part-of" = "oficina-phase4"
    "oficina/environment"       = var.environment_id
    "oficina/managed-by"        = "platform"
  }
  metadata = { namespace = local.namespace, labels = local.labels }
  security_context = {
    allowPrivilegeEscalation = false
    readOnlyRootFilesystem   = true
    capabilities             = { drop = ["ALL"] }
  }
  pod_security = {
    runAsNonRoot   = true
    runAsUser      = 10001
    runAsGroup     = 10001
    fsGroup        = 10001
    seccompProfile = { type = "RuntimeDefault" }
  }
}

data "aws_caller_identity" "current" {}

data "aws_eks_cluster" "target" {
  name = var.infra_contract.eks.name
}

resource "terraform_data" "platform_guard" {
  input = { environment = module.environment.contract, cluster = var.infra_contract.eks.arn }
  lifecycle {
    precondition {
      condition = (
        module.environment.contract.ready &&
        var.infra_contract.environment_id == var.environment_id &&
        var.infra_contract.aws_account_id == var.aws_account_id &&
        var.infra_contract.aws_region == var.aws_region &&
        var.infra_contract.name_prefix == local.namespace &&
        var.infra_contract.vpc_cidr == (var.environment_id == "demo-a" ? "10.42.0.0/16" : "10.43.0.0/16") &&
        var.infra_contract.tags.EnvironmentId == var.environment_id &&
        var.infra_contract.tags.Phase == "4" && var.infra_contract.tags.Lifecycle == "ephemeral" &&
        var.infra_contract.tags.Shared == "false" &&
        var.infra_contract.eks.name == local.namespace &&
        var.infra_contract.eks.arn == "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${local.namespace}" &&
        data.aws_caller_identity.current.account_id == var.aws_account_id &&
        data.aws_eks_cluster.target.arn == var.infra_contract.eks.arn &&
        data.aws_eks_cluster.target.endpoint == var.infra_contract.eks.endpoint &&
        data.aws_eks_cluster.target.certificate_authority[0].data == var.infra_contract.eks.certificate_authority_data &&
        data.aws_eks_cluster.target.vpc_config[0].vpc_id == var.infra_contract.vpc_id &&
        alltrue([for subnet in var.infra_contract.private_subnets : contains(data.aws_eks_cluster.target.vpc_config[0].subnet_ids, subnet.id)])
      )
      error_message = "Platform bloqueada: catalogo ou identidade/cluster/VPC/subnets do contrato nao correspondem ao ambiente esperado."
    }
    precondition {
      condition     = var.application_replicas == 0 || (var.jwt != null && var.images.auth != null && var.auth_image_verified && var.auth_migration_completed && var.auth_internal_runtime_approved && var.bootstrap_completed && var.secrets_delivered && var.network_policy_enforced)
      error_message = "Aplicacoes bloqueadas: confirmar runtime Auth produtivo, bootstrap, secrets e enforcement de NetworkPolicy antes de iniciar replicas."
    }
    precondition {
      condition     = !var.auth_migration_enabled || (var.images.auth != null && var.auth_image_verified && var.bootstrap_completed && var.secrets_delivered && var.network_policy_enforced)
      error_message = "Migracao Auth bloqueada: imagem real verificada, bootstrap, CA/Secrets e dataplane sao obrigatorios."
    }
  }
}

output "environment_contract" {
  description = "Identidade controlada da platform; manifests nao contem valores secretos."
  value       = module.environment.contract

  precondition {
    condition     = module.environment.contract.ready
    error_message = "Operacao bloqueada: ambiente desabilitado ou conta/backend nao aprovados no catalogo."
  }
}
