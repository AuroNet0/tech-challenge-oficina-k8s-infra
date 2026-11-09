variable "artifact_bucket_installation_approved" {
  type    = bool
  default = false
}

resource "aws_s3_bucket" "artifacts" {
  count  = var.artifact_bucket_installation_approved ? 1 : 0
  bucket = "${local.prefix}-artifacts"
  lifecycle { prevent_destroy = true }
  depends_on = [terraform_data.guard]
}

resource "aws_s3_bucket_versioning" "artifacts" {
  count  = length(aws_s3_bucket.artifacts)
  bucket = aws_s3_bucket.artifacts[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count                   = length(aws_s3_bucket.artifacts)
  bucket                  = aws_s3_bucket.artifacts[0].id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  count  = length(aws_s3_bucket.artifacts)
  bucket = aws_s3_bucket.artifacts[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
