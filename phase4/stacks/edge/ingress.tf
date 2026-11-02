locals {
  ingress_objects = jsondecode(!var.prepare_alb || var.alb_tls == null ? "[]" : jsonencode(concat([
    {
      apiVersion = "networking.k8s.io/v1", kind = "IngressClass"
      metadata   = { name = "${local.name_prefix}-edge", labels = local.labels }
      spec       = { controller = "ingress.k8s.aws/alb" }
    }
    ], [for group, routes in local.ingress_route_groups : {
      apiVersion = "networking.k8s.io/v1", kind = "Ingress"
      metadata = {
        name = "edge-${group}", namespace = var.platform_contract.namespace, labels = local.labels
        annotations = {
          "alb.ingress.kubernetes.io/group.name"                          = "${local.name_prefix}-edge"
          "alb.ingress.kubernetes.io/load-balancer-name"                  = "${local.name_prefix}-edge"
          "alb.ingress.kubernetes.io/scheme"                              = "internal"
          "alb.ingress.kubernetes.io/ip-address-type"                     = "ipv4"
          "alb.ingress.kubernetes.io/subnets"                             = join(",", local.subnet_ids)
          "alb.ingress.kubernetes.io/security-groups"                     = aws_security_group.edge["alb"].id
          "alb.ingress.kubernetes.io/manage-backend-security-group-rules" = "false"
          "alb.ingress.kubernetes.io/listen-ports"                        = jsonencode([{ HTTPS = 443 }])
          "alb.ingress.kubernetes.io/certificate-arn"                     = var.alb_tls.certificate_arn
          "alb.ingress.kubernetes.io/ssl-policy"                          = "ELBSecurityPolicy-TLS13-1-2-2021-06"
          "alb.ingress.kubernetes.io/backend-protocol"                    = "HTTPS"
          "alb.ingress.kubernetes.io/target-type"                         = "ip"
          "alb.ingress.kubernetes.io/healthcheck-protocol"                = "HTTPS"
          "alb.ingress.kubernetes.io/healthcheck-port"                    = "traffic-port"
          "alb.ingress.kubernetes.io/healthcheck-path"                    = "/actuator/health/readiness"
          "alb.ingress.kubernetes.io/success-codes"                       = "200"
          "alb.ingress.kubernetes.io/use-regex-path-match"                = "true"
          "alb.ingress.kubernetes.io/conditions.${routes[0].service}-service" = jsonencode([
            { field = "http-request-method", httpRequestMethodConfig = { values = [routes[0].method] } }
          ])
          "alb.ingress.kubernetes.io/tags" = "EnvironmentId=${var.environment_id},Phase=4,Lifecycle=ephemeral,Shared=false"
        }
      }
      spec = {
        ingressClassName = "${local.name_prefix}-edge"
        rules = [{ http = { paths = [for entry in routes : {
          path     = "/^${replace(split(" ", entry.route)[1], "/\\{[^}]+\\}/", "[^/]+")}$"
          pathType = "ImplementationSpecific"
          backend  = { service = { name = var.platform_contract.services[entry.service].name, port = { number = 8443 } } }
        }] } }]
      }
      }], [for service in ["os", "execution", "billing"] : {
      apiVersion = "networking.k8s.io/v1", kind = "NetworkPolicy"
      metadata   = { name = "${service}-from-edge", namespace = var.platform_contract.namespace, labels = local.labels }
      spec = {
        podSelector = { matchLabels = { "app.kubernetes.io/name" = "${service}-service" } }
        policyTypes = ["Ingress"]
        ingress = [{
          from  = [for index in [10, 11] : { ipBlock = { cidr = cidrsubnet(var.environment_id == "demo-a" ? "10.42.0.0/16" : "10.43.0.0/16", 8, index) } }]
          ports = [{ protocol = "TCP", port = 8443 }]
        }]
      }
  }])))
}
