output "vpc_id" {
  description = "ID of the Tech Challenge Oficina VPC."
  value       = aws_vpc.tech_challenge_oficina.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets."
  value = [
    aws_subnet.public_a.id,
    aws_subnet.public_b.id,
  ]
}

output "private_subnet_ids" {
  description = "IDs of the private subnets."
  value = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id,
  ]
}

output "eks_cluster_name" {
  description = "Name of the EKS cluster."
  value       = aws_eks_cluster.tech_challenge_oficina.name
}

output "eks_cluster_endpoint" {
  description = "Endpoint of the EKS cluster."
  value       = aws_eks_cluster.tech_challenge_oficina.endpoint
}

output "eks_node_group_name" {
  description = "Name of the EKS managed node group."
  value       = aws_eks_node_group.tech_challenge_oficina.node_group_name
}
