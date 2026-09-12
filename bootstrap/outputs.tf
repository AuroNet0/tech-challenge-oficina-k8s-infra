output "terraform_state_bucket_name" {
  description = "Name of the S3 bucket that stores Terraform remote states."
  value       = aws_s3_bucket.terraform_state.bucket
}
