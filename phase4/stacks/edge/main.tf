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
  name_prefix      = module.environment.contract.name_prefix
  subnet_ids       = sort([for subnet in var.infra_contract.private_subnets : subnet.id])
  auth_enabled     = var.publish_auth && var.auth_release != null
  business_enabled = var.publish_business && var.alb_listener_arn != null
  labels = {
    "app.kubernetes.io/part-of" = "oficina-phase4"
    "oficina/environment"       = var.environment_id
    "oficina/managed-by"        = "edge"
  }
  auth_secrets = { for purpose in ["auth-postgres", "auth-signing"] : purpose =>
    "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:/oficina/phase4/${var.environment_id}/${purpose}-??????"
  }
}

data "aws_caller_identity" "current" {}

data "aws_subnet" "private" {
  for_each = var.infra_contract.private_subnets
  id       = each.value.id
}

resource "terraform_data" "edge_guard" {
  input = var.environment_id
  lifecycle {
    precondition {
      condition = (
        module.environment.contract.planning_ready &&
        data.aws_caller_identity.current.account_id == var.aws_account_id &&
        var.infra_contract.environment_id == var.environment_id &&
        var.infra_contract.aws_account_id == var.aws_account_id &&
        var.infra_contract.aws_region == var.aws_region &&
        var.infra_contract.name_prefix == local.name_prefix &&
        var.infra_contract.tags.EnvironmentId == var.environment_id &&
        var.infra_contract.tags.Phase == "4" && var.infra_contract.tags.Shared == "false" &&
        var.infra_contract.tags.Lifecycle == "ephemeral" &&
        var.platform_contract.environment_id == var.environment_id &&
        var.platform_contract.aws_account_id == var.aws_account_id &&
        var.platform_contract.aws_region == var.aws_region &&
        var.platform_contract.namespace == local.name_prefix &&
        var.platform_contract.vpc_id == var.infra_contract.vpc_id &&
        toset(var.platform_contract.private_subnet_ids) == toset(local.subnet_ids) &&
        toset(keys(var.infra_contract.private_subnets)) == toset(["0", "1"]) &&
        alltrue([for key, subnet in data.aws_subnet.private :
          subnet.vpc_id == var.infra_contract.vpc_id &&
          subnet.cidr_block == cidrsubnet(var.environment_id == "demo-a" ? "10.42.0.0/16" : "10.43.0.0/16", 8, tonumber(key) + 10) &&
          subnet.availability_zone == var.infra_contract.private_subnets[key].availability_zone &&
          !subnet.map_public_ip_on_launch
        ]) &&
        length(distinct([for subnet in var.infra_contract.private_subnets : subnet.availability_zone])) >= 2 &&
        alltrue([for service in ["os", "execution", "billing"] :
          var.platform_contract.services[service].name == "${service}-service" &&
          var.platform_contract.services[service].port == 8443 &&
          var.platform_contract.services[service].protocol == "HTTPS"
        ])
      )
      error_message = "Edge bloqueada: catalogo, conta, regiao, ambiente, VPC, subnets ou Services divergentes."
    }
    precondition {
      condition     = !var.publish_auth || var.auth_release != null
      error_message = "Lambda bloqueada: auth_release obrigatorio para publicar Auth."
    }
    precondition {
      condition = !var.publish_business || (
        var.publish_auth && var.prepare_alb && var.alb_listener_arn != null
      )
      error_message = "Rotas de negocio bloqueadas: publicar Auth, preparar ALB e informar listener obrigatorios."
    }
    precondition {
      condition     = !var.publish_webhook || var.publish_business
      error_message = "Webhook bloqueado: publicacao de negocio obrigatoria."
    }
  }
}

output "environment_contract" {
  description = "Identidade e backend isolados da Edge; sem payloads secretos."
  value       = module.environment.contract

  precondition {
    condition     = module.environment.contract.planning_ready
    error_message = "Operacao bloqueada: ambiente desabilitado ou conta/backend nao aprovados no catalogo."
  }
}
