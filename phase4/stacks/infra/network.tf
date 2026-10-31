resource "aws_vpc" "environment" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name_prefix}-vpc" }

  depends_on = [terraform_data.environment_guard]
}

resource "aws_subnet" "public" {
  for_each = local.zones

  vpc_id                  = aws_vpc.environment.id
  cidr_block              = cidrsubnet(local.vpc_cidr, 8, tonumber(each.key))
  availability_zone       = each.value
  map_public_ip_on_launch = true
  tags = {
    Name = "${local.name_prefix}-public-${each.value}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  for_each = local.zones

  vpc_id                  = aws_vpc.environment.id
  cidr_block              = cidrsubnet(local.vpc_cidr, 8, 10 + tonumber(each.key))
  availability_zone       = each.value
  map_public_ip_on_launch = false
  tags = {
    Name                              = "${local.name_prefix}-private-${each.value}"
    Tier                              = "private"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

resource "aws_internet_gateway" "environment" {
  vpc_id = aws_vpc.environment.id
  tags   = { Name = "${local.name_prefix}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.environment.id
  tags   = { Name = "${local.name_prefix}-public" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.environment.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.environment.id
  route  = []
  tags   = { Name = "${local.name_prefix}-isolated" }
}

resource "aws_route_table_association" "public" {
  for_each = local.zones

  subnet_id      = aws_subnet.public[each.key].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  for_each = local.zones

  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private.id
}
