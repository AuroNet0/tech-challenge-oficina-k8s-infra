locals {
  common_tags = {
    Project     = "tech-challenge-oficina"
    Environment = "shared"
  }
}

resource "aws_vpc" "tech_challenge_oficina" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-vpc"
  })
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.tech_challenge_oficina.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-public-${var.aws_region}a"
    Tier = "public"
  })
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.tech_challenge_oficina.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = "${var.aws_region}b"
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-public-${var.aws_region}b"
    Tier = "public"
  })
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.tech_challenge_oficina.id
  cidr_block        = "10.0.11.0/24"
  availability_zone = "${var.aws_region}a"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-private-${var.aws_region}a"
    Tier = "private"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.tech_challenge_oficina.id
  cidr_block        = "10.0.12.0/24"
  availability_zone = "${var.aws_region}b"

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-private-${var.aws_region}b"
    Tier = "private"
  })
}

resource "aws_internet_gateway" "tech_challenge_oficina" {
  vpc_id = aws_vpc.tech_challenge_oficina.id

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-igw"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.tech_challenge_oficina.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.tech_challenge_oficina.id
  }

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-public-rt"
  })
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}
