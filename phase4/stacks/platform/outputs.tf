locals {
  manifest_objects = {
    foundation   = local.foundation
    network      = local.network_policies
    data         = [local.mongodb, local.rabbitmq]
    bootstrap    = [local.postgres_bootstrap]
    applications = slice(local.application_manifests, 0, var.jwt == null ? 0 : length(local.application_manifests))
  }
  secret_contract = merge({
    "postgres-bootstrap-admin" = { keys = ["username", "password"], source = var.infra_contract.rds.master_secret_arn, consumers = ["postgres-bootstrap"], temporary = true }
    "mongodb-admin"            = { keys = ["username", "password"], source = "environment-secret", consumers = ["mongodb"], temporary = false }
    "mongodb-keyfile"          = { keys = ["keyfile"], source = "environment-secret", consumers = ["mongodb"], temporary = false }
    "execution-mongodb"        = { keys = ["username", "password"], source = "environment-secret", consumers = ["execution-service", "mongodb"], temporary = false }
    "rabbitmq-cookie"          = { keys = ["cookie"], source = "environment-secret", consumers = ["rabbitmq"], temporary = false }
    "rabbitmq-definitions"     = { keys = ["definitions.json"], source = "environment-secret", consumers = ["rabbitmq"], temporary = false }
    "billing-mercado-pago"     = { keys = ["access-token", "webhook-secret"], source = "github-environment-or-secrets-manager", consumers = ["billing-service"], temporary = false }
    }, { for service in ["auth", "os", "billing"] : "${service}-postgres" => {
      keys = ["username", "password"], source = "environment-secret", consumers = ["postgres-bootstrap", service == "auth" ? "auth-internal" : "${service}-service"], temporary = false
      } }, { for service in ["os-service", "execution-service", "billing-service", "auth-internal"] : "${service}-tls" => {
      keys = ["identity.p12", "truststore.p12", "password"], source = "environment-pki", consumers = [service], temporary = false
      } }, { for service in ["os-service", "execution-service", "billing-service"] : "${service}-rabbitmq" => {
      keys = ["username", "password"], source = "environment-secret", consumers = [service], temporary = false
  } })
}

output "manifests" {
  description = "Bundles Kubernetes List em JSON (subconjunto YAML 1.2), sem Secrets. Renderizacao somente; Terraform nao se conecta ao Kubernetes. Aplicar separadamente na ordem documentada e apos preflight."
  value       = { for phase, objects in local.manifest_objects : phase => jsonencode({ apiVersion = "v1", kind = "List", items = objects }) }
}

output "secret_contract" {
  description = "Nomes/chaves/fontes esperados; nenhum valor secreto e lido ou armazenado no state Terraform."
  value       = local.secret_contract
}

output "auth_internal_contract" {
  value = {
    runtime_available           = false
    deployment_declared         = false
    service                     = "auth-internal.${local.namespace}.svc.cluster.local"
    port                        = 8443
    path                        = "/internal/v1/identity/client-access/check"
    required_client_subject     = "CN=os-service"
    database                    = var.infra_contract.rds.application_databases.auth.database
    user                        = var.infra_contract.rds.application_databases.auth.username
    public_signing_key_required = false
    blocked_reason              = "AuthLocalRuntime e exclusivo de E2E. Implementar/testar adapter produtivo no repositorio Auth antes de iniciar aplicacoes."
  }
}

output "mtls_contract" {
  description = "CA exclusiva por ambiente, identidades distintas e SANs para validacao HTTPS. Somente metadados; nenhum certificado/chave e gerado por Terraform."
  value = {
    environment_id = var.environment_id
    links          = ["os-service -> auth-internal", "execution-service -> os-service", "billing-service -> os-service"]
    identities = { for service in ["os-service", "execution-service", "billing-service", "auth-internal"] : service => {
      subject  = "CN=${service}"
      dns_sans = [service, "${service}.${local.namespace}.svc.cluster.local"]
      secret   = "${service}-tls"
      eku      = ["serverAuth", "clientAuth"]
    } }
    truststore_contents = "JRE public roots plus this environment's private CA; never only the E2E CA"
  }
}

output "external_configmaps" {
  value = {
    "rds-ca" = {
      keys        = ["ca.pem"]
      source      = "https://truststore.pki.rds.amazonaws.com/${var.aws_region}/${var.aws_region}-bundle.pem"
      requirement = "Validate TLS, checksum and certificate expiry before distribution; public material only."
    }
  }
}

output "network_controller_contract" {
  value = {
    installed              = false
    chart                  = "aws-load-balancer-controller"
    namespace              = "kube-system"
    service_account        = "aws-load-balancer-controller"
    cluster_name           = var.infra_contract.eks.name
    region                 = var.aws_region
    vpc_id                 = var.infra_contract.vpc_id
    private_subnet_ids     = [for subnet in var.infra_contract.private_subnets : subnet.id]
    node_security_group_id = var.infra_contract.security_groups.nodes
    required_scheme        = "internal"
    remaining_work         = "Review and scope controller IAM policy, pin chart/images, declare Helm release and strict ALB-to-pod rules before creating private Ingress."
  }
}

output "edge_contract" {
  value = {
    environment_id                    = var.environment_id
    aws_account_id                    = var.aws_account_id
    aws_region                        = var.aws_region
    namespace                         = local.namespace
    vpc_id                            = var.infra_contract.vpc_id
    private_subnet_ids                = [for subnet in var.infra_contract.private_subnets : subnet.id]
    services                          = { for service, name in local.services : service => { name = name, port = 8443, protocol = "HTTPS" } }
    listener_ready                    = false
    alb_listener_arn                  = null
    internal_paths_must_not_be_public = ["/internal/*"]
  }
}

output "observability" {
  value = {
    namespace   = "${local.namespace}-observability"
    chart       = "nri-bundle"
    repository  = "https://helm-charts.newrelic.com"
    version     = var.new_relic_chart_version
    secret_name = "newrelic-license"
    secret_keys = ["licenseKey"]
    values_yaml = yamlencode({
      global = { cluster = local.namespace, customSecretName = "newrelic-license", customSecretLicenseKey = "licenseKey", lowDataMode = true }
      "newrelic-infrastructure" = {
        enabled = true
        kubelet = { kubelet = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } }, agent = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } } }
        ksm     = { ksm = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } }, forwarder = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } } }
      }
      "kube-state-metrics"     = { enabled = true, resources = { requests = { cpu = "25m", memory = "48Mi" }, limits = { memory = "128Mi" } } }
      "newrelic-logging"       = { enabled = true, resources = { requests = { cpu = "50m", memory = "128Mi" }, limits = { memory = "256Mi" } } }
      "nri-kube-events"        = { enabled = true, resources = { requests = { cpu = "25m", memory = "64Mi" }, limits = { memory = "128Mi" } } }
      "nri-metadata-injection" = { enabled = false }
      "newrelic-pixie"         = { enabled = false }
    })
    apm_agent_bundled = false
  }
}
