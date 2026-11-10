# Primeiro Terraform Infra Plan — fluxo consolidado

Este é o procedimento vigente para `homolog`. Nenhuma aprovação é preenchida
pelo código; `demo-a` e o apply permanecem desabilitados. Operações AWS e Terraform
conectado são executadas somente pelo GitHub Actions quando o operador as autorizar.

## Três intenções distintas

| Fluxo | O que exige | O que não autoriza |
|---|---|---|
| Preflight manual, `inspect` | Aprovação de leitura existente, identidade/OIDC, conta/região e consultas de configuração/recursos | Não autoriza plan, escrita no backend ou apply |
| Up, `execution_mode=plan`, `phase=infra` | Aprovação explícita de planejamento e revisão vigente do backend, identidade, contratos, versões, AZs/ofertas, quota e inventário | Não habilita catálogo, não exige aprovações de criação nem pode executar apply |
| Up, `execution_mode=apply` | Hash revisado, autorização explícita de execução, catálogo/runtime habilitados, elegibilidade específica, backend e recuperação/teardown revisados | Não usa a aprovação de planejamento como aprovação de criação |

O Preflight manual agora apresenta `scope=infra|full`, com padrão `infra`.
O contrato reutilizável mantém `scope=full` e comportamento estrito por padrão
(`intent=apply`); Environment Up passa sua intenção explicitamente.
`inspect` e `plan` registram requisitos de criação como DEFERRED, não PASS.
`environment_ready` continua falso. O escopo e a intenção ficam no relatório.

O modo `plan` aceita exclusivamente Infra, rejeita hash preenchido e usa
`PHASE4_PLAN_APPROVED`. Fornecer um hash nunca troca o modo automaticamente.
O modo `apply` exige hash para fases Terraform; sem hash é rejeitado antes do init.
Fases de delivery continuam mutáveis e exigem modo `apply` e suas aprovações.

## Serviços do Free Plan: evidência e limites

A [lista oficial da AWS](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html),
verificada em 10/11/2026, inclui na seção Free Tier da nova experiência:
Amazon Elastic Kubernetes Service, EC2, RDS, ECR, S3 e AWS IAM.
CloudWatch Logs e Secrets Manager também constam nessa lista.

Essa evidência documental resolve a dúvida sobre a presença dos serviços no
catálogo público. **Não demonstra permissões efetivas, disponibilidade, quotas,
elegibilidade da configuração específica desta conta nem sucesso de Create.**
O relatório registra a fonte e mantém
`account_configuration_eligibility_confirmed=false`.

Por isso a revisão específica de criação FREE não bloqueia uma inspeção ou um
plan que não cria recursos. Ela continua obrigatória para apply, com referência
verificável, conta/região, escopo, validade inferior a 24 horas e confirmação
humana das configurações e serviços usados. Créditos disponíveis não substituem
essa revisão. Não há upgrade automático de plano ou concessão automática de IAM.

## Procedimento exato para o primeiro plan

1. Revisar as permissões declaradas abaixo e as políticas efetivamente instaladas.
   Autorizar escrita de lock/state para planejamento é uma decisão do operador:
   não existe write probe nem simulação convertida em prova de permissão.
2. Após essa revisão, preencher manualmente
   `phase4/environments/demo-a.runtime.json.preflight_authorization`:

   ```json
   {
     "account": "446517826146",
     "region": "us-east-1",
     "scope": "infra",
     "valid_until": 0,
     "reference": "",
     "backend_permissions_reviewed": false
   }
   ```

   O exemplo não é uma aprovação: o operador deve registrar uma referência real,
   `valid_until` epoch em segundos no futuro e a menos de 24 horas, e atestar
   `backend_permissions_reviewed=true` somente depois da revisão.
3. O operador autoriza `PHASE4_PLAN_APPROVED=true` no Environment provision,
   mantendo a aprovação de leitura `PHASE4_READONLY_APPROVED` existente.
   Esta implementação não altera GitHub Environments nem suas variáveis.
   Não é necessário mudar `catalog.enabled`, `execution_enabled`, runtime
   `approved`, canário ou aprovação de criação para gerar esse primeiro plano.
4. Opcionalmente executar **Environment Preflight → homolog → scope=infra**:
   é inspeção e não substitui a autorização de planejamento.
5. Executar **Environment Up → homolog** com:
   - `confirmation=UP demo-a`;
   - `phase=infra`;
   - `execution_mode=plan`;
   - `approved_plan_sha256` vazio.
6. Revisar `report.json`, `summary.md` e `plan.json` no artifact
   `up-infra-<run>-<attempt>`. O resultado deve ser `PLAN_ONLY`.
   O artifact informa `execution_mode=plan`, SHA256 e
   `requires_enabled_replan=true` quando o catálogo está desabilitado.
   **Parar para revisão, sem executar apply.**

O ambiente não é habilitado nem mesmo temporariamente no catálogo. O módulo
mantém `ready=false` e expõe separadamente `planning_ready`.
A root Infra aceita `planning_only=true` exclusivamente para demo-a na conta
446517826146/us-east-1; o executor só fornece esse input no plan desabilitado e
rejeita apply com esse input. O plano binário fica temporário e não vira artifact.

## Backend: cobertura declarada confirmada

Bucket: `tech-challenge-oficina-terraform-state-b1cfa326`.
A policy efetiva provision é composta de `common`, `state_writer`,
`action_journal` e evidência de backend, em
`phase4/bootstrap/identities/{policies,actions,operations}.tf`.
Testes Terraform mock validam as policies JSON compiladas, incluindo state,
locks e condição de owner. **Nenhuma policy IAM foi ampliada.**

| Ação | Recurso exato do primeiro Infra Plan |
|---|---|
| `s3:GetBucketLocation`, `s3:GetBucketVersioning`, `s3:GetEncryptionConfiguration`, `s3:GetBucketPublicAccessBlock` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326` |
| `s3:ListBucket` | Mesmo bucket; `s3:prefix` limitado a `phase4/environments/demo-a` e `phase4/environments/demo-a/*`, incluindo a listagem de workspaces usada pelo init |
| `s3:GetObject`, `s3:PutObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/infra.tfstate` |
| `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/infra.tfstate.tflock` |
| `s3:GetObject`, `s3:PutObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/operations/inventory.json` |

Existem grants equivalentes para states/locks separados de platform e edge.
As condições S3 restringem `s3:ResourceAccount=446517826146`.
Não se concede DeleteObject no state. `TF_WORKSPACE=default`, criptografia,
versionamento, proteção pública e verificação de checksums permanecem ativos.
Consulte os [requisitos do backend S3](https://developer.hashicorp.com/terraform/language/backend/s3).

Não foi identificada ausência dessas ações na policy **declarada**; a instalação
remota não foi consultada nesta implementação. Se o init negar acesso, registrar
a ação/ARN correspondente antes de mudar IAM. SCP, bucket policy ou KMS ainda
podem restringir acesso. Se houver KMS, revisar a chave real; não usar wildcard
de chaves. Terraform plan escreve locks S3, mesmo sem criar Infra.

## Contratos preservados e dependências posteriores

- Conta 446517826146/us-east-1, OIDC immutable e AZs us-east-1a/us-east-1b.
- EKS 1.35 STANDARD, privado, sem admin implícito do criador nem elevação automática.
- Dois nodes ON_DEMAND m7i-flex.large, volumes gp3 criptografados de 30 GiB.
  O node group atual usa a subnet pública da primeira AZ; não implica nodes
  distribuídos por duas AZs. A oferta é checada em ambas e a quota considera uso.
- RDS privado PostgreSQL 17.10/db.t4g.micro, 20 GiB gp3, Single-AZ.
- ECR pode ser planejado vazio; aplicações, imagens publicadas, Mercado Pago,
  New Relic, migrações e health checks não bloqueiam Infra.
- O canário real será exigido antes de abertura do endpoint e das fases que
  dependem de Kubernetes. Um cluster inexistente não precisa de canário para plan.
- demo-b e o workflow Environment Destroy permanecem desabilitado/inalterado,
  respectivamente. O formato classic segue apenas como opção explícita suportada.

## Decisões adicionais somente antes de apply

Apply continua bloqueado até o operador habilitar explicitamente catálogo,
`execution_enabled`, runtime `approved` e `PHASE4_EXECUTION_APPROVED`,
registrar `destroy_offline_verified` e revisar:
- elegibilidade de criação FREE: `free_plan_creation_reviewed` e serviços
  `eks,ec2,rds,ecr,secretsmanager,logs,iam` para Infra; escopo full também exige
  Lambda/ELB e os requisitos posteriores;
- estratégia privada: `private_creation_reviewed`, `teardown_reviewed` e
  `recovery_reference` com procedimento operacional concreto;
- IAM de criação e inventário: boundaries exatas das roles
  `arn:aws:iam::446517826146:role/tc-oficina-p4-demo-a-eks-cluster` e
  `arn:aws:iam::446517826146:role/tc-oficina-p4-demo-a-eks-nodes`.
  O bootstrap atual mantém `inventory_approved=false`; criar essas roles exige
  revisão do mapa `inventory.workload_boundaries` e das permissões condicionais.
  Não inventar IDs de recursos ou autorizar destruição por prefixo.

O teardown atual de cluster existente usa Kubernetes. A revisão precisa demonstrar
como obter acesso ou retomar o provisionamento parcial por GitHub Actions,
incluindo eventual recuperação via APIs AWS e elevação temporária explicitamente
aprovada/revogada. A existência do workflow Destroy sozinha não é evidência dessa
capacidade. Sem essa revisão, apply falha antes de qualquer chamada Terraform.

As aprovações são revalidadas após gerar o plano e antes de apply, junto ao hash.
Falta de hash, hash divergente, revisão expirada ou recuperação ausente nunca
executam apply. State e inputs são preservados antes de apply e checkpoints
continuam sendo tentados após falha, sem ocultar a causa original.

Depois de habilitar/aprovar criação, é obrigatório gerar e revisar um novo plano:
mudanças de commit, inputs ou catálogo invalidam o hash exploratório.
Somente um plano da mesma revisão/configuração pode ser aplicado em modo
`apply`, com o hash revisado. Nenhuma parte desta preparação dispara esse fluxo.

## Validação offline

Resultado da implementação: 71 testes Node, 149 testes Python (31 de execução,
40 de recuperação, 48 de artefatos e 30 de PKI), 81 testes Terraform mock,
19 verificações PowerShell e três cenários do wrapper Bash aprovados.
Os 23 checks de identidade/OIDC/lease também passaram. `terraform fmt -check`
e `git diff --check` não apontaram problemas. Integração local com containers
de aplicações não faz parte dessas suítes e exige imagens/Docker preparados.

Node, Python, contratos PowerShell e as seis suítes Terraform foram executados
com fixtures/mocks locais. O Terraform de teste usa backend desativado,
mirror local verificado e somente runs `command=plan`; não usa credenciais AWS.
Os testes negativos cobrem hash, autorização, expiração e recuperação revogada
entre plan e apply. Fixtures antigas foram isoladas da conta real, sem afrouxar
validações de produção. As policies IAM compiladas confirmam cobertura de state
e .tflock sem novos grants.

As verificações PKI usam cryptography 50.0.1 conforme requirements e JDK 21
isolado, além de outputs públicos de um plan mock Terraform. Nenhuma aprovação
ou capacidade AWS real é inferida dos testes offline.
