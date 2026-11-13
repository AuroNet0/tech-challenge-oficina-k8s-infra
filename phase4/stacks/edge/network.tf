resource "aws_security_group" "edge" {
  for_each    = toset(["vpc-link", "alb", "lambda", "secrets-endpoint"])
  name        = "${local.name_prefix}-${each.key}"
  description = "Edge privada isolada: ${each.key}"
  vpc_id      = var.infra_contract.vpc_id
  depends_on  = [terraform_data.edge_guard]
}

locals {
  network_flows = {
    link_alb       = { source = aws_security_group.edge["vpc-link"].id, target = aws_security_group.edge["alb"].id, port = var.alb_tls == null ? 80 : 443 }
    alb_pods       = { source = aws_security_group.edge["alb"].id, target = var.infra_contract.security_groups.nodes, port = 8443 }
    lambda_db      = { source = aws_security_group.edge["lambda"].id, target = var.infra_contract.security_groups.database, port = 5432 }
    lambda_secrets = { source = aws_security_group.edge["lambda"].id, target = aws_security_group.edge["secrets-endpoint"].id, port = 443 }
  }
}

resource "aws_vpc_security_group_ingress_rule" "flow" {
  for_each                     = local.network_flows
  security_group_id            = each.value.target
  referenced_security_group_id = each.value.source
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = "Edge ${var.environment_id}: ${each.key}"
  depends_on                   = [terraform_data.edge_guard]
}

resource "aws_vpc_security_group_egress_rule" "flow" {
  for_each                     = local.network_flows
  security_group_id            = each.value.source
  referenced_security_group_id = each.value.target
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = "Edge ${var.environment_id}: ${each.key}"
}

resource "aws_apigatewayv2_vpc_link" "edge" {
  name               = "${local.name_prefix}-edge"
  subnet_ids         = local.subnet_ids
  security_group_ids = [aws_security_group.edge["vpc-link"].id]
}

resource "aws_vpc_endpoint" "secrets" {
  vpc_id              = var.infra_contract.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.subnet_ids
  security_group_ids  = [aws_security_group.edge["secrets-endpoint"].id]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = "*", Action = "secretsmanager:GetSecretValue"
      Resource  = values(local.auth_secrets)
      Condition = { ArnEquals = { "aws:PrincipalArn" = "arn:aws:iam::${var.aws_account_id}:role/${local.name_prefix}-auth-lambda" } }
    }]
  })
}

data "aws_lb_listener" "observed" {
  count = var.publish_business && var.alb_listener_arn != null ? 1 : 0
  arn   = var.alb_listener_arn
}

data "aws_lb" "observed" {
  count = var.publish_business && var.alb_listener_arn != null ? 1 : 0
  arn   = data.aws_lb_listener.observed[0].load_balancer_arn
}

resource "terraform_data" "listener_guard" {
  count = var.publish_business ? 1 : 0
  lifecycle {
    precondition {
      condition = try(
        data.aws_lb.observed[0].internal &&
        data.aws_lb.observed[0].load_balancer_type == "application" &&
        data.aws_lb.observed[0].vpc_id == var.infra_contract.vpc_id &&
        data.aws_lb.observed[0].name == "${local.name_prefix}-edge" &&
        data.aws_lb.observed[0].tags.EnvironmentId == var.environment_id &&
        toset(data.aws_lb.observed[0].subnets) == toset(local.subnet_ids) &&
        toset(data.aws_lb.observed[0].security_groups) == toset([aws_security_group.edge["alb"].id]) &&
        data.aws_lb_listener.observed[0].port == (var.alb_tls == null ? 80 : 443) &&
        data.aws_lb_listener.observed[0].protocol == (var.alb_tls == null ? "HTTP" : "HTTPS") &&
        (var.alb_tls == null ? true : data.aws_lb_listener.observed[0].certificate_arn == var.alb_tls.certificate_arn),
        false
      )
      error_message = "Listener observado nao corresponde ao ALB interno, SG, subnets, ownership ou TLS aprovados."
    }
  }
}
