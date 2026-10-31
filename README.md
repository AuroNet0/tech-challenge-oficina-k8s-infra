# tech-challenge-oficina-k8s-infra

## Fase 4 — preparação de ambientes efêmeros

A base da Etapa 9.2.2-A está em `phase4/`, separada das roots legadas
`bootstrap/` e `terraform/`. Não há infraestrutura Fase 4 provisionada ou
validada na AWS. Os ambientes do catálogo estão bloqueados para operação.

- [Inventário anterior à implementação](docs/9.2.2-A-inventario.md)
- [States, contratos, segurança e migração](docs/9.2.2-A-preparacao.md)
- [Relatório de validação e Git](docs/9.2.2-A-validacao.md)
- [Infraestrutura AWS efêmera — Etapa 9.2.2-B](docs/9.2.2-B-infraestrutura.md)
- [Platform Kubernetes — Etapa 9.2.3](docs/9.2.3-platform-kubernetes.md)

O workflow legado `ci-cd.yml` aceita somente `workflow_dispatch`; os gatilhos
automáticos foram removidos. Ele contém `apply` da infraestrutura legada e não
deve ser utilizado para a Fase 4. Não usar as instruções locais legadas abaixo
para esta preparação; seguir somente as verificações sem backend da documentação.

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

Workflow legado disponível somente por `workflow_dispatch`, com provisionamento
legado preservado para futura adaptação. Não executar para a Fase 4. A inspeção e
os limites da proteção estão no [relatório dos ajustes finais](docs/9.2.2-A-ajustes-finais.md).
