# Evidência de validação offline — 11/11/2026

Escopo: alterações na infraestrutura em `homolog`; revisão somente leitura dos
quatro serviços em checkouts limpos de homolog remoto. Nenhuma alteração foi feita
nos checkouts irmãos que já continham trabalho local.

## Resultados

| Suíte | Resultado observado |
|---|---:|
| Executores, workflows/YAML, autorizações, artifacts, execução completa e fixtures | 90 testes Python aprovados |
| Recovery independente | 44 testes Python aprovados |
| Publicação/transporte de artefatos e scanners | 48 testes Python aprovados |
| Secrets/PKI/transporte | 30 testes Python aprovados |
| Verificador CI Auth da revisão fixada | 7 testes Python aprovados |
| Verificador CI OS da revisão fixada | 15 testes Python aprovados |
| Verificador CI Execution da revisão fixada | 8 testes Python aprovados |
| Verificador CI Billing da revisão fixada | 8 testes Python aprovados |
| Total Python | **250 testes aprovados** |
| Node, incluindo contratos de identidade | **71 testes aprovados** |

As três verificações de PKI inicialmente ignoradas foram reexecutadas com Java 21,
keytool/Linux memfd e a fixture pública Terraform: **30/30, sem skips** nessa suíte.
Houve avisos de depreciação do cryptography sobre certificados antigos do truststore
Java; não houve falha. A dependência permanece fixada em cryptography 50.0.1.

Testes dos verificadores de CI usam processos/runtime simulados para validar os
gates. Mensagens `verification: failed` nesses testes são os casos negativos
esperados; não representam builds produtivos executados ou aprovados nesta revisão.

| Root Terraform, providers mock | Resultado |
|---|---:|
| modules/environment | 8 aprovados |
| stacks/infra | 13 aprovados |
| stacks/platform | 20 aprovados |
| stacks/edge | 19 aprovados |
| bootstrap/identities | 16 aprovados |
| modules/endpoint-recovery | 9 aprovados |
| Total | **85 testes mock aprovados** |

Também foram executados `fmt -check` e `validate` nas seis roots, com backend
remoto desabilitado e instalação dos providers a partir de mirror local.
Os dois novos testes IAM verificam a delegação por Environments existentes,
preservação dos subjects originais, ausência de wildcard/branch subject e rejeição
da delegação no formato classic.

Verificadores adicionais aprovados:

- 18 contratos públicos de secrets; nenhum payload de secret exportado.
- 52 objetos Kubernetes renderizados, referências/segurança e seis cenários
  simulados dos scripts MongoDB. Requests básicos: 1450m CPU / 3200 MiB, excluindo
  sistema, controladores e observabilidade.
- 42 rotas Edge, 12 objetos Kubernetes e isolamento/TLS/caminhos privados.
- Contratos IAM/RBAC: OIDC exato, PassRole, inventário de secrets, recuperação
  close-only, ALB privado e fronteiras de namespace. Isso não é IAM Simulator AWS.
- 18 verificações de compatibilidade artefatos/Terraform.
- Parser YAML com rejeição de chaves duplicadas: três workflows manuais e composite
  action; um job mutável protegido em Deploy e um em Destroy; diagnósticos always().
- `git diff --check` sem erros; scanner local de arquivos sem rejeições na árvore
  validada. Fixtures negativas de credenciais nos testes Node passaram a usar
  escapes JavaScript, preservando os mesmos bytes em runtime sem publicar marcadores
  literais que acionavam o guard. Os testes de sanitização continuaram aprovados.

## O que os testes negativos cobrem

Branch/repositório/evento incorretos, confirmação de Destroy incorreta, Environment
sem revisão, tentativa de promover deploy/destroy a bootstrap, subject classic,
audience OIDC incorreta, renovação da sessão SDK, propagação IAM transitória,
hash/cifra/manifesto alterados, Plan de outro commit, CI de outra revisão,
artifacts de configuração misturada, BLOCKED convertido indevidamente, deriva de
state/inventário, planos incompletos, delete/replacement e expansões de custo.

Também permanecem os casos de erro AWS/Kubernetes, logs sensíveis, revogação de
cluster-admin temporário, expiração/fechamento independente do endpoint, falha
parcial, ownership de teardown e resíduos após Destroy. O Deploy para na falha e
não chama Destroy automaticamente.

## Comandos utilizados

Ferramentas Linux locais foram acessadas por WSL. Node e Git locais também foram
utilizados no Windows. As suítes Python foram executadas com as dependências
locais e Java 21; nenhum agente paralelo foi utilizado.

```bash
bash scripts/environment-actions/offline-review.sh
bash scripts/environment-actions/offline-terraform.sh "$ASSETS"
bash scripts/environment-actions/offline-terraform.sh "$ASSETS" bootstrap/identities
node --test $(rg --files scripts -g '*.test.mjs')
node scripts/artifact-release/check-contracts.mjs
node phase4/stacks/platform/tests/check-delivery-contract.mjs <mock-jsonl> <public-fixture>
node phase4/stacks/platform/tests/check-manifests.mjs <mock-jsonl> <fixture-directory>
node scripts/identity-contracts/check-plans.mjs <bootstrap-mock-jsonl> <platform-identity-mock-jsonl>
node phase4/stacks/edge/tests/check-edge.mjs <edge-mock-jsonl>
python scripts/platform-secrets/check_secret_files.py --root .
git diff --check
```

`offline-terraform.sh` agora aceita roots específicas e usa um snapshot novo a
cada execução, inclusive quando o diretório de relatórios é reutilizado. Não
valida cópias antigas por engano e não remove diretórios existentes.

Logs locais persistentes desta revisão: diretórios
`final-oficina-cicd-review` e `final-oficina-cicd-identities` no workspace de agentes
do IntelliJ. Fixtures exportadas são explicitamente sintéticas e não implantáveis.

## Verificações não executadas

- Maven verify completo/builds Java/Docker dos quatro SHAs e suítes distribuídas
  Docker não foram reexecutados: o Docker Engine local estava indisponível.
  Os testes Python de CI não substituem esses builds; o Plan exige CI do SHA exato.
- Não foi obtida evidência suficiente de CI remoto aprovado ou análise estática
  nesses SHAs. Essa condição bloqueia o Plan quando a evidência estiver ausente.
- Não foram executados IAM Simulator, Plan conectado, preflight AWS real, apply,
  destroy, publicação ECR/S3/Lambda, Helm/Kubernetes conectado, E2E/BDD AWS,
  pagamento Mercado Pago ou consulta de ingestão New Relic.
- Templates/contratos de Helm foram inspecionados; não se declarou validação
  conectada dos charts nem recebimento de telemetria.
- Custos, créditos, capacidade e elegibilidade específica da conta FREE não
  foram medidos. Os preços documentais não comprovam gratuidade ou créditos.

## Evidência de ausência de provisionamento

Os únicos comandos Terraform executados pela revisão foram `fmt`, `validate`,
`init -backend=false` e `test`, com providers mock e cópias locais. Os scripts
offline removem credenciais AWS do ambiente e apontam os arquivos de configuração
AWS para `/dev/null`, com metadata EC2 desabilitada.

Não houve CLI `terraform apply`/`destroy`, chamada AWS mutável, dispatch de
workflow ou comando Kubernetes/Helm conectado. Os textos `AccessDenied`, apply e
destroy encontrados nos testes vêm de stubs/mocks. As ações remotas realizadas
antes da publicação Git foram consultas Git/GitHub e leitura de documentação.

States existentes, backend e credenciais não foram modificados. Alterações de
IAM/trust aqui são código/plano futuro sujeito à revisão e à execução autorizada;
**não foram instaladas na AWS durante a refatoração**.
