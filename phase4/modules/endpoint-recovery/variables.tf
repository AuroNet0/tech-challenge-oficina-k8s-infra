variable "environment_id" { type = string }
variable "account" { type = string }
variable "region" { type = string }
variable "cluster_arn" { type = string }
variable "recovery_role" { type = string }
variable "package_path" { type = string }
variable "alarm_topic_arn" { type = string }

variable "reconcile_interval_minutes" {
  description = "Intervalo inteiro entre 1 e 5 minutos; 5 para uso academico, 1 quando operacionalmente justificado."
  type        = number
  default     = 5
  nullable    = false
  validation {
    condition = (
      var.reconcile_interval_minutes >= 1 && var.reconcile_interval_minutes <= 5 &&
      floor(var.reconcile_interval_minutes) == var.reconcile_interval_minutes
    )
    error_message = "O reconciliador exige intervalo inteiro entre 1 e 5 minutos."
  }
}
