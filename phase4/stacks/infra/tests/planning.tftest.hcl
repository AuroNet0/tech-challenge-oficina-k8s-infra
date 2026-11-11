mock_provider "aws" {
  override_during = plan
  mock_resource "aws_eks_cluster" {
    defaults = {
      certificate_authority = [{ data = "bW9jaw==" }]
      identity              = [{ oidc = [{ issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/MOCK" }] }]
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:446517826146:secret:mock", kms_key_id = "mock", secret_status = "active" }]
    }
  }
}

variables {
  environment_id     = "demo-a"
  aws_region         = "us-east-1"
  aws_account_id     = "446517826146"
  git_revision       = "be519ed9958785336a007d1fd4a323c248365517"
  availability_zones = ["us-east-1a", "us-east-1b"]
  eks_admin_role_arn = "arn:aws:iam::446517826146:role/tc-oficina-p4-demo-a-provision"
  node_count         = 2
  planning_only      = true
}

run "disabled_demo_a_is_plannable_without_attesting_execution_readiness" {
  command = plan
  assert {
    condition = (
      !output.environment_contract.ready && output.environment_contract.planning_ready &&
      aws_eks_node_group.environment.scaling_config[0].desired_size == 2 &&
      !aws_eks_cluster.environment.vpc_config[0].endpoint_public_access
    )
    error_message = "Planning must not enable the environment, change the node contract or open the endpoint."
  }
}

run "disabled_demo_a_still_rejects_apply_inputs" {
  command = plan
  variables { planning_only = false }
  expect_failures = [terraform_data.environment_guard, output.environment_contract]
}

run "planning_does_not_enable_demo_b" {
  command = plan
  variables { environment_id = "demo-b" }
  expect_failures = [terraform_data.environment_guard, output.environment_contract]
}
