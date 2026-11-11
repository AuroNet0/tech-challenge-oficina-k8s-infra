variables {
  environment_id = "demo-a"
  aws_region     = "us-east-1"
  aws_account_id = "123456789012"
  git_revision   = "b2e84c484c6dfb5b9fc8b502d3cc69310f52d4cf"
}

run "preparation_is_blocked" {
  command = plan

  assert {
    condition     = !output.contract.ready && !output.contract.planning_ready && output.contract.backend.bucket == "tech-challenge-oficina-terraform-state-b1cfa326"
    error_message = "O catalogo de preparacao nao pode liberar operacoes remotas."
  }

  assert {
    condition     = output.contract.name_prefix == "tc-oficina-p4-demo-a" && output.contract.tags.Lifecycle == "ephemeral" && output.contract.tags.Shared == "false" && output.contract.tags.GitRevision == var.git_revision
    error_message = "Nomes e tags devem identificar a Fase 4 e a revisao."
  }

  assert {
    condition     = output.contract.backend.state_keys.infra == "phase4/environments/demo-a/infra.tfstate" && output.contract.backend.use_lockfile && output.contract.backend.encrypt
    error_message = "State deve ter chave controlada, criptografia e locking."
  }
}

run "second_environment_is_isolated" {
  command = plan

  variables {
    environment_id = "demo-b"
  }

  assert {
    condition     = output.contract.name_prefix != run.preparation_is_blocked.contract.name_prefix && length(setintersection(toset(values(output.contract.backend.state_keys)), toset(values(run.preparation_is_blocked.contract.backend.state_keys)))) == 0
    error_message = "Ambientes devem ter nomes e states distintos."
  }
}

run "reject_unknown_environment" {
  command = plan

  variables {
    environment_id = "production"
  }

  expect_failures = [var.environment_id]
}

run "reject_region" {
  command = plan

  variables {
    aws_region = "sa-east-1"
  }

  expect_failures = [var.aws_region]
}

run "reject_account_format" {
  command = plan

  variables {
    aws_account_id = "wrong"
  }

  expect_failures = [var.aws_account_id]
}

run "reject_revision" {
  command = plan

  variables {
    git_revision = "main"
  }

  expect_failures = [var.git_revision]
}

run "github_environment_controls_demo_a_without_catalog_edits" {
  command = plan

  variables {
    aws_account_id  = "446517826146"
    approval_source = "github-environment"
  }

  assert {
    condition     = output.contract.ready && !local.environment.enabled
    error_message = "Actions deve delegar autorizacao ao Environment sem reescrever o catalogo."
  }
}

run "github_environment_does_not_enable_demo_b" {
  command = plan

  variables {
    environment_id  = "demo-b"
    aws_account_id  = "446517826146"
    approval_source = "github-environment"
  }

  assert {
    condition     = !output.contract.ready
    error_message = "A autorizacao de demo-a nao pode liberar demo-b."
  }
}
