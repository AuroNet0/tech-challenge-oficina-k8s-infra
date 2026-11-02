locals {
  public_jwt = var.jwt == null ? { issuer = "", audience = "", jwks_uri = "" } : var.jwt
  services   = { os = "os-service", execution = "execution-service", billing = "billing-service" }
  application_config = { for service, name in local.services : service => merge({
    SPRING_PROFILES_ACTIVE                     = "mtls"
    SERVER_ADDRESS                             = "0.0.0.0", SERVER_PORT = "8443", SERVER_SSL_ENABLED = "true"
    SERVER_SSL_KEY_STORE                       = "file:/tls/identity.p12", SERVER_SSL_KEY_STORE_TYPE = "PKCS12"
    SERVER_SSL_TRUST_STORE                     = "file:/tls/inbound-truststore.p12", SERVER_SSL_TRUST_STORE_TYPE = "PKCS12"
    SERVER_SSL_CLIENT_AUTH                     = "want"
    SPRING_RABBITMQ_HOST                       = "rabbitmq", SPRING_RABBITMQ_PORT = "5672", SPRING_RABBITMQ_VIRTUAL_HOST = "/oficina"
    SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE = "3", SPRING_DATASOURCE_HIKARI_MINIMUM_IDLE = "0"
    SPRING_JPA_HIBERNATE_DDL_AUTO              = "validate", SPRING_FLYWAY_ENABLED = "true"
    NEW_RELIC_APP_NAME                         = "${local.namespace}-${name}", NEW_RELIC_DISTRIBUTED_TRACING_ENABLED = "true"
    OFICINA_INTERNAL_AUTH_URI                  = "https://auth-internal.${local.namespace}.svc.cluster.local:8443/internal/v1/identity/client-access/check"
    OFICINA_INTERNAL_OS_URI                    = "https://os-service.${local.namespace}.svc.cluster.local:8443/internal/${service == "billing" ? "financial" : "technical"}-authorization"
    }, service == "execution" ? {
    EXECUTION_JWT_ISSUER = local.public_jwt.issuer, EXECUTION_JWT_AUDIENCE = local.public_jwt.audience, EXECUTION_JWT_JWKS_URI = local.public_jwt.jwks_uri
    MONGO_HOST           = "mongodb-0.mongodb.${local.namespace}.svc.cluster.local", MONGO_PORT = "27017"
    MONGO_DATABASE       = "oficina_execution", MONGO_AUTH_DATABASE = "oficina_execution"
    } : {
    SPRING_DATASOURCE_URL = "jdbc:postgresql://${var.infra_contract.rds.address}:5432/${var.infra_contract.rds.application_databases[service].database}?sslmode=verify-full&sslrootcert=/rds-ca/ca.pem"
    }, service == "os" ? {
    AUTH_JWT_ISSUER = local.public_jwt.issuer, AUTH_JWT_AUDIENCE = local.public_jwt.audience, AUTH_JWKS_URI = local.public_jwt.jwks_uri
    } : {}, service == "billing" ? {
    BILLING_JWT_ISSUER            = local.public_jwt.issuer, BILLING_JWT_AUDIENCE = local.public_jwt.audience, BILLING_JWKS_URI = local.public_jwt.jwks_uri
    MERCADO_PAGO_ENABLED          = tostring(var.mercado_pago.enabled), MERCADO_PAGO_RECONCILIATION_ENABLED = tostring(var.mercado_pago.enabled)
    MERCADO_PAGO_API_BASE_URL     = "https://api.mercadopago.com"
    MERCADO_PAGO_NOTIFICATION_URL = var.mercado_pago.notification_url
    MERCADO_PAGO_SUCCESS_URL      = var.mercado_pago.success_url
    MERCADO_PAGO_FAILURE_URL      = var.mercado_pago.failure_url
    MERCADO_PAGO_PENDING_URL      = var.mercado_pago.pending_url
  } : {}) }

  application_manifests = flatten([for service, name in local.services : [
    {
      apiVersion = "v1", kind = "ConfigMap"
      metadata   = merge(local.metadata, { name = "${name}-config" })
      data       = local.application_config[service]
    },
    {
      apiVersion = "v1", kind = "Service"
      metadata   = merge(local.metadata, { name = name })
      spec = {
        type  = "ClusterIP", selector = { "app.kubernetes.io/name" = name }
        ports = [{ name = "https", port = 8443, targetPort = "https", appProtocol = "https" }]
      }
    },
    {
      apiVersion = "apps/v1", kind = "Deployment"
      metadata   = merge(local.metadata, { name = name })
      spec = {
        replicas = var.application_replicas
        strategy = { type = "Recreate" }
        selector = { matchLabels = { "app.kubernetes.io/name" = name } }
        template = {
          metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = name, "oficina/role" = "business" }) }
          spec = {
            serviceAccountName            = name, automountServiceAccountToken = false
            securityContext               = local.pod_security
            terminationGracePeriodSeconds = 45
            initContainers = service == "execution" ? [] : [{
              name            = "database-ready", image = var.images.postgres
              command         = ["sh", "-ec", "for attempt in $(seq 1 120); do psql -Xq -c 'SELECT 1' >/dev/null 2>&1 && exit 0; sleep 3; done; exit 1"]
              securityContext = local.security_context
              resources       = { requests = { cpu = "50m", memory = "64Mi" }, limits = { cpu = "250m", memory = "128Mi" } }
              env = concat([
                { name = "PGHOST", value = var.infra_contract.rds.address }, { name = "PGPORT", value = "5432" },
                { name = "PGDATABASE", value = var.infra_contract.rds.application_databases[service].database },
                { name = "PGSSLMODE", value = "verify-full" }, { name = "PGSSLROOTCERT", value = "/rds-ca/ca.pem" }
                ], [for env, key in { PGUSER = "username", PGPASSWORD = "password" } : {
                  name                       = env, valueFrom = { secretKeyRef = { name = "${service}-postgres", key = key } }
              }])
              volumeMounts = [{ name = "rds-ca", mountPath = "/rds-ca", readOnly = true }]
            }]
            containers = [{
              name            = name, image = var.images[service], imagePullPolicy = "IfNotPresent"
              command         = ["java", "-Xmx384m", "-XX:ActiveProcessorCount=2", "@/tls/java-options", "-jar", "/app/app.jar"]
              ports           = [{ name = "https", containerPort = 8443 }]
              securityContext = local.security_context
              resources       = { requests = { cpu = "250m", memory = "512Mi" }, limits = { cpu = "1000m", memory = "768Mi" } }
              envFrom         = [{ configMapRef = { name = "${name}-config" } }]
              env = concat([
                { name = "SERVER_SSL_KEY_STORE_PASSWORD", valueFrom = { secretKeyRef = { name = "${name}-tls", key = "password" } } },
                { name = "SERVER_SSL_TRUST_STORE_PASSWORD", valueFrom = { secretKeyRef = { name = "${name}-tls", key = "password" } } }
                ], [for env, key in { SPRING_RABBITMQ_USERNAME                            = "username", SPRING_RABBITMQ_PASSWORD = "password" } : {
                  name                                                                    = env, valueFrom = { secretKeyRef = { name = "${name}-rabbitmq", key = key } }
                  }], service == "execution" ? [for env, key in { MONGO_USERNAME          = "username", MONGO_PASSWORD = "password" } : {
                  name                                                                    = env, valueFrom = { secretKeyRef = { name = "execution-mongodb", key = key } }
                  }] : [for env, key in { SPRING_DATASOURCE_USERNAME                      = "username", SPRING_DATASOURCE_PASSWORD = "password" } : {
                  name                                                                    = env, valueFrom = { secretKeyRef = { name = "${service}-postgres", key = key } }
                  }], service == "billing" ? [for env, key in { MERCADO_PAGO_ACCESS_TOKEN = "access-token", MERCADO_PAGO_WEBHOOK_SECRET = "webhook-secret" } : {
                  name                                                                    = env, valueFrom = { secretKeyRef = { name = "billing-mercado-pago", key = key } }
              }] : [])
              startupProbe   = { httpGet = { scheme = "HTTPS", port = "https", path = "/actuator/health/liveness" }, periodSeconds = 5, timeoutSeconds = 3, failureThreshold = 60 }
              livenessProbe  = { httpGet = { scheme = "HTTPS", port = "https", path = "/actuator/health/liveness" }, periodSeconds = 15, timeoutSeconds = 3, failureThreshold = 4 }
              readinessProbe = { httpGet = { scheme = "HTTPS", port = "https", path = "/actuator/health/readiness" }, periodSeconds = 10, timeoutSeconds = 3, failureThreshold = 3 }
              volumeMounts = [
                { name = "tls", mountPath = "/tls", readOnly = true }, { name = "rds-ca", mountPath = "/rds-ca", readOnly = true },
                { name = "tmp", mountPath = "/tmp" }
              ]
            }]
            volumes = [
              { name = "tls", secret = { secretName = "${name}-tls", defaultMode = 288 } },
              { name = "rds-ca", configMap = { name = "rds-ca" } },
              { name = "tmp", emptyDir = { sizeLimit = "256Mi" } }
            ]
          }
        }
      }
    }
  ]])
}
