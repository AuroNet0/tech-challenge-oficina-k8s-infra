locals {
  lifecycle_updates = [
    {
      Sid       = "ConfigureTaggedNetwork", Effect = "Allow"
      Action    = ["ec2:ModifyVpcAttribute", "ec2:ModifySubnetAttribute", "ec2:AttachInternetGateway", "ec2:CreateRoute", "ec2:AssociateRouteTable", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifyVpcEndpoint", "ec2:CreateLaunchTemplateVersion"]
      Resource  = [for type in ["vpc", "subnet", "security-group", "internet-gateway", "route-table", "launch-template", "vpc-endpoint"] : "arn:aws:ec2:${local.region}:${local.account}:${type}/*"]
      Condition = local.owned
    },
    {
      Sid       = "ConfigureEksManagedClusterSg", Effect = "Allow"
      Action    = ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"]
      Resource  = "arn:aws:ec2:${local.region}:${local.account}:security-group/*"
      Condition = { StringEquals = { "aws:ResourceTag/aws:eks:cluster-name" = local.prefix } }
    },
    {
      Sid       = "KeepNetworkOwnership", Effect = "Deny", Action = ["ec2:CreateTags", "ec2:DeleteTags"], Resource = "arn:aws:ec2:${local.region}:${local.account}:*/*"
      Condition = { "ForAnyValue:StringEquals" = { "aws:TagKeys" = ["EnvironmentId", "Phase", "Lifecycle", "Shared"] }, Null = { "ec2:CreateAction" = "true" } }
    },
    { Sid = "KeepClusterPrivate", Effect = "Allow", Action = "eks:UpdateClusterConfig", Resource = local.cluster, Condition = { Bool = { "eks:endpointPublicAccess" = "false", "eks:endpointPrivateAccess" = "true" } } },
    { Sid = "NeverOpenClusterDirectly", Effect = "Deny", Action = ["eks:CreateCluster", "eks:UpdateClusterConfig"], Resource = "*", Condition = { Bool = { "eks:endpointPublicAccess" = "true" } } },
    { Sid = "NeverDisablePrivateEndpoint", Effect = "Deny", Action = ["eks:CreateCluster", "eks:UpdateClusterConfig"], Resource = "*", Condition = { Bool = { "eks:endpointPrivateAccess" = "false" } } },
    { Sid = "UpdateEksComponents", Effect = "Allow", Action = ["eks:UpdateAddon", "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion", "eks:UpdatePodIdentityAssociation", "eks:TagResource"], Resource = local.eks_arns },
    { Sid = "TemporaryBootstrapElevation", Effect = "Allow", Action = ["eks:AssociateAccessPolicy", "eks:DisassociateAccessPolicy"], Resource = "arn:aws:eks:${local.region}:${local.account}:access-entry/${local.prefix}/*", Condition = { ArnEquals = { "eks:principalArn" = "arn:aws:iam::${local.account}:role/${local.prefix}-provision", "eks:policyArn" = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy" } } },
    { Sid = "UpdateLambdaRelease", Effect = "Allow", Action = ["lambda:UpdateFunctionConfiguration", "lambda:UpdateFunctionCode", "lambda:AddPermission", "lambda:RemovePermission", "lambda:PutFunctionConcurrency"], Resource = local.lambda_arns },
    { Sid = "RdsEncryptionKeyMetadata", Effect = "Allow", Action = "kms:DescribeKey", Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = local.region } } }
  ]
  rotation = var.inventory_approved && length(var.rotate_secret_names) > 0 ? [
    { Sid = "ApprovedSecretRotation", Effect = "Allow", Action = "secretsmanager:PutSecretValue", Resource = [for name in var.rotate_secret_names : var.inventory.secrets[name]], Condition = local.owned }
  ] : []
  deletion = jsondecode(local.destroy_authorized ? jsonencode(concat(
    length(var.inventory.log_groups) > 0 ? [{ Sid = "DeleteInventoriedLogs", Effect = "Allow", Action = "logs:DeleteLogGroup", Resource = [for arn in var.inventory.log_groups : "${arn}:*"] }] : [],
    length(var.inventory.workload_roles) > 0 ? [{ Sid = "DeleteInventoriedWorkloadRoles", Effect = "Allow", Action = ["iam:DeleteRole", "iam:DeleteRolePolicy", "iam:DetachRolePolicy"], Resource = tolist(var.inventory.workload_roles) }] : [],
    length(var.inventory.lambda) > 0 ? [{ Sid = "DeleteInventoriedLambda", Effect = "Allow", Action = ["lambda:DeleteFunction", "lambda:RemovePermission", "lambda:DeleteFunctionConcurrency", "lambda:DeleteLayerVersion"], Resource = tolist(var.inventory.lambda) }] : [],
    length(var.inventory.ecr) > 0 ? [{ Sid = "EmptyAndDeleteInventoriedEcr", Effect = "Allow", Action = ["ecr:BatchDeleteImage", "ecr:DeleteLifecyclePolicy", "ecr:DeleteRepository"], Resource = tolist(var.inventory.ecr) }] : []
  )) : "[]")
  raw_policies = {
    provision = concat(local.provision, local.lifecycle_updates, local.workload_evidence, local.action_journal, local.endpoint_clients, local.action_bucket_evidence, local.action_preflight, local.teardown_api, local.action_provision_external)
    deploy    = concat(local.deploy, local.rotation, local.action_journal, local.endpoint_clients, local.action_bucket_evidence, local.action_deploy, local.action_external_inputs)
    destroy   = concat(local.destroy, local.deletion, local.workload_evidence, local.action_journal, local.endpoint_clients, local.action_bucket_evidence, local.action_destroy, local.discover_partial_secrets)
  }
  effective_policies = { for purpose, statements in local.raw_policies : purpose => [for statement in statements : jsondecode(
    alltrue([for action in flatten([statement.Action]) : startswith(lower(action), "s3:")]) ? jsonencode(merge(statement, {
      Condition = merge(try(statement.Condition, {}), { StringEquals = merge(try(statement.Condition.StringEquals, {}), { "s3:ResourceAccount" = local.account }) })
    })) : jsonencode(statement)
  )] }
  workload_evidence = [{ Sid = "WorkloadIamEvidence", Effect = "Allow", Action = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"], Resource = values(local.workload_roles) }]
}
