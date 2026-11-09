mock_provider "aws" {
  override_during = plan
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_iam_openid_connect_provider" {
    defaults = { url = "token.actions.githubusercontent.com", client_id_list = ["sts.amazonaws.com"] }
  }
}

variables {
  environment_id        = "demo-a"
  aws_account_id        = "123456789012"
  aws_region            = "us-east-1"
  backend_bucket        = "synthetic-persistent-state"
  installation_approved = true
  oidc_claims_verified  = true
}

run "persistent_oidc_and_no_opening" {
  command = plan
  assert {
    condition = (
      output.identity_contract.persistent && !output.identity_contract.endpoint_open_allowed &&
      output.identity_contract.state_key == "phase4/bootstrap/identities/demo-a.tfstate" &&
      output.identity_contract.subjects.deploy == "repo:AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946:environment:phase4-demo-a-deploy" &&
      jsondecode(aws_iam_role.executor["deploy"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      !strcontains(aws_iam_role.executor["deploy"].assume_role_policy, "ref:refs/heads/homolog") &&
      !strcontains(output.policy_documents.deploy, "auth-signing") &&
      !strcontains(output.policy_documents.deploy, "UpdateClusterConfig")
    )
    error_message = "OIDC deve usar Environment real e isolamento, sem abertura direta nem assinatura no executor."
  }
  assert {
    condition = alltrue([for statement in jsondecode(output.policy_documents.provision).Statement :
      statement.Resource != "*" && can(statement.Condition.StringEquals["iam:PassedToService"]) if statement.Action == "iam:PassRole"
    ]) && !strcontains(output.policy_documents.destroy, "DeleteSecret") && !strcontains(output.policy_documents.destroy, "DeleteVpc")
    error_message = "PassRole deve ser exato e destroy deve negar sem inventario aprovado."
  }
  assert {
    condition     = jsondecode(output.policy_documents.recovery).Statement[1].Condition.Bool["eks:endpointPublicAccess"] == "false" && jsondecode(output.policy_documents.recovery).Statement[1].Resource == "arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a"
    error_message = "Recuperacao deve somente fechar cluster selecionado."
  }
}

run "blocked_without_installation_approval" {
  command = plan
  variables { installation_approved = false }
  expect_failures = [terraform_data.guard]
}

run "blocked_without_subject_evidence" {
  command = plan
  variables { oidc_claims_verified = false }
  expect_failures = [terraform_data.guard]
}

run "reject_foreign_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "210987654321" }
  }
  expect_failures = [terraform_data.guard]
}

run "reject_foreign_region" {
  command = plan
  variables { aws_region = "us-west-2" }
  expect_failures = [terraform_data.guard]
}

run "reject_foreign_secret" {
  command = plan
  variables { inventory = { secrets = { auth-postgres = "arn:aws:secretsmanager:us-east-1:123456789012:secret:/oficina/phase4/demo-b/auth-postgres-ABC123" } } }
  expect_failures = [var.inventory]
}

run "reject_issuer_secret" {
  command = plan
  variables { inventory = { secrets = { auth-signing = "arn:aws:secretsmanager:us-east-1:123456789012:secret:/oficina/phase4/demo-a/auth-signing-ABC123" } } }
  expect_failures = [var.inventory]
}

run "reject_foreign_named_resource" {
  command = plan
  variables { inventory = { workload_roles = ["arn:aws:iam::123456789012:role/tc-oficina-p4-demo-b-auth-lambda"] } }
  expect_failures = [terraform_data.guard]
}

run "isolated_demo_b" {
  command = plan
  variables { environment_id = "demo-b" }
  assert {
    condition     = !strcontains(output.policy_documents.deploy, "demo-a") && output.identity_contract.subjects.destroy == "repo:AuroNet0@128399450/tech-challenge-oficina-k8s-infra@1355187946:environment:phase4-demo-b-destroy"
    error_message = "demo-b deve ter subject e recursos exclusivos."
  }
}

run "exact_destroy_inventory" {
  command = plan
  variables {
    inventory_approved   = true
    destroy_confirmation = "DESTROY demo-a 123456789012 tc-oficina-p4-demo-a"
    inventory = {
      secrets = { auth-postgres = "arn:aws:secretsmanager:us-east-1:123456789012:secret:/oficina/phase4/demo-a/auth-postgres-ABC123" }
      ec2     = ["arn:aws:ec2:us-east-1:123456789012:vpc/vpc-00000000000000001"]
    }
  }
  assert {
    condition = strcontains(output.policy_documents.destroy, "DeleteSecret") && strcontains(output.policy_documents.destroy, "vpc-00000000000000001") && alltrue([
      for statement in jsondecode(output.policy_documents.destroy).Statement : alltrue([
        for arn in flatten([statement.Resource]) : contains(values(var.inventory.secrets), arn)
      ]) if contains(flatten([statement.Action]), "secretsmanager:DeleteSecret")
    ])
    error_message = "Delete exige ARNs completos; wildcard de criacao nao pode virar autorizacao destrutiva."
  }
}

run "classic_subject_only_when_explicit" {
  command = plan
  variables { subject_format = "classic" }
  assert {
    condition     = output.identity_contract.subjects.provision == "repo:AuroNet0/tech-challenge-oficina-k8s-infra:environment:phase4-demo-a-provision"
    error_message = "Formato classico verificado deve continuar exato e nao usar wildcard."
  }
}

run "reviewed_workload_boundaries" {
  command = plan
  variables {
    inventory_approved          = true
    workload_boundary_documents = { for name in ["eks-cluster", "eks-nodes", "ebs-csi", "load-balancer-controller", "auth-lambda"] : name => "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"eks:DescribeCluster\",\"Resource\":\"arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a\"}]}" }
    inventory = {
      workload_boundaries = { for name in ["eks-cluster", "eks-nodes", "ebs-csi", "load-balancer-controller", "auth-lambda"] : name => "arn:aws:iam::123456789012:policy/tc-oficina-p4-demo-a-${name}-boundary" }
    }
  }
  assert {
    condition = (
      strcontains(output.policy_documents.provision, "iam:PermissionsBoundary") &&
      strcontains(output.policy_documents.provision, "iam:DeleteRolePermissionsBoundary") &&
      alltrue([for policy in aws_iam_policy.executor : length(policy.policy) <= 6144])
    )
    error_message = "Criacao de roles exige boundaries revisadas e policies dentro da quota AWS."
  }
}

run "reject_administrative_boundary" {
  command = plan
  variables { workload_boundary_documents = { auth-lambda = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"*\",\"Resource\":\"*\"}]}" } }
  expect_failures = [var.workload_boundary_documents]
}
