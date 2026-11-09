output "jwt_contract" {
  description = "Valores exatos para var.jwt da Platform; URL existente nao comprova disponibilidade JWKS."
  value = {
    issuer   = aws_apigatewayv2_api.edge.api_endpoint
    audience = local.name_prefix
    jwks_uri = "${aws_apigatewayv2_api.edge.api_endpoint}/.well-known/jwks.json"
  }
}

output "route_allowlist" {
  value = { auth = local.auth_routes, business = local.business_routes, optional_webhook = local.webhook_routes }
}

output "manifests" {
  description = "List JSON publico para aplicacao futura autorizada; nao executa kubectl. Vazio ate preflight."
  value       = jsonencode({ apiVersion = "v1", kind = "List", items = local.ingress_objects })
}

output "edge_contract" {
  value = {
    environment_id             = var.environment_id
    api_id                     = aws_apigatewayv2_api.edge.id
    api_endpoint               = aws_apigatewayv2_api.edge.api_endpoint
    vpc_link_id                = aws_apigatewayv2_vpc_link.edge.id
    alb_security_group_id      = aws_security_group.edge["alb"].id
    alb_name                   = "${local.name_prefix}-edge"
    alb_listener_arn           = var.alb_listener_arn
    public_auth_enabled        = var.publish_auth
    public_business_enabled    = var.publish_business
    public_webhook_enabled     = var.publish_webhook
    controller                 = { chart_version = "1.14.0", version = "v2.14.0", owner = "platform", installed_by_edge = false }
    public_signing_secret_name = "/oficina/phase4/${var.environment_id}/auth-signing"
    deployment_ready           = false
  }
}

output "lambda_bootstrap_contract" {
  description = "Contrato de empacotamento pendente; nenhum valor privado ou pacote publicado presumido."
  value = {
    runtime                   = "java21"
    handler                   = "br.com.fiap.oficina.auth.handler.AuthHandler::handleRequest"
    source_revision           = "dd10bbd3a31d2148ce111a996b908a48379bc3ba"
    artifact_bucket           = "${local.name_prefix}-artifacts"
    artifact_key              = "auth/<full-source-revision>/auth.jar"
    required_files            = ["/opt/bin/auth-bootstrap", "/opt/java/lib/auth-bootstrap.jar", "/opt/auth-home/.postgresql/root.crt"]
    database_secret           = "/oficina/phase4/${var.environment_id}/auth-postgres"
    signing_secret            = "/oficina/phase4/${var.environment_id}/auth-signing"
    db_password_transport     = "Secrets Manager -> bootstrap -> environment do processo Java; nunca Terraform"
    signing_key_transport     = "Secrets Manager -> SDK Auth; nunca Platform"
    bootstrap_layer_published = false
  }
}
