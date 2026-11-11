# Validação offline da consolidação CI/CD

Base: `19eb230`, branch `homolog`. Validação em 10/11/2026.
Nenhum acesso AWS, backend conectado, workflow, apply ou destroy real foi executado.

| Verificação | Resultado |
|---|---|
| Node: environment-actions e identity-contracts | 71 testes passaram, sem skips |
| Python: environment-actions | 52 testes passaram |
| Python: eks-endpoint-recovery | 40 testes passaram |
| Python: artifact-release | 48 testes passaram |
| Python: platform-secrets, incluindo Java 21/PKI e contrato público | 30 testes passaram, sem skips |
| Terraform modules/environment | 8 testes passaram |
| Terraform stacks/infra | 13 testes passaram |
| Terraform stacks/platform | 20 testes passaram |
| Terraform stacks/edge | 19 testes passaram |
| Terraform bootstrap/identities | 14 testes passaram |
| Terraform modules/endpoint-recovery | 9 testes passaram |
| Terraform fmt/validate, provider mirror local, backend desabilitado | Passaram |
| PowerShell Test-EnvironmentContract | 19 verificações passaram |
| PowerShell Test-IdentityContracts | 23 verificações passaram |
| check-manifests.mjs | 52 objetos e seis cenários de scripts MongoDB verificados |
| check-delivery-contract.mjs | 18 contratos de metadados verificados |
| check-contracts.mjs | 18 assertions de compatibilidade passaram |
| check-plans.mjs | OIDC, IAM, PassRole, inventário, recovery e RBAC passaram |
| check-edge.mjs | 42 rotas e 12 objetos, isolamento/TLS verificados |
| check-bootstrap.sh | Sucesso, erro sanitizado e valor inválido passaram |
| YAML e workflows acionáveis | Apenas Plan, Deploy e Destroy; sem chaves duplicadas |
| git diff --check | Passou |

Os planos Terraform foram produzidos com providers mockados dos testes existentes,
Terraform 1.15.8 e providers locais. Eles não comprovam capacidade, permissões
efetivas, elegibilidade ou readiness AWS. Docker e Helm não estavam disponíveis
para testes que exigem esses executáveis; nenhuma dependência foi substituída por
um resultado positivo artificial.

O teste ampliado de PKI usa o adaptador existente `check-delivery-contract.mjs`,
que identifica sua saída como fixture pública e completa o ARN sintético. Não
altera nem atesta recursos reais. O validador IAM/RBAC foi alinhado à conta
446517826146 já usada pelo teste Terraform; mantém a rejeição da antiga conta
sintética como proprietária dos recursos AWS do bootstrap.

Cobertura adicionada: Plan sem apply ou gravação de inventário; aplicação do
binário exato sem replan; hash, autenticação do artifact, commit, fase, state e
inventário divergentes; aprovação própria de Deploy; confirmação de Destroy;
infra sem aplicações; preservação da causa original e das falhas de cleanup;
diagnósticos em falhas; states isolados; recuperação de infra parcial; manutenção
sem regressão de permissões/opcionais; ausência de workflows antigos acionáveis.

Operação e requisitos restantes: [guia vigente](github-actions-first.md).
