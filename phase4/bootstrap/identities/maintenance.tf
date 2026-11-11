output "maintenance_inventory" {
  description = "Checkpoint tecnico para preservar IAM entre manutencoes; aprovacao real pertence ao GitHub Environment."
  value = {
    inventory              = var.inventory
    inventory_approved     = var.inventory_approved
    destroy_confirmation   = var.destroy_confirmation
    external_input_secrets = var.external_input_secrets
  }
}
