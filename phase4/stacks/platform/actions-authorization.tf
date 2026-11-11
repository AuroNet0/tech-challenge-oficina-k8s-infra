variable "approval_source" {
  description = "Catalogo local ou aprovacao obrigatoria do GitHub Environment no executor Actions."
  type        = string
  default     = "catalog"

  validation {
    condition     = contains(["catalog", "github-environment"], var.approval_source)
    error_message = "Fonte de aprovacao desconhecida."
  }
}
