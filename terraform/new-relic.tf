resource "helm_release" "new_relic" {
  count = var.enable_new_relic ? 1 : 0

  name             = "newrelic-bundle"
  repository       = "https://helm-charts.newrelic.com"
  chart            = "nri-bundle"
  namespace        = "newrelic"
  create_namespace = true
  timeout          = 1800

  values = [
    yamlencode({
      global = {
        cluster                = "tech-challenge-oficina"
        customSecretName       = "newrelic-license"
        customSecretLicenseKey = "licenseKey"
        lowDataMode            = true
      }

      infrastructure = {
        enabled = true
      }

      "newrelic-infrastructure" = {
        enabled = true

        kubelet = {
          kubelet = {
            resources = {
              limits = {
                memory = "256Mi"
              }
              requests = {
                cpu    = "50m"
                memory = "64Mi"
              }
            }
          }

          agent = {
            resources = {
              limits = {
                memory = "256Mi"
              }
              requests = {
                cpu    = "50m"
                memory = "64Mi"
              }
            }
          }
        }

        ksm = {
          ksm = {
            resources = {
              limits = {
                memory = "256Mi"
              }
              requests = {
                cpu    = "50m"
                memory = "64Mi"
              }
            }
          }

          forwarder = {
            resources = {
              limits = {
                memory = "256Mi"
              }
              requests = {
                cpu    = "50m"
                memory = "64Mi"
              }
            }
          }
        }
      }

      "nri-metadata-injection" = {
        enabled = true

        resources = {
          limits = {
            memory = "64Mi"
          }
          requests = {
            cpu    = "25m"
            memory = "20Mi"
          }
        }
      }

      ksm = {
        enabled = true
      }

      "kube-state-metrics" = {
        enabled = true

        resources = {
          limits = {
            memory = "128Mi"
          }
          requests = {
            cpu    = "25m"
            memory = "48Mi"
          }
        }
      }
    }),
  ]

  depends_on = [
    aws_eks_node_group.tech_challenge_oficina,
  ]
}
