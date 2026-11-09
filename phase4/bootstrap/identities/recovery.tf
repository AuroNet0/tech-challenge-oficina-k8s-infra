variable "recovery_installation_approved" {
  type    = bool
  default = false
}

variable "recovery_opening_approved" {
  type    = bool
  default = false
}

variable "recovery_verification_approved" {
  type    = bool
  default = false
}

variable "recovery_package_path" {
  type    = string
  default = ""
}

variable "recovery_alarm_topic_arn" {
  type    = string
  default = ""
}

variable "recovery_reconcile_interval_minutes" {
  description = "Intervalo inteiro de 1 a 5 minutos; padrao academico de 5, reduzir com justificativa operacional."
  type        = number
  default     = 5
  nullable    = false
  validation {
    condition = (
      var.recovery_reconcile_interval_minutes >= 1 && var.recovery_reconcile_interval_minutes <= 5 &&
      floor(var.recovery_reconcile_interval_minutes) == var.recovery_reconcile_interval_minutes
    )
    error_message = "O reconciliador exige intervalo inteiro entre 1 e 5 minutos."
  }
}

module "endpoint_recovery" {
  count                          = var.recovery_installation_approved ? 1 : 0
  source                         = "../../modules/endpoint-recovery"
  environment_id                 = var.environment_id
  account                        = local.account
  region                         = local.region
  cluster_arn                    = local.cluster
  recovery_role                  = aws_iam_role.recovery.name
  package_path                   = var.recovery_package_path
  alarm_topic_arn                = var.recovery_alarm_topic_arn
  reconcile_interval_minutes     = var.recovery_reconcile_interval_minutes
  opening_approved               = var.recovery_opening_approved
  recovery_verification_approved = var.recovery_verification_approved
  depends_on                     = [terraform_data.guard, aws_iam_role_policy.recovery]
}

output "endpoint_recovery_preparation" {
  value = {
    owner                  = "phase4/bootstrap/identities"
    state_key              = "phase4/bootstrap/identities/${var.environment_id}.tfstate"
    resources              = module.endpoint_recovery
    endpoint_open_allowed  = false
    operationally_verified = false
  }
}
