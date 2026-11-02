variable "environment_id" { type = string }
variable "aws_account_id" { type = string }
variable "aws_region" { type = string }
variable "cluster_name" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "alb_security_group_id" { type = string }
variable "certificate_arn" { type = string }

locals {
  base          = "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}"
  load_balancer = "${local.base}:loadbalancer/app/${var.cluster_name}-edge/*"
  listeners     = ["${local.base}:listener/app/${var.cluster_name}-edge/*/*", "${local.base}:listener-rule/app/${var.cluster_name}-edge/*/*/*"]
  target_groups = "${local.base}:targetgroup/*/*"
  cluster_tag   = "elbv2.k8s.aws/cluster"
  owned         = { StringEquals = { "aws:ResourceTag/${local.cluster_tag}" = var.cluster_name, "aws:ResourceTag/EnvironmentId" = var.environment_id } }
  requested     = { "aws:RequestTag/${local.cluster_tag}" = var.cluster_name, "aws:RequestTag/EnvironmentId" = var.environment_id }
  policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RegionalDiscovery", Effect = "Allow", Resource = "*"
        Action    = ["ec2:DescribeAccountAttributes", "ec2:DescribeAddresses", "ec2:DescribeAvailabilityZones", "ec2:DescribeInternetGateways", "ec2:DescribeVpcs", "ec2:DescribeVpcPeeringConnections", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups", "ec2:DescribeInstances", "ec2:DescribeNetworkInterfaces", "ec2:DescribeTags", "ec2:GetSecurityGroupsForVpc", "ec2:DescribeRouteTables", "elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeLoadBalancerAttributes", "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeListenerCertificates", "elasticloadbalancing:DescribeSSLPolicies", "elasticloadbalancing:DescribeRules", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetGroupAttributes", "elasticloadbalancing:DescribeTargetHealth", "elasticloadbalancing:DescribeTags", "elasticloadbalancing:DescribeTrustStores", "elasticloadbalancing:DescribeListenerAttributes", "elasticloadbalancing:DescribeCapacityReservation"]
        Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
      },
      { Sid = "SelectedCertificate", Effect = "Allow", Action = "acm:DescribeCertificate", Resource = var.certificate_arn },
      {
        Sid = "CreatePrivateAlb", Effect = "Allow", Action = "elasticloadbalancing:CreateLoadBalancer", Resource = local.load_balancer
        Condition = {
          StringEquals                          = merge(local.requested, { "elasticloadbalancing:Scheme" = "internal" })
          "ForAllValues:StringEqualsIgnoreCase" = { "elasticloadbalancing:Subnet" = var.private_subnet_ids, "elasticloadbalancing:SecurityGroup" = [var.alb_security_group_id] }
          Null                                  = { "elasticloadbalancing:Subnet" = "false", "elasticloadbalancing:SecurityGroup" = "false" }
        }
      },
      { Sid = "CreateTaggedTargetGroups", Effect = "Allow", Action = "elasticloadbalancing:CreateTargetGroup", Resource = local.target_groups, Condition = { StringEquals = local.requested } },
      {
        Sid       = "TagsOnCreate", Effect = "Allow", Action = "elasticloadbalancing:AddTags", Resource = [local.load_balancer, local.target_groups]
        Condition = { StringEquals = merge(local.requested, { "elasticloadbalancing:CreateAction" = ["CreateLoadBalancer", "CreateTargetGroup"] }) }
      },
      {
        Sid    = "ManageOwnedAlbAndTargets", Effect = "Allow", Resource = [local.load_balancer, local.target_groups], Condition = local.owned
        Action = ["elasticloadbalancing:ModifyLoadBalancerAttributes", "elasticloadbalancing:DeleteLoadBalancer", "elasticloadbalancing:ModifyTargetGroup", "elasticloadbalancing:ModifyTargetGroupAttributes", "elasticloadbalancing:DeleteTargetGroup", "elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets", "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"]
      },
      {
        Sid       = "OwnedAlbSubnets", Effect = "Allow", Action = "elasticloadbalancing:SetSubnets", Resource = local.load_balancer
        Condition = merge(local.owned, { "ForAllValues:StringEqualsIgnoreCase" = { "elasticloadbalancing:Subnet" = var.private_subnet_ids }, Null = { "elasticloadbalancing:Subnet" = "false" } })
      },
      {
        Sid       = "OwnedAlbSecurityGroup", Effect = "Allow", Action = "elasticloadbalancing:SetSecurityGroups", Resource = local.load_balancer
        Condition = merge(local.owned, { "ForAllValues:StringEqualsIgnoreCase" = { "elasticloadbalancing:SecurityGroup" = [var.alb_security_group_id] }, Null = { "elasticloadbalancing:SecurityGroup" = "false" } })
      },
      { Sid = "CreateHttpsListener", Effect = "Allow", Action = "elasticloadbalancing:CreateListener", Resource = [local.load_balancer, local.listeners[0]], Condition = { StringEquals = { "elasticloadbalancing:ListenerProtocol" = "HTTPS" } } },
      {
        Sid    = "SelectedAlbListenersAndRules", Effect = "Allow", Resource = concat([local.load_balancer], local.listeners)
        Action = ["elasticloadbalancing:DeleteListener", "elasticloadbalancing:CreateRule", "elasticloadbalancing:DeleteRule", "elasticloadbalancing:ModifyListener", "elasticloadbalancing:ModifyListenerAttributes", "elasticloadbalancing:AddListenerCertificates", "elasticloadbalancing:RemoveListenerCertificates", "elasticloadbalancing:ModifyRule", "elasticloadbalancing:SetRulePriorities", "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"]
      },
      { Sid = "KeepOwnershipTags", Effect = "Deny", Action = ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"], Resource = [local.load_balancer, local.target_groups], Condition = { "ForAnyValue:StringEquals" = { "aws:TagKeys" = [local.cluster_tag, "EnvironmentId"] }, Null = { "elasticloadbalancing:CreateAction" = "true" } } }
    ]
  }
}

output "json" { value = jsonencode(local.policy) }
