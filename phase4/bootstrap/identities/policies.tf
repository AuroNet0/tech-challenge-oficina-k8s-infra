locals {
  canonical_names = toset([
    "auth-migration-postgres", "auth-postgres", "os-postgres", "billing-postgres", "mongodb-admin", "execution-mongodb",
    "mongodb-keyfile", "rabbitmq-cookie", "billing-mercado-pago", "technical-ca", "auth-internal-tls",
    "os-service-tls", "execution-service-tls", "billing-service-tls", "os-service-rabbitmq", "execution-service-rabbitmq", "billing-service-rabbitmq"
  ])
  secret_patterns      = [for name in local.canonical_names : "arn:aws:secretsmanager:${local.region}:${local.account}:secret:/oficina/phase4/${var.environment_id}/${name}-??????"]
  ecr_arns             = [for name in ["auth", "os", "execution", "billing"] : "arn:aws:ecr:${local.region}:${local.account}:repository/${local.prefix}-${name}"]
  rds_arns             = [for type in ["db", "pg", "subgrp"] : "arn:aws:rds:${local.region}:${local.account}:${type}:${local.prefix}-postgres${type == "pg" ? "17" : ""}"]
  eks_arns             = concat([local.cluster], [for type in ["nodegroup", "addon", "access-entry", "podidentityassociation"] : "arn:aws:eks:${local.region}:${local.account}:${type}/${local.prefix}/*"])
  log_arns             = ["arn:aws:logs:${local.region}:${local.account}:log-group:/aws/eks/${local.prefix}/cluster:*", "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/lambda/${local.prefix}-auth:*", "arn:aws:logs:${local.region}:${local.account}:log-group:/oficina/phase4/${var.environment_id}/edge/http-api:*"]
  lambda_arns          = ["arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-auth", "arn:aws:lambda:${local.region}:${local.account}:layer:${local.prefix}-auth-bootstrap", "arn:aws:lambda:${local.region}:${local.account}:layer:${local.prefix}-auth-bootstrap:*"]
  aws_managed_policies = [for policy in ["AmazonEKSClusterPolicy", "AmazonEKSWorkerNodePolicy", "AmazonEKS_CNI_Policy", "AmazonEBSCSIDriverEKSClusterScopedPolicy"] : "arn:aws:iam::aws:policy/${policy}"]
  boundary_ready       = var.inventory_approved && length(var.inventory.workload_boundaries) > 0 && alltrue([for name in keys(var.inventory.workload_boundaries) : contains(keys(var.workload_boundary_documents), name)])
  state_prefix         = "phase4/environments/${var.environment_id}"
  state_objects        = [for stack in ["infra", "platform", "edge"] : "arn:aws:s3:::${var.backend_bucket}/${local.state_prefix}/${stack}.tfstate"]
  common = [
    { Sid = "NoRoleChaining", Effect = "Deny", Action = "sts:AssumeRole", Resource = "*" },
    { Sid = "EcrToken", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = local.region } } },
    { Sid = "ClusterEvidence", Effect = "Allow", Action = ["eks:DescribeCluster", "eks:DescribeUpdate", "eks:ListUpdates"], Resource = local.cluster },
    { Sid = "RepositoryEvidence", Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:ListTagsForResource", "ecr:GetLifecyclePolicy"], Resource = local.ecr_arns },
    { Sid = "StateBucketRegion", Effect = "Allow", Action = "s3:GetBucketLocation", Resource = "arn:aws:s3:::${var.backend_bucket}" },
    { Sid = "StateList", Effect = "Allow", Action = "s3:ListBucket", Resource = "arn:aws:s3:::${var.backend_bucket}", Condition = { StringLike = { "s3:prefix" = [local.state_prefix, "${local.state_prefix}/*"] } } },
    { Sid = "StateRead", Effect = "Allow", Action = "s3:GetObject", Resource = local.state_objects }
  ]
  state_writer = [
    { Sid = "StateWrite", Effect = "Allow", Action = "s3:PutObject", Resource = local.state_objects },
    { Sid = "StateLock", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = [for arn in local.state_objects : "${arn}.tflock"] }
  ]
  evidence = [
    {
      Sid       = "RegionalDiscovery", Effect = "Allow", Resource = "*"
      Action    = ["ec2:DescribeVpcs", "ec2:DescribeVpcAttribute", "ec2:DescribeSubnets", "ec2:DescribeAvailabilityZones", "ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules", "ec2:DescribeNetworkInterfaces", "ec2:DescribeVolumes", "ec2:DescribeInstances", "ec2:DescribeRouteTables", "ec2:DescribeInternetGateways", "ec2:DescribeVpcEndpoints", "ec2:DescribeLaunchTemplates", "ec2:DescribeLaunchTemplateVersions", "ec2:DescribeTags", "ec2:DescribeAccountAttributes", "elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTargetHealth", "elasticloadbalancing:DescribeTags", "eks:DescribeAddonVersions", "logs:DescribeLogGroups", "acm:ListCertificates"]
      Condition = { StringEquals = { "aws:RequestedRegion" = local.region } }
    },
    { Sid = "EksChildrenEvidence", Effect = "Allow", Action = ["eks:DescribeNodegroup", "eks:DescribeAddon", "eks:ListAddons", "eks:ListNodegroups", "eks:DescribeAccessEntry", "eks:ListAccessEntries", "eks:ListAssociatedAccessPolicies", "eks:DescribePodIdentityAssociation", "eks:ListPodIdentityAssociations", "eks:ListTagsForResource"], Resource = local.eks_arns },
    { Sid = "DatabaseEvidence", Effect = "Allow", Action = ["rds:DescribeDBInstances", "rds:DescribeDBSubnetGroups", "rds:DescribeDBParameterGroups", "rds:DescribeDBParameters", "rds:ListTagsForResource"], Resource = local.rds_arns },
    { Sid = "LambdaEvidence", Effect = "Allow", Action = ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:GetPolicy", "lambda:GetFunctionConcurrency", "lambda:GetFunctionCodeSigningConfig", "lambda:GetLayerVersion", "lambda:ListTags", "lambda:ListVersionsByFunction"], Resource = local.lambda_arns },
    { Sid = "LogsEvidence", Effect = "Allow", Action = ["logs:DescribeLogStreams", "logs:FilterLogEvents", "logs:GetLogEvents", "logs:ListTagsForResource"], Resource = local.log_arns }
  ]
  provision = concat(local.common, local.state_writer, local.evidence, [
    { Sid = "CreateTaggedNetwork", Effect = "Allow", Action = ["ec2:CreateVpc", "ec2:CreateSubnet", "ec2:CreateSecurityGroup", "ec2:CreateInternetGateway", "ec2:CreateRouteTable", "ec2:CreateLaunchTemplate", "ec2:CreateVpcEndpoint"], Resource = [for type in ["vpc", "subnet", "security-group", "internet-gateway", "route-table", "launch-template", "vpc-endpoint"] : "arn:aws:ec2:${local.region}:${local.account}:${type}/*"], Condition = local.requested },
    { Sid = "TagNetworkOnCreate", Effect = "Allow", Action = "ec2:CreateTags", Resource = "arn:aws:ec2:${local.region}:${local.account}:*/*", Condition = { StringEquals = { "ec2:CreateAction" = ["CreateVpc", "CreateSubnet", "CreateSecurityGroup", "CreateInternetGateway", "CreateRouteTable", "CreateLaunchTemplate", "CreateVpcEndpoint"], "aws:RequestTag/EnvironmentId" = var.environment_id } } },
    { Sid = "CreateCluster", Effect = "Allow", Action = "eks:CreateCluster", Resource = "*", Condition = merge(local.requested, { Bool = { "eks:endpointPublicAccess" = "false", "eks:endpointPrivateAccess" = "true", "eks:bootstrapClusterCreatorAdminPermissions" = "false" }, StringEquals = merge(local.requested.StringEquals, { "aws:RequestedRegion" = local.region }) }) },
    { Sid = "CreateEksComponents", Effect = "Allow", Action = ["eks:CreateNodegroup", "eks:CreateAddon", "eks:CreatePodIdentityAssociation"], Resource = local.eks_arns },
    { Sid = "CreateExecutorEntries", Effect = "Allow", Action = "eks:CreateAccessEntry", Resource = local.cluster, Condition = { ArnEquals = { "eks:principalArn" = [for purpose in ["provision", "deploy", "destroy"] : "arn:aws:iam::${local.account}:role/${local.prefix}-${purpose}"] } } },
    { Sid = "CreateDatabase", Effect = "Allow", Action = ["rds:CreateDBInstance", "rds:CreateDBSubnetGroup", "rds:CreateDBParameterGroup", "rds:AddTagsToResource", "rds:ModifyDBParameterGroup"], Resource = local.rds_arns },
    { Sid = "RdsManagedSecret", Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"], Resource = "*", Condition = { StringEquals = { "aws:CalledViaLast" = "rds.amazonaws.com" } } },
    { Sid = "CreateEcr", Effect = "Allow", Action = ["ecr:CreateRepository", "ecr:PutLifecyclePolicy", "ecr:TagResource"], Resource = local.ecr_arns },
    { Sid = "CreateLambda", Effect = "Allow", Action = ["lambda:CreateFunction", "lambda:TagResource", "lambda:PutFunctionConcurrency"], Resource = local.lambda_arns },
    { Sid = "CreateNamedApi", Effect = "Allow", Action = "apigateway:POST", Resource = "arn:aws:apigateway:${local.region}::/apis", Condition = { StringEquals = { "apigateway:Request/ApiName" = "${local.prefix}-edge" } } },
    { Sid = "CreateTaggedVpcLink", Effect = "Allow", Action = "apigateway:POST", Resource = "arn:aws:apigateway:${local.region}::/vpclinks", Condition = local.requested },
    { Sid = "ManageLogGroups", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:PutRetentionPolicy", "logs:TagResource", "logs:TagLogGroup", "logs:ListTagsLogGroup"], Resource = local.log_arns },
    { Sid = "LogDeliverySetup", Effect = "Allow", Action = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:ListLogDeliveries", "logs:DescribeResourcePolicies", "logs:PutResourcePolicy"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = local.region } } },
    { Sid = "ServiceLinkedRoles", Effect = "Allow", Action = "iam:CreateServiceLinkedRole", Resource = [for service in ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "rds.amazonaws.com", "elasticloadbalancing.amazonaws.com"] : "arn:aws:iam::${local.account}:role/aws-service-role/${service}/*"], Condition = { StringEquals = { "iam:AWSServiceName" = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "rds.amazonaws.com", "elasticloadbalancing.amazonaws.com"] } } },
    { Sid = "WorkloadRoleRead", Effect = "Allow", Action = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"], Resource = values(local.workload_roles) },
    { Sid = "NoBoundaryRemoval", Effect = "Deny", Action = ["iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary"], Resource = "*" }
    ], [for name, arn in local.workload_roles : {
      Sid       = "Pass${replace(name, "-", "")}", Effect = "Allow", Action = "iam:PassRole", Resource = arn
      Condition = { StringEquals = { "iam:PassedToService" = name == "auth-lambda" ? ["lambda.amazonaws.com"] : name == "eks-nodes" ? ["eks.amazonaws.com", "ec2.amazonaws.com"] : name == "eks-cluster" ? ["eks.amazonaws.com"] : ["pods.eks.amazonaws.com"] } }
      }], jsondecode(local.boundary_ready ? jsonencode(flatten([for name, boundary in var.inventory.workload_boundaries : [
        { Sid = "Create${replace(name, "-", "")}", Effect = "Allow", Action = "iam:CreateRole", Resource = local.workload_roles[name], Condition = { ArnEquals = { "iam:PermissionsBoundary" = boundary } } },
        { Sid = "Configure${replace(name, "-", "")}", Effect = "Allow", Action = ["iam:PutRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:TagRole"], Resource = local.workload_roles[name] },
        { Sid = "Attach${replace(name, "-", "")}", Effect = "Allow", Action = "iam:AttachRolePolicy", Resource = local.workload_roles[name], Condition = { ArnEquals = { "iam:PolicyARN" = local.aws_managed_policies } } }
    ]])) : "[]"), var.inventory_approved && length(var.inventory.ec2) > 0 ? [
    { Sid = "UpdateInventoriedNetwork", Effect = "Allow", Action = ["ec2:ModifyVpcAttribute", "ec2:ModifySubnetAttribute", "ec2:AttachInternetGateway", "ec2:CreateRoute", "ec2:AssociateRouteTable", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifyVpcEndpoint", "ec2:CreateLaunchTemplateVersion", "ec2:CreateTags"], Resource = tolist(var.inventory.ec2) }
    ] : [], var.inventory_approved && length(var.inventory.api_resources) > 0 ? [
    { Sid = "UpdateInventoriedApi", Effect = "Allow", Action = ["apigateway:GET", "apigateway:POST", "apigateway:PATCH", "apigateway:PUT"], Resource = tolist(var.inventory.api_resources) }
  ] : [])
  deploy = concat(local.common, local.evidence, [
    { Sid = "PublishImages", Effect = "Allow", Action = ["ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = local.ecr_arns },
    { Sid = "PublishAuthJar", Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion"], Resource = "arn:aws:s3:::${local.prefix}-artifacts/auth/*" },
    { Sid = "ArtifactBucketRegion", Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:GetBucketVersioning"], Resource = "arn:aws:s3:::${local.prefix}-artifacts" },
    { Sid = "PublishLayerAndCode", Effect = "Allow", Action = ["lambda:PublishLayerVersion", "lambda:UpdateFunctionCode"], Resource = local.lambda_arns },
    { Sid = "CreateCanonicalSecrets", Effect = "Allow", Action = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"], Resource = local.secret_patterns, Condition = { StringEquals = { "aws:RequestTag/EnvironmentId" = var.environment_id, "aws:RequestTag/ManagedBy" = "platform-secrets", "aws:RequestTag/Lifecycle" = "ephemeral" } } }
    ], var.inventory_approved && length(var.inventory.secrets) > 0 ? [
    { Sid = "ReadCanonicalSecrets", Effect = "Allow", Action = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"], Resource = values(var.inventory.secrets), Condition = local.owned }
    ] : [], var.inventory_approved && var.inventory.rds_master_secret_arn != null ? [
    { Sid = "ReadBootstrapMasterOnly", Effect = "Allow", Action = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"], Resource = var.inventory.rds_master_secret_arn }
  ] : [])
  destroy_authorized = var.inventory_approved && var.destroy_confirmation == "DESTROY ${var.environment_id} ${local.account} ${local.prefix}"
  destroy = concat(local.common, local.state_writer, local.evidence, jsondecode(local.destroy_authorized ? jsonencode(concat(
    length(var.inventory.ec2) > 0 ? [{ Sid = "DeleteInventoriedNetwork", Effect = "Allow", Action = ["ec2:DeleteVpc", "ec2:DeleteSubnet", "ec2:DeleteSecurityGroup", "ec2:DetachInternetGateway", "ec2:DeleteInternetGateway", "ec2:DeleteRoute", "ec2:DisassociateRouteTable", "ec2:DeleteRouteTable", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:DeleteVpcEndpoints", "ec2:DeleteLaunchTemplate"], Resource = tolist(var.inventory.ec2) }] : [],
    length(var.inventory.eks_children) > 0 ? [{ Sid = "DeleteInventoriedEks", Effect = "Allow", Action = ["eks:DeleteCluster", "eks:DeleteNodegroup", "eks:DeleteAddon", "eks:DeleteAccessEntry", "eks:DisassociateAccessPolicy", "eks:DeletePodIdentityAssociation"], Resource = tolist(var.inventory.eks_children) }] : [],
    length(var.inventory.api_resources) > 0 ? [{ Sid = "DeleteInventoriedEdge", Effect = "Allow", Action = ["apigateway:GET", "apigateway:DELETE"], Resource = tolist(var.inventory.api_resources) }] : [],
    length(var.inventory.rds) > 0 ? [{ Sid = "DeleteInventoriedRds", Effect = "Allow", Action = ["rds:DeleteDBInstance", "rds:DeleteDBSubnetGroup", "rds:DeleteDBParameterGroup"], Resource = tolist(var.inventory.rds) }] : [],
    length(var.inventory.secrets) > 0 ? [{ Sid = "DeleteExactCanonicalSecrets", Effect = "Allow", Action = ["secretsmanager:DescribeSecret", "secretsmanager:DeleteSecret"], Resource = values(var.inventory.secrets), Condition = local.owned }] : []
  )) : "[]"))
}
