locals {
  auth_routes = {
    "POST /api/v1/auth/login"    = "auth"
    "GET /.well-known/jwks.json" = "auth"
  }
  business_routes = merge(
    { for route in [
      "POST /api/v1/clientes", "GET /api/v1/clientes", "GET /api/v1/clientes/{id}",
      "POST /api/v1/veiculos", "GET /api/v1/veiculos", "GET /api/v1/veiculos/{id}",
      "POST /api/v1/filiais", "GET /api/v1/filiais", "GET /api/v1/filiais/{id}",
      "PUT /api/v1/clientes/{clienteId}/filiais/{filialId}",
      "PUT /api/v1/veiculos/{veiculoId}/filiais/{filialId}",
      "POST /api/v1/ordens-servico", "GET /api/v1/ordens-servico", "GET /api/v1/ordens-servico/{id}",
      "GET /api/v1/ordens-servico/{id}/historico", "GET /api/v1/ordens-servico/{id}/saga",
      "GET /api/v1/ordens-servico/{id}/operacional", "GET /api/v1/ordens-servico/{id}/veiculo-operacional"
    ] : route => "os" },
    { for route in [
      "GET /api/v1/diagnosticos", "GET /api/v1/diagnosticos/{id}",
      "POST /api/v1/diagnosticos/{id}/inicio", "POST /api/v1/diagnosticos/{id}/itens",
      "PATCH /api/v1/diagnosticos/{id}/observacoes", "POST /api/v1/diagnosticos/{id}/conclusao",
      "GET /api/v1/reparos", "GET /api/v1/reparos/{id}", "POST /api/v1/reparos/{id}/inicio",
      "PATCH /api/v1/reparos/{id}/observacoes", "POST /api/v1/reparos/{id}/conclusao",
      "GET /api/v1/execucoes/fila", "GET /api/v1/execucoes/historico"
    ] : route => "execution" },
    { for route in [
      "GET /api/v1/orcamentos", "GET /api/v1/orcamentos/{id}", "GET /api/v1/orcamentos/{id}/aprovacao",
      "POST /api/v1/orcamentos/{id}/aprovar", "POST /api/v1/orcamentos/{id}/rejeitar",
      "GET /api/v1/pagamentos", "GET /api/v1/pagamentos/{id}", "POST /api/v1/pagamentos/{id}/checkout",
      "GET /api/v1/notificacoes"
    ] : route => "billing" }
  )
  webhook_routes           = { "POST /api/v1/webhooks/mercado-pago" = "billing" }
  selected_business_routes = merge(local.business_routes, var.publish_webhook ? local.webhook_routes : {})
  ingress_route_groups = {
    for route, service in local.selected_business_routes :
    "${service}-${lower(split(" ", route)[0])}" => { route = route, service = service, method = split(" ", route)[0] }...
  }
}

resource "aws_apigatewayv2_api" "edge" {
  name          = "${local.name_prefix}-edge"
  protocol_type = "HTTP"
  depends_on    = [terraform_data.edge_guard]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/oficina/phase4/${var.environment_id}/edge/http-api"
  retention_in_days = 7
  depends_on        = [terraform_data.edge_guard]
}

resource "aws_apigatewayv2_stage" "environment" {
  api_id      = aws_apigatewayv2_api.edge.id
  name        = "$default"
  auto_deploy = true
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      environment = var.environment_id, requestId = "$context.requestId", routeKey = "$context.routeKey"
      status      = "$context.status", latency = "$context.responseLatency", integrationLatency = "$context.integrationLatency"
    })
  }
  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 20
    throttling_rate_limit    = 10
  }
}

resource "aws_apigatewayv2_integration" "auth" {
  count                  = local.auth_enabled ? 1 : 0
  api_id                 = aws_apigatewayv2_api.edge.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.auth[0].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

resource "aws_apigatewayv2_route" "auth" {
  for_each           = local.auth_enabled ? local.auth_routes : {}
  api_id             = aws_apigatewayv2_api.edge.id
  route_key          = each.key
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.auth[0].id}"
  depends_on         = [aws_lambda_permission.api]
}

resource "aws_lambda_permission" "api" {
  for_each       = local.auth_enabled ? local.auth_routes : {}
  statement_id   = each.key == "GET /.well-known/jwks.json" ? "JwksOnly" : "LoginOnly"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.auth[0].function_name
  principal      = "apigateway.amazonaws.com"
  source_account = var.aws_account_id
  source_arn     = "${aws_apigatewayv2_api.edge.execution_arn}/$default/${split(" ", each.key)[0]}${split(" ", each.key)[1]}"
}

resource "aws_apigatewayv2_integration" "business" {
  count                  = local.business_enabled ? 1 : 0
  api_id                 = aws_apigatewayv2_api.edge.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = var.alb_listener_arn
  connection_type        = "VPC_LINK"
  connection_id          = aws_apigatewayv2_vpc_link.edge.id
  payload_format_version = "1.0"
  timeout_milliseconds   = 15000
  request_parameters     = { "overwrite:path" = "$request.path" }
  dynamic "tls_config" {
    for_each = var.alb_tls == null ? [] : [var.alb_tls]
    content {
      server_name_to_verify = tls_config.value.server_name
    }
  }
  depends_on = [terraform_data.listener_guard, terraform_data.certificate_guard]
}

resource "aws_apigatewayv2_route" "business" {
  for_each           = local.business_enabled ? local.selected_business_routes : {}
  api_id             = aws_apigatewayv2_api.edge.id
  route_key          = each.key
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.business[0].id}"
}
