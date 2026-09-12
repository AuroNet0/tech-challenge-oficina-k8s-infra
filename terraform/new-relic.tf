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
      }

      ksm = {
        enabled = true
      }

      "kube-state-metrics" = {
        enabled = true
      }
    }),
  ]

  depends_on = [
    aws_eks_node_group.tech_challenge_oficina,
  ]
}
