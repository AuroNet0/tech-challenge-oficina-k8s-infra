# Primeiro bootstrap plan-only — demo-a

Entradas preparadas em `approved-inputs.json`: conta `446517826146`, regiao `us-east-1`, bucket `tech-challenge-oficina-terraform-state-b1cfa326`, `subject_format=classic`. OIDC/trust inicial considerados confirmados pelo operador, nao consultados nesta implementacao.
`installation_approved=true` e `oidc_claims_verified=true` satisfazem os guards do Terraform; **nao disparam apply**. A variavel remota `PHASE4_BOOTSTRAP_APPROVED=false` continua bloqueando o workflow antes da autenticacao. Nao alteramos GitHub nem habilitamos ambientes.

## Boundaries para revisao

Os cinco JSONs legiveis em `boundaries/demo-a/` sao identicos aos documentos serializados no campo `workload_boundary_documents`. Nao sao policies anexadas a bootstrap: serao limites das workload roles futuras. Uma boundary limita a identity policy; nao concede acesso sozinha.

| Documento | Origem e limites |
| --- | --- |
| `eks-cluster.json` | `stacks/infra/iam.tf` + minimo EKS documentado: discovery regional, tagging da chave exata `kubernetes.io/cluster/tc-oficina-p4-demo-a` em nodes com EnvironmentId e DescribeKey apenas na conta/regiao. Exclui ELB legado e provisionamento de discos, a cargo de ALB/CSI. |
| `eks-nodes.json` | WorkerNode + CNI realmente anexados em `stacks/infra/iam.tf`; Pod Identity e DescribeCluster no cluster exato; pull nos quatro ECR e registry oficial EKS `602401143452`; ENI/CNI limitados a tipos de recursos da conta/regiao, attachment a instances com EnvironmentId. |
| `ebs-csi.json` | `stacks/platform/controllers.tf`, policy EKSClusterScoped e PVCs gp3: volumes com tag `ebs.csi.aws.com/cluster-name=tc-oficina-p4-demo-a`, attach/detach em nodes com as tags de cluster aceitas pela policy AWS, tags on-create e manutencao sem alterar ownership. Sem snapshots, copy, lock, FSR ou KMS grants, nao utilizados pelos manifests atuais. |
| `load-balancer-controller.json` | Teto do modulo existente `modules/alb-controller-policy`: ALB `tc-oficina-p4-demo-a-edge`, scheme internal, tags obrigatorias, target groups com ownership, listeners HTTPS e regras do ALB selecionado. Sem criacao/mutacao SG, WAF, Shield ou IAM. |
| `auth-lambda.json` | `stacks/edge/lambda.tf`: somente escrita de logs da Auth, GetSecretValue em auth-postgres/auth-signing e ENIs VPC do servico Lambda; Deny de mutacao ENI originada pelo codigo da propria funcao. Sem escrita de secrets nem chaves KMS. |

`Resource="*"` e usado somente em discovery regional, token ECR e no conjunto documentado de seis acoes ENI exigido pelo servico Lambda. O Deny ENI tambem e global de proposito. Nao ha wildcard em Action ou acoes IAM/STS. ARNs com IDs ainda desconhecidos usam wildcard **apenas no ID**, mantendo conta/regiao/tipo; Secrets Manager usa seis `?` para o sufixo obrigatorio, sem ampliar o nome canonico.

Limites a revisar antes de um Up, nao bloqueadores do plano IAM:
- IDs de ENIs/subnets/SG/certificado ainda nao existem. O teto CNI cobre recursos desses tipos na conta/regiao, nao e isolamento completo entre VPCs. A policy ALB concreta do modulo continuara exigindo subnets/SG/certificado exatos; a boundary nao substitui esse contrato. Reduzir o teto CNI apos observar a Infra, antes de autorizar workloads.
- Pod Identity precisa entregar as session/principal tags e o CSI precisa criar a tag de cluster prevista pela policy AWS. Isto exige prova real de PVC/attach/delete; testes offline nao atestam readiness.
- Nenhuma service-linked role e criada por estas boundaries. Verificar/aprovar separadamente as identidades de servico que AWS exigir no primeiro Up.
- O perfil gp3 atual nao usa snapshots nem CMKs proprias. Autorizar essas funcionalidades exige novas policies e revisao de key policies; nao adicionar acoes genericas preventivamente.

## Compatibilidade e escopo desta primeira execucao

- `bootstrap.mjs` aceita os campos, cinco documentos e formato classico. Terraform valida cada documento com limite 6144 caracteres; o maior e ALB, 4948 caracteres.
- Inventario vazio e nao aprovado: preserva os mesmos **13 shards** (provision 0..6, deploy/destroy 0..2) e **cinco boundaries** autorizados na inline policy instalada. Quatro roles, incluindo `endpoint-recovery` inativa, permanecem declaradas.
- Recovery operacional, abertura/verificacao EKS e bucket artifacts explicitamente false. Nada disso sera instalado neste plano.
- `approved_plan_sha256` vazio leva ao retorno `plan-only` antes de `terraform apply`; hash incorreto falha. Os testes exercitam o entrypoint real com TODOS os processos AWS/Terraform substituidos por mocks.
- Um plan-only com backend real ainda usa `.tflock`; `terraform init` pode inicializar state vazio se ausente. Isto nao cria workload/IAM, mas nao e uma sessao estritamente read-only no S3. Nao se promete zero escritas no backend.

## Primeiro run: somente pelo operador, apos revisao

1. Revisar estes arquivos e os cinco documentos. Publicar as mudancas em `homolog` pelo processo normal do repositorio; nao foi feito commit/push aqui. O workflow `workflow_dispatch` precisa estar disponivel na default branch para aparecer no GitHub Actions.
2. No Environment `phase4-demo-a-bootstrap`, manter as protecoes/aprovacoes exigidas e, **somente para a execucao autorizada**, alterar `PHASE4_BOOTSTRAP_APPROVED` para a string `true`. Enquanto false, o run falhara antes de STS/Terraform. Isto nao habilita demo-a nem a variavel de Up/Destroy.
3. GitHub → Actions → **Environment Bootstrap Identities** → Run workflow → branch **homolog**.
4. `confirmation`: **`INSTALL demo-a IDENTITIES`**. Deixar **`approved_plan_sha256` completamente vazio**. Nao copiar hash de outro run.
5. Autorizar o deployment do Environment, se houver required reviewer. Aguardar o job.
6. Conferir artifact `bootstrap-demo-a-<run_id>-<attempt>`: `report.json`, `summary.md`, `identity-plan.json`. Esperado: fase `plan-only=PASS`, ausencia de `terraform-apply`, `environment_ready=false`, apenas recursos IAM/base e terraform_data. Nao publicar o plano binario ou state.
7. Restaurar `PHASE4_BOOTSTRAP_APPROVED=false` apos a execucao. **Nao realizar o segundo run com hash nesta etapa**; isso seria instalacao real, com nova autorizacao independente.

Bloqueadores reais nao verificados: disponibilidade do workflow na default branch/revisao publicada, protecoes do Environment, claim OIDC emitido, bucket policy/criptografia/SCP/boundary/permissoes efetivas. Se o backend exigir CMK, a inline policy atual nao concede KMS: revisar separadamente, sem trocar para acesso amplo. O plano no GitHub e quem verificara acesso real; nenhum AWS/plan conectado foi executado localmente.

## Fontes publicas oficiais

- [Minimo EKS cluster](https://docs.aws.amazon.com/eks/latest/userguide/cluster-iam-role.html), [WorkerNode](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonEKSWorkerNodePolicy.html), [CNI](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonEKS_CNI_Policy.html), [EKS Auth resource cluster](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks-auth.html).
- [EBS CSI EKSClusterScoped](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AmazonEBSCSIDriverEKSClusterScopedPolicy.html) e [permissoes do driver](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/example-iam-policy.json). Subconjunto intencional para PVC gp3, nao copia irrestrita da policy.
- [Lambda VPC: seis acoes ENI e protecao SourceFunctionArn](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html).
