# Actions-first: operação de demo-a

Os executores reais estão em `scripts/environment-actions/`. Todas as operações
conectadas pertencem aos workflows; não executar o runtime, apply ou destroy local.
Esta revisão mantém catálogos, execução e configuração **desabilitados**.

## Uso e retomada

`Environment Up` é dispatch por **fase**, não uma promessa de deploy completo em
um clique. Cada fase pode ser repetida na mesma revisão. Os planos Terraform
publicam `plan.json` com endereços/ações/hash; sem hash não fazem apply. Revisar o
plano e repetir a mesma fase/revisão com `approved_plan_sha256`, sob aprovação do
environment. O apply usa o arquivo de plano recém-calculado, somente se seu hash
coincidir. Replacements/deletes são rejeitados no Up.

Ordem operacional:

1. `infra` plan/apply; `release` compila/testa/publica quatro imagens, JAR e layer.
2. `platform` plan/apply; `edge` plan/apply da fundação; repetir `platform` para
   identidade do controller após observar SG/certificado da Edge.
3. `foundation` instala manifests/RBAC/addons, verifica dois nodes e prova
   NetworkPolicy com conexões permitida e negada; `controller` instala Helm/Pod Identity.
4. `secrets` entrega PKI/credenciais usando os adaptadores existentes; `data`
   instala Mongo/Rabbit, espera readiness e executa PostgreSQL bootstrap.
5. `platform-migration` plan/apply; `migrate` executa Job Auth e remove projeções
   temporárias somente após sucesso. `edge-auth` plan/apply; `jwks` valida HTTP/JWKS.
6. `platform-apps` plan/apply; `apps` aplica quatro Deployments e verifica probes;
   `edge-alb` plan/apply; `ingress` instala Ingress e valida listener/targets;
   `edge-business` plan/apply; `observability`; `health`; `e2e`.

Cada fase Terraform exige sua própria revisão de plano. Receipts são de execução
real, vinculados à revisão e válidos por duas horas. Readiness de Terraform é
derivada desses receipts, não de flags livres. Após mudança de revisão, repetir
as verificações/entregas necessárias. PASS de uma fase **não** significa ambiente
completo; `environment_ready` permanece falso enquanto a aceitação final faltar.

O builder existente continua offline por padrão; o adaptador Actions opta
explicitamente por resolução Maven/Docker online, preservando os scanners/testes
e executando builds sem credenciais AWS no ambiente dos subprocessos de build.
Não modifica os quatro workflows CI dos serviços.

## Destroy antes do primeiro Up

`Environment Destroy`, confirmação `DESTROY demo-a`, não depende do preflight de
criação nem de Up bem-sucedido. Usa dois jobs aprovados: provision faz teardown
Kubernetes privilegiado; destroy remove os states. Ambos compartilham com Up e
bootstrap o lock `phase4-demo-a-mutation`, sem cancelar execução em andamento.

O teardown relê os states, bloqueia APIs inventariadas, remove Ingress e espera
ALB desaparecer com controller ativo, remove workloads/PVCs, registra IDs dos
volumes via PV e espera sua exclusão antes de Helm/CSI. Remove somente namespaces,
releases e recursos globais com identidade/ownership esperado. Depois: Edge,
Platform, imagens ECR, versões S3/layers e secrets próprios, Infra por último.
O inventário de retomada fica no backend versionado em
`phase4/environments/demo-a/operations/inventory.json`; tfvars públicos são salvos
antes de apply, inclusive quando a execução é interrompida. State/inventário não
são apagados. Bucket backend, bucket de artifacts vazio, OIDC e identidades persistem.
Signing/entradas externas não pertencem ao executor de secrets e são preservados.

Falha interrompe as fases destrutivas dependentes; repetir o workflow após corrigir
a causa. State ausente com inventário não vazio e AccessDenied nunca significam
limpeza concluída. Verifica resíduos EKS/RDS/ALB/EBS/ENI; custos ficam explicitamente
`UNVERIFIED_BILLING_LAG`. Não existe destroy automático ao falhar Up.

## EKS: resultado da avaliação

O cliente de broker está conectado: IP público observado, lease de até 1800s,
prepare antes de open, CIDR /32 conferido, fechamento no finally e verificação
independente por DescribeCluster. Um passo `always()` adicional tenta recuperar
o fechamento sem substituir o relatório original. Expiração/scheduler/recuperador
existentes independem da vida do runner; perda abrupta não depende de finally.

**O caminho público temporário continua desabilitado nesta revisão.** O módulo
instala `enabled=false/recovery_verified=false` por padrão. Sua ativação agora tem
workflow e flags de bootstrap explícitas, mas não há canário AWS real executado.
O marcador local `independent_canary_verified` sozinho não habilita o broker:
seu runtime também exige verificação recente em DynamoDB, versão de código,
cluster/createdAt, agenda, identidade e probe do recuperador.
Não alterar a configuração Lambda pelo Console para contornar esse bloqueio.

Sequência sem runner/EC2 adicional:

1. Bootstrap Identities instala o módulo existente com
   `recovery_installation_approved=true`, `recovery_package_path=endpoint-recovery.zip`,
   `recovery_alarm_topic_arn` real e ambos os flags de abertura/verificação falsos.
   O ZIP é montado deterministicamente no próprio workflow, a partir da revisão.
2. Up `infra`: o preflight permite **somente controle AWS**, com acesso Kubernetes
   marcado DEFERRED, e exige endpoint privado se cluster já existir. Plano revisado
   pode criar o primeiro cluster fechado sem presumir canário de cluster inexistente.
3. `Environment Verify Recovery`, modo `closed-canary`: faz somente atualização
   privada, espera o **scheduler independente** concluir CANARY, verifica código,
   cluster, atualização e quatro alarmes OK, então registra VERIFICATION com validade
   máxima de 24 horas. O recuperador não tem permissão de escrever VERIFICATION.
4. Novo plano aprovado do bootstrap com `recovery_opening_approved=true` e
   `recovery_verification_approved=true` instala permissão de abertura **somente no
   broker**. As roles GitHub continuam explicitamente impedidas de abrir diretamente.
5. Verify Recovery, modo `lease-expiry`: abre /32 por 300 segundos com agenda já
   armada, **não chama close pelo runner**, observa abertura e espera a recuperação
   registrar CLOSED após expiração. Se cancelado, a agenda continua. O artifact
   indica se a transição público→privado foi comprovada; falha não gera prova.
6. Só após essa evidência aprovada marcar `independent_canary_verified=true` no
   contrato operacional. Preflight completo invoca probe somente leitura e bloqueia
   prova vencida, código diferente ou cluster recriado. Repetir canário quando expirar.

A role bootstrap precisa, além da instalação revisada dos recursos do módulo,
de Invoke/GetFunctionConfiguration nas duas funções, GetSchedule na agenda exata,
DescribeCluster/DescribeUpdate no cluster, DescribeAlarms nos quatro alarmes,
GetItem na tabela `tc-oficina-p4-demo-a-endpoint-leases` e PutItem **somente** com
`dynamodb:LeadingKeys=["VERIFICATION"]`. Revisar PassRole para as roles Lambda/
Scheduler exatas, restrito ao serviço destinatário. O canal SNS de alarmes já
deve existir e ser acompanhado pelo operador. Nada disso foi aplicado nesta etapa.

Alternativa mínima suportada: **runner privado já existente**, sem criar EC2 ou
self-hosted novo. Configurar `endpoint.mode=existing-private`, `runner_mode` e
`private_probe_host` no contrato, além das variáveis JSON de labels
`PHASE4_EXECUTOR_RUNNER_LABELS` e `PHASE4_PREFLIGHT_RUNNER_LABELS`. O runtime exige
self-hosted, endpoint somente privado e `/readyz` alcançável. Sem executor existente
comprovado ou broker operacional, o preflight bloqueia Up. A conta não tem cluster
para comprovar canário agora; sua execução autorizada continua pendente na AWS.

## Bootstrap mínimo e configuração

1. Administrador não root, com sessão temporária/MFA, usa Console IAM para criar
   ou reutilizar provider `https://token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`. Nunca usar root keys em Actions.
2. Criar role `tc-oficina-p4-demo-a-bootstrap` com trust OIDC **somente** para o
   environment `phase4-demo-a-bootstrap`. Confirmar subject real: clássico
   `repo:AuroNet0/tech-challenge-oficina-k8s-infra:environment:phase4-demo-a-bootstrap`
   ou immutable do contrato existente; não assumir IDs/customizações sem verificar.
3. Reutilizar/criar backend S3 em us-east-1, privado, versionado e criptografado.
   Role bootstrap recebe Get/Put no state bootstrap e Get/Put/Delete no `.tflock`,
   ListBucket restrito ao prefixo e GetBucketLocation. IAM limitado às roles/policies
   `tc-oficina-p4-demo-a-*`: ações explícitas de Get/List/Create/Tag/Update de roles,
   policies/versions e attachments, sem AdministratorAccess. Revisar conteúdo das
   policies, pois prefixo de ARN não limita privilégios que uma policy concede.
4. Preparar `phase4/bootstrap/identities/approved-inputs.json`: conta/região,
   `environment_id`, `backend_bucket`, `installation_approved`, `oidc_claims_verified`,
   `subject_format` e cinco JSON strings em `workload_boundary_documents`, conforme
   `boundaries.tf`. Arquivo ainda ausente de propósito. Não aplicar a root legada.
5. Bootstrap Identities: plan-only, revisão de `identity-plan.json`, nova execução
   com hash e aprovação. Pode criar o bucket de artifacts persistente do contrato
   com `artifact_bucket_installation_approved=true` explicitamente revisado;
   nesse caso a role bootstrap também precisa das ações S3 de criação/configuração
   **somente** em `tc-oficina-p4-demo-a-artifacts`. Bucket existente deve ser reutilizado
   com a flag falsa; não tentar recriá-lo ou importar pelo terminal local.
6. A cada fase, revisar `identity-inventory.json` e instalar pelo mesmo workflow
   as permissões exatas ainda dependentes de inventário (incluindo master secret RDS,
   deleções e APIs). Preencher `inventory`, `inventory_approved`, `destroy_confirmation`
   e `external_input_secrets` apenas com ARNs observados. Campo approved do artifact
   é falso: não há concessão IAM automática a partir de tags. Desabilitar a identidade
   bootstrap entre manutenções. Terraform bootstrap rejeita deletes/replacements.

Environments `phase4-demo-a-{bootstrap,provision,deploy,destroy}`: reviewer operador,
impedir autoaprovação, branch `homolog`; verificar suporte no plano GitHub.
`PHASE4_BOOTSTRAP_APPROVED`, `PHASE4_READONLY_APPROVED`, `PHASE4_EXECUTION_APPROVED`
começam falsos. Não configurados remotamente nesta revisão. Provision precisa da
elevação bootstrap EKS existente para foundation/Helm/teardown; revogar após a janela
e reautorizar por plano aprovado antes de teardown quando necessário.

Preencher `demo-a.runtime.json` com inputs públicos reais das três roots, revisões
dos quatro serviços e nomes exatos de checks CI obrigatórios, digest da imagem Python
de probe, ARNs de entrada Mercado Pago **teste** e licença New Relic. Credenciais
externas ficam em `/oficina/phase4/demo-a/inputs/{mercado-pago,newrelic}` no Secrets
Manager, autorizadas pelos ARNs exatos no bootstrap. `SOURCE_READ_TOKEN` é opcional
para checkouts privados (somente leitura); nenhuma AWS key no GitHub. Aprovar CA RDS
em `rds-ca-policy.json`. A revisão datada de acesso aos serviços FREE/backend em
`preflight_authorization` é atestação humana, não prova de capacidade física AWS.

## Diagnóstico, testes e pendências

Resumo/JSON por fase, duração, recurso/endereço Terraform, operação do provider,
código AWS/RequestId quando disponível e próxima ação. Collector Kubernetes registra
status/probes/exit codes/event reasons e assinaturas conhecidas de logs limitados;
descarta mensagens arbitrárias, env, tokens e conteúdo privado. State, plano binário,
tfvars completos e payloads de Secrets não viram artifacts. Upload `always()`;
cancelamento forçado/checkout falho pode impedir upload. Inventário backend mantém
retomada. Erro do coletor/checkpoint/fechamento não oculta o erro original.

Testes offline cobrem gates, hashes, falhas parciais, interrupção, fechamento,
retomada, ownership, sanitização e ordem de teardown. São testes com clientes falsos;
não comprovam IAM efetivo, disponibilidade/capacidade, conectividade ou fechamento AWS.
Validação desta revisão: 28 testes do executor, 40 de recuperação, 20 checks Node
do executor, 20 testes do builder, 23 checks IAM e 18 checks de artefatos aprovados;
Terraform fmt, validate das quatro roots com backend desativado, 13 testes mock
de identidades e nove do módulo de recuperação aprovados; seis YAMLs parseados.
O módulo foi testado em cópia temporária com o lock Windows já validado do bootstrap,
pois seu lock local não incluía o checksum desse pacote/plataforma. Sem downloads.
O harness Billing 9.1.4 ainda usa Docker/provider mock: a fase `e2e` falha com
`AWS_E2E_ADAPTER_REQUIRED`. Login/atores/signing e pagamento sandbox também precisam
de preparação autorizada e aceitação real; não há relato fictício de E2E concluído.

Próximo passo: revisar Destroy e IAM/inventário, resolver o caminho EKS e completar
a aceitação AWS/E2E antes de autorizar o primeiro ambiente. Não habilitar demo-a
apenas porque os testes offline passaram.
