resource "aws_db_subnet_group" "environment" {
  name       = "${local.name_prefix}-postgres"
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name_prefix}-postgres17"
  family = "postgres17"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  depends_on = [terraform_data.environment_guard]
}

resource "aws_db_instance" "environment" {
  identifier                   = "${local.name_prefix}-postgres"
  engine                       = "postgres"
  engine_version               = var.postgres_version
  engine_lifecycle_support     = "open-source-rds-extended-support-disabled"
  instance_class               = var.db_instance_class
  allocated_storage            = var.db_allocated_storage
  max_allocated_storage        = 0
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = "oficina_bootstrap"
  username                     = "infra_admin"
  manage_master_user_password  = true
  port                         = 5432
  publicly_accessible          = false
  multi_az                     = false
  availability_zone            = var.availability_zones[0]
  db_subnet_group_name         = aws_db_subnet_group.environment.name
  parameter_group_name         = aws_db_parameter_group.postgres.name
  vpc_security_group_ids       = [aws_security_group.database.id]
  deletion_protection          = false
  skip_final_snapshot          = true
  delete_automated_backups     = true
  backup_retention_period      = 0
  auto_minor_version_upgrade   = true
  allow_major_version_upgrade  = false
  apply_immediately            = true
  performance_insights_enabled = false
  monitoring_interval          = 0
  copy_tags_to_snapshot        = true

  depends_on = [terraform_data.environment_guard, aws_route_table_association.private]
}
