mock_provider "aws" {
  source          = "./tests/aws"
  override_during = plan
  mock_data "aws_security_group" {
    defaults = { vpc_id = "vpc-00000000000000001", name = "tc-oficina-p4-demo-a-alb", tags = { EnvironmentId = "demo-a" } }
  }
}

variables {
  environment_id = "demo-a"
  aws_account_id = "123456789012"
  aws_region     = "us-east-1"
  git_revision   = "9985d0fa5f5a1883ed5992a19b221ff598558755"
  infra_contract = jsondecode(file("tests/fixture.json"))
  images = {
    os        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-os@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    execution = "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-execution@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    billing   = "123456789012.dkr.ecr.us-east-1.amazonaws.com/tc-oficina-p4-demo-a-billing@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    mongodb   = "docker.io/library/mongo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    rabbitmq  = "docker.io/library/rabbitmq@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    postgres  = "docker.io/library/postgres@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  addon_versions          = { pod_identity = "v1.3.4-eksbuild.1", ebs_csi = "v1.58.0-eksbuild.1", vpc_cni = "v1.20.0-eksbuild.1" }
  new_relic_chart_version = "6.0.0"
}

override_module {
  target  = module.environment
  outputs = { contract = { ready = true, name_prefix = "tc-oficina-p4-demo-a", tags = { EnvironmentId = "demo-a", Phase = "4", Lifecycle = "ephemeral", Shared = "false" } } }
}

run "rbac_without_cluster_admin" {
  command = plan
  assert {
    condition     = length(jsondecode(output.executor_rbac).items) == 6 && !strcontains(output.executor_rbac, "cluster-admin") && !strcontains(output.executor_rbac, "impersonate") && !strcontains(output.executor_rbac, "pods/exec") && length(aws_iam_role.alb_controller) == 0
    error_message = "Executor deve receber RBAC namespaced e evidencia read-only; identidade ALB desabilitada por padrao."
  }
}

run "private_alb_pod_identity" {
  command = plan
  variables {
    alb_controller_identity_enabled = true
    alb_identity_inputs             = { security_group_id = "sg-00000000000000009", certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" }
  }
  assert {
    condition = (
      aws_eks_pod_identity_association.alb_controller[0].namespace == "kube-system" &&
      aws_eks_pod_identity_association.alb_controller[0].service_account == "aws-load-balancer-controller" &&
      jsondecode(aws_iam_role.alb_controller[0].assume_role_policy).Statement[0].Condition.StringEquals["aws:RequestTag/eks-cluster-arn"] == var.infra_contract.eks.arn &&
      !strcontains(output.alb_identity_contract.policy_json, "waf") && !strcontains(output.alb_identity_contract.policy_json, "shield") &&
      !strcontains(output.alb_identity_contract.policy_json, "AuthorizeSecurityGroupIngress") &&
      jsondecode(output.alb_identity_contract.policy_json).Statement[2].Condition.StringEquals["elasticloadbalancing:Scheme"] == "internal"
    )
    error_message = "ALB requer identidade unica, trust exato e policy privada sem WAF/Shield/escritas SG."
  }
}

run "reject_foreign_alb_security_group" {
  command = plan
  variables {
    alb_controller_identity_enabled = true
    alb_identity_inputs             = { security_group_id = "sg-00000000000000009", certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" }
  }
  override_data {
    target = data.aws_security_group.alb[0]
    values = { vpc_id = "vpc-00000000000000002", name = "tc-oficina-p4-demo-b-alb", tags = { EnvironmentId = "demo-b" } }
  }
  expect_failures = [terraform_data.alb_identity_guard]
}
