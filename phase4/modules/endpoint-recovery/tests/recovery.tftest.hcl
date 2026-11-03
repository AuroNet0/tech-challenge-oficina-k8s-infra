mock_provider "aws" {
  override_during = plan
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:tc-oficina-p4-demo-a-endpoint-recovery-dlq" }
  }
}

variables {
  environment_id  = "demo-a"
  account         = "123456789012"
  region          = "us-east-1"
  cluster_arn     = "arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a"
  recovery_role   = "tc-oficina-p4-demo-a-endpoint-recovery"
  alarm_topic_arn = "arn:aws:sns:us-east-1:123456789012:approved-operations"
}

run "persistent_recovery" {
  command = plan
  assert {
    condition     = aws_dynamodb_table.leases.deletion_protection_enabled && aws_dynamodb_table.leases.billing_mode == "PAY_PER_REQUEST"
    error_message = "Persistencia protegida obrigatoria."
  }
  assert {
    condition     = aws_sqs_queue.failures.message_retention_seconds == 1209600 && aws_sqs_queue.failures.sqs_managed_sse_enabled
    error_message = "DLQ deve reter falhas por 14 dias com SSE."
  }
  assert {
    condition     = aws_lambda_function_event_invoke_config.recovery.maximum_retry_attempts == 2 && aws_scheduler_schedule.reconcile.target[0].retry_policy[0].maximum_retry_attempts == 10
    error_message = "Retries de entrega e execucao sao distintos e obrigatorios."
  }
  assert {
    condition     = aws_lambda_function_event_invoke_config.recovery.destination_config[0].on_failure[0].destination == aws_sqs_queue.failures.arn
    error_message = "Erro definitivo da Lambda deve chegar a DLQ."
  }
  assert {
    condition     = !local.config.enabled && !local.config.recovery_verified && !output.contract.opening_enabled
    error_message = "Esta etapa nao autoriza abertura."
  }
  assert {
    condition     = aws_scheduler_schedule.reconcile.schedule_expression == "rate(5 minutes)" && local.config.reconcile_interval_minutes == 5 && length(aws_cloudwatch_metric_alarm.recovery) == 4
    error_message = "Reconciliacao e observabilidade independentes obrigatorias."
  }
  assert {
    condition = (
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].period == 300 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].evaluation_periods == 3 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].datapoints_to_alarm == 3 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].metric_name == "Invocations" &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].threshold == 1 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].comparison_operator == "LessThanThreshold" &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].treat_missing_data == "breaching" &&
      aws_cloudwatch_metric_alarm.recovery["errors"].treat_missing_data == "notBreaching" &&
      aws_cloudwatch_metric_alarm.recovery["dlq"].treat_missing_data == "notBreaching" &&
      aws_cloudwatch_metric_alarm.recovery["destination"].treat_missing_data == "notBreaching"
    )
    error_message = "Heartbeat deve acompanhar a cadencia com tres periodos; ausencia normal de erros/DLQ nao e falha."
  }
  assert {
    condition     = alltrue([for statement in local.broker_statements : !contains(flatten([statement.Action]), "eks:UpdateClusterConfig")])
    error_message = "Broker sem abertura nesta etapa; recovery conserva policy CloseOnly do bootstrap."
  }
}

run "foreign_cluster" {
  command = plan
  variables { cluster_arn = "arn:aws:eks:us-east-1:123456789012:cluster/foreign" }
  expect_failures = [terraform_data.guard]
}

run "missing_alarm_channel" {
  command = plan
  variables { alarm_topic_arn = "" }
  expect_failures = [terraform_data.guard]
}

run "one_minute_when_justified" {
  command = plan
  variables { reconcile_interval_minutes = 1 }
  assert {
    condition = (
      aws_scheduler_schedule.reconcile.schedule_expression == "rate(1 minute)" &&
      local.config.reconcile_interval_minutes == 1 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].period == 60 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].evaluation_periods == 6 &&
      aws_cloudwatch_metric_alarm.recovery["heartbeat"].datapoints_to_alarm == 6
    )
    error_message = "Cadencia de um minuto exige seis periodos de 60 segundos para tolerar o timeout de 300s da Lambda."
  }
}

run "reject_zero_interval" {
  command = plan
  variables { reconcile_interval_minutes = 0 }
  expect_failures = [var.reconcile_interval_minutes]
}

run "reject_excessive_interval" {
  command = plan
  variables { reconcile_interval_minutes = 6 }
  expect_failures = [var.reconcile_interval_minutes]
}

run "reject_fractional_interval" {
  command = plan
  variables { reconcile_interval_minutes = 1.5 }
  expect_failures = [var.reconcile_interval_minutes]
}
