mock_provider "aws" {
  source          = "./tests/aws"
  override_during = plan
}

variables {
  environment_id = "demo-a"
  aws_region     = "us-east-1"
  aws_account_id = "123456789012"
  git_revision   = "8002cbdf341295c86d35727f4e4995b6513a3532"
  infra_contract = jsondecode(file("../platform/tests/fixture.json"))
  platform_contract = {
    environment_id     = "demo-a", aws_region = "us-east-1", aws_account_id = "123456789012"
    namespace          = "tc-oficina-p4-demo-a", vpc_id = "vpc-00000000000000001"
    private_subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"]
    services = {
      os        = { name = "os-service", port = 8443, protocol = "HTTPS" }
      execution = { name = "execution-service", port = 8443, protocol = "HTTPS" }
      billing   = { name = "billing-service", port = 8443, protocol = "HTTPS" }
    }
  }
}

override_module {
  target = module.environment
  outputs = { contract = {
    ready = true, name_prefix = "tc-oficina-p4-demo-a"
    tags  = { EnvironmentId = "demo-a", Phase = "4", Lifecycle = "ephemeral", Shared = "false" }
  } }
}

override_data {
  target = data.aws_subnet.private["0"]
  values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.42.10.0/24", availability_zone = "us-east-1a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.private["1"]
  values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.42.11.0/24", availability_zone = "us-east-1b", map_public_ip_on_launch = false }
}

run "safe_defaults" {
  command = plan
  assert {
    condition     = length(aws_apigatewayv2_route.auth) == 0 && length(aws_apigatewayv2_route.business) == 0 && length(aws_lambda_function.auth) == 0 && length(jsondecode(output.manifests).items) == 0
    error_message = "Nenhuma rota, Lambda ou Ingress sem gates reais."
  }
  assert {
    condition = (
      length(output.route_allowlist.auth) == 2 && length(output.route_allowlist.business) == 40 &&
      alltrue([for route in keys(merge(local.auth_routes, local.business_routes, local.webhook_routes)) :
        !can(regex("internal|actuator|swagger|api-docs|\\+|ANY|\\$default", route))
      ]) && aws_apigatewayv2_api.edge.protocol_type == "HTTP" &&
      output.jwt_contract.issuer == "https://synthetic.execute-api.us-east-1.amazonaws.com" &&
      output.jwt_contract.jwks_uri == "https://synthetic.execute-api.us-east-1.amazonaws.com/.well-known/jwks.json"
    )
    error_message = "Allowlist explicita, JWT derivado e HTTP API obrigatorios."
  }
  assert {
    condition     = aws_vpc_endpoint.secrets.private_dns_enabled && length(aws_security_group.edge) == 4 && length(aws_vpc_security_group_ingress_rule.flow) == 4
    error_message = "Conectividade sem NAT deve manter endpoint privado e SGs restritos."
  }
}

run "reject_foreign_environment" {
  command = plan
  variables { infra_contract = merge(jsondecode(file("../platform/tests/fixture.json")), { environment_id = "demo-b" }) }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_foreign_account" {
  command = plan
  override_data {
    target = data.aws_caller_identity.current
    values = { account_id = "210987654321" }
  }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_foreign_region" {
  command = plan
  variables {
    platform_contract = {
      environment_id     = "demo-a", aws_region = "us-west-2", aws_account_id = "123456789012"
      namespace          = "tc-oficina-p4-demo-a", vpc_id = "vpc-00000000000000001"
      private_subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"]
      services = {
        os        = { name = "os-service", port = 8443, protocol = "HTTPS" }
        execution = { name = "execution-service", port = 8443, protocol = "HTTPS" }
        billing   = { name = "billing-service", port = 8443, protocol = "HTTPS" }
      }
    }
  }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_foreign_subnet" {
  command = plan
  override_data {
    target = data.aws_subnet.private["0"]
    values = { vpc_id = "vpc-00000000000000002", cidr_block = "10.43.10.0/24", availability_zone = "us-east-1a", map_public_ip_on_launch = false }
  }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_auth_without_release" {
  command = plan
  variables { publish_auth = true }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_ingress_without_prerequisites" {
  command = plan
  variables { prepare_alb = true }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_business_without_destinations" {
  command = plan
  variables { publish_business = true }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_unsigned_webhook_gate" {
  command = plan
  variables { publish_webhook = true }
  expect_failures = [terraform_data.edge_guard]
}

run "reject_foreign_listener" {
  command = plan
  variables { alb_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/tc-oficina-p4-demo-b-edge/aaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb" }
  expect_failures = [var.alb_listener_arn]
}

run "reject_foreign_artifact" {
  command = plan
  variables {
    auth_release = {
      bucket              = "tc-oficina-p4-demo-b-artifacts", key = "auth/dd10bbd3a31d2148ce111a996b908a48379bc3ba/auth.jar", version_id = "synthetic-version"
      sha256_base64       = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", revision = "dd10bbd3a31d2148ce111a996b908a48379bc3ba"
      bootstrap_layer_arn = "arn:aws:lambda:us-east-1:123456789012:layer:tc-oficina-p4-demo-a-auth-bootstrap:1", bootstrap_revision = "8002cbdf341295c86d35727f4e4995b6513a3532"
    }
  }
  expect_failures = [var.auth_release]
}

run "render_ingress_and_lambda" {
  command = plan
  variables {
    prepare_alb  = true
    publish_auth = true
    auth_release = {
      bucket              = "tc-oficina-p4-demo-a-artifacts", key = "auth/dd10bbd3a31d2148ce111a996b908a48379bc3ba/auth.jar", version_id = "synthetic-version"
      sha256_base64       = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", revision = "dd10bbd3a31d2148ce111a996b908a48379bc3ba"
      bootstrap_layer_arn = "arn:aws:lambda:us-east-1:123456789012:layer:tc-oficina-p4-demo-a-auth-bootstrap:1", bootstrap_revision = "8002cbdf341295c86d35727f4e4995b6513a3532"
    }
    alb_tls = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    readiness = {
      artifact_verified        = true, bootstrap_layer_verified = true, secrets_verified = true, bootstrap_completed = true
      auth_migration_completed = true, controller_ready = true, workloads_ready = true, network_policy_enforced = true, pki_valid_for_operation = true
    }
  }
  assert {
    condition = (
      length(aws_apigatewayv2_route.auth) == 2 && length(aws_apigatewayv2_route.business) == 0 &&
      aws_lambda_function.auth[0].runtime == "java21" &&
      aws_apigatewayv2_integration.auth[0].payload_format_version == "2.0" &&
      !contains(keys(aws_lambda_function.auth[0].environment[0].variables), "AUTH_DB_PASSWORD") &&
      length(jsondecode(output.manifests).items) == 12 &&
      alltrue([for item in jsondecode(output.manifests).items : item.metadata.labels["oficina/environment"] == "demo-a"])
    )
    error_message = "Render com fixtures deve manter rotas reais, JVM e isolamento, sem senha no state."
  }
}

run "business_ready" {
  command = plan
  variables {
    publish_auth     = true
    prepare_alb      = true
    publish_business = true
    auth_release     = jsondecode(file("tests/release.json"))
    alb_tls          = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    alb_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/tc-oficina-p4-demo-a-edge/aaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb"
    readiness = {
      artifact_verified        = true, bootstrap_layer_verified = true, secrets_verified = true, bootstrap_completed = true
      auth_migration_completed = true, controller_ready = true, workloads_ready = true, network_policy_enforced = true
      pki_valid_for_operation  = true, jwks_ready = true, targets_healthy = true, listener_verified = true
    }
  }
  assert {
    condition = (
      length(aws_apigatewayv2_route.business) == 40 &&
      aws_apigatewayv2_integration.business[0].request_parameters["overwrite:path"] == "$request.path" &&
      aws_apigatewayv2_integration.business[0].tls_config[0].server_name_to_verify == "edge.synthetic.example.org" &&
      aws_apigatewayv2_integration.business[0].connection_type == "VPC_LINK" &&
      aws_apigatewayv2_integration.business[0].payload_format_version == "1.0"
    )
    error_message = "Integracao privada exige listener verificado, TLS e remocao do prefixo de stage."
  }
}

run "reject_observed_public_alb" {
  command = plan
  variables {
    publish_auth     = true
    prepare_alb      = true
    publish_business = true
    auth_release     = jsondecode(file("tests/release.json"))
    alb_tls          = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    alb_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/tc-oficina-p4-demo-a-edge/aaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb"
    readiness = {
      artifact_verified        = true, bootstrap_layer_verified = true, secrets_verified = true, bootstrap_completed = true
      auth_migration_completed = true, controller_ready = true, workloads_ready = true, network_policy_enforced = true
      pki_valid_for_operation  = true, jwks_ready = true, targets_healthy = true, listener_verified = true
    }
  }
  override_data {
    target = data.aws_lb.observed[0]
    values = {
      name            = "tc-oficina-p4-demo-a-edge", internal = false, load_balancer_type = "application"
      vpc_id          = "vpc-00000000000000001", tags = { EnvironmentId = "demo-a" }
      subnets         = ["subnet-00000000000000001", "subnet-00000000000000002"]
      security_groups = ["sg-00000000000000009"]
    }
  }
  expect_failures = [terraform_data.listener_guard]
}

run "isolated_demo_b" {
  command = plan
  variables {
    environment_id = "demo-b"
    infra_contract = merge(jsondecode(file("../platform/tests/fixture.json")), {
      environment_id = "demo-b", name_prefix = "tc-oficina-p4-demo-b"
      tags           = { EnvironmentId = "demo-b", Phase = "4", Lifecycle = "ephemeral", Shared = "false" }
      rds            = merge(jsondecode(file("../platform/tests/fixture.json")).rds, { address = "tc-oficina-p4-demo-b-postgres.test.us-east-1.rds.amazonaws.com" })
    })
    platform_contract = {
      environment_id     = "demo-b", aws_region = "us-east-1", aws_account_id = "123456789012"
      namespace          = "tc-oficina-p4-demo-b", vpc_id = "vpc-00000000000000001"
      private_subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"]
      services = {
        os        = { name = "os-service", port = 8443, protocol = "HTTPS" }
        execution = { name = "execution-service", port = 8443, protocol = "HTTPS" }
        billing   = { name = "billing-service", port = 8443, protocol = "HTTPS" }
      }
    }
  }
  override_module {
    target  = module.environment
    outputs = { contract = { ready = true, name_prefix = "tc-oficina-p4-demo-b", tags = { EnvironmentId = "demo-b", Phase = "4", Lifecycle = "ephemeral", Shared = "false" } } }
  }
  override_data {
    target = data.aws_subnet.private["0"]
    values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.43.10.0/24", availability_zone = "us-east-1a", map_public_ip_on_launch = false }
  }
  override_data {
    target = data.aws_subnet.private["1"]
    values = { vpc_id = "vpc-00000000000000001", cidr_block = "10.43.11.0/24", availability_zone = "us-east-1b", map_public_ip_on_launch = false }
  }
  assert {
    condition     = output.jwt_contract.audience == "tc-oficina-p4-demo-b" && aws_apigatewayv2_api.edge.name == "tc-oficina-p4-demo-b-edge" && !strcontains(aws_iam_role_policy.auth.policy, "demo-a")
    error_message = "demo-b nao deve reutilizar nomes, audience ou credenciais de demo-a."
  }
}

run "catalog_stays_disabled" {
  command = plan
  assert {
    condition     = var.environment_id == "demo-a" && alltrue([for environment in jsondecode(file("../../environments/catalog.json")).environments : !environment.enabled])
    error_message = "Fixtures nao podem habilitar os ambientes reais."
  }
}

run "reject_different_observed_certificate" {
  command = plan
  variables {
    prepare_alb = true
    alb_tls     = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    readiness   = { controller_ready = true, workloads_ready = true, network_policy_enforced = true, pki_valid_for_operation = true }
  }
  override_data {
    target = data.aws_acm_certificate.alb[0]
    values = { arn = "arn:aws:acm:us-east-1:123456789012:certificate/ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee", domain = "edge.synthetic.example.org", status = "ISSUED" }
  }
  expect_failures = [terraform_data.certificate_guard]
}

run "reject_certificate_not_issued" {
  command = plan
  variables {
    prepare_alb = true
    alb_tls     = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    readiness   = { controller_ready = true, workloads_ready = true, network_policy_enforced = true, pki_valid_for_operation = true }
  }
  override_data {
    target = data.aws_acm_certificate.alb[0]
    values = { arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", domain = "edge.synthetic.example.org", status = "EXPIRED" }
  }
  expect_failures = [terraform_data.certificate_guard]
}

run "webhook_only_after_explicit_verification" {
  command = plan
  variables {
    publish_auth     = true
    prepare_alb      = true
    publish_business = true
    publish_webhook  = true
    auth_release     = jsondecode(file("tests/release.json"))
    alb_tls          = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", server_name = "edge.synthetic.example.org" }
    alb_listener_arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/tc-oficina-p4-demo-a-edge/aaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbb"
    readiness = {
      artifact_verified        = true, bootstrap_layer_verified = true, secrets_verified = true, bootstrap_completed = true
      auth_migration_completed = true, controller_ready = true, workloads_ready = true, network_policy_enforced = true
      pki_valid_for_operation  = true, jwks_ready = true, targets_healthy = true, listener_verified = true, mercado_pago_verified = true
    }
  }
  assert {
    condition     = length(aws_apigatewayv2_route.business) == 41 && aws_apigatewayv2_route.business["POST /api/v1/webhooks/mercado-pago"].authorization_type == "NONE"
    error_message = "Somente webhook assinado e verificado deve entrar na allowlist opcional."
  }
}
