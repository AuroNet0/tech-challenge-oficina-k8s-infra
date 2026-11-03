locals {
  prefix               = "tc-oficina-p4-${var.environment_id}"
  recovery_arn         = "arn:aws:lambda:${var.region}:${var.account}:function:${local.prefix}-endpoint-recovery"
  broker_arn           = "arn:aws:lambda:${var.region}:${var.account}:function:${local.prefix}-endpoint-broker"
  group_arn            = "arn:aws:scheduler:${var.region}:${var.account}:schedule-group/${local.prefix}-recovery"
  schedule_arn         = "arn:aws:scheduler:${var.region}:${var.account}:schedule/${local.prefix}-recovery/lease-*"
  reconcile_expression = "rate(${var.reconcile_interval_minutes} ${var.reconcile_interval_minutes == 1 ? "minute" : "minutes"})"
  config = {
    environment_id             = var.environment_id, account = var.account, region = var.region
    cluster_arn                = var.cluster_arn, cluster_name = local.prefix
    table                      = aws_dynamodb_table.leases.name, schedule_group = aws_scheduler_schedule_group.recovery.name
    recovery_arn               = local.recovery_arn, broker_arn = local.broker_arn
    recovery_role_arn          = "arn:aws:iam::${var.account}:role/${var.recovery_role}"
    recovery_role_name         = var.recovery_role, broker_role_name = aws_iam_role.broker.name
    scheduler_role_arn         = aws_iam_role.scheduler.arn, dlq_arn = aws_sqs_queue.failures.arn
    code_sha256                = filebase64sha256(var.package_path)
    enabled                    = false, recovery_verified = false
    reconcile_interval_minutes = var.reconcile_interval_minutes
  }
}

resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition = (
        contains(["demo-a", "demo-b"], var.environment_id) &&
        can(regex("^[0-9]{12}$", var.account)) && var.region == "us-east-1" &&
        var.cluster_arn == "arn:aws:eks:${var.region}:${var.account}:cluster/${local.prefix}" &&
        var.recovery_role == "${local.prefix}-endpoint-recovery" &&
        can(regex("^arn:aws:sns:${var.region}:${var.account}:[A-Za-z0-9_-]+$", var.alarm_topic_arn))
      )
      error_message = "Contrato de recuperacao ou canal independente de alarmes invalido."
    }
  }
}

resource "aws_dynamodb_table" "leases" {
  name         = "${local.prefix}-endpoint-leases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
  deletion_protection_enabled = true
  depends_on                  = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_sqs_queue" "failures" {
  name                      = "${local.prefix}-endpoint-recovery-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  depends_on                = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_scheduler_schedule_group" "recovery" {
  name       = "${local.prefix}-recovery"
  depends_on = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "scheduler" {
  name = "${local.prefix}-recovery-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole"
    Condition = { StringEquals = { "aws:SourceAccount" = var.account }, ArnEquals = { "aws:SourceArn" = local.group_arn } }
  }] })
  depends_on = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = local.recovery_arn },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn }
  ] })
}

resource "aws_iam_role" "broker" {
  name = "${local.prefix}-endpoint-broker"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
  depends_on = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_cloudwatch_log_group" "runtime" {
  for_each          = toset(["endpoint-recovery", "endpoint-broker"])
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = 30
  depends_on        = [terraform_data.guard]
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "storage" {
  for_each = { recovery = var.recovery_role, broker = aws_iam_role.broker.name }
  name     = "endpoint-lease-runtime"
  role     = each.value
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "dynamodb:GetItem", Resource = aws_dynamodb_table.leases.arn },
    { Effect = "Allow", Action = "dynamodb:PutItem", Resource = aws_dynamodb_table.leases.arn,
    Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["CURRENT", "????????????????????????????????"] } } },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.runtime["endpoint-${each.key}"].arn}:*" },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn }
  ] })
}

locals {
  broker_statements = [
    { Effect = "Allow", Action = "eks:DescribeCluster", Resource = var.cluster_arn },
    { Effect = "Allow", Action = ["scheduler:CreateSchedule", "scheduler:GetSchedule"], Resource = local.schedule_arn },
    { Effect = "Allow", Action = "scheduler:GetSchedule", Resource = "arn:aws:scheduler:${var.region}:${var.account}:schedule/${local.prefix}-recovery/reconcile" },
    { Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.scheduler.arn, Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } } },
    { Effect = "Allow", Action = ["lambda:InvokeFunction", "lambda:GetFunctionConfiguration"], Resource = local.recovery_arn }
  ]
}

resource "aws_iam_role_policy" "broker" {
  role   = aws_iam_role.broker.id
  policy = jsonencode({ Version = "2012-10-17", Statement = local.broker_statements })
}

resource "aws_lambda_function" "runtime" {
  for_each                       = { recovery = var.recovery_role, broker = aws_iam_role.broker.name }
  function_name                  = "${local.prefix}-endpoint-${each.key}"
  role                           = "arn:aws:iam::${var.account}:role/${each.value}"
  runtime                        = "python3.13"
  handler                        = "recovery.${each.key}_handler"
  filename                       = var.package_path
  source_code_hash               = filebase64sha256(var.package_path)
  timeout                        = each.key == "broker" ? 60 : 300
  memory_size                    = 128
  reserved_concurrent_executions = 1
  environment { variables = { RECOVERY_CONFIG = jsonencode(local.config) } }
  depends_on = [aws_iam_role_policy.storage, aws_iam_role_policy.broker, aws_cloudwatch_log_group.runtime]
  lifecycle { prevent_destroy = true }
}

resource "aws_lambda_function_event_invoke_config" "recovery" {
  function_name                = aws_lambda_function.runtime["recovery"].function_name
  maximum_event_age_in_seconds = 3600
  maximum_retry_attempts       = 2
  destination_config {
    on_failure { destination = aws_sqs_queue.failures.arn }
  }
}

resource "aws_scheduler_schedule" "reconcile" {
  name                         = "reconcile"
  group_name                   = aws_scheduler_schedule_group.recovery.name
  schedule_expression          = local.reconcile_expression
  schedule_expression_timezone = "UTC"
  state                        = "ENABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.runtime["recovery"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ reconcile = true })
    dead_letter_config { arn = aws_sqs_queue.failures.arn }
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 10
    }
  }
  depends_on = [aws_iam_role_policy.scheduler, aws_lambda_function_event_invoke_config.recovery]
  lifecycle { prevent_destroy = true }
}

locals {
  heartbeat_periods = max(3, ceil(aws_lambda_function.runtime["recovery"].timeout / (var.reconcile_interval_minutes * 60)) + 1)
  monitors = {
    dlq         = { namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", dimensions = { QueueName = aws_sqs_queue.failures.name }, threshold = 0, comparison = "GreaterThanThreshold", missing = "notBreaching" }
    errors      = { namespace = "AWS/Lambda", metric = "Errors", dimensions = { FunctionName = aws_lambda_function.runtime["recovery"].function_name }, threshold = 0, comparison = "GreaterThanThreshold", missing = "notBreaching" }
    destination = { namespace = "AWS/Lambda", metric = "DestinationDeliveryFailures", dimensions = { FunctionName = aws_lambda_function.runtime["recovery"].function_name }, threshold = 0, comparison = "GreaterThanThreshold", missing = "notBreaching" }
    heartbeat   = { namespace = "AWS/Lambda", metric = "Invocations", dimensions = { FunctionName = aws_lambda_function.runtime["recovery"].function_name }, threshold = 1, comparison = "LessThanThreshold", missing = "breaching" }
  }
}

resource "aws_cloudwatch_metric_alarm" "recovery" {
  for_each            = local.monitors
  alarm_name          = "${local.prefix}-recovery-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dimensions
  statistic           = "Sum"
  period              = each.key == "heartbeat" ? var.reconcile_interval_minutes * 60 : 300
  evaluation_periods  = each.key == "heartbeat" ? local.heartbeat_periods : 1
  datapoints_to_alarm = each.key == "heartbeat" ? local.heartbeat_periods : 1
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  treat_missing_data  = each.value.missing
  alarm_actions       = [var.alarm_topic_arn]
  lifecycle { prevent_destroy = true }
}

output "contract" {
  value = {
    table                      = aws_dynamodb_table.leases.name, recovery_arn = local.recovery_arn, broker_arn = local.broker_arn
    schedule_group             = aws_scheduler_schedule_group.recovery.name, dlq_arn = aws_sqs_queue.failures.arn
    independent_of_eks         = true, opening_enabled = false, verification_required = true
    reconcile_interval_minutes = var.reconcile_interval_minutes
  }
}
