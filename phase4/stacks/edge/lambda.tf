resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}-auth"
  retention_in_days = 7
  depends_on        = [terraform_data.edge_guard]
}

resource "aws_iam_role" "auth" {
  name = "${local.name_prefix}-auth-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  depends_on = [terraform_data.edge_guard]
}

resource "aws_iam_role_policy" "auth" {
  name = "${local.name_prefix}-auth-runtime"
  role = aws_iam_role.auth.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda.arn}:*" },
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = values(local.auth_secrets) },
      {
        Effect = "Allow", Resource = "*"
        Action = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSubnets", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"]
      },
      {
        Effect    = "Deny", Resource = "*"
        Action    = ["ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"]
        Condition = { ArnEquals = { "lambda:SourceFunctionArn" = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${local.name_prefix}-auth" } }
      }
    ]
  })
}

resource "aws_lambda_function" "auth" {
  count                          = var.publish_auth && var.auth_release != null ? 1 : 0
  function_name                  = "${local.name_prefix}-auth"
  role                           = aws_iam_role.auth.arn
  runtime                        = "java21"
  architectures                  = ["x86_64"]
  handler                        = "br.com.fiap.oficina.auth.handler.AuthHandler::handleRequest"
  s3_bucket                      = var.auth_release.bucket
  s3_key                         = var.auth_release.key
  s3_object_version              = var.auth_release.version_id
  source_code_hash               = var.auth_release.sha256_base64
  layers                         = [var.auth_release.bootstrap_layer_arn]
  timeout                        = 12
  memory_size                    = 512
  reserved_concurrent_executions = 2
  vpc_config {
    subnet_ids         = local.subnet_ids
    security_group_ids = [aws_security_group.edge["lambda"].id]
  }
  environment {
    variables = {
      AUTH_ENVIRONMENT        = var.environment_id
      AUTH_KEY_PROVIDER       = "secrets-manager"
      AUTH_KEY_SECRET_ID      = "/oficina/phase4/${var.environment_id}/auth-signing"
      AUTH_DB_SECRET_ID       = "/oficina/phase4/${var.environment_id}/auth-postgres"
      AUTH_DB_HOST            = var.infra_contract.rds.address
      AUTH_DB_PORT            = "5432"
      AUTH_DB_NAME            = var.infra_contract.rds.application_databases.auth.database
      AUTH_DB_USER            = var.infra_contract.rds.application_databases.auth.username
      AUTH_DB_SSLMODE         = "verify-full"
      AUTH_JWT_ISSUER         = aws_apigatewayv2_api.edge.api_endpoint
      AUTH_JWT_AUDIENCE       = local.name_prefix
      AUTH_JWT_TTL_SECONDS    = "900"
      AWS_LAMBDA_EXEC_WRAPPER = "/opt/bin/auth-bootstrap"
      JAVA_TOOL_OPTIONS       = "-Duser.home=/opt/auth-home"
    }
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }
  depends_on = [aws_iam_role_policy.auth, aws_vpc_endpoint.secrets, aws_vpc_security_group_ingress_rule.flow]
}
