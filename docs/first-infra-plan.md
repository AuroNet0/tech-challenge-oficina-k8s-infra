# Primeiro Environment Up / Infra Plan

Base inspecionada: branch `homolog`, HEAD
`ad681d9b7951ad7c883b4af7927eac51c02bba13`. Preparação offline; nenhum comando
AWS, Terraform conectado, apply, destroy ou dispatch foi executado.

## Bloqueadores reais

O contexto operacional informa bootstrap, OIDC, backend e inventário já
funcionando. Isso não preenche as aprovações locais restantes:

| Bloqueador | Estado nesta entrega | Responsável pela próxima decisão |
|---|---|---|
| Habilitação de demo-a | `catalog.enabled=false`, `actions-first.execution_enabled=false` | Operador, em revisão explícita futura; ambos os gates continuam necessários para Up |
| Runtime | `approved=false`, `destroy_offline_verified=false` | Operador revisa backend, resultados offline e procedimento de recuperação antes de atestar |
| Elegibilidade FREE | `preflight_authorization={}` | Evidência específica da conta/configuração; saldo de créditos sozinho não aprova serviços |
| Permissões de backend | Revisão ainda não registrada | Conferir ações/ARNs e políticas efetivas, inclusive negativa explícita e KMS se aplicável |
| Acesso recuperável EKS | `private_creation_reviewed=false`, referência vazia | Revisar como recuperar Infra parcial sem depender de aplicações ou de abertura não protegida |
| Capacidade atual | Não consultada nesta implementação | Próximo Preflight autorizado consulta versões, ofertas e quota EC2; revisar também limites EKS/RDS antes de apply |

O catálogo e `actions-first.json` não são habilitados por esta alteração.
Nenhuma aprovação foi marcada como verdadeira. Alterar apenas arquivos de
configuração para satisfazer checks sem evidência não constitui aprovação.

## Separação por etapa

| Antes do Infra Plan | Depois de criar Infra | Platform, Edge e E2E |
|---|---|---|
| Dispatch manual em homolog, confirmação `UP demo-a` e aprovações dos gates | Existência/estado ACTIVE do cluster e recursos observados | Imagens/JAR/layer publicados com digest/versão |
| STS na conta 446517826146/us-east-1, OIDC e trusts exatos | Prova real do caminho privado ou canário independente antes de abrir endpoint | Pins/CI dos quatro serviços e recibos de publicação |
| Backend seguro, leitura do inventário e revisão de escrita/locks | RBAC e eventual elevação temporária aprovada para bootstrap | Secrets, PKI, migrações, health checks, ALB/JWKS |
| Versões, AZs, ofertas, quota e revisão de elegibilidade FREE | Atualização do inventário com IDs/ARNs efetivamente criados | Mercado Pago de teste, New Relic e aceitação E2E |
| Estratégia de recuperação e Destroy revisados; endpoint inicialmente privado | Revisão de IAM exato para retomada/remoção parcial | Aprovação específica das integrações e da janela de endpoint |

`scope=infra` registra acesso Kubernetes e artefatos de serviços como DEFERRED,
com motivos distintos. Isso não significa PASS desses requisitos, nem ambiente
pronto. Conta, backend, IAM/OIDC, versões, capacidade, revisão FREE e recuperação
continuam obrigatórios. Uma negativa AWS continua FAIL, inclusive na consulta de
um cluster existente; um endpoint público também continua bloqueando Infra.

`scope=full` preserva o gate do canário independente e os checks de revisões/CI.
A aprovação FREE é específica do escopo: a de Infra não autoriza Lambda/ELB ou
as fases posteriores. Health checks e integrações já pertencem às fases de
delivery; nenhum foi transformado em PASS antecipado.

## Contratos atuais da Infra

O runtime agora contém os inputs públicos necessários; o Preflight confere seu
alinhamento com `actions-first.json` antes do plan:

- EKS `1.35`, suporte STANDARD, autenticação API, sem administrador implícito do
  criador. `eks_public_access_cidrs=[]` mantém o endpoint privado desde a criação.
- Dois nodes ON_DEMAND `m7i-flex.large`, AL2023 x86_64, desejado/mínimo/máximo 2,
  volumes gp3 criptografados de 30 GiB. O default Terraform é 1, mas o executor e
  os inputs desta operação fixam 2. O Preflight exige quota para 4 vCPUs mais uso
  existente, assumindo o retorno real de 2 vCPUs por instância.
- As AZs são `us-east-1a` e `us-east-1b`; a oferta do tipo é verificada em ambas.
  O node group atual usa somente a subnet pública da primeira AZ. Dois nodes
  não significam nodes distribuídos entre duas AZs; não se alterou essa topologia.
- Um RDS PostgreSQL `17.10`, `db.t4g.micro`, gp3 20 GiB, privado, Single-AZ na
  primeira AZ, senha master gerenciada pelo RDS. A subnet group usa as duas AZs.
  Não há backup automático/final snapshot nesta configuração efêmera; revisar
  perda de dados antes de apply. Bancos/usuários de aplicação são etapa posterior.
- Quatro repositórios ECR vazios são criados na Infra. Imagens publicadas não são
  necessárias para o plan/criação desses repositórios.
- Access Entries usam as roles persistentes provision/deploy/destroy exatas;
  a administração aponta a provision. As duas workload roles usam boundaries
  exatas `eks-cluster` e `eks-nodes`. A elevação cluster-admin continua falsa.

A [documentação EKS](https://docs.aws.amazon.com/eks/latest/userguide/cluster-endpoint.html)
distingue acesso à API Kubernetes de seu gerenciamento AWS. Esta root usa somente
provider AWS: cluster, node group e Access Entries podem ser gerenciados pelo
GitHub Actions sem kubectl nem acesso ao endpoint Kubernetes. O broker não precisa
ter canário de um cluster inexistente para gerar esse plan ou criar o cluster
privado. O Preflight exige, porém, revisão da estratégia de recuperação e não
permite endpoint público nessa etapa.

## Aprovação mínima do plano FREE

A [AWS distingue plano FREE, créditos e serviços elegíveis](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html).
Get/List, versões disponíveis e uma simulação de permissões não demonstram que
CreateCluster, CreateDBInstance ou lançamento EC2 serão aceitos nessa conta.
Não fazer uma criação de teste nem mudar para PAID automaticamente.

O operador precisa registrar uma referência verificável de revisão, datada e
específica da conta 446517826146/us-east-1, confirmando:

1. Plano FREE ativo, vencimento da conta/créditos, saldo, orçamento e duração
   máxima pretendida para manter EKS, dois nodes, RDS, discos, IPv4 e logs.
2. Elegibilidade **para criar** EKS gerenciado STANDARD 1.35, EC2 ON_DEMAND
   m7i-flex.large e RDS PostgreSQL 17.10/db.t4g.micro/20 GiB gp3, nessas AZs.
   Usar informação da conta/console/suporte AWS; permissão IAM é requisito separado.
3. Elegibilidade dos serviços auxiliares efetivamente usados pela Infra:
   ECR, Secrets Manager, CloudWatch Logs e IAM. Lambda/ELB ficam para revisão futura.
4. Ausência de restrição de conta/SCP/quota conhecida que impeça as configurações.
   Se a evidência não confirmar um serviço, manter o gate bloqueado e consultar
   a AWS; não interpretar créditos disponíveis como autorização irrestrita.
5. Permissões exatas de backend e estratégia de recuperação descritas adiante.

Formato para preenchimento **manual**, sem estes valores constituírem aprovação:

```json
{
  "account": "446517826146",
  "region": "us-east-1",
  "scope": "infra",
  "valid_until": 0,
  "reference": "",
  "free_plan_creation_reviewed": false,
  "free_services_reviewed": [],
  "backend_permissions_reviewed": false
}
```

Esse objeto ocupa `demo-a.runtime.json.preflight_authorization`. Após a revisão,
o operador registra `valid_until` como epoch em segundos, futuro e inferior a
24 horas, referência não vazia e a lista revisada
`eks,rds,ec2,ecr,secretsmanager,logs,iam`. Somente com evidência pode atestar os
booleans correspondentes. O gate trata isso como atestação humana, não como prova
técnica de sucesso de uma futura criação. Renovar a revisão se mudar escopo,
configuração, condição de elegibilidade ou se expirar a janela.

## Backend exato para plan/apply

Bucket existente: `tech-challenge-oficina-terraform-state-b1cfa326`, conta
446517826146, região us-east-1. `TF_WORKSPACE=default`, versionamento, criptografia
e bloqueio público permanecem obrigatórios. [Permissões do backend S3](https://developer.hashicorp.com/terraform/language/backend/s3):

| Ações | ARN/restrição |
|---|---|
| `s3:GetBucketLocation`, `s3:GetBucketVersioning`, `s3:GetEncryptionConfiguration`, `s3:GetBucketPublicAccessBlock` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326` |
| `s3:ListBucket` | Mesmo bucket; prefixo limitado a `phase4/environments/demo-a/`, incluindo listagem de `workspaces/` usada pelo init |
| `s3:GetObject`, `s3:PutObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/infra.tfstate` |
| `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/infra.tfstate.tflock` |
| `s3:GetObject`, `s3:PutObject` | `arn:aws:s3:::tech-challenge-oficina-terraform-state-b1cfa326/phase4/environments/demo-a/operations/inventory.json` |

Restringir ao owner `s3:ResourceAccount=446517826146`; sem DeleteObject no state
ou inventário. As políticas locais já declaram esses acessos, com states separados
para platform/edge. Não foi alterado IAM local/remoto. Não solicitar `s3:*` nem
permissões do backend bootstrap no backend de ambiente. Se o backend efetivo usar
KMS, revisar Decrypt/GenerateDataKey na **chave real** e sua key policy; não usar
ARN fictício ou wildcard de chaves. O executor usa SSE AES256 no journal.

Plan não é totalmente sem escrita AWS: Terraform usa locks S3. O caminho sem
hash retorna `PLAN_ONLY` antes de salvar o inventário de apply ou executar apply.
O plano binário fica temporário no runner; não é publicado como artifact.

## Inventário e IAM antes de apply

No primeiro plan, são necessárias leituras do state/inventário e dos recursos
que já existam; não se exige publicar aplicações. O bootstrap local ainda tem
`inventory={}` e `inventory_approved=false`. Isso deixa a permissão condicional
de **criar** workload roles sem ser emitida; é bloqueador de apply, não prova de
que Terraform plan precise criar essas roles.

Proposta mínima para revisão no bootstrap, sem aprová-la nesta entrega:

```json
{
  "inventory": {
    "workload_boundaries": {
      "eks-cluster": "arn:aws:iam::446517826146:policy/tc-oficina-p4-demo-a-eks-cluster-boundary",
      "eks-nodes": "arn:aws:iam::446517826146:policy/tc-oficina-p4-demo-a-eks-nodes-boundary"
    }
  },
  "inventory_approved": false,
  "destroy_confirmation": ""
}
```

Revisar existência/conteúdo das boundaries e os statements condicionais:

| Ação | ARN e condição |
|---|---|
| `iam:CreateRole` | `arn:aws:iam::446517826146:role/tc-oficina-p4-demo-a-eks-cluster`, boundary `arn:aws:iam::446517826146:policy/tc-oficina-p4-demo-a-eks-cluster-boundary` |
| `iam:CreateRole` | `arn:aws:iam::446517826146:role/tc-oficina-p4-demo-a-eks-nodes`, boundary `arn:aws:iam::446517826146:policy/tc-oficina-p4-demo-a-eks-nodes-boundary` |
| `iam:PutRolePolicy`, `iam:UpdateAssumeRolePolicy`, `iam:TagRole` | Somente essas duas roles |
| `iam:AttachRolePolicy` | Cluster: `arn:aws:iam::aws:policy/AmazonEKSClusterPolicy`; nodes: `arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy` e `arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy` |
| `iam:PassRole` | Role cluster somente para `eks.amazonaws.com`; nodes para EKS/EC2, conforme statements existentes |

Aplicar eventual ajuste de bootstrap apenas por seu GitHub Actions, com inventário
revisado e hash exato aprovado. Não é parte desta implementação nem do primeiro
Infra Plan. As permissões existentes de criação EC2 são limitadas por tipo,
região e tags; não substituir por permissões genéricas para contornar uma negativa.

IDs de VPC/subnet/SG/route table/nodegroup ainda desconhecidos não devem ser
inventados. Após qualquer apply, inclusive parcial, revisar o checkpoint e
`identity-inventory.json` com ARNs observados, estado Terraform e ownership.
Só então aprovar permissões de atualização/remoção desses recursos. A criação
de RDS com senha gerenciada pode produzir ARN de secret master ainda desconhecido;
nenhuma leitura de seu valor é necessária para o primeiro plan.

## Recuperação e Destroy

`Environment Destroy`, suas roles, confirmações, isolamento de demo-b, ordem de
teardown e preservação de state/inventário permanecem inalterados. O fluxo salva
inputs antes de apply e tenta checkpoint após falha; uma falha de checkpoint não
oculta o erro original. Não executar Destroy como parte do primeiro plan.

O operador deve revisar o procedimento e preencher manualmente
`endpoint.recovery_reference`, `endpoint.private_creation_reviewed` e
`destroy_offline_verified` somente após aceitar a estratégia. O canário
`independent_canary_verified` deve continuar falso até prova real.

Limite importante antes de qualquer apply: o teardown atual de um cluster
existente usa Kubernetes e requer caminho privado utilizável ou broker com
recuperação comprovada. Portanto um cluster parcialmente criado não implica
Destroy automaticamente executável. A referência de recuperação precisa incluir
retomada via Terraform/AWS management APIs pelo GitHub Actions, atualização de
IAM com ARNs observados e obtenção do acesso necessário para esse teardown.
Se necessário, a elevação temporária da role provision exige revisão explícita
e revogação posterior; ela não foi ativada aqui. Sem procedimento operacional
viável, bloquear apply mesmo que o plan seja gerado com sucesso.

## Execução do primeiro plano pelo operador

1. Revisar este diff em homolog. Registrar as evidências FREE/backend/recuperação.
   O operador ainda precisa aprovar manualmente a habilitação do catálogo,
   `actions-first.execution_enabled`, runtime `approved` e
   `destroy_offline_verified`. Não preencher só para obter resultado verde.
   Nenhum desses valores foi habilitado nesta entrega.
2. Manter os inputs públicos preparados, sem CIDRs públicos e sem elevação
   cluster-admin. Manter platform/edge sem aprovação e sem inventar recibos,
   imagens, secrets de Mercado Pago/New Relic ou canário.
3. Confirmar as aprovações já administradas do GitHub Environment provision:
   `PHASE4_EXECUTION_APPROVED` e a inspeção `PHASE4_READONLY_APPROVED`, além de
   reviewers/proteções existentes. Esta implementação não altera Environments.
4. Pela interface Actions, selecionar **Environment Up**, branch **homolog**,
   `confirmation=UP demo-a`, `phase=infra`, `approved_plan_sha256` **vazio**.
   Não executar Terraform/AWS localmente. O workflow chama Preflight com
   `scope=infra`, assume provision via OIDC e gera o plano.
5. No artifact `up-infra-<run>-<attempt>`, revisar `report.json`, `summary.md` e
   `plan.json`: resultado `PLAN_ONLY`, SHA256 e lista de mudanças, além dos logs.
   Conferir o SHA Git efetivo do run após a revisão; não reutilizar hash do HEAD
   base anterior às alterações. State e plano binário não são artifacts públicos.
6. **Parar para revisão.** Não fornecer o hash nesta primeira execução. Futuro
   apply exige nova decisão explícita, pré-requisitos de recuperação/IAM e o
   mesmo plano na mesma revisão/fase. Hash incorreto ou plano com deletes é
   rejeitado. A geração de um plano não aprova a execução nem garante capacidade.

## Testes

Os testes do gate Up agora incluem `preflight.test.mjs`. Testes de autorização
usam fixtures aprovadas/desabilitadas, em vez de exigir que o arquivo real nunca
possa ser aprovado pelo operador. Os arquivos desta entrega continuam desabilitados.
Os novos cenários cobrem Infra sem aplicações, revisão por escopo, aprovações
ausentes/expiradas, inputs divergentes, estratégia de recuperação ausente,
endpoint público e negativas reais. Testes existentes do executor cobrem plan
sem hash, hash incorreto, falha parcial, fechamento e Destroy isolado/retomável.

Validação executada, sem acesso AWS ou Terraform real:

| Suíte | Resultado |
|---|---|
| `node --test scripts/environment-actions/control.test.mjs scripts/environment-actions/preflight.test.mjs` | 43/43 passaram |
| `python3 -B -m unittest discover -s scripts/environment-actions -p 'test_*.py'` | 28/28 passaram, incluindo aprovação por hash e falhas parciais |
| `python3 -B -m unittest discover -s scripts/eks-endpoint-recovery -p 'test_*.py'` | 40/40 passaram |
| `node --test scripts/environment-actions/*.test.mjs scripts/identity-contracts/contracts.test.mjs` | 65 passaram, 1 falha preexistente |
| `git diff --check` | Passou |

A falha preexistente está em `bootstrap-inputs.test.mjs:19`: espera subject
`classic`, enquanto o HEAD base já configura `immutable`. Foi confirmada com
`git show HEAD` nos dois arquivos e não foi alterada por ser fora dos gates
modificados. Essa suíte bootstrap não faz parte do gate Up. Os 23 checks internos
de contratos OIDC/lease também passaram. Os resultados offline não comprovam IAM,
elegibilidade FREE, quotas ou capacidade atuais da conta.
