variable "aws_region" {
  description = "AWS region to be used by the Terraform AWS provider."
  type        = string
  default     = "us-east-1"
}

variable "api_backend_url" {
  description = "Public base URL of the API Load Balancer on EKS."
  type        = string
  default     = null

  validation {
    condition     = !var.enable_api_gateway || var.api_backend_url != null
    error_message = "api_backend_url must be set when enable_api_gateway is true."
  }
}

variable "enable_api_gateway" {
  description = "Whether to create the HTTP API Gateway resources."
  type        = bool
  default     = false
}

variable "enable_new_relic" {
  description = "Whether to install the New Relic Kubernetes observability integration."
  type        = bool
  default     = false
}

variable "new_relic_account_id" {
  description = "New Relic account ID used to create dashboards, alerts, and synthetics resources."
  type        = number
  default     = null

  validation {
    condition     = !var.enable_new_relic || var.new_relic_account_id != null
    error_message = "new_relic_account_id must be set when enable_new_relic is true."
  }
}

variable "new_relic_api_key" {
  description = "New Relic API key used by the provider."
  type        = string
  default     = ""
  sensitive   = true
}

variable "new_relic_api_base_url" {
  description = "Public base URL of the API used by the New Relic /actuator/health synthetic monitor. Leave null to skip creating the monitor."
  type        = string
  default     = null

  validation {
    condition     = var.new_relic_api_base_url == null || can(regex("^https?://", var.new_relic_api_base_url))
    error_message = "new_relic_api_base_url must start with http:// or https:// when set."
  }
}

variable "auth_lambda_function_name" {
  description = "Name of the authentication Lambda used by API Gateway."
  type        = string
  default     = "tech-challenge-oficina-auth"
}
