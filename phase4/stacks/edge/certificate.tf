data "aws_acm_certificate" "alb" {
  count       = var.prepare_alb && var.alb_tls != null ? 1 : 0
  domain      = var.alb_tls.server_name
  statuses    = ["ISSUED"]
  types       = ["AMAZON_ISSUED"]
  most_recent = false
  tags = {
    EnvironmentId = var.environment_id
    Phase         = "4"
    Lifecycle     = "ephemeral"
    Shared        = "false"
  }
}

resource "terraform_data" "certificate_guard" {
  count = var.prepare_alb && var.alb_tls != null ? 1 : 0
  lifecycle {
    precondition {
      condition = (
        data.aws_acm_certificate.alb[0].arn == var.alb_tls.certificate_arn &&
        data.aws_acm_certificate.alb[0].domain == var.alb_tls.server_name &&
        data.aws_acm_certificate.alb[0].status == "ISSUED"
      )
      error_message = "Certificado ACM observado nao corresponde ao ARN, dominio primario ou estado ISSUED aprovados."
    }
  }
}
