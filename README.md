# tech-challenge-oficina-k8s-infra

Propósito:
Repositório responsável pelo provisionamento da infraestrutura Kubernetes da aplicação Tech Challenge Oficina usando Terraform e AWS.

## Objetivo

Organizar a base de infraestrutura como código para evolução da stack Kubernetes com Terraform, mantendo validações automáticas no repositório.

## Tecnologias

- Terraform
- AWS Provider
- GitHub Actions

## Estrutura do projeto

```text
.
|-- .github/
|   `-- workflows/
|       `-- terraform-ci.yml
`-- terraform/
    |-- main.tf
    |-- outputs.tf
    |-- providers.tf
    |-- variables.tf
    `-- versions.tf
```

## Execução local

No diretório `terraform/`:

```bash
terraform init
terraform fmt
terraform validate
```

## CI

Pipeline básica de validação Terraform executada via GitHub Actions em `push`, `pull_request` e `workflow_dispatch`.
