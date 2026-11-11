# CI/CD de demo-a: Plan → Deploy → Destroy

Esta é a documentação operacional vigente. Há somente três workflows acionáveis,
todos por `workflow_dispatch` em `homolog`. Os documentos de bootstrap/preflight
anteriores são históricos. Nenhum comando conectado deve ser executado localmente.

## Operação mínima

1. **Plan**: escolher `infra`, branch `homolog`. Revisar `summary.md`,
   `plan.json` e o SHA256 publicado no resumo do run. Não precisa habilitar o
   catálogo, publicar aplicações ou fornecer Mercado Pago/New Relic.
2. **Deploy**: mesma fase e mesmo commit de `homolog`; informar `plan_run`
   (ID numérico do Plan) e `approved_plan_sha256`. O revisor aprova o
   GitHub Environment deste novo run depois de revisar mudanças e custos.
3. **Destroy**: branch `homolog`, confirmação exata `DESTROY demo-a`.
   Aprovar os Environments correspondentes ao teardown e à destruição.

Cada fase necessária é um checkpoint retomável dentro dos mesmos workflows.
Deploy aplica **o binário Terraform produzido pelo Plan**, sem recalcular outro
plano. Confere procedência do run, fase, commit, conta, região, backend,
configuração, inventário e versões S3 dos states. Mudança nesses contratos exige
novo Plan; o executor nunca aceita outro plano em lugar do aprovado.
O Terraform também verifica lineage/serial e lock ao aplicar o plano salvo.
Antes de aplicar Platform ou Edge, Deploy revalida a revisão e a validade de
duas horas dos receipts usados no plano. Uma aprovação demorada não renova
evidências de readiness vencidas nem autoriza outro plano.

## Configuração única no GitHub

- Secret de repositório `PLAN_ARTIFACT_KEY`: chave aleatória de 32 bytes em
  base64, igual para Plan e Deploy. Pode ser gerada fora dos logs de Actions com
  `openssl rand -base64 32`. O binário pode conter dados sensíveis: artifact
  contém somente resumo sem valores e payload cifrado/autenticado com AES-GCM.
  Trocar a chave invalida os planos pendentes. Artifacts expiram em sete dias.
- Os Environments existentes `phase4-demo-a-provision`,
  `phase4-demo-a-deploy` e `phase4-demo-a-destroy` precisam de revisores
  obrigatórios e restrição à branch `homolog`. O executor verifica a presença
  dos revisores na API do GitHub e falha se a proteção estiver ausente.
- `phase4-demo-a-bootstrap` é usado somente nas fases opcionais de manutenção
  ou verificação independente. Também exige revisores para executar mudanças.
- O token do run precisa conseguir ler metadados dos Environments. Os workflows
  solicitam `contents: read`, `actions: read` e `id-token: write`.
  `SOURCE_READ_TOKEN` é necessário apenas quando os repositórios dos serviços
  não podem ser lidos com o token padrão.
- Runners privados já existentes podem ser selecionados por
  `PHASE4_EXECUTOR_RUNNER_LABELS`. Não se provisiona runner automaticamente.

O OIDC **immutable** instalado vincula as roles a esses Environments. Por isso
Plan utiliza o Environment provision existente para autenticar: se ele já exige
aprovação, essa proteção continua valendo. Remover essa aprovação apenas de Plan
exigiria uma mudança explícita do contrato OIDC; esta refatoração não a executa.
Destroy mantém duas identidades: provision para RBAC/Kubernetes e destroy para
AWS. As duas aprovações correspondem a permissões diferentes impostas pelas
trusts existentes, não a novas flags de autorização.

Não há `PHASE4_PLAN_APPROVED`, `PHASE4_READONLY_APPROVED` ou
`PHASE4_EXECUTION_APPROVED` nos workflows. Não se atualizam timestamps ou
`preflight_authorization`. As funções legadas continuam disponíveis para
compatibilidade/testes, mas não são o executor operacional.
A seleção técnica Terraform `approval_source=github-environment` delega a
autorização ao executor protegido. Os defaults locais continuam usando o
catálogo bloqueado; demo-b continua isolado e bloqueado. Não se altera o tipo do
plano da conta AWS nem se habilita automaticamente um serviço opcional.

## Configuração técnica das fases seguintes

Os contratos estáveis permanecem versionados em `phase4/environments/`.
É possível fornecer os campos técnicos abaixo por **variables de repositório**,
sem editar JSON por execução. Plan e Deploy devem receber os mesmos valores.

| Fase | Variables técnicas |
|---|---|
| release | `SOURCE_AUTH_REVISION`, `SOURCE_OS_REVISION`, `SOURCE_EXECUTION_REVISION`, `SOURCE_BILLING_REVISION`: SHAs completos; respectivos `SOURCE_*_CHECKS`: nomes dos checks CI separados por vírgula |
| platform | `MONGODB_IMAGE`, `RABBITMQ_IMAGE`, `POSTGRES_IMAGE`: referências por digest; `EKS_VPC_CNI_VERSION`, `EKS_EBS_CSI_VERSION`, `EKS_POD_IDENTITY_VERSION`: versões exatas compatíveis |
| foundation | `NETWORK_PROBE_IMAGE`: imagem Python por digest para a prova positiva/negativa de NetworkPolicy |
| secrets | `MERCADO_PAGO_TEST_SECRET_ARN`: ARN da credencial TEST já armazenada no Secrets Manager |
| edge-alb e seguintes | `ALB_CERTIFICATE_ARN` e `ALB_SERVER_NAME`: certificado ACM e SAN controlado |
| observability | `NEW_RELIC_SECRET_ARN`: ARN do secret de ingestão já existente |
| recovery-foundation | `RECOVERY_ALARM_TOPIC_ARN`: tópico SNS independente já autorizado |

As variáveis contêm referências técnicas; valores secretos continuam no GitHub
Secrets ou Secrets Manager. Pins e verificações CI dos quatro serviços são
validados antes da publicação. Inputs ausentes produzem erro objetivo antes
da autenticação ou da operação dependente. Infra não depende desses campos.

Ordem dos checkpoints de aplicação, conforme os componentes existentes:

1. `infra`; `release`; `platform`; `edge`; repetir `platform` para a identidade ALB.
2. `foundation`; `controller`; `secrets`; `data`.
3. `platform-migration`; `migrate`; `edge-auth`; `jwks`.
4. `platform-apps`; `apps`; `edge-alb`; `ingress`; `edge-business`.
5. `observability`; `health`; `diagnose` quando necessário.

Cada checkpoint possui Plan e Deploy próprios. Nas fases sem Terraform, Plan
valida dependências e publica a autorização cifrada do checkpoint; não instala
manifests ou publica imagens. Os receipts reais mantêm revisão e validade de duas
horas. Não se marca readiness global como comprovada sem a aceitação completa.

## Manutenção e recovery opcionais

O bootstrap IAM já concluído não é repetido como dependência. Quando necessário,
selecionar no próprio Plan/Deploy uma fase de manutenção, sob role bootstrap:

- `iam-maintenance`: reutiliza inputs, boundaries, validação e revisão de escopo
  do bootstrap existente, com o state persistente já instalado. Reconstrói o
  inventário dos states de demo-a sem gravá-los durante Plan e prepara as
  permissões exatas de provision/deploy/destroy para revisão no Environment.
  O checkpoint Terraform preserva esse inventário nas manutenções seguintes;
  isso não executa nem substitui a confirmação obrigatória de Destroy.
- `artifact-foundation`: cria/valida o bucket versionado de artifacts somente
  após aprovação do seu plano.
- `recovery-foundation`: instala os mecanismos independentes já implementados,
  mantendo abertura desabilitada inicialmente.
- `recovery-closed`: executa o canário real de fechamento e registra evidência
  no DynamoDB; não fabrica prova a partir de uma aprovação humana.
- `recovery-activate`: só planeja/ativa abertura depois de um probe real válido.
- `recovery-expiry`: prova fechamento independente da lease /32 de 300 segundos.

Recursos opcionais já presentes no state bootstrap são preservados nas fases
seguintes; não se desabilitam buckets/recovery por flags default falsas.
O state `phase4/bootstrap/identities/demo-a.tfstate` nunca é confundido com
os states efêmeros. Bootstrap, OIDC, boundaries, bucket de backend e mecanismos
independentes persistentes ficam fora do Destroy normal.

Essas fases dependem de permissões específicas da role bootstrap. A policy
mínima instalada originalmente pode não autorizar Lambda, DynamoDB, Scheduler
ou leitura dos três states e do inventário para manutenção IAM. AccessDenied deve ser corrigido no escopo exato
reportado pelo plano, pelo responsável IAM; não se amplia IAM automaticamente.
A autorização humana de custos não é apresentada como prova de elegibilidade
AWS. Plan inspeciona conta FREE ativa, versões, ofertas AZ e quotas; não garante
capacidade EC2 futura nem permissões efetivas de todas as criações.

## Destruição e falhas

Teardown recupera o inventário a partir dos states, remove ingress antes de
workloads/PVCs e mantém controller até remover recursos externos. Destroy usa
os inputs persistidos e a ordem edge → platform → artifacts/secrets → infra.
Confere ownership, conta, região e os states exatos; prefixo sozinho nunca
autoriza remoção. States legados `terraform/` e `bootstrap/` não são alterados.

O job AWS continua elegível após falha do teardown, mas seu executor exige
receipt de teardown correspondente à geração do cluster. A exceção segura é
um cluster de infra parcial: nenhum state de platform/edge, nenhum inventário
dessas stacks, nenhum ALB ou volume de dados. Assim um problema de acesso ao
Kubernetes não bloqueia a recuperação de uma infra ainda sem aplicações.
Caso existam aplicações/recursos externos, bloqueia com diagnóstico e preserva
o inventário para uma retomada autorizada.

Todos os jobs coletam diagnósticos e publicam artifacts com `if: always()`.
Falhas de checkpoint, diagnóstico ou fechamento não substituem a causa original.
Os relatórios consolidados registram fase, operação, revisão, conta/região,
etapa, código, recurso, correção, resultado Terraform e fechamento/recovery.
Os detalhes Terraform são sanitizados; valores de secrets, planos em claro,
kubeconfigs e tokens não são publicados.
Relatórios incompletos são registrados como falha de diagnóstico sem impedir
o resumo dos demais relatórios. Destroy registra o resultado Terraform por
stack e tenta verificar recursos remanescentes também depois de uma falha,
preservando a causa original quando essa verificação adicional falhar.

## Migração e validação

| Workflow removido | Função reaproveitada internamente |
|---|---|
| environment-preflight | inspeção read-only em control.mjs dentro de Plan |
| environment-up | Driver, fases de delivery, publicação, receipts, checkpoints e endpoint closure |
| environment-destroy | teardown provision + destroy com role própria e verificação residual |
| environment-bootstrap-identities | validateInputs/reviewPlan e root persistente nas fases de manutenção |
| environment-verify-recovery | verify_recovery.verify nas fases opcionais de recovery |
| ci-cd | substituído pelos três contratos; wrapper legado já estava desativado |
| destroy-infra | teardown/state/ownership da Fase 4; root legado preservado sem execução |
| debug-new-relic | observability e diagnose com coleta sanitizada e ownership de demo-a |

Testes locais disponíveis: Node (`node --test scripts/environment-actions/*.test.mjs
scripts/identity-contracts/contracts.test.mjs`), Python (`bash
scripts/environment-actions/offline.sh`), contratos PowerShell e Terraform
offline (`bash scripts/environment-actions/offline-terraform.sh <tools>`).
O diretório de ferramentas precisa conter `bin/terraform` e o mirror local
`corrected-data/providers`; o runner usa backend desabilitado, sem credenciais.
Para os testes Java/PKI, fornecer Java 21 no PATH e o contrato exportado em
`PLATFORM_PUBLIC_CONTRACT`. Os mocks Terraform existentes verificam contratos,
não atestam capacidade, credenciais ou funcionamento AWS.

Bloqueio real preservado: a fase `e2e` não tem adapter AWS do Billing/Mercado
Pago no projeto atual. Ela falha com `AWS_E2E_ADAPTER_REQUIRED` já no gate.
Também não se afirma que roles, bucket, quotas, versão disponível, recovery ou
readiness foram verificados na AWS durante esta refatoração offline.
