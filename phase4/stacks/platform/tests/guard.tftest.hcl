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

run "block_unapproved_environment" {
  command         = plan
  expect_failures = [terraform_data.platform_guard, output.environment_contract]
}
