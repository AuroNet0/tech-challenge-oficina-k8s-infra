data "aws_lambda_function" "auth" {
  function_name = "tech-challenge-oficina-auth"
}

resource "aws_apigatewayv2_api" "oficina" {
  name          = "tech-challenge-oficina-api"
  protocol_type = "HTTP"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-gateway"
  })
}

resource "aws_apigatewayv2_integration" "eks_backend" {
  api_id                 = aws_apigatewayv2_api.oficina.id
  integration_type       = "HTTP_PROXY"
  integration_method     = "ANY"
  integration_uri        = var.api_backend_url
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_integration" "auth_lambda" {
  api_id                 = aws_apigatewayv2_api.oficina.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = data.aws_lambda_function.auth.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "auth" {
  api_id    = aws_apigatewayv2_api.oficina.id
  route_key = "POST /auth"
  target    = "integrations/${aws_apigatewayv2_integration.auth_lambda.id}"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.oficina.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.eks_backend.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.oficina.id
  name        = "$default"
  auto_deploy = true

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-gateway-default-stage"
  })
}

resource "aws_lambda_permission" "allow_api_gateway_auth" {
  statement_id  = "AllowExecutionFromApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = data.aws_lambda_function.auth.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.oficina.execution_arn}/*/POST/auth"
}
