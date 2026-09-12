variable "aws_region" {
  description = "AWS region where the Terraform state bucket will be created."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name_prefix" {
  description = "Prefix used to generate a globally unique S3 bucket name for Terraform state."
  type        = string
  default     = "tech-challenge-oficina-terraform-state"
}
