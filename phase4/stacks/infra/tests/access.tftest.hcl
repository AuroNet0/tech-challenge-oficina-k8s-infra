mock_provider "aws" { override_during = plan }

variables {
  environment_id     = "demo-a"
  aws_region         = "us-east-1"
  aws_account_id     = "123456789012"
  git_revision       = "9985d0fa5f5a1883ed5992a19b221ff598558755"
  availability_zones = ["us-east-1a", "us-east-1b"]
  eks_admin_role_arn = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-provision"
  automation_roles = {
    provision = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-provision"
    deploy    = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-deploy"
    destroy   = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-destroy"
  }
}

override_module {
  target  = module.environment
  outputs = { contract = { ready = true, name_prefix = "tc-oficina-p4-demo-a", tags = { EnvironmentId = "demo-a", Phase = "4", Lifecycle = "ephemeral", Shared = "false" } } }
}

run "executor_entries_without_permanent_admin" {
  command = plan
  assert {
    condition     = length(aws_eks_access_policy_association.administrator) == 0 && length(aws_eks_access_entry.executor) == 2 && aws_eks_access_entry.executor["deploy"].kubernetes_groups == toset(["oficina:demo-a:executor"])
    error_message = "Access Entries devem mapear grupo RBAC sem AmazonEKSClusterAdminPolicy permanente."
  }
}

run "explicit_bootstrap_elevation" {
  command = plan
  variables { cluster_bootstrap_access_enabled = true }
  assert {
    condition     = length(aws_eks_access_policy_association.administrator) == 1 && aws_eks_access_policy_association.administrator[0].principal_arn == var.automation_roles.provision
    error_message = "Elevacao deve ser explicita e somente para provision."
  }
}

run "reject_foreign_executor" {
  command = plan
  variables {
    automation_roles = {
      provision = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-provision"
      deploy    = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-b-deploy"
      destroy   = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-destroy"
    }
  }
  expect_failures = [var.automation_roles]
}
