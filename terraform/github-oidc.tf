locals {
  github_actions_oidc_host = "token.actions.githubusercontent.com"
  github_actions_oidc_url  = "https://${local.github_actions_oidc_host}"

  github_actions_api_owner         = "AuroNet0"
  github_actions_api_owner_id      = "128399450"
  github_actions_api_repository    = "tech-challenge-oficina-api"
  github_actions_api_repository_id = "1219271917"
  github_actions_api_environments  = ["homolog", "production"]

  github_actions_auth_owner         = "AuroNet0"
  github_actions_auth_owner_id      = "128399450"
  github_actions_auth_repository    = "tech-challenge-oficina-auth"
  github_actions_auth_repository_id = "1355187289"
  github_actions_auth_environments  = ["homolog", "production"]

  github_actions_database_owner         = "AuroNet0"
  github_actions_database_owner_id      = "128399450"
  github_actions_database_repository    = "tech-challenge-oficina-database-infra"
  github_actions_database_repository_id = "1355188260"
  github_actions_database_environments  = ["homolog", "production"]

  github_actions_k8s_infra_owner         = "AuroNet0"
  github_actions_k8s_infra_owner_id      = "128399450"
  github_actions_k8s_infra_repository    = "tech-challenge-oficina-k8s-infra"
  github_actions_k8s_infra_repository_id = "1355187946"
  github_actions_k8s_infra_environments  = ["homolog", "production"]
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url = local.github_actions_oidc_url

  client_id_list = [
    "sts.amazonaws.com",
  ]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
  ]

  tags = merge(local.common_tags, {
    Name = "github-actions-oidc"
  })
}

resource "aws_iam_role" "github_actions_api_deploy" {
  name        = "tech-challenge-oficina-api-deploy-role"
  description = "Role assumed by GitHub Actions to deploy the Tech Challenge Oficina API."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.github_actions_oidc_host}:aud" = "sts.amazonaws.com"
            "${local.github_actions_oidc_host}:sub" = [
              for environment in local.github_actions_api_environments :
              "repo:${local.github_actions_api_owner}@${local.github_actions_api_owner_id}/${local.github_actions_api_repository}@${local.github_actions_api_repository_id}:environment:${environment}"
            ]
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-deploy-role"
  })
}

resource "aws_iam_role_policy" "github_actions_api_deploy" {
  name = "tech-challenge-oficina-api-deploy-policy"
  role = aws_iam_role.github_actions_api_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcrLogin"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        Sid    = "EcrPushApiImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.tech_challenge_oficina_api.arn
      },
      {
        Sid    = "DescribeEksCluster"
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
        ]
        Resource = aws_eks_cluster.tech_challenge_oficina.arn
      },
      {
        Sid    = "DescribeRdsInstances"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "github_actions_auth_deploy" {
  name        = "tech-challenge-oficina-auth-deploy-role"
  description = "Role assumed by GitHub Actions to deploy the Tech Challenge Oficina Auth Lambda."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.github_actions_oidc_host}:aud" = "sts.amazonaws.com"
            "${local.github_actions_oidc_host}:sub" = [
              for environment in local.github_actions_auth_environments :
              "repo:${local.github_actions_auth_owner}@${local.github_actions_auth_owner_id}/${local.github_actions_auth_repository}@${local.github_actions_auth_repository_id}:environment:${environment}"
            ]
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-auth-deploy-role"
  })
}

resource "aws_iam_role_policy" "github_actions_auth_deploy" {
  name = "tech-challenge-oficina-auth-deploy-policy"
  role = aws_iam_role.github_actions_auth_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageAuthLambda"
        Effect = "Allow"
        Action = [
          "lambda:AddPermission",
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionCodeSigningConfig",
          "lambda:GetPolicy",
          "lambda:ListVersionsByFunction",
          "lambda:PublishVersion",
          "lambda:RemovePermission",
          "lambda:TagResource",
          "lambda:UntagResource",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:tech-challenge-oficina-auth*"
      },
      {
        Sid    = "ManageAuthLambdaExecutionIam"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:CreatePolicy",
          "iam:CreateRole",
          "iam:DeletePolicy",
          "iam:DeleteRole",
          "iam:DeleteRolePolicy",
          "iam:DetachRolePolicy",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:ListPolicyVersions",
          "iam:ListRolePolicies",
          "iam:PassRole",
          "iam:PutRolePolicy",
          "iam:TagPolicy",
          "iam:TagRole",
          "iam:UntagPolicy",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/tech-challenge-oficina-auth*",
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/tech-challenge-oficina-auth*",
        ]
      },
      {
        Sid    = "RestrictPassRoleToLambda"
        Effect = "Deny"
        Action = [
          "iam:PassRole",
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "iam:PassedToService" = "lambda.amazonaws.com"
          }
        }
      },
      {
        Sid    = "ReadVpcAndRdsConfig"
        Effect = "Allow"
        Action = [
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSecurityGroupRules",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcAttribute",
          "ec2:DescribeVpcs",
          "rds:DescribeDBInstances",
        ]
        Resource = "*"
      },
      {
        Sid    = "CreateAuthLambdaSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
        ]
        Resource = aws_vpc.tech_challenge_oficina.arn
      },
      {
        Sid    = "CreateTaggedAuthLambdaSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*",
        ]
        Condition = {
          StringEquals = {
            "aws:RequestTag/Project"     = "tech-challenge-oficina"
            "aws:RequestTag/Environment" = "shared"
          }
        }
      },
      {
        Sid    = "TagAuthLambdaSecurityGroupOnCreate"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*"
        Condition = {
          StringEquals = {
            "ec2:CreateAction"           = "CreateSecurityGroup"
            "aws:RequestTag/Project"     = "tech-challenge-oficina"
            "aws:RequestTag/Environment" = "shared"
            "aws:RequestTag/Name"        = "tech-challenge-oficina-auth-lambda-sg"
          }
        }
      },
      {
        Sid    = "ManageAuthLambdaSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:CreateTags",
          "ec2:DeleteSecurityGroup",
          "ec2:DeleteTags",
          "ec2:RevokeSecurityGroupEgress",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/Project"     = "tech-challenge-oficina"
            "aws:ResourceTag/Environment" = "shared"
            "aws:ResourceTag/Name"        = "tech-challenge-oficina-auth-lambda-sg"
          }
        }
      },
      {
        Sid    = "ManageAuthLambdaLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:ListTagsLogGroup",
          "logs:PutRetentionPolicy",
          "logs:TagLogGroup",
          "logs:UntagLogGroup",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/tech-challenge-oficina-auth*"
      },
      {
        Sid    = "ListAuthTerraformStatePrefix"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326"
        Condition = {
          StringLike = {
            "s3:prefix" = "auth/*"
          }
        }
      },
      {
        Sid    = "ReadWriteAuthTerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/auth/terraform.tfstate"
      },
      {
        Sid    = "ManageAuthTerraformStateLock"
        Effect = "Allow"
        Action = [
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/auth/terraform.tfstate.tflock"
      }
    ]
  })
}

resource "aws_iam_role" "github_actions_database_deploy" {
  name        = "tech-challenge-oficina-database-infra-deploy-role"
  description = "Role assumed by GitHub Actions to deploy the Tech Challenge Oficina database infrastructure."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.github_actions_oidc_host}:aud" = "sts.amazonaws.com"
            "${local.github_actions_oidc_host}:sub" = [
              for environment in local.github_actions_database_environments :
              "repo:${local.github_actions_database_owner}@${local.github_actions_database_owner_id}/${local.github_actions_database_repository}@${local.github_actions_database_repository_id}:environment:${environment}"
            ]
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-database-infra-deploy-role"
  })
}

resource "aws_iam_role_policy" "github_actions_database_deploy" {
  name = "tech-challenge-oficina-database-infra-deploy-policy"
  role = aws_iam_role.github_actions_database_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadNetworkForDatabase"
        Effect = "Allow"
        Action = [
          "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSecurityGroupRules",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcAttribute",
          "ec2:DescribeVpcs",
        ]
        Resource = "*"
      },
      {
        Sid    = "CreateDatabaseSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
        ]
        Resource = aws_vpc.tech_challenge_oficina.arn
      },
      {
        Sid    = "CreateTaggedDatabaseSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:CreateSecurityGroup",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*",
        ]
        Condition = {
          StringEquals = {
            "aws:RequestTag/Project"     = "tech-challenge-oficina"
            "aws:RequestTag/Environment" = "shared"
          }
        }
      },
      {
        Sid    = "TagDatabaseSecurityGroupOnCreate"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*"
        Condition = {
          StringEquals = {
            "ec2:CreateAction"           = "CreateSecurityGroup"
            "aws:RequestTag/Project"     = "tech-challenge-oficina"
            "aws:RequestTag/Environment" = "shared"
            "aws:RequestTag/Name"        = "tech-challenge-oficina-rds-sg"
          }
        }
      },
      {
        Sid    = "ManageDatabaseSecurityGroup"
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:CreateTags",
          "ec2:DeleteSecurityGroup",
          "ec2:DeleteTags",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:security-group/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/Project"     = "tech-challenge-oficina"
            "aws:ResourceTag/Environment" = "shared"
            "aws:ResourceTag/Name"        = "tech-challenge-oficina-rds-sg"
          }
        }
      },
      {
        Sid    = "ReadDatabaseResources"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBSubnetGroups",
          "rds:ListTagsForResource",
        ]
        Resource = "*"
      },
      {
        Sid    = "ManagePostgresDbSubnetGroup"
        Effect = "Allow"
        Action = [
          "rds:AddTagsToResource",
          "rds:CreateDBSubnetGroup",
          "rds:DeleteDBSubnetGroup",
          "rds:ModifyDBSubnetGroup",
          "rds:RemoveTagsFromResource",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:subgrp:tech-challenge-oficina-db-subnet-group"
      },
      {
        Sid    = "CreatePostgresDbInstance"
        Effect = "Allow"
        Action = [
          "rds:CreateDBInstance",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:db:tech-challenge-oficina-postgres",
          "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:subgrp:tech-challenge-oficina-db-subnet-group",
        ]
      },
      {
        Sid    = "ManagePostgresDbInstance"
        Effect = "Allow"
        Action = [
          "rds:AddTagsToResource",
          "rds:DeleteDBInstance",
          "rds:ModifyDBInstance",
          "rds:RemoveTagsFromResource",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:db:tech-challenge-oficina-postgres"
      },
      {
        Sid    = "ListDatabaseInfraTerraformStatePrefix"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326"
        Condition = {
          StringLike = {
            "s3:prefix" = "database-infra/*"
          }
        }
      },
      {
        Sid    = "ReadWriteDatabaseInfraTerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/database-infra/terraform.tfstate"
      },
      {
        Sid    = "ManageDatabaseInfraTerraformStateLock"
        Effect = "Allow"
        Action = [
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/database-infra/terraform.tfstate.tflock"
      }
    ]
  })
}

resource "aws_iam_role" "github_actions_k8s_infra_deploy" {
  name        = "tech-challenge-oficina-k8s-infra-deploy-role"
  description = "Role assumed by GitHub Actions to deploy the Tech Challenge Oficina Kubernetes infrastructure."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github_actions.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.github_actions_oidc_host}:aud" = "sts.amazonaws.com"
            "${local.github_actions_oidc_host}:sub" = [
              for environment in local.github_actions_k8s_infra_environments :
              "repo:${local.github_actions_k8s_infra_owner}@${local.github_actions_k8s_infra_owner_id}/${local.github_actions_k8s_infra_repository}@${local.github_actions_k8s_infra_repository_id}:environment:${environment}"
            ]
          }
        }
      }
    ]
  })

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-k8s-infra-deploy-role"
  })
}

resource "aws_iam_role_policy" "github_actions_k8s_infra_deploy" {
  name = "tech-challenge-oficina-k8s-infra-deploy-policy"
  role = aws_iam_role.github_actions_k8s_infra_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageProjectNetwork"
        Effect = "Allow"
        Action = [
          "ec2:AssociateRouteTable",
          "ec2:AttachInternetGateway",
          "ec2:CreateInternetGateway",
          "ec2:CreateRoute",
          "ec2:CreateRouteTable",
          "ec2:CreateSubnet",
          "ec2:CreateTags",
          "ec2:CreateVpc",
          "ec2:DeleteInternetGateway",
          "ec2:DeleteRoute",
          "ec2:DeleteRouteTable",
          "ec2:DeleteSubnet",
          "ec2:DeleteTags",
          "ec2:DeleteVpc",
          "ec2:DescribeInternetGateways",
          "ec2:DescribeRouteTables",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:DetachInternetGateway",
          "ec2:DisassociateRouteTable",
          "ec2:ModifySubnetAttribute",
          "ec2:ModifyVpcAttribute",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestedRegion" = var.aws_region
          }
        }
      },
      {
        Sid    = "ManageProjectEcrRepository"
        Effect = "Allow"
        Action = [
          "ecr:CreateRepository",
          "ecr:DeleteLifecyclePolicy",
          "ecr:DeleteRepository",
          "ecr:DescribeRepositories",
          "ecr:GetLifecyclePolicy",
          "ecr:ListTagsForResource",
          "ecr:PutLifecyclePolicy",
          "ecr:TagResource",
          "ecr:UntagResource",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/tech-challenge-oficina-api"
      },
      {
        Sid    = "CreateProjectEcrRepository"
        Effect = "Allow"
        Action = [
          "ecr:CreateRepository",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:RequestTag/Project"     = "tech-challenge-oficina"
            "aws:RequestTag/Environment" = "shared"
          }
        }
      },
      {
        Sid    = "ManageProjectEks"
        Effect = "Allow"
        Action = [
          "eks:AssociateAccessPolicy",
          "eks:CreateAccessEntry",
          "eks:CreateAddon",
          "eks:CreateCluster",
          "eks:CreateNodegroup",
          "eks:DeleteAccessEntry",
          "eks:DeleteAddon",
          "eks:DeleteCluster",
          "eks:DeleteNodegroup",
          "eks:DescribeAccessEntry",
          "eks:DescribeAddon",
          "eks:DescribeAddonVersions",
          "eks:DescribeCluster",
          "eks:DescribeNodegroup",
          "eks:DisassociateAccessPolicy",
          "eks:ListAssociatedAccessPolicies",
          "eks:ListTagsForResource",
          "eks:TagResource",
          "eks:UntagResource",
          "eks:UpdateAccessEntry",
          "eks:UpdateAddon",
          "eks:UpdateClusterConfig",
          "eks:UpdateNodegroupConfig",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/tech-challenge-oficina",
          "arn:${data.aws_partition.current.partition}:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:nodegroup/tech-challenge-oficina/tech-challenge-oficina-nodes/*",
          "arn:${data.aws_partition.current.partition}:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:addon/tech-challenge-oficina/metrics-server/*",
          "arn:${data.aws_partition.current.partition}:eks:${var.aws_region}:${data.aws_caller_identity.current.account_id}:access-entry/tech-challenge-oficina/*",
        ]
      },
      {
        Sid    = "ReadEksAccessPolicies"
        Effect = "Allow"
        Action = [
          "eks:DescribeAccessPolicy",
          "eks:ListAccessPolicies",
        ]
        Resource = "*"
      },
      {
        Sid    = "ListK8sInfraTerraformStatePrefix"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326"
        Condition = {
          StringLike = {
            "s3:prefix" = "k8s-infra/*"
          }
        }
      },
      {
        Sid    = "ReadWriteK8sInfraTerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/k8s-infra/terraform.tfstate"
      },
      {
        Sid    = "ManageK8sInfraTerraformStateLock"
        Effect = "Allow"
        Action = [
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::tech-challenge-oficina-terraform-state-b1cfa326/k8s-infra/terraform.tfstate.tflock"
      },
      {
        Sid    = "ManageProjectIam"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:CreateOpenIDConnectProvider",
          "iam:CreatePolicy",
          "iam:CreateRole",
          "iam:DeleteOpenIDConnectProvider",
          "iam:DeletePolicy",
          "iam:DeleteRole",
          "iam:DeleteRolePolicy",
          "iam:DetachRolePolicy",
          "iam:GetOpenIDConnectProvider",
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:ListInstanceProfilesForRole",
          "iam:ListPolicyVersions",
          "iam:ListRolePolicies",
          "iam:PutRolePolicy",
          "iam:TagOpenIDConnectProvider",
          "iam:TagPolicy",
          "iam:TagRole",
          "iam:UntagOpenIDConnectProvider",
          "iam:UntagPolicy",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:UpdateOpenIDConnectProviderThumbprint",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.github_actions_oidc_host}",
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/tech-challenge-oficina-*",
          "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/tech-challenge-oficina-*",
        ]
      },
      {
        Sid    = "PassProjectRolesToEks"
        Effect = "Allow"
        Action = [
          "iam:PassRole",
        ]
        Resource = [
          aws_iam_role.eks_cluster.arn,
          aws_iam_role.eks_nodes.arn,
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "eks.amazonaws.com"
          }
        }
      },
      {
        Sid    = "CreateEksServiceLinkedRole"
        Effect = "Allow"
        Action = [
          "iam:CreateServiceLinkedRole",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/eks.amazonaws.com/AWSServiceRoleForAmazonEKS"
        Condition = {
          StringEquals = {
            "iam:AWSServiceName" = "eks.amazonaws.com"
          }
        }
      },
      {
        Sid    = "ReadAuthLambdaForApiGateway"
        Effect = "Allow"
        Action = [
          "lambda:GetFunction",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.auth_lambda_function_name}"
      },
      {
        Sid    = "ManageApiGatewayAuthLambdaPermission"
        Effect = "Allow"
        Action = [
          "lambda:AddPermission",
          "lambda:GetPolicy",
          "lambda:RemovePermission",
        ]
        Resource = "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.auth_lambda_function_name}"
      },
      {
        Sid    = "ManageProjectHttpApiGateway"
        Effect = "Allow"
        Action = [
          "apigateway:DELETE",
          "apigateway:GET",
          "apigateway:PATCH",
          "apigateway:POST",
          "apigateway:PUT",
          "apigateway:TagResource",
          "apigateway:UntagResource",
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:apigateway:${var.aws_region}::/apis",
          "arn:${data.aws_partition.current.partition}:apigateway:${var.aws_region}::/apis/*",
        ]
      }
    ]
  })
}

resource "aws_eks_access_entry" "github_actions_api_deploy" {
  cluster_name  = aws_eks_cluster.tech_challenge_oficina.name
  principal_arn = aws_iam_role.github_actions_api_deploy.arn
  type          = "STANDARD"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api-deploy-role"
  })
}

resource "aws_eks_access_policy_association" "github_actions_api_deploy_edit" {
  cluster_name  = aws_eks_cluster.tech_challenge_oficina.name
  principal_arn = aws_iam_role.github_actions_api_deploy.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [
    aws_eks_access_entry.github_actions_api_deploy,
  ]
}

resource "aws_eks_access_entry" "github_actions_k8s_infra_deploy" {
  cluster_name  = aws_eks_cluster.tech_challenge_oficina.name
  principal_arn = aws_iam_role.github_actions_k8s_infra_deploy.arn
  type          = "STANDARD"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-k8s-infra-deploy-role"
  })
}

resource "aws_eks_access_policy_association" "github_actions_k8s_infra_deploy_cluster_admin" {
  cluster_name  = aws_eks_cluster.tech_challenge_oficina.name
  principal_arn = aws_iam_role.github_actions_k8s_infra_deploy.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [
    aws_eks_access_entry.github_actions_k8s_infra_deploy,
  ]
}
