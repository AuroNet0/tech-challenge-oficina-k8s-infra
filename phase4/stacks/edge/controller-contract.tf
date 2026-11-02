output "controller_identity_contract" {
  description = "Identidade a instalar pelo executor da Platform; Edge nao instala outro controller nem role administrativa."
  value = {
    installed            = false
    policy_ready_for_aws = false
    implemented_in       = "phase4/stacks/platform/identities.tf"
    policy_module        = "phase4/modules/alb-controller-policy"
    role_name            = "${local.name_prefix}-load-balancer-controller"
    role_arn             = "arn:aws:iam::${var.aws_account_id}:role/${local.name_prefix}-load-balancer-controller"
    cluster_name         = local.name_prefix
    namespace            = "kube-system"
    service_account      = "aws-load-balancer-controller"
    mechanism            = "EKS Pod Identity"
    trust_policy_json = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" }
        Action = ["sts:AssumeRole", "sts:TagSession"]
        Condition = { StringEquals = {
          "aws:RequestTag/eks-cluster-arn"            = "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${local.name_prefix}"
          "aws:RequestTag/kubernetes-namespace"       = "kube-system"
          "aws:RequestTag/kubernetes-service-account" = "aws-load-balancer-controller"
        } }
      }]
    })
    policy_source = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.14.0/docs/install/iam_policy.json"
    required_policy_review = [
      "Restringir escritas a conta/regiao/VPC e cluster exatos; nao aceitar apenas presenca de tag de qualquer cluster.",
      "Edge possui SGs e regras; remover escritas EC2 de security groups apos validar controller com backend SG desabilitado.",
      "Remover permissoes Cognito/WAF/Shield nao usados; limitar ACM ao certificado observado.",
      "Revisar operacoes Describe/Create que exigem Resource *, service-linked role ELB e tags de criacao.",
      "Criar uma unica associacao Pod Identity com session tags habilitadas; simular IAM e validar reconciliacao antes do gate controller_ready."
    ]
    helm_values_overlay = {
      enableShield               = false, enableWaf = false, enableWafv2 = false
      enableBackendSecurityGroup = false
    }
  }
}
