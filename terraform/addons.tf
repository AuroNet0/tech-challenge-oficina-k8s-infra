resource "aws_eks_addon" "metrics_server" {
  cluster_name = aws_eks_cluster.tech_challenge_oficina.name
  addon_name   = "metrics-server"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-metrics-server"
  })

  depends_on = [
    aws_eks_node_group.tech_challenge_oficina,
  ]
}
