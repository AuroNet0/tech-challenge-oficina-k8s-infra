output "platform_contract" {
  description = "Contrato explicito infra -> platform; nao contem passwords ou tokens. Bancos/usuarios sao alvos de bootstrap futuro, nao criados pelo RDS."
  value = {
    environment_id         = var.environment_id
    aws_account_id         = var.aws_account_id
    aws_region             = var.aws_region
    name_prefix            = local.name_prefix
    tags                   = local.environment.tags
    vpc_id                 = aws_vpc.environment.id
    vpc_cidr               = local.vpc_cidr
    public_subnets         = { for key, subnet in aws_subnet.public : key => { id = subnet.id, availability_zone = subnet.availability_zone } }
    private_subnets        = { for key, subnet in aws_subnet.private : key => { id = subnet.id, availability_zone = subnet.availability_zone } }
    node_availability_zone = var.availability_zones[0]
    security_groups = {
      nodes         = aws_security_group.nodes.id
      database      = aws_security_group.database.id
      control_plane = aws_eks_cluster.environment.vpc_config[0].cluster_security_group_id
    }
    eks = {
      name                       = aws_eks_cluster.environment.name
      arn                        = aws_eks_cluster.environment.arn
      endpoint                   = aws_eks_cluster.environment.endpoint
      certificate_authority_data = aws_eks_cluster.environment.certificate_authority[0].data
      oidc_issuer                = aws_eks_cluster.environment.identity[0].oidc[0].issuer
      version                    = aws_eks_cluster.environment.version
      node_group_name            = aws_eks_node_group.environment.node_group_name
      node_role_arn              = aws_iam_role.nodes.arn
    }
    rds = {
      identifier                  = aws_db_instance.environment.identifier
      arn                         = aws_db_instance.environment.arn
      address                     = aws_db_instance.environment.address
      port                        = aws_db_instance.environment.port
      engine_version              = aws_db_instance.environment.engine_version
      master_secret_arn           = aws_db_instance.environment.master_user_secret[0].secret_arn
      bootstrap_database          = aws_db_instance.environment.db_name
      application_databases       = local.databases
      application_databases_ready = false
      sslmode                     = "verify-full"
    }
    ecr = { for service, repository in aws_ecr_repository.services : service => {
      arn = repository.arn, name = repository.name, url = repository.repository_url
    } }
  }

  depends_on = [aws_eks_access_policy_association.administrator]
}

output "edge_contract" {
  description = "Rede privada infra -> edge. Listener ALB, SGs de ALB/VPC Link e Auth Lambda dependerao das etapas seguintes."
  value = {
    environment_id     = var.environment_id
    aws_account_id     = var.aws_account_id
    aws_region         = var.aws_region
    name_prefix        = local.name_prefix
    tags               = local.environment.tags
    vpc_id             = aws_vpc.environment.id
    private_subnet_ids = [for subnet in aws_subnet.private : subnet.id]
  }
}
