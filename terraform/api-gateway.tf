data "aws_lambda_function" "auth" {
  count = var.enable_api_gateway ? 1 : 0

  function_name = var.auth_lambda_function_name
}

resource "aws_apigatewayv2_api" "oficina" {
  count = var.enable_api_gateway ? 1 : 0

  name          = "tech-challenge-oficina-api"
  protocol_type = "HTTP"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-gateway"
  })
}

resource "aws_apigatewayv2_integration" "eks_backend" {
  count = var.enable_api_gateway ? 1 : 0

  api_id                 = aws_apigatewayv2_api.oficina[0].id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = var.api_backend_url
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_integration" "auth_lambda" {
  count = var.enable_api_gateway ? 1 : 0

  api_id                 = aws_apigatewayv2_api.oficina[0].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = data.aws_lambda_function.auth[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "auth" {
  count = var.enable_api_gateway ? 1 : 0

  api_id    = aws_apigatewayv2_api.oficina[0].id
  route_key = "POST /auth"
  target    = "integrations/${aws_apigatewayv2_integration.auth_lambda[0].id}"
}

resource "aws_apigatewayv2_route" "default" {
  count = var.enable_api_gateway ? 1 : 0

  api_id    = aws_apigatewayv2_api.oficina[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.eks_backend[0].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count = var.enable_api_gateway ? 1 : 0

  api_id      = aws_apigatewayv2_api.oficina[0].id
  name        = "$default"
  auto_deploy = true

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-gateway-default-stage"
  })
}

resource "aws_lambda_permission" "allow_api_gateway_auth" {
  count = var.enable_api_gateway ? 1 : 0

  statement_id  = "AllowExecutionFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = data.aws_lambda_function.auth[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.oficina[0].execution_arn}/*/POST/auth"
}
