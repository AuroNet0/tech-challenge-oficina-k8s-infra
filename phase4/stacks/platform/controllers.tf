resource "aws_eks_addon" "pod_identity" {
  cluster_name                = var.infra_contract.eks.name
  addon_name                  = "eks-pod-identity-agent"
  addon_version               = var.addon_versions.pod_identity
  resolve_conflicts_on_create = "NONE"
  resolve_conflicts_on_update = "NONE"

  depends_on = [terraform_data.platform_guard]
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = var.infra_contract.eks.name
  addon_name                  = "vpc-cni"
  addon_version               = var.addon_versions.vpc_cni
  preserve                    = true
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "NONE"
  configuration_values        = jsonencode({ enableNetworkPolicy = "true" })

  depends_on = [terraform_data.platform_guard]
}

resource "aws_iam_role" "ebs_csi" {
  name                 = "${local.namespace}-ebs-csi"
  permissions_boundary = lookup(var.workload_permissions_boundaries, "ebs-csi", null)
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Condition = {
        StringEquals = {
          "aws:RequestTag/eks-cluster-arn"            = var.infra_contract.eks.arn
          "aws:RequestTag/kubernetes-namespace"       = "kube-system"
          "aws:RequestTag/kubernetes-service-account" = "ebs-csi-controller-sa"
        }
      }
    }]
  })
  tags = { "eks-cluster-name" = var.infra_contract.eks.name }

  depends_on = [terraform_data.platform_guard]
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverEKSClusterScopedPolicy"
}

resource "aws_eks_pod_identity_association" "ebs_csi" {
  cluster_name    = var.infra_contract.eks.name
  namespace       = "kube-system"
  service_account = "ebs-csi-controller-sa"
  role_arn        = aws_iam_role.ebs_csi.arn

  depends_on = [aws_eks_addon.pod_identity, aws_iam_role_policy_attachment.ebs_csi]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = var.infra_contract.eks.name
  addon_name                  = "aws-ebs-csi-driver"
  addon_version               = var.addon_versions.ebs_csi
  resolve_conflicts_on_create = "NONE"
  resolve_conflicts_on_update = "NONE"
  configuration_values = jsonencode({
    controller = {
      replicaCount    = 1
      extraVolumeTags = module.environment.contract.tags
    }
  })

  depends_on = [aws_eks_pod_identity_association.ebs_csi]
}
