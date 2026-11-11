# Validação offline da consolidação CI/CD

Base da consolidação: `19eb230`; revisão complementar sobre `c587ff6`, branch
`homolog`. Validação em 11/11/2026.
Nenhum acesso AWS, backend conectado, workflow, apply ou destroy real foi executado.

| Verificação | Resultado |
|---|---|
| Node: environment-actions e identity-contracts | 71 testes passaram, sem skips |
| Python: environment-actions | 56 testes passaram |
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
| AuthDatabaseBootstrapTest.java | 1 documento válido e 11 inválidos; Java 21, JARs locais, sem rede |
| check-bootstrap.sh | Evidência anterior de 10/11; não repetido nesta revisão, requer paths do runtime Lambda |
| YAML e workflows acionáveis | Apenas Plan, Deploy e Destroy; sem chaves duplicadas |
| git diff --check | Passou |

Os planos Terraform foram produzidos com providers mockados dos testes existentes,
Terraform 1.15.8 e providers locais. Eles não comprovam capacidade, permissões
efetivas, elegibilidade ou readiness AWS. O daemon Docker e o executável Helm não
estavam disponíveis para os testes de containers e charts; nenhuma dependência
foi substituída por um resultado positivo artificial. As quatro suítes Python
foram executadas no WSL com cryptography 50.0.1 e JDK 21 em cache isolado. A
preparação dessas ferramentas baixou dependências; os testes foram offline.
Terraform usou o mirror Windows já disponível, cópia temporária do código,
backend desabilitado e credenciais removidas. Foram 328 testes Node/Python/
Terraform aprovados, além dos verificadores de contratos listados na tabela.

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

Revisão complementar: Deploy rejeita receipts que venceram entre Plan e a
aprovação; Destroy tenta verificar remanescentes após falha sem substituir a
causa original e registra resultado Terraform por stack; relatórios corrompidos
não impedem a publicação do resumo dos demais diagnósticos. O teste de workflows
também detecta arquivos acionáveis com extensão `.yaml`.

Operação e requisitos restantes: [guia vigente](github-actions-first.md).
