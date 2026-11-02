mock_provider "aws" {
  source = "./tests/aws"
}

variables {
  environment_id          = "demo-a"
  aws_region              = "us-east-1"
  aws_account_id          = "123456789012"
  git_revision            = "d8e0bdd330cd9db89e9f0c0ba6dce2207e70cdf8"
  infra_contract          = jsondecode(file("tests/fixture.json"))
  jwt                     = { issuer = "https://auth.test.example.org", audience = "oficina-demo-a", jwks_uri = "https://auth.test.example.org/.well-known/jwks.json" }
  addon_versions          = { pod_identity = "v1.3.4-eksbuild.1", ebs_csi = "v1.58.0-eksbuild.1", vpc_cni = "v1.20.0-eksbuild.1" }
  new_relic_chart_version = "6.0.0"
  images                  = { "auth" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-auth@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "os" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-os@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "execution" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-execution@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "billing" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-billing@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "mongodb" : "docker.io/library/mongo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "rabbitmq" : "docker.io/library/rabbitmq@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "postgres" : "docker.io/library/postgres@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
}

override_module {
  target = module.environment
  outputs = {
    contract = {
      ready       = true
      name_prefix = "tc-oficina-p4-demo-a"
      tags        = { Project = "oficina", Owner = "tech-challenge-oficina", EnvironmentId = "demo-a", Phase = "4", Lifecycle = "ephemeral", Shared = "false", ManagedBy = "terraform", GitRevision = "d8e0bdd330cd9db89e9f0c0ba6dce2207e70cdf8" }
    }
  }
}
run "render_isolated_platform" {
  command = plan
  assert {
    condition = (
      jsondecode(output.manifests.foundation).items[0].metadata.name == "tc-oficina-p4-demo-a" &&
      jsondecode(output.manifests.foundation).items[1].reclaimPolicy == "Delete" &&
      jsondecode(output.manifests.foundation).items[1].volumeBindingMode == "WaitForFirstConsumer" &&
      jsondecode(output.manifests.data).items[0].spec.replicas == 1 &&
      contains(jsondecode(output.manifests.data).items[0].spec.template.spec.containers[0].command, "--replSet") &&
      output.auth_internal_contract.deployment_declared && !output.edge_contract.listener_ready
    )
    error_message = "Render deve isolar namespace/storage e nao inventar runtime Auth ou ALB."
  }
  assert {
    condition     = alltrue([for resource in jsondecode(output.manifests.applications).items : resource.spec.replicas == 0 if resource.kind == "Deployment"])
    error_message = "Aplicacoes devem permanecer paradas por padrao."
  }
  assert {
    condition = (
      jsondecode(aws_eks_addon.vpc_cni.configuration_values).enableNetworkPolicy == "true" &&
      aws_iam_role_policy_attachment.ebs_csi.policy_arn == "arn:aws:iam::aws:policy/AmazonEBSCSIDriverEKSClusterScopedPolicy" &&
      jsondecode(aws_iam_role.ebs_csi.assume_role_policy).Statement[0].Condition.StringEquals["aws:RequestTag/eks-cluster-arn"] == var.infra_contract.eks.arn
    )
    error_message = "CNI deve habilitar policies e CSI deve ter identidade restrita ao cluster."
  }
}
run "reject_cross_environment" {
  command = plan
  variables {
    infra_contract = merge(jsondecode(file("tests/fixture.json")), { environment_id = "demo-b" })
  }
  expect_failures = [terraform_data.platform_guard]
}
run "reject_observed_foreign_cluster" {
  command = plan
  override_data {
    target = data.aws_eks_cluster.target
    values = { arn = "arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-b" }
  }
  expect_failures = [terraform_data.platform_guard]
}
run "reject_observed_foreign_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "210987654321" }
  }
  expect_failures = [terraform_data.platform_guard]
}
run "reject_start_without_auth_and_bootstrap" {
  command = plan
  variables { application_replicas = 1 }
  expect_failures = [terraform_data.platform_guard]
}
run "reject_mutable_images" {
  command = plan
  variables {
    images = { "os" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-os:latest", "execution" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-execution@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "billing" : "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-billing@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "mongodb" : "docker.io/library/mongo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "rabbitmq" : "docker.io/library/rabbitmq@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "postgres" : "docker.io/library/postgres@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  }
  expect_failures = [var.images]
}

run "foundation_without_public_jwks" {
  command = plan
  variables { jwt = null }
  assert {
    condition     = length(jsondecode(output.manifests.applications).items) == 0 && length(jsondecode(output.manifests.data).items) == 2
    error_message = "Sem JWT real, preparar somente fundacao/dados; nunca aplicacoes com URLs ficticias."
  }
}

run "reject_mercado_pago_without_real_callbacks" {
  command = plan
  variables { mercado_pago = { enabled = true } }
  expect_failures = [var.mercado_pago]
}

run "reject_csi_without_cluster_tag_support" {
  command = plan
  variables {
    addon_versions = { pod_identity = "v1.3.4-eksbuild.1", ebs_csi = "v1.40.0-eksbuild.1", vpc_cni = "v1.20.0-eksbuild.1" }
  }
  expect_failures = [var.addon_versions]
}

run "auth_absent_without_image" {
  command = plan
  variables {
    images = merge(var.images, { auth = null })
  }
  assert {
    condition     = length(jsondecode(output.manifests.auth).items) == 0 && !output.auth_internal_contract.deployment_rendered
    error_message = "Sem imagem Auth, nao renderizar Deployment ficticio."
  }
}

run "reject_missing_database_image" {
  command = plan
  variables {
    images = merge(var.images, { postgres = null })
  }
  expect_failures = [var.images]
}

run "reject_foreign_auth_image" {
  command = plan
  variables {
    images = merge(var.images, { auth = "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-b-auth@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" })
  }
  expect_failures = [var.images]
}

run "reject_mutable_auth_image" {
  command = plan
  variables {
    images = merge(var.images, { auth = "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-auth:latest" })
  }
  expect_failures = [var.images]
}

run "reject_unverified_migration" {
  command = plan
  variables { auth_migration_enabled = true }
  expect_failures = [terraform_data.platform_guard]
}

run "reject_start_before_release_migration" {
  command = plan
  variables {
    application_replicas           = 1
    auth_internal_runtime_approved = true
    auth_image_verified            = true
    bootstrap_completed            = true
    secrets_delivered              = true
    network_policy_enforced        = true
  }
  expect_failures = [terraform_data.platform_guard]
}

run "render_controlled_migration" {
  command = plan
  variables {
    auth_migration_enabled  = true
    auth_image_verified     = true
    bootstrap_completed     = true
    secrets_delivered       = true
    network_policy_enforced = true
  }
  assert {
    condition = (
      jsondecode(output.manifests.migration).items[0].spec.parallelism == 1 &&
      jsondecode(output.manifests.migration).items[0].spec.backoffLimit == 0 &&
      jsondecode(output.manifests.migration).items[0].spec.template.spec.containers[0].command[1] == "-Duser.home=/home/auth" &&
      jsondecode(output.manifests.migration).items[0].spec.template.spec.volumes[0].configMap.items[0].path == "root.crt"
    )
    error_message = "Migrador deve usar CA reconhecida pelo JDBC e uma execucao por release."
  }
}
