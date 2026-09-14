provider "aws" {
  region = var.aws_region
}

data "aws_eks_cluster_auth" "tech_challenge_oficina" {
  name = aws_eks_cluster.tech_challenge_oficina.name
}

provider "helm" {
  kubernetes {
    host                   = aws_eks_cluster.tech_challenge_oficina.endpoint
    cluster_ca_certificate = base64decode(aws_eks_cluster.tech_challenge_oficina.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.tech_challenge_oficina.token
  }
}

provider "newrelic" {}
