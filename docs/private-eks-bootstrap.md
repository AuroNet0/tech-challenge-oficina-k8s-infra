# Bootstrap e recuperação do EKS privado

Base: `1c5f2a7`. Esta alteração não executa AWS, workflows ou Terraform conectado.
Os únicos workflows continuam sendo Plan, Deploy e Destroy. **Gerar novos planos
no commit desta correção**: os anteriores não têm o protocolo `broker-lease-v1`,
os mesmos inputs/grupos Kubernetes, políticas IAM ou checksum do recovery.
Deploy rejeita o protocolo anterior e nunca recalcula ou substitui o binário.

## Sequência efetiva

1. Revisar as permissões de manutenção e usar os checkpoints existentes
   `iam-maintenance`/`recovery-foundation` por Plan e Deploy protegidos. A
   fundação persistente inclui broker, recovery, DynamoDB, Scheduler, DLQ e
   alarmes independentes, inicialmente sem abertura. Nenhum desses recursos é
   criado por este patch ou automaticamente por Infra.
2. Plan/Deploy `infra`: o executor exige a fundação com ambas as Lambdas ativas,
   roles exatas, código correspondente ao repositório e reconciliador habilitado.
   Esse gate antecede qualquer apply ou gravação do inventário. O cluster nasce
   privado, sem privilégios do criador e sem policy administrativa persistente.
   Access entries autenticam provision/deploy/destroy; sozinhas não autorizam
   instalar Kubernetes RBAC ou controllers.
3. Plan/Deploy `recovery-closed`: a role IAM de manutenção `...-bootstrap`
   verifica fechamento por EKS API e evidência do Scheduler, sem Kubernetes.
   Plan/Deploy `recovery-activate` habilita abertura somente após o probe real.
   `recovery-expiry` continua disponível para comprovar expiração independente.
4. Seguir os checkpoints release/platform/edge necessários ao contrato existente.
   Nos checkpoints `foundation`, `controller` e `observability`, o plano informa
   `temporary_cluster_admin=true`. Deploy exige a própria aprovação do Environment.
   O executor de bootstrap Kubernetes é **a role provision**, não a role IAM de
   manutenção bootstrap. O teardown protegido também usa provision.
5. O broker prepara a lease de 1800 segundos, persiste o registro e verifica os
   schedules antes de abrir somente o IPv4 público observado do runner em `/32`.
   O endpoint privado permanece habilitado. O executor confirma a configuração
   e o estado ACTIVE antes de solicitar elevação.
6. O broker aceita elevação somente com lease aberta, válida e sem policies
   preexistentes na access entry de provision. Persiste ELEVATING e uma barreira
   de 120 segundos antes de associar **somente** AmazonEKSClusterAdminPolicy,
   scope cluster, à role provision exata. A Lambda broker tem timeout de 60
   segundos; a barreira impede fechamento concorrente antes do fim de uma
   associação cujo retorno possa ter sido perdido.
7. O executor testa autorização real com `kubectl auth can-i` para CRDs,
   ClusterRoles, ClusterRoleBindings, bind e escalate; uma access entry ou um
   `/readyz` acessível não substitui esses testes. Somente então instala RBAC,
   controllers e demais manifests. Provision também pertence ao grupo executor,
   que conserva somente o RBAC restrito após a revogação.
8. Em sucesso, erro ou interrupção, recovery desassocia a policy temporária,
   relê as associações e fecha o endpoint. Só informa CLOSED quando a revogação
   está confirmada e o endpoint está privado. Uma falha na revogação não impede
   solicitar fechamento da rede; nenhuma delas é convertida em sucesso.
   Se o runner desaparecer, o schedule por lease e o reconciliador de cinco
   minutos retomam a operação. Expiração não é garantia de fechamento instantâneo:
  respeita a barreira de dispatch e operações EKS em andamento, com retries/DLQ.
   Uma abertura com resposta perdida exige uma nova operação de fechamento
   concluída com sucesso depois da barreira; observar o endpoint privado uma
   única vez não basta e não obriga editar registros DynamoDB manualmente.

## Falhas parciais e diagnóstico

Fechamento usa STS, o receipt local validado por run/commit e as APIs AWS. Não
depende de Kubernetes, outputs Terraform ou leitura bem-sucedida do backend.
O receipt local é persistido antes da escrita do inventário e de qualquer abertura.
Cada execução do recovery independe do processo Terraform e usa o registro durável.

Destroy admite a recuperação sem Kubernetes somente com states platform/edge
ausentes, sem inventário dessas stacks, sem ALB e sem volumes de dados. Volumes
root são aceitos somente quando associados a instâncias com ownership demo-a,
membership no ASG informado pelo nodegroup EKS e DeleteOnTermination. Um volume
PVC, solto, desconhecido ou sem prova suficiente bloqueia o atalho; prefixos não
autorizam remoção. O caminho Terraform continua usando os states exatos e inputs
persistidos. Recursos persistentes de IAM/recovery/backend não entram no Destroy.

Falhas de bootstrap, CIDR, abertura, lease expirada, teardown e fechamento ficam
em report.json/summary.md e artifacts `always()` já existentes. Logs das Lambdas,
DLQ e alarmes persistentes cobrem desaparecimento do runner. Falhas adicionais de
diagnóstico, revogação ou fechamento não substituem a causa original do executor.

## Permissões exatas e pendências antes de Deploy

As alterações abaixo são propostas Terraform, **não permissões já verificadas
ou instaladas na AWS**. Atualizar a implantação persistente com plano e aprovação
próprios antes de Infra. Não alterar secrets ou Environments para contornar gates.

- Broker: `eks:AssociateAccessPolicy` somente no access entry
  `arn:aws:eks:us-east-1:446517826146:access-entry/tc-oficina-p4-demo-a/role/446517826146/tc-oficina-p4-demo-a-provision/*`,
  com principal provision, policy AmazonEKSClusterAdminPolicy e scope cluster
  exatos. `eks:ListAssociatedAccessPolicies` somente nesse recurso. A permissão
  de associar foi retirada de provision; não foi concedida a deploy/destroy.
- Recovery: `eks:DisassociateAccessPolicy` e `eks:ListAssociatedAccessPolicies`
  no mesmo access entry, com principal/policy exatos na revogação. Conserva
  apenas UpdateClusterConfig de fechamento para o cluster selecionado;
  nunca pode abrir o endpoint, associar policies ou excluir o cluster.
- Executores: `lambda:GetFunctionConfiguration` somente nas funções broker e
  recovery do ambiente, e `scheduler:GetSchedule` somente em
  `tc-oficina-p4-demo-a-recovery/reconcile`. InvokeFunction já era restrito a
  essas funções. Leituras EKS/EC2 necessárias à recuperação já existiam.
- A policy mínima originalmente instalada na role de manutenção bootstrap
  pode não permitir instalar o módulo opcional. O responsável IAM deve aprovar
  o escopo dos recursos declarados em `phase4/modules/endpoint-recovery/main.tf`:
  roles broker/scheduler, policies inline dessas roles e recovery; duas Lambdas;
  tabela de leases; schedule group e reconcile; DLQ; log groups e quatro alarmes.
  Também precisa permitir leitura dos states/inventário para iam-maintenance.
  A policy base restrita não foi ampliada genericamente para realizar instalação.
- O ARN SNS de alarmes independentes deve estar configurado e autorizado. Se a
  fundação estiver ausente, desatualizada ou sem permissão, Infra bloqueia antes
  de apply. Não se interpreta aprovação humana como evidência de IAM efetivo.
- Após criar o cluster, canário fechado e ativação do broker são necessários
  antes de qualquer checkpoint Kubernetes; ausência/expiração da evidência bloqueia.

Referências de autorização:
[EKS access policies](https://docs.aws.amazon.com/eks/latest/userguide/access-policies.html)
e [ações/recursos/condition keys EKS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html).

## Validação offline

Executar as quatro suítes Python e os testes Node já existentes, os contratos
PowerShell, fmt/validate/test das seis roots Terraform com mirror local e backend
desabilitado, e os validadores dos contratos IAM/RBAC/manifests/Edge gerados.
Os testes negativos exercitam erros de policy/RBAC, broker ausente/desatualizado,
grant negado, revogação negada sem bloquear o fechamento da rede, expiração sem
runner, acesso sem aprovação, ausência de recovery antes de apply e volumes sem
ownership suficiente. Mocks de providers não comprovam permissões ou readiness AWS.

Resultados em 11/11/2026: Node 71; Python environment-actions 64, recovery 44,
artifact-release 48, platform-secrets 30 (incluindo Java 21/PKI, sem skips);
Terraform 83 (8 environment, 13 infra, 20 platform, 19 edge, 14 identities,
9 endpoint-recovery). Total: 340 testes. Também passaram os 42 checks PowerShell,
os validadores IAM/RBAC, 52 objetos Kubernetes, 18 contratos de secrets,
42 rotas/12 objetos Edge e 18 assertions de compatibilidade de artifacts.
Docker sem daemon e Helm indisponível impedem os testes locais de containers/charts;
não foram marcados como PASS. Nenhum teste conectado ou workflow foi executado.
