# AWS Demo MVP — prontidão de `demo-a`

Revisão local em 08/11/2026. Repositório `AuroNet0/tech-challenge-oficina-k8s-infra`,
branch `homolog`, HEAD confirmado `02f08249276f9148fbbb1305a321d7056f3d9c0b`.
Árvore inicialmente limpa. Escopo exclusivo: `demo-a`, `us-east-1`.
`demo-a` e `demo-b` permanecem desabilitados, com conta e bucket nulos.
Nenhuma chamada AWS CLI, plan conectado, apply/destroy, publicação, operação
Kubernetes, alteração remota, commit ou push foi executada.

**Conclusão:** há uma base reutilizável, mas ainda não um executor operacional
completo. O primeiro deploy depende principalmente de configuração real,
bootstrap IAM/backend, transporte de secrets, publicação dos seis artefatos,
identidades de demonstração e instalação dos controllers. Os flags de readiness
existentes não substituem essas operações nem suas evidências.

## A. O que já está pronto

### Inventário e limites da comprovação

| Componente | Implementação reutilizável | Situação observada |
| --- | --- | --- |
| Identidade do ambiente e states | `phase4/environments/catalog.json`, `phase4/modules/environment`, `scripts/Resolve-Environment.ps1`, `scripts/Assert-AwsIdentity.ps1` | Três states separados; validação de conta/região/revisão; operação bloqueada enquanto catálogo não aprovado. |
| Rede | `phase4/stacks/infra/network.tf`, `security.tf` | VPC `10.42.0.0/16`; duas sub-redes públicas e duas privadas em duas AZs; IGW; sem NAT. Nodes públicos com saída HTTPS; RDS, ALB e Lambda privados. Duas AZs de rede atendem EKS/ALB/subnet group; o banco continua Single-AZ. |
| EKS | `infra/eks.tf`, `iam.tf`, `access.tf` | AL2023 x86_64, node group fixo `m7i-flex.large`, disco raiz gp3 de 30 GiB/node; acesso privado por padrão; acesso administrativo temporário declarado. |
| ECR | `infra/ecr.tf` | Quatro repositórios `tc-oficina-p4-demo-a-{auth,os,execution,billing}`, tags imutáveis, scan no push, retenção de dez imagens. `force_delete=false`. |
| PostgreSQL | `infra/rds.tf`, `infra/outputs.tf` | PostgreSQL 17, TLS obrigatório, privado, um RDS Single-AZ, senha master gerenciada pelo RDS/Secrets Manager. Contrato de três bancos/usuários exclusivos, sem senha no state. Criar a instância não cria esses três bancos. |
| Platform | `phase4/stacks/platform` | Terraform cria addons/identidades AWS e **renderiza** Lists JSON; não instala os manifests Kubernetes. Namespace `tc-oficina-p4-demo-a`, SAs, RBAC, NetworkPolicies, Services, Deployments e Jobs declarados. |
| Dados Kubernetes | `platform/kubernetes.tf`, `platform/scripts/*` | MongoDB com `rs0`, keyfile, bootstrap e probe de PRIMARY; RabbitMQ com definitions, vhost `/oficina`, três usuários. Um StatefulSet/replica e PVC gp3 de 5 GiB para cada dependência. StorageClass com `Delete` e `WaitForFirstConsumer`. |
| Bancos e migrações | `platform/scripts/bootstrap.sql`, `verify-postgres.sql`, `auth-internal.tf` | Job SQL cria bancos/roles/grants; Auth usa `MigrateIdentity` separado por release e usuário migrador; OS/Billing executam Flyway no startup e Hibernate `validate`. |
| Secrets e PKI | `scripts/platform-secrets/*`, `scripts/Prepare-PlatformSecrets.ps1`, outputs Platform | Núcleo de geração, reuso idempotente, validação PKCS12 e projeção de 18 Secrets, com adaptadores que recebem clientes. CLI/PowerShell expõem apenas dry-run. Chaves/senhas não passam pelo Terraform. |
| Aplicações | `platform/applications.tf`, `auth-internal.tf` | Uma replica por aplicação quando autorizada; requests/limits, startup/readiness/liveness, TLS e secrets por referência. Auth interno sem chave de assinatura pública. |
| Edge | `phase4/stacks/edge` | HTTP API, VPC Link, SGs, endpoint privado Secrets Manager, Lambda Java 21/x86_64, logs e allowlist. Auth possui duas rotas; negócio possui 40, mais webhook opt-in. Ingress interno/ALB HTTPS é renderizado para controller externo. |
| JWT e comunicação | Outputs Edge/Platform e ConfigMaps | Issuer é o endpoint real da HTTP API, audience `tc-oficina-p4-demo-a`, JWKS `/.well-known/jwks.json`. REST interno mTLS OS→Auth, Execution→OS e Billing→OS; Saga por RabbitMQ. Execution usa MongoDB com `replicaSet=rs0`. |
| IAM/OIDC | `phase4/bootstrap/identities`, `phase4/modules/alb-controller-policy`, RBAC Platform | Contratos provision/deploy/destroy, boundaries, inventário e associação Pod Identity preparados; instalação real e documentos das boundaries ainda dependem do operador. |
| Recuperação EKS | `phase4/modules/endpoint-recovery`, `scripts/eks-endpoint-recovery/recovery.py`, `phase4/bootstrap/identities/recovery.tf` | Broker/lease/recuperador declarados e testáveis offline. `enabled=false` e `recovery_verified=false`; não presumir abertura operacional. Outputs históricos também conservam indicadores falsos. |
| Artefatos | `scripts/artifact-release/release.py`, `docs/9.2.5-B-artifact-release.md` | Build/validação/recibos offline para quatro imagens, JAR público Auth e layer. `publish` rejeita publicação; não há transporte AWS operacional nessa CLI. |
| New Relic | `platform/outputs.tf`, output `observability` | Values do `nri-bundle`: infraestrutura/kubelet, kube-state-metrics, logging e eventos, lowDataMode. `installed=false`, licença externa. `apm_agent_bundled=false`. |
| E2E/BDD | Checkout irmão Billing, `integration/9.1.4` | Cenários distribuídos existentes, feature em português e runner; evidência histórica de 206 verificações integradas e 641 testes Maven. Essa evidência é local, não valida AWS. |
| CI | `.github/workflows/ci.yml` dos quatro checkouts irmãos | Build Java 21, testes e verificadores de pacote/imagem presentes. CI validado no GitHub é informação fornecida pelo operador; não houve nova consulta remota nesta revisão. |

Os quatro Dockerfiles locais são multistage, Java 21, usuário 10001 e bases por
digest. Auth preserva `InternalAuthMain`; OS/Execution/Billing usam `/app/app.jar`,
compatível com o comando sobrescrito nos Deployments. `EXPOSE 8080` não impede
o uso de `SERVER_PORT=8443`. RDS t4g ARM não exige imagens ARM: clientes Java
executam nos nodes x86_64 e acessam PostgreSQL pela rede.

A inspeção Git somente leitura dos irmãos encontrou Auth em
`6177535915d7f37bf42496e0c423b6af523bbfef`, limpo. OS/Execution/Billing têm HEADs
`9391d3b9d4a5949a2c6447fc01becbb87d45142e`,
`929c6de05e6b482d82fd097e6cd61ac1d38f9b30` e
`c87c72f69c44978ff1211e19c582dd0c6ec2a6ad`, com alterações locais pré-existentes
em Docker/CI/verificadores. Portanto esses HEADs antigos não identificam todos
os bytes inspecionados. Não houve alteração nesses repositórios. Para publicar,
selecionar as revisões limpas efetivamente aprovadas no CI; não inferir que os
checkouts locais representam os HEADs remotos atuais.

### Alinhamento com o PDF FIAP

Consultado o PDF local `../12SOAT - Fase 4 - Tech challenge.pdf`, páginas 2–6.
Exige serviços/repositórios/bancos independentes, SQL e NoSQL, REST/mensageria,
Saga com compensação, testes unitários, um fluxo BDD, cobertura ≥80%, qualidade
no CI, deploy Kubernetes automatizado e observabilidade. O vídeo de até 15 minutos
deve mostrar fluxo completo, falha/Saga, deploy automatizado de pelo menos um
serviço com testes e monitoramento/rastreamento. Mercado Pago é requisito.

Três bancos PostgreSQL separados com roles próprias no mesmo RDS preservam a
separação lógica já escolhida, e Execution tem MongoDB próprio. Não é necessário
criar três instâncias RDS para esta demonstração. O procedimento manual permite
o primeiro ambiente, mas não encerra o requisito de CD automatizado. Também será
necessário registrar evidência da ferramenta de qualidade usada no CI e da
proteção de `main`; não foram verificadas configurações remotas nesta etapa.

### Validação desta revisão

- Branch/HEAD/status inicial confirmados por Git instalado fora do PATH.
- `Test-EnvironmentContract.ps1`: 19 verificações offline aprovadas.
- `node scripts/identity-contracts/contracts.test.mjs`: 23 checks aprovados.
- `node scripts/artifact-release/check-contracts.mjs`: 16 checks aprovados.
- Nenhum scanner profundo de dependências foi repetido. Os bloqueios históricos
  Netty não devem ser reabertos automaticamente: a revisão B.1-S do documento de
  artefatos registra a classificação contextual já implementada.
- Terraform, Python, Maven e Java 21 não estão disponíveis no PATH desta sessão.
  Há JDK 25 instalado, que não comprova Java 21. Docker instalado recusou acesso
  ao daemon no sandbox. Não foram repetidos build, Terraform validate/test, PKI,
  bancos ou E2E. Resultados anteriores estão nos documentos de cada etapa.

## B. O que falta para subir o ambiente

Prioridades: P0 antes de criar recursos ou iniciar a fase dependente; P1 para
concluir a demonstração/entrega; P2 melhoria dispensável agora. **BLOQUEADOR**
impede o primeiro deploy funcional completo; **IMPORTANTE** atende à entrega;
**OPCIONAL** não precisa ser feito agora. Não há evidência para refatorar os
módulos funcionais ou aumentar a arquitetura.

| Bloqueador / classificação | Motivo e evidência | Arquivo ou módulo responsável | Correção mínima | Prioridade | Autorização / informação do operador |
| --- | --- | --- | --- | --- | --- |
| Catálogo e backend — BLOQUEADOR | `enabled=false`, conta/bucket nulos; guards recusam operação. | `catalog.json`, módulo environment, `bootstrap/` | Informar conta real e bucket persistente aprovado com versionamento, criptografia e lock S3; só depois habilitar `demo-a`. Bootstrap antigo de bucket é referência, não aplicar legado inteiro. | P0 | Conta, sessão temporária, bucket real e autorização explícita. `demo-b` fica intacto. |
| IAM/bootstrap — BLOQUEADOR | Roles só declaradas; OIDC existente é data source; boundaries começam vazias e criação automática exige `boundary_ready`. Inventário vazio não autoriza todas as fases seguintes. | `phase4/bootstrap/identities/{main,policies,boundaries,operations}.tf` | Instalar com identidade privilegiada já autorizada, subjects GitHub reais, cinco documentos mínimos de boundary e backend próprio. Alimentar inventário com ARNs observados após cada fase; não dar AdministratorAccess ao deploy. | P0 | OIDC/provider existente, subjects/IDs verificados, roles e ownership aprovados. |
| Caminho até API EKS — BLOQUEADOR para kubectl | Default privado; runner/estação externa sem caminho não alcança cluster. Broker ainda desabilitado. | `infra/eks.tf`, módulo endpoint-recovery e bootstrap recovery | Usar caminho privado já disponível; se inexistente, concluir teste/ativação autorizada do broker e recuperação independente antes de abrir /32 observado. Não basta fornecer CIDR ao Terraform: role provision nega abertura direta. | P0 | Caminho de rede, executor e responsável; autorização posterior para eventual abertura/ativação. |
| Versões/AZs/capacidade AWS — BLOQUEADOR de configuração | Defaults EKS `1.35`, PostgreSQL `17.10`; addons exigem versões completas, CSI ≥1.58; nenhuma disponibilidade regional foi consultada. | `infra/variables.tf`, `platform/variables.tf`, locks | Confirmar versões suportadas/compatíveis e duas AZs em preflight autorizado; preencher tfvars com valores reais. Não afirmar que esses defaults são inválidos sem consulta. | P0 | Operador valida suporte/oferta/quotas de conta em `us-east-1`. |
| Capacidade de um node — BLOQUEADOR de prontidão para carga completa | Aplicações + dados + controller já pedem 1550m; observabilidade e addons disputam o restante de 2 vCPU, antes das reservas. | `infra/variables.tf`, `eks.tf`; resources Platform | Usar `node_count=2` no tfvars exclusivo de `demo-a`, sem mudar default global, réplicas ou autoscaler; conferir allocatable e requests reais após instalar charts. | P0 | Aprovar custo de dois nodes fixos; detalhes abaixo. |
| Bucket/publicação — BLOQUEADOR | Nenhuma root Fase 4 cria `<prefix>-artifacts`; CLI release publica apenas offline. IAM deploy não cria bucket. | `scripts/artifact-release/release.py`, contrato `auth_release`, bootstrap IAM | Criar bucket privado/versionado regional por bootstrap autorizado; publicar manualmente quatro imagens/JAR/layer com readback. Registrar digest ECR, VersionId, checksum e ARN layer reais. Não ampliar scanner. | P0 | Bucket exato, permissão de criação/upload e revisões limpas dos quatro serviços. |
| CA pública RDS — BLOQUEADOR | `rds-ca-policy.json` vazio; pacote operacional é rejeitado sem CA aprovada. | `scripts/artifact-release/rds-ca-policy.json`, `platform/outputs.tf` | Obter bundle regional público oficial, validar validade/cadeia, revisar SHA-256/origem no formato existente e entregar os mesmos bytes na layer e ConfigMap `rds-ca`. | P0 | Revisão do material público, sem certificado fictício. |
| Executor Secrets/PKI — BLOQUEADOR | CLI metadata-only; output `live_transport_enabled=false`. Adaptadores precisam clientes, inventário e execução. | `scripts/platform-secrets/{platform_secrets,transports,pki}.py`, wrapper PS | Conectar os adaptadores existentes a clientes autenticados, chamar prepare/deliver e cleanup; gerar em memória e guardar fontes no Secrets Manager. Sem novo cofre, `SecretString` em Terraform ou arquivos Git. | P0 | Sessão permitida, ARNs reais, roots públicas dos JREs produtivos e entradas externas. |
| Assinatura e atores Auth — BLOQUEADOR de login/fluxo | Platform não gera `auth-signing`; migrações não cadastram usuários. `GenerateLocalKeys` só permite ambiente `local`. | Edge `lambda.tf`, Auth `KeyDocuments`, `docs/identity.md`; harness 9.1.4 | Provisionar documento RS256 no formato existente por operador do emissor; provisionar gerente, cliente, mecânico, vínculos e atribuições técnicas usando casos de uso existentes em ferramenta administrativa restrita. Não copiar fixtures nem abrir novas rotas. | P0 | Identidades/senhas sintéticas de demo, operador autorizado; runner deploy não recebe leitura de `auth-signing`. |
| Bootstrap de bancos — BLOQUEADOR de execução | RDS cria só `oficina_bootstrap`; Jobs ainda não executados; flags falsos. | `platform/scripts/bootstrap.sql`, `verify-postgres.sql`, `auth-internal.tf` | Entregar secrets/CA; executar Job PostgreSQL e Job Auth da release; verificar grants/Flyway/conexões. Depois remover projeções temporárias e ligar apps. | P0 | Evidência real dos Jobs e acesso EKS autorizado. |
| Instalação Kubernetes — BLOQUEADOR | Terraform renderiza manifests, não faz apply; CSI/Pod Identity/VPC CNI e enforcement precisam estar operacionais. | `platform/controllers.tf`, `outputs.tf`, RBAC | Exportar Lists públicas, aplicar na ordem da seção C, aguardar addons/dados/Jobs, depois Deployments. Usar `application_replicas=1` só após evidência dos gates. | P0 | Janela de bootstrap admin, sessão e permissão kubectl corretas. |
| Controller/ACM/listener — BLOQUEADOR de APIs de negócio | Controller Helm não instalado; `alb_tls=null`; ALB é criado pelo Ingress, não pela root AWS. | Platform identities/outputs; Edge ingress/certificate/network | Instalar associação Pod Identity/Helm existentes, informar ACM confiável e SAN de domínio controlado, aplicar Ingress, observar listener e targets reais antes de `publish_business`. | P0 | Domínio/DNS para validação ACM e ARN regional; não trocar por CA técnica/autossinada. |
| Pagamento teste — BLOQUEADOR do fluxo completo | Mercado Pago desativado; token/webhook/URLs vazios. Mock 9.1.4 é local, não provedor real. | `platform/applications.tf`, variável `mercado_pago`, Edge webhook | Fornecer credenciais de teste Mercado Pago e URLs HTTPS, validar assinatura/callback/reconciliação e habilitar webhook após negócio pronto. | P0 para pagamento | Conta sandbox/teste, comprador/vendedor de teste e credenciais fora de tfvars/state. |
| Aceitação AWS — IMPORTANTE | Runner 9.1.4 usa hosts/bancos de fixture, provider mock e controle Docker; não é runner AWS pronto. | Billing `integration/9.1.4/{runner.py,oficina.feature,*.ps1}` | Reutilizar passos/asserções para execução HTTP pela API real e inspeção autorizada dos recursos; parametrizar transporte/URLs em adaptador do harness existente, ou executar checklist manual no primeiro deploy. | P1 | Revisões/atores/URLs finais; alterações no serviço/harness proprietário exigem etapa própria. |
| New Relic — IMPORTANTE | Só values renderizados, licença externa e nenhum agente APM carregado. `NEW_RELIC_APP_NAME` sozinho não instrumenta Java. | `platform/outputs.tf`, `applications.tf` | Instalar `nri-bundle` com secret de licença fora do Terraform; mostrar métricas/pods/logs/eventos. Para rastreamento distribuído do PDF, demonstrar correlação real por OS/Saga/eventId ou instrumentação já existente; se insuficiente, preparar agente Java em etapa específica. | P1 | Conta/região/licença/plano New Relic, chart exato e aprovação de ingestão. |
| CD manual automatizado — IMPORTANTE | Único workflow Infra é legado, ainda contém apply. Não há Environment Up/Destroy Fase 4. | `.github/workflows/ci-cd.yml`, contratos IAM/OIDC | Manter legado sem uso. Concluir dois workflows dispatch após validar procedimento manual; demo do PDF precisa ao menos um deploy automatizado com testes. Desenho abaixo, sem YAML operacional novo nesta entrega. | P1 | Environments GitHub/subjects, revisores e autorização para ativação posterior. |
| Limpeza coordenada — IMPORTANTE, preparar antes do Up | ECR não vazio recusa delete; Ingress/PVC/Helm/secrets/artefatos externos não são geridos integralmente pelo Terraform. Destroy IAM depende de inventário aprovado. | `infra/ecr.tf`, Platform/Edge, bootstrap `operations.tf` | Registrar inventário desde criação; executar ordem C.8; incluir versões S3/layer/Secrets e verificação EBS/ENIs. Não remover backend/bootstrap persistente junto do ambiente. | P1 | Confirmação destrutiva futura e ARNs/UIDs exatos. |
| HA, autoscaling avançado, dashboards próprios — OPCIONAL | Não impedem demonstração acadêmica. | Sem alteração necessária | Manter RDS Single-AZ, uma replica/app, Mongo/Rabbit single-node e telas New Relic existentes. | P2 | Nenhuma ação agora. |

### Capacidade mínima proposta

`m7i-flex.large` tem 2 vCPU/8 GiB ([especificações AWS](https://aws.amazon.com/ec2/instance-types/m7i/)).
Requests declarados: quatro apps = 1000m/2048 MiB; Mongo = 300m/768 MiB;
Rabbit = 150m/384 MiB; ALB controller = 100m/128 MiB. Total estável conhecido
**1550m/3328 MiB**, sem infraestrutura Kubernetes/New Relic. Job PostgreSQL
adiciona 100m/128 MiB; Auth migrate adiciona 250m/512 MiB, preferencialmente antes
dos apps. Limits estáveis conhecidos somam 5500m/5376 MiB; limits CPU não são
reserva nem necessidade simultânea garantida.

O values New Relic declara outros 250m/432 MiB por conjunto dos componentes
listados; multiplicidade efetiva inclui DaemonSets e depende do chart escolhido.
Somam-se CoreDNS, CNI/agente NetworkPolicy, Pod Identity, CSI e reservas do SO.
Um node não oferece margem comprovada para todos. **Dois nodes fixos na mesma
AZ** são a configuração inicial proposta, usando a variável existente; não são HA.
Depois de instalar charts, confirmar `kubectl describe nodes`, requests por node,
limite de pods/IPs e ausência de Pending/OOM. Ajustar só se houver evidência real.
`db.t4g.micro` permanece hipótese para carga sequencial, pools OS/Billing de três
conexões e Auth interno de duas; não há motivo comprovado nesta revisão para
aumentar o RDS. Verificar conexões/memória/latência com Lambda ativa.

### Entradas externas e valores gerados

| Operador precisa fornecer/aprovar | Pode ser obtido/gerado durante provisionamento autorizado |
| --- | --- |
| Conta AWS real, região, backend e sessão temporária; budget/janela de demo | IDs/ARNs de VPC, subnets, SG, EKS/RDS/ECR; outputs públicos e inventário |
| OIDC/provider/subjects reais ou identidade manual já autorizada com acesso EKS; boundaries e privilégios de bootstrap | Roles e associações declaradas, após aprovação; inventário atualizado entre fases |
| Duas AZs, versões compatíveis, `node_count=2`, revisões limpas e evidências CI | Build de imagens linux/amd64/JAR/layer; digests ECR, S3 VersionId e ARN de layer por leitura real |
| Bundle CA RDS revisado, roots públicas dos JREs, domínio/SAN e validação DNS ACM | Certificado ACM público após validação; CA técnica/identidades PKCS12 por ambiente com SANs dos Services |
| Autorização do operador de identidade e dados sintéticos dos atores/papéis/vínculos | Senhas fortes, keyfile Mongo, cookie Rabbit, credenciais separadas PostgreSQL/Mongo/Rabbit e definitions derivadas pelo núcleo existente |
| Material/documento de assinatura RS256 sob controle do emissor | Chave pode ser gerada em memória pelo procedimento autorizado do emissor e gravada diretamente no Secrets Manager; não há comando operacional demo-a pronto para isso |
| Credenciais de teste Mercado Pago e licença/conta/região New Relic | Endpoint HTTP API, issuer/JWKS/audience e URLs webhook; nunca gerar tokens de fornecedor fictícios |

Guardar secrets nas fontes canônicas `/oficina/phase4/demo-a/*` já contratadas;
master RDS vem do secret gerenciado pelo RDS. Terraform recebe **somente metadados**.
Não usar `aws_secretsmanager_secret_version`, `random_password`, `tls_private_key`
ou `kubernetes_secret` para colocar esses valores no state. PKI técnica atual tem
validade padrão de 48 horas: gerar perto da demonstração e conferir janela antes
de reutilizar, sem regeneração silenciosa de fontes existentes.

## C. Plano de execução

### Regras de uso dos comandos

Todos os comandos AWS/Terraform operacional/kubectl/Helm a seguir são **roteiro
para uma etapa futura autorizada**, não foram executados. Exemplos operacionais
usam Bash com ferramentas instaladas, credenciais temporárias já obtidas e
diretório privado de trabalho fora do Git (`$RUN`). Metavariáveis como `$ACCOUNT`,
`$REV`, `$AUTH_SHA`, `$BACKEND` e `$CLUSTER` devem vir dos valores reais aprovados;
não representam números, ARNs ou checksums de exemplo. Conferir exit code a cada
comando; interromper se falhar. Não executar blocos como receita única sem os
gates intermediários. Não inserir segredos em argumentos, tfvars ou evidências.

### 1. Configurar pré-requisitos AWS

1. Instalar Terraform da versão fixada no catálogo (`1.15.8`, confirmar pacote
   disponível), AWS CLI v2, kubectl compatível, Helm, Git, Python com requirements
   de PKI, Java 21, Maven e Docker Linux. O PATH desta sessão precisa ser corrigido.
2. Aprovar conta/backend e janela; autenticar sem chaves permanentes. Na futura
   sessão autorizada, `aws sts get-caller-identity` deve corresponder à conta e
   principal esperados. O script `Assert-AwsIdentity.ps1` compara valores; **não
   consulta STS por conta própria**. Não aprovar identidade usando inputs como
   se fossem observações reais.
3. Instalar/reutilizar backend persistente; revisar `bootstrap/` apenas para o
   bucket. Não executar roots legadas `terraform/` ou Terraform das aplicações.
4. Preparar `phase4/bootstrap/identities` com state
   `phase4/bootstrap/identities/demo-a.tfstate`. `installation_approved`,
   `oidc_claims_verified`, subjects e boundaries exigem comprovação. Provisionar
   OIDC existente se necessário em bootstrap autorizado; essa root apenas o lê.
5. Se usar execução manual em estação com caminho privado já existente,
   selecionar explicitamente a role administrativa temporária real em
   `eks_admin_role_arn`. As roles GitHub só confiam em WebIdentity do GitHub:
   um `aws sts assume-role` comum não as torna utilizáveis pela estação.
   Para GitHub usar os contratos provision/deploy/destroy e suas access entries.
6. Só após autorização editar `demo-a` com conta/backend reais e `enabled=true`.
   Manter `demo-b` disabled. Não reutilizar inventários/fixtures de outro ambiente.

Resolver existente, já com observações reais em mãos:

```powershell
./scripts/Resolve-Environment.ps1 -EnvironmentId demo-a -Stack infra -GitRevision $Revision -RequireReady -ObservedAccountId $ObservedAccount -ObservedRegion us-east-1
```

Gerar backend HCL/JSON e tfvars públicos aprovados por stack a partir do resolver;
não existe orquestrador que faça isso automaticamente. Backend não aceita conta
nula. Usar keys exatas do catálogo, `encrypt=true`, `use_lockfile=true` e
`allowed_account_ids` real em todas as roots.

**Sucesso:** identidade/backend revisados, IAM e caminho EKS definidos, versões/AZs
confirmadas e único ambiente autorizado pronto. Sem essas condições, não iniciar
o relógio de cobrança com criação parcial sem caminho de conclusão.

### 2. Provisionar Infra

Em `$RUN/infra.tfvars.json`, além de identidade/revisão, informar as duas AZs,
versões confirmadas, role admin, boundaries/roles quando usando OIDC,
`node_count=2` e elevação temporária `cluster_bootstrap_access_enabled=true`
somente na janela aprovada. Endpoint privado continua default. Abertura pública,
se necessária, segue broker/lease/recuperação existentes após teste autorizado;
não é um simples override livre de CIDR nas roles GitHub.

```bash
terraform -chdir=phase4/stacks/infra init -input=false -lockfile=readonly -backend-config="$RUN/infra.backend.hcl"
terraform -chdir=phase4/stacks/infra validate
terraform -chdir=phase4/stacks/infra plan -input=false -var-file="$RUN/infra.tfvars.json" -out="$RUN/infra.plan"
terraform -chdir=phase4/stacks/infra apply "$RUN/infra.plan"
terraform -chdir=phase4/stacks/infra output -json platform_contract > "$RUN/infra-contract.json"
```

Conferir node group/2 nodes Ready, rede, repositórios e RDS disponível. Atualizar
inventário IAM com ARNs reais, inclusive master secret, antes de fases que os
exigem. Guardar só `platform_contract`, não distribuir state completo.
`aws eks update-kubeconfig --name "$CLUSTER" --region us-east-1` usa a sessão
autorizada; não resolve ausência de rota privada nem de access entry.

**Sucesso:** Infra consistente no state correto; nodes acessíveis pelo executor,
RDS privado/TLS e quatro ECR reais; contrato sem senha exportado.

### 3. Publicar artefatos

Construir revisões limpas dos quatro repositórios e reutilizar comandos dos seus
`ci.yml`: Docker linux/amd64 com labels source/revision, `mvn -B -ntp clean verify`
e `python -B scripts/verify_build.py --image "$IMAGE"`. Auth exige
`-Dauth.internal.image="$IMAGE"` no verify; Billing possui smoke próprio.
O build Docker faz package sem testes, portanto não substitui o verify.

Na Infra, comandos existentes opcionais para recibos offline:

```bash
python scripts/artifact-release/release.py build-app --service os --repository "$OS_REPO" --revision "$OS_SHA" --output "$RUN/build-os"
python scripts/artifact-release/release.py layer --revision "$REV" --ca "$RUN/us-east-1-bundle.pem" --output "$RUN/layer"
```

Repetir `build-app` para auth/execution/billing quando usando esse caminho.
Ele requer cache offline de dependências/bases e árvore limpa; não prometer que
completa com cache vazio. A publicação manual pode consumir builds já validados
pelos comandos CI; não precisa repetir scanner profundo ou nova suíte.
`layer` precisa de CA real aprovada em `rds-ca-policy.json`; `--fixture` não serve
para deploy. A layer deve conter `bin/auth-bootstrap` executável/LF,
`java/lib/auth-bootstrap.jar` e `auth-home/.postgresql/root.crt`.

Publicação futura por serviço, usando conta/repositório/SHA conferidos:

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com"
docker tag "$LOCAL_IMAGE" "$REPOSITORY_URL:$SERVICE_SHA"
docker push "$REPOSITORY_URL:$SERVICE_SHA"
aws ecr describe-images --region us-east-1 --repository-name "$REPOSITORY_NAME" --image-ids imageTag="$SERVICE_SHA" --query 'imageDetails[0].imageDigest' --output text
```

Usar `DOCKER_CONFIG` temporário privado e remover login/token ao terminar.
Repetir para os quatro destinos exatos. Verificar plataforma/Java do artefato e
manifesto remoto (`batch-get-image`, digest/config ou filho amd64 se houver index).
Passar para Platform somente `$REPOSITORY_URL@sha256:<digest observado>`.
SHA de arquivo/image ID não é digest ECR. Fixar também digests reais das imagens
compatíveis MongoDB 8, RabbitMQ 4 e cliente PostgreSQL 17.

Criar previamente `tc-oficina-p4-demo-a-artifacts` privado, versionado e na mesma
conta/região por bootstrap autorizado. Publicar Auth JAR e layer:

```bash
aws s3api put-object --region us-east-1 --bucket tc-oficina-p4-demo-a-artifacts --expected-bucket-owner "$ACCOUNT" --key "auth/$AUTH_SHA/auth.jar" --body "$AUTH_JAR" --if-none-match '*'
aws lambda publish-layer-version --region us-east-1 --layer-name tc-oficina-p4-demo-a-auth-bootstrap --zip-file "fileb://$LAYER_ZIP" --compatible-runtimes java21 --compatible-architectures x86_64
```

Capturar VersionId retornado, reler objeto pela versão com owner esperado,
comparar SHA-256; reconsultar layer/CodeSha256/runtime/arquitetura e conferir
bytes. Para reexecução de chave existente, comparar bytes da versão anterior;
não sobrescrever tag/chave imutável. Registrar `auth_release` com bucket/key,
version_id, SHA-256 **base64 do JAR**, revision Auth e bootstrap_revision Infra,
bootstrap_layer_arn real. Não confundir checksum hexadecimal com base64.

**Sucesso:** seis artefatos verificáveis remotamente e quatro digests ECR reais.
O script offline não emitirá aprovação remota: registrar recibo operacional
separado, sem alterar seu contrato para simular publicação.

### 4. Implantar Platform e preparar Auth público

A sequência é incremental para quebrar a dependência JWT/Platform/Edge:

1. Inicializar/aplicar Platform com backend próprio, `infra_contract` real,
   `images` completos, addon_versions e new_relic_chart_version exatos,
   `jwt=null`, `application_replicas=0`, flags de migração/readiness falsos.
   Essa fase cria addons/identidades e outputs; não inicia aplicações.
2. Exportar `edge_contract` da Platform. Fazer **primeiro apply Edge** com
   `publish_auth=false`, `prepare_alb=false`, `publish_business=false`,
   `publish_webhook=false`. A HTTP API sem rotas cria endpoint/jwt_contract e
   SG/rede para os próximos passos; já gera custos. Fornecer à Edge
   `infra_contract` = `infra.platform_contract` e `platform_contract` =
   `platform.edge_contract`; `infra.edge_contract` sozinho não atende à Edge.
3. Aplicar bundle `foundation`, depois `network`; aguardar Pod Identity/CSI/VPC
   CNI e verificar enforcement. Entregar ConfigMap `rds-ca` com bundle validado.
4. Montar contrato público agregado com `infra_contract`, `secret_contract` e
   `secret_delivery_contract`, conforme testes de integração dos adaptadores.
   Reutilizar `prepare`/`deliver` em memória com os clientes reais e inventário.
   `Prepare-PlatformSecrets.ps1` só faz dry-run, não é entrega de secrets.
5. Aplicar `data`; aguardar Mongo PRIMARY/usuário/transação e Rabbit saudável.
   Aplicar `bootstrap`, aguardar Job e conferir bancos/grants. Alterar flags
   correspondentes somente após evidência; habilitar `auth_migration_enabled`
   e aplicar bundle `migration`, ainda sem replicas de apps. Verificar migração
   Auth/Flyway e pós-condições de `verify-postgres.sql` por execução restrita.
6. Remover projeções administrativas temporárias `postgres-bootstrap-admin` e
   `auth-migration-postgres` pelo cleanup existente após Jobs/pós-condições;
   não apagar `auth-postgres` de runtime. Provisionar assinatura/atores reais de
   demo pelo procedimento restrito pendente; nenhuma migração cria conta padrão.
7. Na Edge, `publish_auth=true` só após JAR/layer/secrets/bootstrap/migração
   verificados. Testar JWKS e login; comprovar bootstrap wrapper dentro do runtime
   Lambda com TLS RDS e sem exposição de senha. Preencher Platform `jwt` com o
   output Edge real. Sem isso, bundles `applications`/`auth` podem ficar vazios.
8. Com `auth_image_verified`, `auth_migration_completed`,
   `auth_internal_runtime_approved`, `bootstrap_completed`, `secrets_delivered`
   e `network_policy_enforced` comprovados, renderizar `application_replicas=1`
   e aplicar `auth`/`applications`. Aguardar Auth interno e três apps; conferir
   Flyway OS/Billing, mTLS, SAN/subject e comunicação REST/AMQP. Readiness de
   aplicação não comprova Rabbit: há health Rabbit desabilitado nas aplicações.

Padrão Terraform de Platform/Edge, usando diretório e arquivos correspondentes:

```bash
terraform -chdir=phase4/stacks/platform init -input=false -lockfile=readonly -backend-config="$RUN/platform.backend.hcl"
terraform -chdir=phase4/stacks/platform plan -input=false -var-file="$RUN/platform.tfvars.json" -out="$RUN/platform.plan"
terraform -chdir=phase4/stacks/platform apply "$RUN/platform.plan"
terraform -chdir=phase4/stacks/platform output -json manifests > "$RUN/platform-bundles.json"
```

Cada valor do map `manifests` é uma **string JSON**, não um arquivo já instalado.
Extração pública, repetindo somente as fases autorizadas e respeitando as esperas:

```bash
jq -r '.foundation' "$RUN/platform-bundles.json" > "$RUN/foundation.json"
kubectl apply -f "$RUN/foundation.json"
```

Extrair da mesma forma `.network`, `.data`, `.bootstrap`, `.migration`, `.auth`
e `.applications` após cada novo apply/output. Aguardar por nomes efetivos:
`kubectl -n "$NS" rollout status statefulset/mongodb`,
`kubectl -n "$NS" rollout status statefulset/rabbitmq`,
`kubectl -n "$NS" wait --for=condition=complete job/<nome-do-output> --timeout=300s`
e rollout dos quatro Deployments. Jobs antigos/TTL não são prova de execução da
release nova. Não reaplicar bootstrap administrativo após remover seus secrets.

**Sucesso:** bancos/persistência e quatro apps saudáveis, Auth Lambda/JWKS
funcionais, secrets fora do state e nenhum gate ligado apenas por suposição.

### 5. Implantar Edge e New Relic

1. Obter ACM/SAN controlado em `us-east-1`, informar `alb_tls` e retornar à Platform
   com `alb_controller_identity_enabled=true`, `alb_identity_inputs` contendo SG
   Edge real/certificado e boundary correspondente. Aplicar identidade, conferir
   associação Pod Identity. Exportar `network_controller_contract.values_yaml`.
   O lookup atual exige certificado `AMAZON_ISSUED`, `ISSUED`, domínio primário
   igual a `server_name` e tags `EnvironmentId=demo-a`, `Phase=4`,
   `Lifecycle=ephemeral`, `Shared=false`; apenas um SAN coincidente não satisfaz
   o guard. Solicitar/selecionar o certificado com esse contrato.
2. Instalar o único controller:

```bash
helm repo add eks https://aws.github.io/eks-charts
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller --namespace kube-system --version 1.14.0 -f "$RUN/alb-controller-values.yaml" --wait
```

   Instalar CRDs/RBAC durante a elevação já prevista; verificar Deployment e
   webhook. Não criar segunda role/controller. IMDS tem hop limit 1, portanto
   `region`/`vpcId` explícitos e Pod Identity dos values são necessários.
3. `prepare_alb=true` após gates reais; aplicar List de Edge obtida com
   `terraform -chdir=phase4/stacks/edge output -raw manifests` em arquivo público.
   Aguardar reconciliação, listener HTTPS:443, certificado, targets HTTPS:8443
   saudáveis, ALB interno/SG/subnets corretos e VPC Link AVAILABLE.
4. Informar `alb_listener_arn` observado, aprovar gates e `publish_business=true`.
   Depois configurar URLs/credenciais teste Mercado Pago e validar opt-in
   `publish_webhook`. Rotas internas/actuator/Swagger não devem ser públicas.
5. Exportar `observability.values_yaml`; criar namespace separado
   `tc-oficina-p4-demo-a-observability` e Secret `newrelic-license`, chave
   `licenseKey`, usando entrega segura fora do Terraform. Instalar chart pinado:

```bash
helm repo add newrelic https://helm-charts.newrelic.com
helm upgrade --install newrelic-bundle newrelic/nri-bundle --namespace "$OBS_NS" --version "$NR_CHART_VERSION" -f "$RUN/newrelic-values.yaml" --wait
```

   Conferir pods/RBAC/ingestão, cluster correto, pods por serviço, eventos e logs
   de um fluxo real. Não declarar tracing APM só pelas env vars. Registrar os
   IDs de OS/Saga e sua correlação nos logs; avaliar rastreamento exigido pelo PDF
   antes da gravação. Dashboards personalizados são dispensáveis.
6. Revogar `cluster_bootstrap_access_enabled` após instalar recursos privilegiados;
   preservar RBAC dos executores. Fechar eventual endpoint público e confirmar
   `public=false/private=true` por sessão independente do executor principal,
   conforme recuperação existente. Próxima manutenção exige novo acesso válido.

**Sucesso:** APIs de negócio pelo Gateway, login/JWKS, alvos saudáveis e dados
New Relic reais, com evidência do fechamento/revogação administrativa.

### 6. Executar E2E

Reutilizar Billing `integration/9.1.4/oficina.feature`, funções/asserções de
`runner.py` e cenários de segurança/compensação existentes. `start.ps1`,
`recover.ps1`, `RepairFailureFixture.java` e Compose são **locais**: não apontá-los
diretamente à AWS. A fixture de falha exige banco `e2e_execution`; não remover
essa proteção nem inventar endpoint produtivo para falha. A rejeição pública
do orçamento já permite demonstrar compensação real sem essa fixture.

Não existe comando E2E AWS pronto. A implementação mínima pendente é adaptar o
transporte do harness existente para base URL Gateway, atores/CA reais e coleta
autorizada de snapshots/controle de workload, preservando feature/asserções.
No primeiro deploy o operador pode percorrer o mesmo cenário manualmente e
registrar os resultados; isso não substitui a evidência BDD automatizada existente
nem comprova que o harness já executa remoto.

| Aceitação | Execução / evidência de sucesso |
| --- | --- |
| APIs acessíveis | Login/JWKS e rotas da allowlist respondem via endpoint Edge; `/internal/*`, actuator e métodos não previstos continuam indisponíveis externamente. |
| Autenticação | Login real com senha correta; senha errada/token inválido/expirado rejeitados; RS256/kid/issuer/audience/JWKS coerentes; isolamento de cliente terceiro. |
| Cliente/veículo | Cadastrar filial, cliente, veículo e vínculos por REST; provisionar vínculo de identidade e atribuição técnica pelo canal restrito existente. Registrar IDs. |
| OS e diagnóstico | Abrir OS, observar Saga/comando, iniciar diagnóstico autorizado, incluir itens e concluir; eventos/estados e histórico coerentes. |
| Orçamento/aprovação | Gerar orçamento e notificação, aprovar pelo cliente proprietário; retry de aprovação não duplica efeitos. |
| Reparo | Mecânico atribuído inicia/conclui trabalho; terceiro/sem atribuição é negado; evento de conclusão chega à Saga. |
| Pagamento teste | Checkout com Mercado Pago de teste, confirmação real por webhook assinado/reconciliação; repetição idempotente. Não usar cobrança real nem chamar mock local de integração externa. |
| Finalização/BDD | Pagamento `CONFIRMED`, OS `FINALIZADA`, Saga `COMPLETED`, como `oficina.feature`; histórico completo correlacionado. |
| Falha e compensação | Outro fluxo com orçamento rejeitado: `quote.rejected.v1`, OS `CANCELADA`, Saga `COMPENSATED`, sem reparo/pagamento. Falha de reparo iniciado com Saga `FAILED`/intervenção manual permanece cenário específico local até transporte autorizado. |
| Rabbit e persistência | Exibir exchanges/filas/bindings, confirmações e deduplicação por eventId. Em janela controlada, indisponibilizar/restaurar broker pelo workload exato, comprovar outbox/retry/recuperação e preservação dos PVCs, sem editar dados de domínio. |
| Mongo/PostgreSQL | Replica set PRIMARY/transação, dados após reinício controlado, bancos exclusivos, Flyway/grants corretos; sem acesso direto de um serviço ao banco do outro. |
| Health checks | Quatro Deployments disponíveis, dependências saudáveis, probes/targets estáveis, nenhum CrashLoop/OOM/Pending. Rabbit validado separadamente das probes HTTP. |
| New Relic | Capturas de cluster/nodes/pods, eventos/logs do teste e correlação OS/Saga/serviço; declarar separadamente eventual evidência APM/traces. |

**Sucesso:** checklist com PASS/FAIL e evidência real por cenário; nenhuma aprovação
por reprodução de evidência histórica. Corrigir somente falhas demonstradas.

### 7. Registrar evidências e concluir automação necessária

Guardar revisões e links CI aprovados, testes/cobertura ≥80%/qualidade por serviço,
digests, VersionId/checksums/layer ARN, outputs públicos selecionados, versões
efetivas, status nodes/pods/PVC, URLs públicas, transições OS/Saga, compensação,
pagamento teste, New Relic e inventário de limpeza. Não incluir state/plan completo,
JWT, headers Authorization, senhas, private.env, chave RS256 ou PKCS12.

Preparar depois dois workflows, sem adicionar YAML operacional nesta revisão:

| Workflow manual | Sequência mínima / contratos |
| --- | --- |
| Environment Up | Apenas `workflow_dispatch`, input restrito a `demo-a`, revisão imutável de cada serviço, GitHub Environment `phase4-demo-a-provision` para Terraform/bootstrap e `phase4-demo-a-deploy` para publicação/kubectl. Usar subjects/roles do output `identity_contract`, `contents: read`, `id-token: write`, concurrency por ambiente e `cancel-in-progress=false`. Executar preflight, planos revisados e fases 2–6 com inventário/recibos públicos; cleanup do acesso EKS e recuperação independente já contratada. |
| Environment Destroy | Apenas `workflow_dispatch`, confirmação exata prevista no contrato, inventário real aprovado e `phase4-demo-a-destroy`. Executar sequência 8 e verificar recursos externos antes de infra. Ajustes de configuração que exijam provision usam sua role/fase aprovada; não ampliar destroy para fazer Up. |

Não usar gatilho deploy em push na `homolog`. O legado `.github/workflows/ci-cd.yml`
continua sem uso: manual ainda pode provisionar legado. Os dois workflows novos
só devem ser ativados após revisão e autorização, consumindo comandos/harness
existentes em vez de nova camada de release. Falta esse CD para completar o PDF;
gravar ao menos um deploy automatizado associado aos testes aprovados.

**Sucesso:** evidências suficientes para o vídeo/PDF, pendências de CD/tracing
explicitamente fechadas ou reportadas, inventário completo antes do destroy.

### 8. Destruir ambiente e conferir custos residuais

Executar futuramente sob confirmação destrutiva, conta/região/ambiente verificados,
sem novos deploys concorrentes. Reabrir acesso EKS só pelo caminho aprovado.
Não desabilitar o catálogo antes de terminar: guards precisam dele para operar.

1. Exportar evidência e inventário, confirmar descarte de dados. Atualizar
   bootstrap IAM com inventário real, `inventory_approved` e confirmação destroy
   exigida. Não executar `terraform destroy` na root persistente identities.
2. Despublicar rotas negócio/webhook e Auth em fase Edge revisada, parar entrada
   de novos pedidos, aguardar consumidores/outboxes conforme demonstração.
   Na implementação atual desligar `publish_auth` também remove Lambda; conferir
   término/ENIs. Coordenar retirada do VPC Link antes da rede/SG Edge.
3. **Com ALB controller ainda ativo**, excluir os manifests Edge guardados:
   `kubectl delete -f "$RUN/edge-ingress.json"`; esperar finalizers e verificar
   exclusão real de ALB/listeners/target groups/ENIs. Não apagar finalizers à força.
4. Parar apps e remover bundles Jobs/Deployments/data; verificar PVC/PV e volumes
   EBS excluídos. `whenDeleted=Delete` e reclaim Delete ajudam, mas o controller
   CSI/identidade devem permanecer até concluir exclusão. `whenScaled=Retain`
   significa que apenas escalar StatefulSet para zero não limpa volumes.
5. Remover Helm New Relic e ALB controller somente após consumidores/ALB;
   excluir secrets/projeções/configmaps/RBAC/namespaces/StorageClass externos
   depois dos respectivos consumidores/PVCs. Remover sources Secrets Manager
   canônicos por ARNs do inventário; incluir `technical-ca` e `auth-signing`
   com o operador responsável, além de credenciais de teste da demo. Não tentar
   apagar master RDS manualmente antes da instância que o gerencia.
6. Destruir Edge e Platform por planos revisados, preservando dependências até
   o fim das fases anteriores; aguardar ENIs de Lambda/VPC Link/endpoint.
7. Esvaziar **os quatro ECR inventariados** com `batch-delete-image` dos digests
   observados, inclusive imagens além da primeira página, antes de Infra.
   `force_delete=false` impede apagar repo com imagens. Inventariar versões
   S3 de JARs/delete markers e versões da layer; o publicador não as remove.
   Cleanup S3 requer permissão específica de bootstrap/operador, não existe
   DeleteObject amplo no contrato deploy. Manter backend/state fora dessa limpeza.
   Incluir certificado ACM criado exclusivamente para a demo após retirar o
   listener; certificado/domínio previamente compartilhados ficam preservados.
8. Destruir Infra por último: RDS usa `skip_final_snapshot=true`, sem retenção
   automática de backups; confirmar descarte. Remover node group/EKS/rede apenas
   após os controllers e recursos externos. Verificar exclusão efetiva AWS.

Padrão de comando por stack, **somente após os passos coordenados correspondentes**:

```bash
terraform -chdir=phase4/stacks/edge plan -destroy -input=false -var-file="$RUN/edge.tfvars.json" -out="$RUN/edge-destroy.plan"
terraform -chdir=phase4/stacks/edge apply "$RUN/edge-destroy.plan"
```

Repetir Platform e Infra nessa ordem, com seus próprios tfvars/backend/plans.
Não usar `-target` como receita geral para esconder dependências; manter acesso e
contratos reais necessários ao refresh. Se houver falha, retomar mesmo state e
inventário. Não usar `state rm`, delete por prefixo, prune ou limpeza de recursos
de outros ambientes para declarar sucesso.

**Sucesso:** sem EKS/node group/RDS/ALB/VPC Link/ENIs/volumes/ECR/endpoint da demo,
sem ingestão New Relic ativa, secrets/artefatos efêmeros removidos ou retenção
explicitamente inventariada. Registrar resíduos cobrados. Backend, OIDC, roles,
boundaries e recuperação persistente têm dono/limpeza separados; suas funções,
logs/agenda/armazenamento podem continuar cobrando. Só então desabilitar `demo-a`
com revisão; `demo-b` não participa.

### Estimativa de custos

Referência de planejamento em USD, sem impostos/câmbio/créditos/free tier, para
`us-east-1`, dois nodes e carga pequena. Consultadas páginas públicas AWS nesta
revisão, sem acesso à conta. Tarifas EC2/RDS exatas devem ser confirmadas no
Calculator antes do Up; intervalos abaixo são **reserva estimada**, não cotação.

| Componente | Base / ordem de grandeza |
| --- | --- |
| EKS | US$ 0,10/h em suporte padrão ≈ US$ 2,40/dia; não usar suporte estendido como default ([AWS](https://aws.amazon.com/eks/pricing/)). |
| EC2/node group | Dois `m7i-flex.large` On-Demand; reservar cerca de US$ 0,18–0,22/h pelo par e confirmar preço regional ([AWS EC2](https://aws.amazon.com/ec2/pricing/on-demand/)). |
| RDS | `db.t4g.micro` Single-AZ + 20 GiB gp3: reservar US$ 0,02–0,04/h incluindo storage proporcional; confirmar tarifa PostgreSQL e créditos CPU excedentes ([AWS RDS](https://aws.amazon.com/rds/postgresql/pricing/)). |
| EBS nodes e PVCs | 2×30 GiB + 2×5 GiB = 70 GiB; a US$ 0,08/GB-mês ≈ US$ 5,60/mês ou US$ 0,008/h proporcional. Não inclui storage RDS ([AWS gp3](https://aws.amazon.com/ebs/general-purpose/)). |
| ALB | Base US$ 0,0225/h + LCU/h/tráfego; reservar US$ 0,025–0,04/h em baixa carga ([AWS ELB](https://aws.amazon.com/elasticloadbalancing/pricing/)). |
| IPv4 e PrivateLink | Dois IPv4 públicos dos nodes: US$ 0,01/h juntos. Secrets Manager Interface Endpoint em duas AZs cobra por AZ/h + dados; confirmar preço regional e inventariar ambos ([AWS VPC](https://aws.amazon.com/vpc/pricing/), [AWS PrivateLink](https://aws.amazon.com/privatelink/pricing/)). Sem NAT Gateway declarado. |
| API Gateway e Lambda | HTTP API por requisição, faixa inicial de referência US$ 1/milhão; Lambda por chamadas/duração/memória, não só existir. Confirmar modelo do VPC Link HTTP API aplicável, sem presumir tarifa fixa adicional ([API Gateway](https://aws.amazon.com/api-gateway/pricing/), [Lambda](https://aws.amazon.com/lambda/pricing/)). |
| CloudWatch | Logs EKS/API/Lambda com retenção 7 dias, ingestão/armazenamento/métricas/alarmes cobrados; reservar conforme volume, evitar logs privados ([AWS](https://aws.amazon.com/cloudwatch/pricing/)). |
| Outros | ECR/S3/Secrets Manager, dados/transferência, recuperação persistente e eventual DNS/domínio; ACM conforme modalidade e domínio já disponível. Incluir no cálculo, não criar serviços extras ([Calculator](https://calculator.aws/)). |
| New Relic | Depende de plano/usuários/ingestão contratados; lowDataMode reduz volume, não garante gratuidade ([New Relic](https://newrelic.com/pricing)). |

Reserva preliminar **US$ 0,40–0,55/h**, cerca de **US$ 3,20–4,40 por oito horas**
ou **US$ 9,60–13,20 por dia**, mais New Relic/ingestão/transferência/itens persistentes.
Essa soma é orçamento aproximado, não preço verificado de cada SKU. EKS/RDS/ALB
continuam cobrando sem uso; parar pods não encerra a cobrança. Fazer Up perto da
demo, registrar horário e destruir no mesmo dia, verificando resíduos na conta.

### Implementação mínima restante e decisão para revisão

Não houve correção funcional nesta etapa: os impedimentos observados são
configuração/execução ainda não conectadas, sem justificativa para alterar os
módulos ou criar automação que possa provisionar acidentalmente. Único arquivo
adicionado: este relatório. Catálogo, workflows e microsserviços preservados.

Antes do primeiro Up, completar em mudanças focadas: ligação dos adaptadores
Secrets/PKI, procedimento do emissor/atores Auth, bootstrap do bucket de artefatos,
publicação/readback manual dos seis artefatos e coleta pública dos bundles.
Usar Terraform/kubectl/Helm existentes para instalação e a sequência de limpeza
acima; não é obrigatório criar um orquestrador novo. Depois concluir os dois
dispatch workflows e adaptação do E2E existente para satisfazer a entrega FIAP.

Próxima ação recomendada: revisar esta matriz e obter do operador conta/backend,
caminho EKS, domínio ACM, credenciais de teste Mercado Pago/New Relic e revisões
CI. Isso autoriza preparar os arquivos operacionais concretos em etapa própria;
**não constitui autorização para habilitar ambiente ou executar AWS**.
