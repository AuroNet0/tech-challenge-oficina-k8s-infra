resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.name_prefix}/cluster"
  retention_in_days = 7

  depends_on = [terraform_data.environment_guard]
}

resource "aws_eks_cluster" "environment" {
  name                      = local.name_prefix
  role_arn                  = aws_iam_role.cluster.arn
  version                   = var.kubernetes_version
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  upgrade_policy {
    support_type = "STANDARD"
  }

  vpc_config {
    subnet_ids              = [for subnet in aws_subnet.private : subnet.id]
    endpoint_private_access = true
    endpoint_public_access  = length(var.eks_public_access_cidrs) > 0
    public_access_cidrs     = length(var.eks_public_access_cidrs) > 0 ? var.eks_public_access_cidrs : null
  }

  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.eks]
}

resource "aws_eks_access_entry" "administrator" {
  cluster_name      = aws_eks_cluster.environment.name
  principal_arn     = var.eks_admin_role_arn
  type              = "STANDARD"
  kubernetes_groups = ["oficina:${var.environment_id}:bootstrap", "oficina:${var.environment_id}:executor"]
}

resource "aws_eks_access_policy_association" "administrator" {
  cluster_name  = aws_eks_cluster.environment.name
  principal_arn = aws_eks_access_entry.administrator.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}

resource "aws_launch_template" "nodes" {
  name = "${local.name_prefix}-nodes"

  vpc_security_group_ids = [aws_security_group.nodes.id]

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 30
      encrypted             = true
      delete_on_termination = true
    }
  }

  dynamic "tag_specifications" {
    for_each = toset(["instance", "volume", "network-interface"])
    content {
      resource_type = tag_specifications.value
      tags          = merge(local.environment.tags, { Name = "${local.name_prefix}-node" })
    }
  }
}

resource "aws_eks_node_group" "environment" {
  cluster_name    = aws_eks_cluster.environment.name
  node_group_name = "${local.name_prefix}-nodes"
  node_role_arn   = aws_iam_role.nodes.arn
  subnet_ids      = [aws_subnet.public["0"].id]
  version         = var.kubernetes_version
  ami_type        = "AL2023_x86_64_STANDARD"
  instance_types  = ["m7i-flex.large"]
  capacity_type   = "ON_DEMAND"

  launch_template {
    id      = aws_launch_template.nodes.id
    version = aws_launch_template.nodes.latest_version
  }

  scaling_config {
    desired_size = var.node_count
    min_size     = var.node_count
    max_size     = var.node_count
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    "phase"          = "4"
    "environment-id" = var.environment_id
  }

  depends_on = [
    aws_iam_role_policy_attachment.nodes,
    aws_iam_role_policy.ecr_pull,
    aws_route.internet,
    aws_route_table_association.public,
    aws_vpc_security_group_ingress_rule.nodes_internal,
    aws_vpc_security_group_ingress_rule.control_plane,
    aws_vpc_security_group_ingress_rule.cluster_api,
    aws_vpc_security_group_egress_rule.nodes_internal,
    aws_vpc_security_group_egress_rule.https,
    aws_vpc_security_group_egress_rule.dns,
  ]
}
