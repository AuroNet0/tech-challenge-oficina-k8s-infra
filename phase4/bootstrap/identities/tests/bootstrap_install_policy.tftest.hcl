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
    condition = (
      toset([for statement in jsondecode(output.policy_documents.provision).Statement : statement.Resource if statement.Sid == "StateRead"][0]) ==
      toset([for stack in ["infra", "platform", "edge"] : "arn:aws:s3:::${var.backend_bucket}/phase4/environments/demo-a/${stack}.tfstate"]) &&
      [for statement in jsondecode(output.policy_documents.provision).Statement : statement.Action if statement.Sid == "StateRead"][0] == "s3:GetObject" &&
      [for statement in jsondecode(output.policy_documents.provision).Statement : statement.Action if statement.Sid == "StateWrite"][0] == "s3:PutObject" &&
      toset([for statement in jsondecode(output.policy_documents.provision).Statement : statement.Resource if statement.Sid == "StateWrite"][0]) ==
      toset([for stack in ["infra", "platform", "edge"] : "arn:aws:s3:::${var.backend_bucket}/phase4/environments/demo-a/${stack}.tfstate"])
    )
    error_message = "Provision must retain exact state read/write coverage."
  }
  assert {
    condition = length([for statement in jsondecode(output.policy_documents.provision).Statement : statement if statement.Sid == "StateLock"]) == 1 && alltrue([for statement in jsondecode(output.policy_documents.provision).Statement :
      toset(statement.Action) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]) &&
      toset(statement.Resource) == toset([for stack in ["infra", "platform", "edge"] : "arn:aws:s3:::${var.backend_bucket}/phase4/environments/demo-a/${stack}.tfstate.tflock"]) &&
      statement.Condition.StringEquals["s3:ResourceAccount"] == "446517826146" if statement.Sid == "StateLock"
    ])
    error_message = "Provision locking must cover exact lock objects with resource owner restriction."
  }

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
      output.identity_contract.subjects.provision == "repo:AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946:environment:phase4-demo-a-provision" &&
      alltrue([for name, policy in aws_iam_policy.workload_boundary :
        length(policy.policy) <= 6144 && jsondecode(policy.policy) == jsondecode(var.workload_boundary_documents[name])
      ])
    )
    error_message = "Real boundary documents and immutable subjects must survive provider validation without granting optional installation."
  }
}
