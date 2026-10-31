mock_data "aws_caller_identity" {
  defaults = { account_id = "123456789012" }
}

mock_data "aws_eks_cluster" {
  defaults = {
    arn                   = "arn:aws:eks:us-east-1:123456789012:cluster/tc-oficina-p4-demo-a"
    endpoint              = "https://test.eks.amazonaws.com"
    certificate_authority = [{ data = "dGVzdA==" }]
    vpc_config = [{
      vpc_id     = "vpc-00000000000000001"
      subnet_ids = ["subnet-00000000000000001", "subnet-00000000000000002"]
    }]
  }
}

mock_resource "aws_iam_role" {
  defaults = { arn = "arn:aws:iam::123456789012:role/tc-oficina-p4-demo-a-ebs-csi" }
}
