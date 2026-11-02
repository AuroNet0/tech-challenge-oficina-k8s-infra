mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}

mock_data "aws_acm_certificate" {
  defaults = {
    arn    = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    status = "ISSUED"
  }
}

mock_data "aws_lb_listener" {
  defaults = {
    load_balancer_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/tc-oficina-p4-demo-a-edge/aaaaaaaaaaaaaaaa"
    port              = 443, protocol = "HTTPS"
    certificate_arn   = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
  }
}

mock_data "aws_lb" {
  defaults = {
    name            = "tc-oficina-p4-demo-a-edge", internal = true, load_balancer_type = "application"
    vpc_id          = "vpc-00000000000000001", tags = { EnvironmentId = "demo-a" }
    subnets         = ["subnet-00000000000000001", "subnet-00000000000000002"]
    security_groups = ["sg-00000000000000009"]
  }
}

mock_resource "aws_apigatewayv2_api" {
  defaults = {
    api_endpoint  = "https://synthetic.execute-api.us-east-1.amazonaws.com"
    execution_arn = "arn:aws:execute-api:us-east-1:123456789012:synthetic"
  }
}

mock_resource "aws_security_group" {
  defaults = { id = "sg-00000000000000009" }
}

mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-auth-lambda" }
}

mock_resource "aws_cloudwatch_log_group" {
  defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:synthetic" }
}

mock_resource "aws_lambda_function" {
  defaults = { invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:tc-oficina-p4-demo-a-auth/invocations" }
}
