locals {
  catalog     = jsondecode(file("${path.module}/../../environments/catalog.json"))
  environment = lookup(local.catalog.environments, var.environment_id, local.catalog.environments["demo-a"])
  name_prefix = "${local.environment.resource_prefix}-${local.environment.project_name}-p4-${var.environment_id}"

  tags = {
    Project       = local.environment.project_name
    Owner         = local.environment.owner
    EnvironmentId = var.environment_id
    Phase         = "4"
    Lifecycle     = "ephemeral"
    Shared        = "false"
    ManagedBy     = "terraform"
    GitRevision   = var.git_revision
  }

  catalog_valid = (
    local.catalog.schema_version == 1 &&
    (local.environment.enabled == true || local.environment.enabled == false) &&
    can(regex("^[a-z][a-z0-9-]{1,15}$", local.environment.project_name)) &&
    can(regex("^[a-z][a-z0-9-]{1,7}$", local.environment.resource_prefix)) &&
    can(regex("^[a-z][a-z0-9-]{2,62}$", local.environment.owner)) &&
    can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", local.environment.aws_region)) &&
    toset(keys(local.environment.state_keys)) == toset(["infra", "platform", "edge"]) &&
    alltrue([
      for stack, key in local.environment.state_keys :
      key == "phase4/environments/${var.environment_id}/${stack}.tfstate"
    ]) &&
    length(distinct(flatten([
      for environment in values(local.catalog.environments) : values(environment.state_keys)
    ]))) == length(local.catalog.environments) * 3
  )

  planning_ready = (
    local.catalog_valid &&
    local.environment.aws_account_id == var.aws_account_id &&
    can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", local.environment.backend_bucket))
  )
  ready = local.planning_ready && local.environment.enabled
}
