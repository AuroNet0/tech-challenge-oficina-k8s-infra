locals {
  foundation = concat([
    {
      apiVersion = "v1", kind = "Namespace"
      metadata = {
        name = local.namespace
        labels = merge(local.labels, {
          "pod-security.kubernetes.io/enforce"         = "restricted"
          "pod-security.kubernetes.io/enforce-version" = "v${var.infra_contract.eks.version}"
        })
      }
    },
    {
      apiVersion           = "storage.k8s.io/v1", kind = "StorageClass"
      metadata             = { name = "${local.namespace}-gp3", labels = local.labels }
      provisioner          = "ebs.csi.aws.com"
      reclaimPolicy        = "Delete"
      volumeBindingMode    = "WaitForFirstConsumer"
      allowVolumeExpansion = true
      parameters = {
        type                 = "gp3", encrypted = "true", "csi.storage.k8s.io/fstype" = "ext4"
        "tagSpecification_1" = "EnvironmentId=${var.environment_id}"
        "tagSpecification_2" = "Lifecycle=ephemeral"
        "tagSpecification_3" = "Phase=4"
      }
    },
    {
      apiVersion = "v1", kind = "ConfigMap"
      metadata   = merge(local.metadata, { name = "platform-scripts" })
      data = {
        "mongo-init.js"       = file("${path.module}/scripts/mongo-init.js")
        "mongo-ready.js"      = file("${path.module}/scripts/mongo-ready.js")
        "bootstrap.sql"       = file("${path.module}/scripts/bootstrap.sql")
        "verify-postgres.sql" = file("${path.module}/scripts/verify-postgres.sql")
      }
    },
    {
      apiVersion = "v1", kind = "ConfigMap"
      metadata   = merge(local.metadata, { name = "database-config" })
      data = merge({
        PGHOST                 = var.infra_contract.rds.address, PGPORT = "5432"
        PGDATABASE             = var.infra_contract.rds.bootstrap_database, PGSSLMODE = "verify-full"
        PGSSLROOTCERT          = "/rds-ca/ca.pem"
        AUTH_MIGRATION_DB_USER = local.auth_migration_user
        }, merge([for service, database in var.infra_contract.rds.application_databases : {
          "${upper(service)}_DB_NAME" = database.database
          "${upper(service)}_DB_USER" = database.username
      }]...))
    },
    {
      apiVersion = "v1", kind = "ConfigMap"
      metadata   = merge(local.metadata, { name = "rabbitmq-config" })
      data = {
        "rabbitmq.conf" = join("\n", [
          "listeners.tcp.default = 5672", "management.tcp.port = 15672", "loopback_users.guest = true",
          "definitions.import_backend = local_filesystem", "definitions.local.path = /definitions/definitions.json",
          "vm_memory_high_watermark.absolute = 384MiB", "disk_free_limit.absolute = 1GB",
        ])
      }
    }
    ], [for service in ["os-service", "execution-service", "billing-service", "auth-internal", "auth-migrate", "mongodb", "rabbitmq", "postgres-bootstrap"] : {
      apiVersion                          = "v1", kind = "ServiceAccount"
      metadata                            = merge(local.metadata, { name = service })
      automountServiceAccountToken        = false
      }], [for service, port in { mongodb = 27017, rabbitmq = 5672, auth-internal = 8443 } : {
      apiVersion                          = "v1", kind = "Service"
      metadata                            = merge(local.metadata, { name = service })
      spec = merge({
        type  = "ClusterIP", selector = { "app.kubernetes.io/name" = service }
        ports = [merge({ name = { mongodb = "mongo", rabbitmq = "amqp", auth-internal = "https" }[service], port = port, targetPort = port }, service == "auth-internal" ? { appProtocol = "https" } : {})]
      }, contains(["mongodb", "rabbitmq"], service) ? { clusterIP = "None" } : {}, contains(["mongodb", "rabbitmq"], service) ? { publishNotReadyAddresses = true } : {})
  }])

  mongodb = {
    apiVersion = "apps/v1", kind = "StatefulSet"
    metadata   = merge(local.metadata, { name = "mongodb" })
    spec = {
      serviceName                          = "mongodb", replicas = 1
      selector                             = { matchLabels = { "app.kubernetes.io/name" = "mongodb" } }
      persistentVolumeClaimRetentionPolicy = { whenDeleted = "Delete", whenScaled = "Retain" }
      template = {
        metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = "mongodb" }) }
        spec = {
          serviceAccountName            = "mongodb", automountServiceAccountToken = false
          securityContext               = merge(local.pod_security, { runAsUser = 999, runAsGroup = 999, fsGroup = 999 })
          terminationGracePeriodSeconds = 60
          initContainers = [{
            name            = "keyfile", image = var.images.mongodb
            command         = ["sh", "-ec", "cp /source/keyfile /keyfile/keyfile; chmod 400 /keyfile/keyfile"]
            securityContext = local.security_context
            resources       = { requests = { cpu = "10m", memory = "32Mi" }, limits = { cpu = "100m", memory = "64Mi" } }
            volumeMounts    = [{ name = "key-source", mountPath = "/source", readOnly = true }, { name = "keyfile", mountPath = "/keyfile" }]
          }]
          containers = [{
            name            = "mongodb", image = var.images.mongodb, imagePullPolicy = "IfNotPresent"
            command         = ["mongod", "--replSet", "rs0", "--auth", "--keyFile", "/keyfile/keyfile", "--bind_ip_all", "--wiredTigerCacheSizeGB", "0.25"]
            ports           = [{ containerPort = 27017, name = "mongo" }]
            securityContext = local.security_context
            resources       = { requests = { cpu = "300m", memory = "768Mi" }, limits = { cpu = "1000m", memory = "1280Mi" } }
            env = concat([
              { name = "HOME", value = "/tmp" },
              { name = "MONGO_DATABASE", value = "oficina_execution" },
              { name = "MONGO_MEMBER", value = "mongodb-0.mongodb.${local.namespace}.svc.cluster.local:27017" }
              ], [for name, key in { MONGO_ROOT_USERNAME = "username", MONGO_ROOT_PASSWORD = "password" } : {
                name                                     = name, valueFrom = { secretKeyRef = { name = "mongodb-admin", key = key } }
                }], [for name, key in { MONGO_USERNAME   = "username", MONGO_PASSWORD = "password" } : {
                name                                     = name, valueFrom = { secretKeyRef = { name = "execution-mongodb", key = key } }
            }])
            lifecycle      = { postStart = { exec = { command = ["sh", "-ec", "for attempt in $(seq 1 90); do mongosh --quiet /scripts/mongo-init.js && exit 0; sleep 2; done; exit 1"] } } }
            startupProbe   = { exec = { command = ["mongosh", "--quiet", "/scripts/mongo-ready.js"] }, periodSeconds = 5, failureThreshold = 60, timeoutSeconds = 4 }
            readinessProbe = { exec = { command = ["mongosh", "--quiet", "/scripts/mongo-ready.js"] }, periodSeconds = 10, timeoutSeconds = 4 }
            livenessProbe  = { tcpSocket = { port = 27017 }, periodSeconds = 15, failureThreshold = 4 }
            volumeMounts = [
              { name = "data", mountPath = "/data/db" }, { name = "tmp", mountPath = "/tmp" },
              { name = "keyfile", mountPath = "/keyfile", readOnly = true }, { name = "scripts", mountPath = "/scripts", readOnly = true }
            ]
          }]
          volumes = [
            { name = "key-source", secret = { secretName = "mongodb-keyfile", defaultMode = 288 } },
            { name = "keyfile", emptyDir = { medium = "Memory" } }, { name = "tmp", emptyDir = { sizeLimit = "128Mi" } },
            { name = "scripts", configMap = { name = "platform-scripts" } }
          ]
        }
      }
      volumeClaimTemplates = [{
        metadata = { name = "data", labels = local.labels }
        spec     = { accessModes = ["ReadWriteOnce"], storageClassName = "${local.namespace}-gp3", resources = { requests = { storage = "5Gi" } } }
      }]
    }
  }

  rabbitmq = {
    apiVersion = "apps/v1", kind = "StatefulSet"
    metadata   = merge(local.metadata, { name = "rabbitmq" })
    spec = {
      serviceName                          = "rabbitmq", replicas = 1
      selector                             = { matchLabels = { "app.kubernetes.io/name" = "rabbitmq" } }
      persistentVolumeClaimRetentionPolicy = { whenDeleted = "Delete", whenScaled = "Retain" }
      template = {
        metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = "rabbitmq" }) }
        spec = {
          serviceAccountName            = "rabbitmq", automountServiceAccountToken = false
          securityContext               = merge(local.pod_security, { runAsUser = 999, runAsGroup = 999, fsGroup = 999 })
          terminationGracePeriodSeconds = 90
          initContainers = [{
            name            = "cookie", image = var.images.rabbitmq
            command         = ["sh", "-ec", "cp /cookie/cookie /data/.erlang.cookie; chmod 600 /data/.erlang.cookie"]
            securityContext = local.security_context
            resources       = { requests = { cpu = "10m", memory = "32Mi" }, limits = { cpu = "100m", memory = "64Mi" } }
            volumeMounts    = [{ name = "data", mountPath = "/data" }, { name = "cookie", mountPath = "/cookie", readOnly = true }]
          }]
          containers = [{
            name            = "rabbitmq", image = var.images.rabbitmq, command = ["rabbitmq-server"]
            securityContext = local.security_context
            env = [
              { name = "RABBITMQ_NODENAME", value = "rabbit@rabbitmq-0.rabbitmq.${local.namespace}.svc.cluster.local" },
              { name = "RABBITMQ_USE_LONGNAME", value = "true" },
              { name = "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS", value = "+S 2:2 +SDcpu 1 +SDio 1" }
            ]
            ports          = [{ name = "amqp", containerPort = 5672 }]
            resources      = { requests = { cpu = "150m", memory = "384Mi" }, limits = { cpu = "1000m", memory = "768Mi" } }
            startupProbe   = { exec = { command = ["rabbitmq-diagnostics", "-q", "ping"] }, periodSeconds = 5, failureThreshold = 60, timeoutSeconds = 5 }
            readinessProbe = { exec = { command = ["rabbitmq-diagnostics", "-q", "check_running"] }, periodSeconds = 10, timeoutSeconds = 5 }
            livenessProbe  = { exec = { command = ["rabbitmq-diagnostics", "-q", "ping"] }, periodSeconds = 30, failureThreshold = 4, timeoutSeconds = 5 }
            volumeMounts = [
              { name = "data", mountPath = "/var/lib/rabbitmq" }, { name = "tmp", mountPath = "/tmp" },
              { name = "config", mountPath = "/etc/rabbitmq/rabbitmq.conf", subPath = "rabbitmq.conf", readOnly = true },
              { name = "definitions", mountPath = "/definitions", readOnly = true }
            ]
          }]
          volumes = [
            { name = "cookie", secret = { secretName = "rabbitmq-cookie", defaultMode = 288 } },
            { name = "tmp", emptyDir = { sizeLimit = "128Mi" } },
            { name = "config", configMap = { name = "rabbitmq-config" } },
            { name = "definitions", secret = { secretName = "rabbitmq-definitions" } }
          ]
        }
      }
      volumeClaimTemplates = [{
        metadata = { name = "data", labels = local.labels }
        spec     = { accessModes = ["ReadWriteOnce"], storageClassName = "${local.namespace}-gp3", resources = { requests = { storage = "5Gi" } } }
      }]
    }
  }

  postgres_bootstrap = {
    apiVersion = "batch/v1", kind = "Job"
    metadata   = merge(local.metadata, { name = "postgres-bootstrap-${substr(var.git_revision, 0, 12)}" })
    spec = {
      backoffLimit = 1, activeDeadlineSeconds = 300, ttlSecondsAfterFinished = 600
      template = {
        metadata = { labels = merge(local.labels, { "app.kubernetes.io/name" = "postgres-bootstrap" }) }
        spec = {
          restartPolicy   = "Never", serviceAccountName = "postgres-bootstrap", automountServiceAccountToken = false
          securityContext = local.pod_security
          containers = [{
            name = "bootstrap", image = var.images.postgres
            command = ["sh", "-ec", join("; ", [
              "psql -Xq --set=ON_ERROR_STOP=1 --file=/scripts/bootstrap.sql >/dev/null",
              "PGUSER=\"$AUTH_DB_USER\" PGPASSWORD=\"$AUTH_DB_PASSWORD\" PGDATABASE=\"$AUTH_DB_NAME\" psql -Xq --set=ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null 2>&1",
              "PGUSER=\"$OS_DB_USER\" PGPASSWORD=\"$OS_DB_PASSWORD\" PGDATABASE=\"$OS_DB_NAME\" psql -Xq --set=ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null 2>&1",
              "PGUSER=\"$BILLING_DB_USER\" PGPASSWORD=\"$BILLING_DB_PASSWORD\" PGDATABASE=\"$BILLING_DB_NAME\" psql -Xq --set=ON_ERROR_STOP=1 -c 'SELECT 1' >/dev/null 2>&1"
            ])]
            securityContext = local.security_context
            resources       = { requests = { cpu = "100m", memory = "128Mi" }, limits = { cpu = "500m", memory = "256Mi" } }
            envFrom         = [{ configMapRef = { name = "database-config" } }]
            env = concat([{ name = "AUTH_MIGRATION_DB_PASSWORD", valueFrom = { secretKeyRef = { name = "auth-migration-postgres", key = "password" } } }], [for name, key in { PGUSER = "username", PGPASSWORD = "password" } : {
              name = name, valueFrom = { secretKeyRef = { name = "postgres-bootstrap-admin", key = key } }
              }], [for service in ["auth", "os", "billing"] : {
              name = "${upper(service)}_DB_PASSWORD", valueFrom = { secretKeyRef = { name = "${service}-postgres", key = "password" } }
            }])
            volumeMounts = [{ name = "scripts", mountPath = "/scripts", readOnly = true }, { name = "ca", mountPath = "/rds-ca", readOnly = true }, { name = "tmp", mountPath = "/tmp" }]
          }]
          volumes = [{ name = "scripts", configMap = { name = "platform-scripts" } }, { name = "ca", configMap = { name = "rds-ca" } }, { name = "tmp", emptyDir = {} }]
        }
      }
    }
  }
}
