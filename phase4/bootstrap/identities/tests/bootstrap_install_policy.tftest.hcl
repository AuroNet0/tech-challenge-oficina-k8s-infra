mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "446517826146" } }
  mock_data "aws_iam_openid_connect_provider" {
    defaults = { url = "token.actions.githubusercontent.com", client_id_list = ["sts.amazonaws.com"] }
  }
}

variables {
  environment_id                        = "demo-a"
  aws_account_id                        = "446517826146"
  aws_region                            = "us-east-1"
  backend_bucket                        = "tech-challenge-oficina-terraform-state-b1cfa326"
  installation_approved                 = true
  oidc_claims_verified                  = true
  subject_format                        = jsondecode(file("approved-inputs.json")).subject_format
  workload_boundary_documents           = jsondecode(file("approved-inputs.json")).workload_boundary_documents
  artifact_bucket_installation_approved = false
  recovery_installation_approved        = false
}

run "console_policy_matches_base_bootstrap_resources" {
  command = plan

  assert {
    condition = toset([for statement in jsondecode(file("${path.module}/console/demo-a-bootstrap-install-policy.json")).Statement : statement.Resource if statement.Sid == "ManageDeclaredPolicies"][0]) == toset(concat(
      [for key, policy in aws_iam_policy.executor : "arn:aws:iam::446517826146:policy/${policy.name}"],
      [for key, policy in aws_iam_policy.workload_boundary : "arn:aws:iam::446517826146:policy/${policy.name}"]
    ))
    error_message = "Managed policy ARNs changed: review the Console inline policy, do not expand to wildcard resources."
  }

  assert {
    condition = toset([for statement in jsondecode(file("${path.module}/console/demo-a-bootstrap-install-policy.json")).Statement : statement.Resource if statement.Sid == "ManageDeclaredRoles"][0]) == toset(concat(
      [for purpose, role in aws_iam_role.executor : "arn:aws:iam::446517826146:role/${role.name}"],
      ["arn:aws:iam::446517826146:role/${aws_iam_role.recovery.name}"]
    ))
    error_message = "Role ARNs changed: separately authorize any additional bootstrap resources."
  }

  assert {
    condition = (
      length(aws_iam_policy.executor) == 13 && length(aws_iam_policy.workload_boundary) == 5 &&
      length(aws_s3_bucket.artifacts) == 0 && length(module.endpoint_recovery) == 0 &&
      aws_iam_role_policy.recovery.name == "close-selected-endpoint"
    )
    error_message = "Base policy must not authorize optional artifact/recovery installation."
  }

  assert {
    condition = (
      output.identity_contract.subjects.provision == "repo:AuroNet0/tech-challenge-oficina-k8s-infra:environment:phase4-demo-a-provision" &&
      alltrue([for name, policy in aws_iam_policy.workload_boundary :
        length(policy.policy) <= 6144 && jsondecode(policy.policy) == jsondecode(var.workload_boundary_documents[name])
      ])
    )
    error_message = "Real boundary documents and classic subjects must survive provider validation without granting optional installation."
  }
}
