variable "aws_region" {
  description = "AWS region to be used by the Terraform AWS provider."
  type        = string
  default     = "us-east-1"
}

variable "api_backend_url" {
  description = "Base URL pública do Load Balancer da API no EKS"
  type        = string
}
