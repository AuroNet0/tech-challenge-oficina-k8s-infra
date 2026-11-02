locals {
  manifest_objects = {
    foundation   = local.foundation
    network      = local.network_policies
    data         = [local.mongodb, local.rabbitmq]
    bootstrap    = [local.postgres_bootstrap]
    applications = slice(local.application_manifests, 0, var.jwt == null ? 0 : length(local.application_manifests))
    auth         = slice(local.auth_manifests, 0, var.jwt == null || var.images.auth == null ? 0 : length(local.auth_manifests))
    migration    = slice([local.auth_migration], 0, var.auth_migration_enabled ? 1 : 0)
  }
  secret_contract = merge({
    "postgres-bootstrap-admin" = { keys = ["username", "password"], source = var.infra_contract.rds.master_secret_arn, consumers = ["postgres-bootstrap"], temporary = true }
    "auth-migration-postgres"  = { keys = ["username", "password"], source = "environment-secret", consumers = ["postgres-bootstrap", "auth-migrate"], temporary = true }
    "mongodb-admin"            = { keys = ["username", "password"], source = "environment-secret", consumers = ["mongodb"], temporary = false }
    "mongodb-keyfile"          = { keys = ["keyfile"], source = "environment-secret", consumers = ["mongodb"], temporary = false }
    "execution-mongodb"        = { keys = ["username", "password"], source = "environment-secret", consumers = ["execution-service", "mongodb"], temporary = false }
    "rabbitmq-cookie"          = { keys = ["cookie"], source = "environment-secret", consumers = ["rabbitmq"], temporary = false }
    "rabbitmq-definitions"     = { keys = ["definitions.json"], source = "environment-secret", consumers = ["rabbitmq"], temporary = false }
    "billing-mercado-pago"     = { keys = ["access-token", "webhook-secret"], source = "github-environment-or-secrets-manager", consumers = ["billing-service"], temporary = false }
    }, { for service in ["auth", "os", "billing"] : "${service}-postgres" => {
      keys = ["username", "password"], source = "environment-secret", consumers = ["postgres-bootstrap", service == "auth" ? "auth-internal" : "${service}-service"], temporary = false
      } }, { for service in ["os-service", "execution-service", "billing-service", "auth-internal"] : "${service}-tls" => {
      keys = concat(["identity.p12", "truststore.p12", "password"], service == "auth-internal" ? [] : ["java-options", "inbound-truststore.p12"]), source = "environment-pki", consumers = [service], temporary = false
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

output "secret_delivery_contract" {
  description = "Somente metadados operacionais. Geracao e transporte de valores acontecem fora do Terraform."
  value = {
    schema_version       = 1
    environment_id       = var.environment_id
    namespace            = local.namespace
    canonical_prefix     = "/oficina/phase4/${var.environment_id}/"
    bootstrap_source     = var.infra_contract.rds.master_secret_arn
    ca_canonical_name    = "/oficina/phase4/${var.environment_id}/technical-ca"
    ca_projected         = false
    mercado_pago_enabled = var.mercado_pago.enabled
    secrets = { for name, contract in local.secret_contract : name => {
      keys      = contract.keys
      consumers = contract.consumers
      lifecycle = name == "postgres-bootstrap-admin" ? "bootstrap" : name == "auth-migration-postgres" ? "migration" : endswith(name, "-tls") ? "certificate" : "environment"
      source    = name == "postgres-bootstrap-admin" ? var.infra_contract.rds.master_secret_arn : name == "rabbitmq-definitions" ? "derived-in-memory" : "/oficina/phase4/${var.environment_id}/${name}"
    } }
    live_transport_enabled = false
  }
}

output "auth_internal_contract" {
  value = {
    runtime_available           = true
    deployment_declared         = true
    deployment_rendered         = var.jwt != null && var.images.auth != null
    image_verified              = var.auth_image_verified
    migration_completed         = var.auth_migration_completed
    enabled                     = var.application_replicas > 0
    source_revision             = "dd10bbd3a31d2148ce111a996b908a48379bc3ba"
    service                     = "auth-internal.${local.namespace}.svc.cluster.local"
    port                        = 8443
    path                        = "/internal/v1/identity/client-access/check"
    required_client_subject     = "CN=os-service"
    database                    = var.infra_contract.rds.application_databases.auth.database
    user                        = var.infra_contract.rds.application_databases.auth.username
    public_signing_key_required = false
    migration_user              = local.auth_migration_user
    blocked_reason              = "Replicas zero ate imagem ECR verificada, PKI/Secrets, bootstrap/migracao e dataplane validados no ambiente. Flags sao atestacoes externas, nao evidencia automatica."
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
      eku      = service == "auth-internal" ? ["serverAuth"] : ["serverAuth", "clientAuth"]
    } }
    truststore_contents = "Auth inbound /tls/truststore.p12: ONLY environment technical CA. Auth outbound JWKS: unmodified JRE public roots. OS JSSE outbound store: JRE public roots plus environment CA."
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
    chart_version          = "1.14.0"
    controller_version     = "v2.14.0"
    identity = {
      mechanism     = "EKS Pod Identity"
      role_name     = "${local.namespace}-load-balancer-controller"
      installed     = false
      declared      = true
      enabled       = var.alb_controller_identity_enabled
      role_arn      = try(aws_iam_role.alb_controller[0].arn, null)
      policy_source = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.14.0/docs/install/iam_policy.json"
      requirement   = "Scope trust to cluster/kube-system/aws-load-balancer-controller; scope resource tags/VPC; review actions requiring wildcard; attach policy and association before Helm."
    }
    values_yaml = yamlencode({
      clusterName                 = local.namespace, region = var.aws_region, vpcId = var.infra_contract.vpc_id
      replicaCount                = 1
      serviceAccount              = { create = true, name = "aws-load-balancer-controller", automountServiceAccountToken = true }
      enableServiceMutatorWebhook = false
      createIngressClassResource  = false
      enableShield                = false
      enableWaf                   = false
      enableWafv2                 = false
      enableBackendSecurityGroup  = false
      resources                   = { requests = { cpu = "100m", memory = "128Mi" }, limits = { cpu = "500m", memory = "256Mi" } }
      defaultTags                 = module.environment.contract.tags
    })
    remaining_work = "Review and scope controller IAM policy, pin chart/images, declare Helm release and strict ALB-to-pod rules before creating private Ingress."
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
    installed                      = false
    iam_required                   = false
    service_accounts               = "Chart-created per component with Kubernetes RBAC; token needed for API/kubelet. Separate observability namespace, never application SAs."
    namespace_security_requirement = "Review chart hostPath/privileged agents before installing outside restricted business namespace. No blanket exemption for business pods."
    namespace                      = "${local.namespace}-observability"
    chart                          = "nri-bundle"
    repository                     = "https://helm-charts.newrelic.com"
    version                        = var.new_relic_chart_version
    secret_name                    = "newrelic-license"
    secret_keys                    = ["licenseKey"]
    values_yaml = yamlencode({
      global = { cluster = local.namespace, customSecretName = "newrelic-license", customSecretLicenseKey = "licenseKey", lowDataMode = true }
      "newrelic-infrastructure" = {
        enabled      = true
        kubelet      = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } }
        controlPlane = { enabled = false }
        ksm          = { ksm = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } }, forwarder = { resources = { requests = { cpu = "50m", memory = "64Mi" }, limits = { memory = "256Mi" } } } }
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
