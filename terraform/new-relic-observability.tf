locals {
  new_relic_app_name               = "tech-challenge-oficina-api"
  new_relic_cluster_name           = "tech-challenge-oficina"
  new_relic_api_base_url           = var.new_relic_api_base_url == null ? "" : trimspace(var.new_relic_api_base_url)
  new_relic_health_monitor_enabled = var.enable_new_relic && local.new_relic_api_base_url != ""
  new_relic_health_check_url       = "${trimsuffix(local.new_relic_api_base_url, "/")}/actuator/health/readiness"
  new_relic_health_monitor_name    = "tech-challenge-oficina-api-health"
}

resource "newrelic_one_dashboard" "tech_challenge_oficina" {
  count = var.enable_new_relic ? 1 : 0

  account_id  = var.new_relic_account_id
  name        = "Tech Challenge Oficina Observability"
  permissions = "public_read_only"

  page {
    name = "API and Kubernetes"

    widget_line {
      title  = "API latency"
      row    = 1
      column = 1
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM Transaction SELECT average(duration), percentile(duration, 95) WHERE appName = '${local.new_relic_app_name}' TIMESERIES"
      }
    }

    widget_line {
      title  = "API HTTP errors"
      row    = 1
      column = 7
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM Transaction SELECT count(*) WHERE appName = '${local.new_relic_app_name}' AND (http.statusCode >= 500 OR httpResponseCode LIKE '5%') TIMESERIES"
      }
    }

    widget_line {
      title  = "Kubernetes CPU"
      row    = 4
      column = 1
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM K8sNodeSample SELECT 100 * sum(cpuUsedCores) / sum(allocatableCpuCores) AS 'CPU used %' WHERE clusterName = '${local.new_relic_cluster_name}' TIMESERIES"
      }
    }

    widget_line {
      title  = "Kubernetes memory"
      row    = 4
      column = 7
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM K8sNodeSample SELECT 100 * sum(memoryWorkingSetBytes) / sum(allocatableMemoryBytes) AS 'Memory used %' WHERE clusterName = '${local.new_relic_cluster_name}' TIMESERIES"
      }
    }

    widget_table {
      title  = "Pod health"
      row    = 7
      column = 1
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM K8sPodSample SELECT latest(status) AS 'Status', latest(restartCount) AS 'Restarts' WHERE clusterName = '${local.new_relic_cluster_name}' FACET namespaceName, podName LIMIT MAX"
      }
    }

    widget_bar {
      title  = "Daily service order volume"
      row    = 7
      column = 7
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM OrdemServicoCreated SELECT count(*) TIMESERIES 1 day SINCE 7 days ago"
      }
    }

    widget_bar {
      title  = "Average time by service order stage"
      row    = 10
      column = 1
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM OrdemServicoStageDuration SELECT average(durationSeconds) FACET stage SINCE 7 days ago"
      }
    }

    widget_table {
      title  = "External integration errors"
      row    = 10
      column = 7
      width  = 6
      height = 3

      nrql_query {
        account_id = var.new_relic_account_id
        query      = "FROM ExternalIntegrationError SELECT count(*) WHERE appName = '${local.new_relic_app_name}' FACET integrationName, errorType SINCE 24 hours ago LIMIT MAX"
      }
    }
  }
}

resource "newrelic_alert_policy" "tech_challenge_oficina" {
  count = var.enable_new_relic ? 1 : 0

  account_id          = var.new_relic_account_id
  name                = "Tech Challenge Oficina Observability"
  incident_preference = "PER_CONDITION"
}

resource "newrelic_synthetics_monitor" "api_health" {
  count = local.new_relic_health_monitor_enabled ? 1 : 0

  account_id                = var.new_relic_account_id
  name                      = local.new_relic_health_monitor_name
  type                      = "SIMPLE"
  status                    = "ENABLED"
  period                    = "EVERY_MINUTE"
  uri                       = local.new_relic_health_check_url
  locations_public          = ["US_EAST_1"]
  verify_ssl                = true
  treat_redirect_as_failure = true
}

resource "newrelic_nrql_alert_condition" "api_health_failure" {
  count = local.new_relic_health_monitor_enabled ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "API health check failure"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM SyntheticCheck SELECT percentage(count(*), WHERE result = 'FAILED') WHERE monitorName = '${local.new_relic_health_monitor_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 0
    threshold_duration    = 300
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "api_http_errors" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "API HTTP 5xx errors increased"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM Transaction SELECT percentage(count(*), WHERE http.statusCode >= 500 OR httpResponseCode LIKE '5%') WHERE appName = '${local.new_relic_app_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 5
    threshold_duration    = 300
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "api_high_latency" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "API high latency"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM Transaction SELECT percentile(duration, 95) WHERE appName = '${local.new_relic_app_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 2
    threshold_duration    = 300
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "kubernetes_pods_unavailable" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "Kubernetes pods unavailable or failing"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM K8sPodSample SELECT uniqueCount(podName) WHERE clusterName = '${local.new_relic_cluster_name}' AND status != 'Running'"
  }

  critical {
    operator              = "above"
    threshold             = 0
    threshold_duration    = 300
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "kubernetes_high_cpu" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "Kubernetes high CPU"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM K8sNodeSample SELECT 100 * sum(cpuUsedCores) / sum(allocatableCpuCores) WHERE clusterName = '${local.new_relic_cluster_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 80
    threshold_duration    = 600
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "kubernetes_high_memory" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "Kubernetes high memory"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM K8sNodeSample SELECT 100 * sum(memoryWorkingSetBytes) / sum(allocatableMemoryBytes) WHERE clusterName = '${local.new_relic_cluster_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 80
    threshold_duration    = 600
    threshold_occurrences = "ALL"
  }
}

resource "newrelic_nrql_alert_condition" "external_integration_errors" {
  count = var.enable_new_relic ? 1 : 0

  account_id                   = var.new_relic_account_id
  policy_id                    = newrelic_alert_policy.tech_challenge_oficina[0].id
  type                         = "static"
  name                         = "External integration errors"
  enabled                      = true
  violation_time_limit_seconds = 3600
  aggregation_window           = 60
  aggregation_method           = "event_flow"
  aggregation_delay            = 120

  nrql {
    query = "FROM ExternalIntegrationError SELECT count(*) WHERE appName = '${local.new_relic_app_name}'"
  }

  critical {
    operator              = "above"
    threshold             = 0
    threshold_duration    = 300
    threshold_occurrences = "AT_LEAST_ONCE"
  }
}
