resource "aws_ecr_repository" "tech_challenge_oficina_api" {
  name                 = "tech-challenge-oficina-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Name = "tech-challenge-oficina-api"
  })
}

resource "aws_ecr_lifecycle_policy" "tech_challenge_oficina_api" {
  repository = aws_ecr_repository.tech_challenge_oficina_api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
