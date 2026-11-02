locals {
  network_policies = concat([
    {
      apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
      metadata   = merge(local.metadata, { name = "default-deny" })
      spec       = { podSelector = {}, policyTypes = ["Ingress", "Egress"] }
    },
    {
      apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
      metadata   = merge(local.metadata, { name = "dns" })
      spec = {
        podSelector = {}, policyTypes = ["Egress"]
        egress = [{
          to    = [{ namespaceSelector = { matchLabels = { "kubernetes.io/metadata.name" = "kube-system" } }, podSelector = { matchLabels = { "k8s-app" = "kube-dns" } } }]
          ports = [{ protocol = "UDP", port = 53 }, { protocol = "TCP", port = 53 }]
        }]
      }
    },
    {
      apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
      metadata   = merge(local.metadata, { name = "public-https" })
      spec = {
        podSelector = { matchLabels = { "oficina/role" = "business" } }, policyTypes = ["Egress"]
        egress = [{
          to    = [{ ipBlock = { cidr = "0.0.0.0/0", except = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8"] } }]
          ports = [{ protocol = "TCP", port = 443 }]
        }]
      }
    }
    ], [for service in ["os-service", "billing-service", "auth-internal", "auth-migrate", "postgres-bootstrap"] : {
      apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
      metadata   = merge(local.metadata, { name = "${service}-postgres" })
      spec = {
        podSelector = { matchLabels = { "app.kubernetes.io/name" = service } }, policyTypes = ["Egress"]
        egress = [{
          to    = [for index in [10, 11] : { ipBlock = { cidr = cidrsubnet(var.infra_contract.vpc_cidr, 8, index) } }]
          ports = [{ protocol = "TCP", port = 5432 }]
        }]
      }
      }], flatten([for connection in [
        { source = "os-service", target = "auth-internal", port = 8443 },
        { source = "execution-service", target = "os-service", port = 8443 },
        { source = "billing-service", target = "os-service", port = 8443 },
        { source = "execution-service", target = "mongodb", port = 27017 },
        { source = "os-service", target = "rabbitmq", port = 5672 },
        { source = "execution-service", target = "rabbitmq", port = 5672 },
        { source = "billing-service", target = "rabbitmq", port = 5672 }
        ] : [
        {
          apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
          metadata   = merge(local.metadata, { name = "${connection.source}-to-${connection.target}" })
          spec = {
            podSelector = { matchLabels = { "app.kubernetes.io/name" = connection.source } }, policyTypes = ["Egress"]
            egress      = [{ to = [{ podSelector = { matchLabels = { "app.kubernetes.io/name" = connection.target } } }], ports = [{ protocol = "TCP", port = connection.port }] }]
          }
        },
        {
          apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
          metadata   = merge(local.metadata, { name = "${connection.target}-from-${connection.source}" })
          spec = {
            podSelector = { matchLabels = { "app.kubernetes.io/name" = connection.target } }, policyTypes = ["Ingress"]
            ingress     = [{ from = [{ podSelector = { matchLabels = { "app.kubernetes.io/name" = connection.source } } }], ports = [{ protocol = "TCP", port = connection.port }] }]
          }
        }
  ]]))
}
