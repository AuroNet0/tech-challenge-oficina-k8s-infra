resource "aws_security_group" "nodes" {
  name        = "${local.name_prefix}-nodes"
  description = "Nodes e pods privados; sem ingresso de internet ou SSH"
  vpc_id      = aws_vpc.environment.id
  tags        = { Name = "${local.name_prefix}-nodes" }
}

resource "aws_security_group" "database" {
  name        = "${local.name_prefix}-postgres"
  description = "PostgreSQL somente dos nodes deste ambiente"
  vpc_id      = aws_vpc.environment.id
  tags        = { Name = "${local.name_prefix}-postgres" }
}

resource "aws_vpc_security_group_ingress_rule" "nodes_internal" {
  security_group_id            = aws_security_group.nodes.id
  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "-1"
  description                  = "Comunicacao entre nodes e pods do ambiente"
}

resource "aws_vpc_security_group_ingress_rule" "control_plane" {
  for_each = toset(["443", "9443", "10250"])

  security_group_id            = aws_security_group.nodes.id
  referenced_security_group_id = aws_eks_cluster.environment.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)
  description                  = "Control plane para kubelet e webhooks privados"
}

resource "aws_vpc_security_group_ingress_rule" "cluster_api" {
  security_group_id            = aws_eks_cluster.environment.vpc_config[0].cluster_security_group_id
  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Nodes para API privada EKS"
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Bancos privados para pods e bootstrap SQL autorizado"
}

resource "aws_vpc_security_group_egress_rule" "nodes_internal" {
  security_group_id            = aws_security_group.nodes.id
  referenced_security_group_id = aws_security_group.nodes.id
  ip_protocol                  = "-1"
  description                  = "Comunicacao interna; NetworkPolicies ficam em platform"
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.nodes.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "EKS, ECR, S3, STS e telemetria por TLS sem NAT"
}

resource "aws_vpc_security_group_egress_rule" "dns" {
  for_each = toset(["tcp", "udp"])

  security_group_id = aws_security_group.nodes.id
  cidr_ipv4         = "${cidrhost(local.vpc_cidr, 2)}/32"
  ip_protocol       = each.value
  from_port         = 53
  to_port           = 53
  description       = "DNS do resolver da VPC"
}

resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.nodes.id
  referenced_security_group_id = aws_security_group.database.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL privado do ambiente"
}
