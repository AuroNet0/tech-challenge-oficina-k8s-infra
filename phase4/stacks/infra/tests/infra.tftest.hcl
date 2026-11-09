mock_provider "aws" {
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-eks"
    }
  }
  mock_resource "aws_eks_cluster" {
    defaults = {
      arn                   = "arn:aws:eks:us-east-1:123456789012:cluster/mock"
      certificate_authority = [{ data = "bW9jaw==" }]
      identity              = [{ oidc = [{ issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/MOCK" }] }]
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mock"
        kms_key_id    = "mock"
        secret_status = "active"
      }]
    }
  }
}

variables {
  environment_id     = "demo-a"
  aws_region         = "us-east-1"
  aws_account_id     = "123456789012"
  git_revision       = "6d55d2bc06ecf84b16325a1b9c288138a7602e1c"
  availability_zones = ["us-east-1a", "us-east-1b"]
  eks_admin_role_arn = "arn:aws:iam::123456789012:role/test-administrator"
}

run "disabled_catalog_blocks_infra" {
  command = plan

  expect_failures = [terraform_data.environment_guard, output.environment_contract]
}

run "isolated_ephemeral_infrastructure" {
  command = plan

  override_module {
    target = module.environment
    outputs = {
      contract = {
        ready       = true
        name_prefix = "tc-oficina-p4-demo-a"
        tags = {
          Project     = "oficina", Owner = "tech-challenge-oficina", EnvironmentId = "demo-a"
          Phase       = "4", Lifecycle = "ephemeral", Shared = "false", ManagedBy = "terraform"
          GitRevision = "6d55d2bc06ecf84b16325a1b9c288138a7602e1c"
        }
      }
    }
  }

  assert {
    condition = (
      aws_vpc.environment.cidr_block == "10.42.0.0/16" &&
      length(aws_subnet.public) == 2 && length(aws_subnet.private) == 2 &&
      alltrue([for subnet in aws_subnet.private : !subnet.map_public_ip_on_launch]) &&
      alltrue([for subnet in aws_subnet.public : subnet.map_public_ip_on_launch]) &&
      length(aws_route_table.private.route) == 0 &&
      !contains(keys(aws_subnet.public["0"].tags), "kubernetes.io/role/elb")
    )
    error_message = "Rede deve separar nodes publicos sem descoberta de ALB publico das subnets privadas."
  }

  assert {
    condition = (
      aws_eks_cluster.environment.name == "tc-oficina-p4-demo-a" &&
      aws_eks_cluster.environment.version == "1.35" &&
      aws_eks_cluster.environment.vpc_config[0].endpoint_private_access &&
      !aws_eks_cluster.environment.vpc_config[0].endpoint_public_access &&
      !aws_eks_cluster.environment.access_config[0].bootstrap_cluster_creator_admin_permissions &&
      aws_eks_cluster.environment.access_config[0].authentication_mode == "API" &&
      aws_eks_node_group.environment.scaling_config[0].desired_size == 1 &&
      length(aws_eks_node_group.environment.instance_types) == 1 &&
      contains(aws_eks_node_group.environment.instance_types, "m7i-flex.large")
    )
    error_message = "EKS deve ter acesso explicito e um node experimental, sem endpoint publico por padrao."
  }

  assert {
    condition = (
      aws_launch_template.nodes.metadata_options[0].http_tokens == "required" &&
      aws_launch_template.nodes.metadata_options[0].http_put_response_hop_limit == 1 &&
      alltrue([for specification in aws_launch_template.nodes.tag_specifications : specification.tags.EnvironmentId == "demo-a" && specification.tags.Lifecycle == "ephemeral"])
    )
    error_message = "Nodes e volumes devem carregar ownership e exigir IMDSv2 limitado."
  }

  assert {
    condition = (
      toset(keys(aws_ecr_repository.services)) == toset(["os", "execution", "billing", "auth"]) &&
      alltrue([for service, repository in aws_ecr_repository.services : repository.name == "tc-oficina-p4-demo-a-${service}" && repository.image_tag_mutability == "IMMUTABLE" && !repository.force_delete])
    )
    error_message = "ECR deve ser exclusivo por ambiente/servico, imutavel e exigir limpeza controlada."
  }

  assert {
    condition = (
      !aws_db_instance.environment.publicly_accessible && !aws_db_instance.environment.multi_az &&
      aws_db_instance.environment.storage_encrypted && aws_db_instance.environment.manage_master_user_password &&
      aws_db_instance.environment.skip_final_snapshot && !aws_db_instance.environment.deletion_protection &&
      aws_db_instance.environment.backup_retention_period == 0 &&
      aws_db_instance.environment.engine_version == "17.10" &&
      aws_db_instance.environment.db_name == "oficina_bootstrap" &&
      aws_db_parameter_group.postgres.family == "postgres17" &&
      !output.platform_contract.rds.application_databases_ready &&
      length(distinct([for database in output.platform_contract.rds.application_databases : database.database])) == 3 &&
      length(distinct([for database in output.platform_contract.rds.application_databases : database.username])) == 3
    )
    error_message = "RDS deve ser privado/efemero com credencial gerenciada e tres bancos ainda pendentes."
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.postgres.from_port == 5432 &&
      aws_vpc_security_group_ingress_rule.postgres.to_port == 5432 &&
      aws_vpc_security_group_ingress_rule.postgres.cidr_ipv4 == null &&
      aws_vpc_security_group_egress_rule.https.to_port == 443 &&
      aws_vpc_security_group_egress_rule.https.ip_protocol == "tcp" &&
      output.edge_contract.environment_id == "demo-a"
    )
    error_message = "Postgres deve aceitar somente SG autorizado e internet somente por TLS."
  }
}

run "second_environment_names_and_network" {
  command = plan

  variables {
    environment_id          = "demo-b"
    eks_public_access_cidrs = ["198.51.100.10/32"]
  }

  override_module {
    target = module.environment
    outputs = {
      contract = {
        ready       = true
        name_prefix = "tc-oficina-p4-demo-b"
        tags = {
          Project     = "oficina", Owner = "tech-challenge-oficina", EnvironmentId = "demo-b"
          Phase       = "4", Lifecycle = "ephemeral", Shared = "false", ManagedBy = "terraform"
          GitRevision = "6d55d2bc06ecf84b16325a1b9c288138a7602e1c"
        }
      }
    }
  }

  assert {
    condition = (
      aws_vpc.environment.cidr_block == "10.43.0.0/16" &&
      aws_eks_cluster.environment.name == "tc-oficina-p4-demo-b" &&
      aws_db_instance.environment.identifier == "tc-oficina-p4-demo-b-postgres" &&
      aws_eks_cluster.environment.vpc_config[0].endpoint_public_access &&
      contains(aws_eks_cluster.environment.vpc_config[0].public_access_cidrs, "198.51.100.10/32") &&
      alltrue([for service, repository in aws_ecr_repository.services : repository.name == "tc-oficina-p4-demo-b-${service}"])
    )
    error_message = "demo-b nao pode colidir com nomes e rede de demo-a."
  }
}

run "reject_public_api_open_to_internet" {
  command = plan
  variables {
    eks_public_access_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.eks_public_access_cidrs, terraform_data.environment_guard, output.environment_contract]
}

run "reject_foreign_administrator" {
  command = plan
  variables {
    eks_admin_role_arn = "arn:aws:iam::210987654321:role/foreign"
  }
  expect_failures = [var.eks_admin_role_arn, terraform_data.environment_guard, output.environment_contract]
}

run "reject_foreign_availability_zone" {
  command = plan
  variables {
    availability_zones = ["us-east-1a", "sa-east-1a"]
  }
  expect_failures = [var.availability_zones, terraform_data.environment_guard, output.environment_contract]
}

run "reject_shared_resource_contract" {
  command = plan

  override_module {
    target = module.environment
    outputs = {
      contract = {
        ready       = true
        name_prefix = "tc-oficina-p4-demo-a"
        tags        = { Phase = "4", Lifecycle = "ephemeral", Shared = "true" }
      }
    }
  }

  expect_failures = [terraform_data.environment_guard]
}
