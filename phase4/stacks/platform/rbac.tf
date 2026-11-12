locals {
  executor_group      = "oficina:${var.environment_id}:executor"
  executor_namespaces = [local.namespace, "${local.namespace}-observability"]
  executor_rbac = concat(flatten([for namespace in local.executor_namespaces : [
    {
      apiVersion = "rbac.authorization.k8s.io/v1", kind = "Role"
      metadata   = { name = "phase4-executor", namespace = namespace, labels = local.labels }
      rules = [
        { apiGroups = [""], resources = ["configmaps", "services", "serviceaccounts", "persistentvolumeclaims"], verbs = ["get", "list", "watch", "create", "update", "patch", "delete"] },
        { apiGroups = [""], resources = ["pods", "events", "endpoints"], verbs = ["get", "list", "watch"] },
        { apiGroups = [""], resources = ["pods/log"], verbs = ["get"] },
        { apiGroups = ["apps"], resources = ["deployments", "statefulsets", "replicasets"], verbs = ["get", "list", "watch", "create", "update", "patch", "delete"] },
        { apiGroups = ["batch"], resources = ["jobs"], verbs = ["get", "list", "watch", "create", "update", "patch", "delete"] },
        { apiGroups = ["networking.k8s.io"], resources = ["networkpolicies", "ingresses"], verbs = ["get", "list", "watch", "create", "update", "patch", "delete"] },
        { apiGroups = [""], resources = ["secrets"], verbs = ["create"] },
        { apiGroups = [""], resources = ["secrets"], resourceNames = namespace == local.namespace ? sort(concat(keys(local.secret_contract), ["demo-fixture-sql"])) : ["newrelic-license"], verbs = ["get", "update", "patch", "delete"] }
      ]
    },
    {
      apiVersion = "rbac.authorization.k8s.io/v1", kind = "RoleBinding"
      metadata   = { name = "phase4-executor", namespace = namespace, labels = local.labels }
      roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "phase4-executor" }
      subjects   = [{ apiGroup = "rbac.authorization.k8s.io", kind = "Group", name = local.executor_group }]
    }
    ]]), [
    {
      apiVersion = "rbac.authorization.k8s.io/v1", kind = "ClusterRole"
      metadata   = { name = "${local.namespace}-evidence", labels = local.labels }
      rules = [
        { apiGroups = [""], resources = ["nodes", "persistentvolumes", "namespaces"], verbs = ["get", "list", "watch"] },
        { apiGroups = ["storage.k8s.io"], resources = ["storageclasses"], resourceNames = ["${local.namespace}-gp3"], verbs = ["get"] },
        { apiGroups = ["networking.k8s.io"], resources = ["ingressclasses"], resourceNames = ["${local.namespace}-edge"], verbs = ["get"] }
      ]
    },
    {
      apiVersion = "rbac.authorization.k8s.io/v1", kind = "ClusterRoleBinding"
      metadata   = { name = "${local.namespace}-evidence", labels = local.labels }
      roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "ClusterRole", name = "${local.namespace}-evidence" }
      subjects   = [{ apiGroup = "rbac.authorization.k8s.io", kind = "Group", name = local.executor_group }]
    }
  ])
}

output "executor_rbac" {
  description = "Aplicar somente na fase bootstrap privilegiada; nao permite autoelevacao pelo executor namespaced."
  value       = jsonencode({ apiVersion = "v1", kind = "List", items = local.executor_rbac })
}
