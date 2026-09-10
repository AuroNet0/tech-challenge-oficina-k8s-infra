variable "aws_region" {
  description = "AWS region to be used by the Terraform AWS provider."
  type        = string
  default     = "us-east-1"
}

variable "api_backend_url" {
  description = "Public base URL of the API Load Balancer on EKS."
  type        = string
}

variable "enable_api_gateway" {
  description = "Whether to create the HTTP API Gateway resources."
  type        = bool
  default     = true
}

variable "auth_lambda_function_name" {
  description = "Name of the authentication Lambda used by API Gateway."
  type        = string
  default     = "tech-challenge-oficina-auth"
}
