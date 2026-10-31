variables {
  environment_id = "demo-a"
  aws_region     = "us-east-1"
  aws_account_id = "123456789012"
  git_revision   = "b2e84c484c6dfb5b9fc8b502d3cc69310f52d4cf"
}

run "block_unapproved_environment" {
  command = plan

  expect_failures = [output.environment_contract]
}
