variable "external_input_secrets" {
  type    = map(string)
  default = {}
  validation {
    condition = alltrue([for name, arn in var.external_input_secrets :
      contains(["newrelic", "mercado-pago", "e2e-fixture"], name) &&
      can(regex("^arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:/oficina/phase4/${var.environment_id}/inputs/${name}-[A-Za-z0-9]{6}$", arn))
    ])
    error_message = "Somente ARNs exatos das entradas externas New Relic, Mercado Pago de teste e fixture E2E."
  }
}

locals {
  action_journal = [{
    Sid      = "DurableEnvironmentJournal", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"]
    Resource = "arn:aws:s3:::${var.backend_bucket}/${local.state_prefix}/operations/inventory.json"
  }]
  endpoint_clients = [{
    Sid      = "InvokeSelectedEndpointBrokerAndRecovery", Effect = "Allow", Action = ["lambda:InvokeFunction", "lambda:GetFunctionConfiguration"]
    Resource = [for name in ["broker", "recovery"] : "arn:aws:lambda:${local.region}:${local.account}:function:${local.prefix}-endpoint-${name}"]
    }, {
    Sid      = "InspectIndependentRecoverySchedule", Effect = "Allow", Action = "scheduler:GetSchedule"
    Resource = "arn:aws:scheduler:${local.region}:${local.account}:schedule/${local.prefix}-recovery/reconcile"
  }]
  action_bucket_evidence = [{
    Sid      = "BackendConfigurationEvidence", Effect = "Allow"
    Action   = ["s3:GetBucketVersioning", "s3:GetEncryptionConfiguration", "s3:GetBucketPublicAccessBlock"]
    Resource = "arn:aws:s3:::${var.backend_bucket}"
  }]
  action_preflight = [
    {
      Sid       = "ReadAccountRegionalReadiness", Effect = "Allow", Resource = "*"
      Action    = ["freetier:GetAccountPlanState", "eks:DescribeClusterVersions", "eks:ListClusters", "rds:DescribeDBEngineVersions", "rds:DescribeOrderableDBInstanceOptions", "rds:DescribeDBInstances", "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings", "servicequotas:GetServiceQuota", "tag:GetResources", "ecr:DescribeRepositories", "autoscaling:DescribeAutoScalingGroups"]
      Condition = { StringEquals = { "aws:RequestedRegion" = local.region } }
    },
    {
      Sid      = "ReadExecutorTrust", Effect = "Allow", Action = "iam:GetRole"
      Resource = [for purpose in ["provision", "deploy", "destroy"] : "arn:aws:iam::${local.account}:role/${local.prefix}-${purpose}"]
    },
    { Sid = "ReadGithubProvider", Effect = "Allow", Action = "iam:GetOpenIDConnectProvider", Resource = local.oidc_provider }
  ]
  action_deploy = [
    {
      Sid      = "ReadBackCreatedCanonicalSources", Effect = "Allow", Action = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
      Resource = local.secret_patterns, Condition = local.owned
    },
    {
      Sid      = "ArtifactBucketPrivacyEvidence", Effect = "Allow", Action = "s3:GetBucketPublicAccessBlock"
      Resource = "arn:aws:s3:::${local.prefix}-artifacts"
    },
    {
      Sid      = "RecoverPublishedLayers", Effect = "Allow", Action = ["lambda:ListLayerVersions"]
      Resource = "arn:aws:lambda:${local.region}:${local.account}:layer:${local.prefix}-auth-bootstrap"
    }
  ]
  action_destroy = jsondecode(local.destroy_authorized ? jsonencode([
    {
      Sid      = "DiscoverAndDeleteArtifactVersions", Effect = "Allow", Action = ["s3:ListBucketVersions"]
      Resource = "arn:aws:s3:::${local.prefix}-artifacts", Condition = { StringLike = { "s3:prefix" = "auth/*" } }
    },
    {
      Sid      = "DeleteReviewedArtifactVersions", Effect = "Allow", Action = ["s3:GetObjectVersion", "s3:DeleteObjectVersion"]
      Resource = "arn:aws:s3:::${local.prefix}-artifacts/auth/*"
    },
    { Sid = "ListOwnedRepositoryImages", Effect = "Allow", Action = "ecr:ListImages", Resource = local.ecr_arns },
    { Sid = "ListOwnedLayerVersions", Effect = "Allow", Action = "lambda:ListLayerVersions", Resource = "arn:aws:lambda:${local.region}:${local.account}:layer:${local.prefix}-auth-bootstrap" }
  ]) : "[]")
  discover_partial_secrets = [{
    Sid      = "DiscoverCanonicalMetadataAfterInterruptedCreation", Effect = "Allow", Action = "secretsmanager:DescribeSecret"
    Resource = local.secret_patterns, Condition = local.owned
  }]
  teardown_api = var.inventory_approved && length(var.inventory.api_resources) > 0 ? [
    { Sid = "BlockInventoriedApiBeforeTeardown", Effect = "Allow", Action = ["apigateway:GET", "apigateway:PATCH"], Resource = tolist(var.inventory.api_resources) }
  ] : []
  action_external_inputs = length(var.external_input_secrets) > 0 ? [
    { Sid = "ReadApprovedExternalInputs", Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = values(var.external_input_secrets) }
  ] : []
  action_provision_external = contains(keys(var.external_input_secrets), "newrelic") ? [
    { Sid = "ReadApprovedObservabilityLicense", Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = var.external_input_secrets.newrelic }
  ] : []
}
