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
