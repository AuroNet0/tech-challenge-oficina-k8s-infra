locals {
  auth_migration_user = "${var.infra_contract.rds.application_databases.auth.username}_migrator"
  auth_release_id     = "${substr(var.git_revision, 0, 12)}-${try(substr(split("@sha256:", var.images.auth)[1], 0, 12), "unavailable")}"
  auth_config = {
    AUTH_INTERNAL_PORT         = "8443"
    AUTH_INTERNAL_KEYSTORE     = "/tls/identity.p12"
    AUTH_INTERNAL_TRUSTSTORE   = "/tls/truststore.p12"
    AUTH_INTERNAL_OS_SUBJECT   = "CN=os-service"
    AUTH_INTERNAL_PROBE_HOST   = "auth-internal.${local.namespace}.svc.cluster.local"
    AUTH_DB_HOST               = var.infra_contract.rds.address
    AUTH_DB_PORT               = "5432"
    AUTH_DB_NAME               = var.infra_contract.rds.application_databases.auth.database
    AUTH_DB_SSLMODE            = "verify-full"
    AUTH_DB_SSLROOTCERT        = "/rds-ca/ca.pem"
    AUTH_INTERNAL_DB_POOL_SIZE = "2"
    AUTH_JWT_ISSUER            = local.public_jwt.issuer
    AUTH_JWT_AUDIENCE          = local.public_jwt.audience
    AUTH_JWKS_URI              = local.public_jwt.jwks_uri
  }
  auth_database_env = [for name, key in { AUTH_DB_USER = "username", AUTH_DB_PASSWORD = "password" } : {
    name = name, valueFrom = { secretKeyRef = { name = "auth-postgres", key = key } }
  }]
  auth_manifests = [
    {
      apiVersion = "v1", kind = "ConfigMap"
      metadata   = merge(local.metadata, { name = "auth-internal-config" })
      data       = local.auth_config
    },
    {
      apiVersion = "apps/v1", kind = "Deployment"
      metadata   = merge(local.metadata, { name = "auth-internal" })
      spec = {
        replicas = var.application_replicas
        strategy = { type = "Recreate" }
        selector = { matchLabels = { "app.kubernetes.io/name" = "auth-internal" } }
        template = {
          metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = "auth-internal", "oficina/role" = "business" }) }
          spec = {
            serviceAccountName            = "auth-internal", automountServiceAccountToken = false
            securityContext               = local.pod_security
            terminationGracePeriodSeconds = 45
            containers = [{
              name            = "auth-internal", image = var.images.auth, imagePullPolicy = "IfNotPresent"
              securityContext = local.security_context
              ports           = [{ name = "https", containerPort = 8443 }]
              resources       = { requests = { cpu = "250m", memory = "512Mi" }, limits = { cpu = "1000m", memory = "768Mi" } }
              envFrom         = [{ configMapRef = { name = "auth-internal-config" } }]
              env = concat(local.auth_database_env, [for name in ["AUTH_INTERNAL_KEYSTORE_PASSWORD", "AUTH_INTERNAL_TRUSTSTORE_PASSWORD"] : {
                name = name, valueFrom = { secretKeyRef = { name = "auth-internal-tls", key = "password" } }
              }])
              startupProbe   = { httpGet = { scheme = "HTTPS", port = "https", path = "/health/live" }, periodSeconds = 5, timeoutSeconds = 5, failureThreshold = 60 }
              livenessProbe  = { httpGet = { scheme = "HTTPS", port = "https", path = "/health/live" }, periodSeconds = 15, timeoutSeconds = 5, failureThreshold = 4 }
              readinessProbe = { httpGet = { scheme = "HTTPS", port = "https", path = "/health/ready" }, periodSeconds = 10, timeoutSeconds = 5, failureThreshold = 3 }
              volumeMounts = [
                { name = "tls", mountPath = "/tls", readOnly = true },
                { name = "rds-ca", mountPath = "/rds-ca", readOnly = true },
                { name = "tmp", mountPath = "/tmp" }
              ]
            }]
            volumes = [
              { name = "tls", secret = { secretName = "auth-internal-tls", defaultMode = 288 } },
              { name = "rds-ca", configMap = { name = "rds-ca" } },
              { name = "tmp", emptyDir = { sizeLimit = "128Mi" } }
            ]
          }
        }
      }
    }
  ]
  auth_migration = {
    apiVersion = "batch/v1", kind = "Job"
    metadata   = merge(local.metadata, { name = "auth-migrate-${local.auth_release_id}" })
    spec = {
      completions = 1, parallelism = 1, backoffLimit = 0, activeDeadlineSeconds = 300
      template = {
        metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = "auth-migrate" }) }
        spec = {
          restartPolicy                 = "Never", serviceAccountName = "auth-migrate", automountServiceAccountToken = false
          securityContext               = local.pod_security
          terminationGracePeriodSeconds = 45
          containers = [{
            name            = "migrate", image = var.images.auth
            command         = ["java", "-Duser.home=/home/auth", "-cp", "/app/app.jar", "br.com.fiap.oficina.auth.infrastructure.config.MigrateIdentity"]
            securityContext = local.security_context
            resources       = { requests = { cpu = "250m", memory = "512Mi" }, limits = { cpu = "1000m", memory = "768Mi" } }
            env = concat([for name, value in {
              AUTH_DB_HOST = var.infra_contract.rds.address, AUTH_DB_PORT = "5432"
              AUTH_DB_NAME = var.infra_contract.rds.application_databases.auth.database, AUTH_DB_SSLMODE = "verify-full"
              } : { name   = name, value = value }], [for name, key in { AUTH_DB_USER = "username", AUTH_DB_PASSWORD = "password" } : {
              name         = name, valueFrom = { secretKeyRef = { name = "auth-migration-postgres", key = key } }
            }])
            volumeMounts = [{ name = "ca", mountPath = "/home/auth/.postgresql", readOnly = true }, { name = "tmp", mountPath = "/tmp" }]
          }]
          volumes = [
            { name = "ca", configMap = { name = "rds-ca", items = [{ key = "ca.pem", path = "root.crt" }] } },
            { name = "tmp", emptyDir = { sizeLimit = "128Mi" } }
          ]
        }
      }
    }
  }
}
