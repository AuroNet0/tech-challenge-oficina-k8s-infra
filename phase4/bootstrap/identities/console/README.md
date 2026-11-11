# Inline policy de instalacao inicial — demo-a

> Registro histórico. Os workflows e gates citados foram substituídos por Plan,
> Deploy e Destroy. Para operar sem editar JSON por execução, consulte o guia
> `docs/github-actions-first.md` na raiz do repositório.

No Console AWS: **IAM → Roles → tc-oficina-p4-demo-a-bootstrap → Add permissions → Create inline policy → JSON**.
Cole integralmente `demo-a-bootstrap-install-policy.json`. Nome recomendado:
**`tc-oficina-p4-demo-a-bootstrap-install`**. Nao e uma trust policy nem uma permissions boundary.
Nao e necessario alterar o provider OIDC nem a trust existente.

## Escopo autorizado agora

Conta `446517826146`, backend existente `tech-challenge-oficina-terraform-state-b1cfa326`, regiao operacional `us-east-1`.
IAM e global: seus ARNs nao possuem regiao. Nao aplicar uma condicao regional indiscriminada ao IAM.

| Recurso atual / origem | Permissoes e motivo |
| --- | --- |
| Provider GitHub existente / `main.tf` | `iam:GetOpenIDConnectProvider` para conferir issuer e audience. Sem criar, atualizar ou excluir OIDC. |
| Roles `provision`, `deploy`, `destroy` e `endpoint-recovery` / `main.tf` | `CreateRole`, `GetRole`, `GetRolePolicy`, `ListRolePolicies`, `ListAttachedRolePolicies`; atualizacao de trust, duracao/descricao e reconciliacao de tags via `UpdateAssumeRolePolicy`, `UpdateRole`, `UpdateRoleDescription`, `TagRole`, `UntagRole`. Todos restritos aos quatro ARNs. O provider le inline policies mesmo quando o atributo nao foi configurado. |
| Inline `close-selected-endpoint` / `main.tf` | `iam:PutRolePolicy` somente na role `endpoint-recovery`. IAM autoriza esta operacao pelo ARN da role, nao por ARN/nome da inline policy; o nome e o documento sao conferidos no plano. Esta role e declarada **mesmo com o modulo recovery desativado**. |
| 13 policies dos executores / `main.tf`, `policies.tf`, `operations.tf`, `actions.tf` | `CreatePolicy`, `GetPolicy`, `GetPolicyVersion`, `ListPolicyVersions`, `CreatePolicyVersion`, `DeletePolicyVersion`, `TagPolicy`, `UntagPolicy`. ARNs enumerados: provision `0..6`, deploy `0..2`, destroy `0..2`. |
| Cinco boundaries / `boundaries.tf` | Mesmas acoes de managed policies, nos ARNs exatos `eks-cluster`, `eks-nodes`, `ebs-csi`, `load-balancer-controller`, `auth-lambda`, com sufixo `-boundary`. Criar estas policies nao cria workload roles nem instala boundaries nas roles executoras. |
| Attachments / `main.tf` | `iam:AttachRolePolicy` limitado ao ARN de cada executor e `iam:PolicyARN` contendo apenas seus proprios shards. Sem AWS managed policies ou attachments na role bootstrap. |
| Backend / `bootstrap.mjs` | `s3:GetBucketLocation` no bucket; `ListBucket` com prefixos exatos; `GetObject`/`PutObject` apenas no state; `GetObject`/`PutObject`/`DeleteObject` apenas no `.tflock`. |

`DeletePolicyVersion` permite atualizar policies apos atingir cinco versoes: o provider remove uma versao antiga nao-default antes de criar a nova default. Nao concede `DeletePolicy`, `DeleteRole`, `DeleteRolePolicy` nem `DetachRolePolicy`; o executor rejeita planos com exclusoes/substituicoes.
Nao e necessario `SetDefaultPolicyVersion`: o provider usa `CreatePolicyVersion` com `SetAsDefault=true`.

**Nenhuma action ou Resource contem wildcard.** `sts:GetCallerIdentity` e chamado pelo workflow/provider mas nao exige Allow na identity policy. A autenticacao OIDC usa a trust existente, sem conceder `sts:AssumeRole` ou `sts:AssumeRoleWithWebIdentity` nesta policy.
As acoes EC2/EKS/RDS/ECR/Secrets Manager presentes nos **documentos gerados para outras roles** nao sao executadas pelo bootstrap; nao devem ser copiadas para esta role.

## Backend exclusivo e compatibilidade

O workflow fixa `TF_WORKSPACE=default`, key `phase4/bootstrap/identities/demo-a.tfstate`, `use_lockfile=true`, `encrypt=true`, Terraform **1.15.8** e provider AWS **5.100.0**.
Os unicos objetos legiveis/gravaveis sao esse state e seu `.tflock`. O state nao pode ser excluido por esta policy. Sem acesso a objetos de fases anteriores, inventario operacional, outros ambientes, versoes de objetos ou criacao/configuracao do bucket.

**Excecao de listagem de metadados, sem acesso a objetos:** o backend 1.15.8 enumera workspaces com `ListObjectsV2(Prefix="phase4/bootstrap/identities/demo-a-workspaces/")`, inclusive durante inicializacao. Como o executor ja configura esse prefixo personalizado, a policy permite exatamente essa consulta adicional (sem wildcard), nao listar todo o bucket. Nao permite ler/gravar states nesses workspaces. Remover essa entrada sem ajustar/testar o executor causa AccessDenied no init; restringir literalmente toda listagem a duas keys nao e compativel com o executor atual.

Esta policy assume bucket pertencente a conta informada (`s3:ResourceAccount`) e criptografia S3 compativel com o backend. Nao concede KMS: se o bucket exigir SSE-KMS, a chave, sua key policy e a configuracao do backend exigem revisao separada com ARN real. Bucket policy, SCP, boundary ou outras policies da role podem negar/acrescentar acesso; nao foram consultadas remotamente. A preservacao indicada e o escopo desta policy, nao uma auditoria das permissoes efetivas da conta.

## Entradas e aprovacoes necessarias, sem habilitar ambiente

O arquivo operacional `approved-inputs.json` esta preparado para o primeiro plan-only, com subject classico informado pelo operador. Instrucoes em `../PLAN-ONLY.md`. Esta policy exige manter:

- `environment_id=demo-a`, `aws_account_id=446517826146`, `aws_region=us-east-1`, backend igual ao informado.
- Cinco `workload_boundary_documents` reais e revisados; documentos ficticios dos testes nao sao boundaries operacionais.
- `inventory={}`, `inventory_approved=false`, `external_input_secrets={}` e sem confirmacao de destroy.
- `artifact_bucket_installation_approved=false`, `recovery_installation_approved=false`, `recovery_opening_approved=false`, `recovery_verification_approved=false`.
- Aprovacoes explicitas `installation_approved` e `oidc_claims_verified`, formato de subject verificado e aprovacao do Environment `phase4-demo-a-bootstrap`.

O workflow continua exigindo a variavel `PHASE4_BOOTSTRAP_APPROVED`, confirmacao `INSTALL demo-a IDENTITIES` e revisao do hash do plano na mesma revisao Git antes de apply. Nenhum desses gates foi habilitado aqui. Os ambientes demo-a/demo-b permanecem desabilitados.
Os ARNs enumerados cobrem a instalacao base acima. Inventario, boundaries ja autorizadas no inventario ou entradas externas podem alterar a quantidade de shards: reexecutar testes e revisar esta policy antes dessas mudancas, sem substituir os ARNs por um wildcard.

**Privilegio administrativo delimitado, nao sandbox de documentos:** IAM nao inspeciona o conteudo de `PolicyDocument` nem da trust na autorizacao destas acoes. Quem controla esta role e o codigo aprovado pode ampliar permissoes por meio das quatro roles/18 policies listadas. As cinco workload boundaries nao limitam os executores no Terraform atual. Manter revisao de codigo/plano, aprovacao independente e duracao curta; remover a inline policy quando nao houver manutencao autorizada. Esta policy nao autoriza modificar a propria role bootstrap, PassRole, criar access keys, service-linked roles ou workload roles.

## Fora desta autorizacao: revisao futura separada

- **Recovery operacional / `recovery.tf` e modulo `endpoint-recovery`:** requer IAM adicional (roles broker/scheduler e inline policies), PassRole com servicos/ARNs exatos, Lambda, DynamoDB leases, Scheduler, SQS, logs/alarmes e eventual publicacao SNS. Exige package revisado, ARN SNS real, fechamento independente comprovado e aprovacao especifica; nada disso e concedido aqui. A role inativa e sua policy de fechamento acima nao tornam a recuperacao operacional.
- **Bucket de artifacts / `artifacts.tf`:** criacao/configuracao do bucket `tc-oficina-p4-demo-a-artifacts`, versionamento, bloqueio publico e SSE requerem policy separada aprovada; nao ampliar acesso ao backend para atender isso.
- **Up/Destroy:** publicacao ECR/S3/layers, secrets, provisionamento e abertura temporaria EKS pertencem as roles executoras e seus gates. Esta inline policy nao libera essas chamadas pela role bootstrap.
- Se resources opcionais ja constarem no state, esta policy base nao basta para o refresh deles. Revisar state/inventario pelos canais autorizados e aprovar permissoes exatas separadamente; nao usar `state rm`, toggles para apagar recursos nem acesso amplo para contornar a falha.

## Verificacao offline

`node --test scripts/environment-actions/bootstrap-policy.test.mjs scripts/environment-actions/bootstrap-inputs.test.mjs` valida JSON, quota de inline policy, ARNs/acoes, isolamento S3, entradas reais e contratos do workflow. O teste Terraform `tests/bootstrap_install_policy.tftest.hcl` usa provider mock e compara os ARNs realmente planejados com o JSON, com as cinco boundaries reais de `approved-inputs.json` e opcionais desligados. Nenhum teste realiza API AWS.
A quota de inline policies por role e **10.240 caracteres agregados**, sem whitespace: conferir o espaco restante se ja houver outras inline policies. A validacao AWS do editor Console e as permissoes efetivas permanecem verificacoes reais do operador, nao resultados simulados dos testes.

Referencias oficiais: [backend S3 e lockfile](https://developer.hashicorp.com/terraform/language/backend/s3), [enumeracao de workspaces no Terraform 1.15.8](https://github.com/hashicorp/terraform/blob/v1.15.8/internal/backend/remote-state/s3/backend_state.go), [provider IAM role 5.100.0](https://github.com/hashicorp/terraform-provider-aws/blob/v5.100.0/internal/service/iam/role.go), [provider IAM policy 5.100.0](https://github.com/hashicorp/terraform-provider-aws/blob/v5.100.0/internal/service/iam/policy.go), [GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html), [quotas IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_iam-quotas.html), [acoes e recursos IAM](https://docs.aws.amazon.com/service-authorization/latest/reference/list_iam.html).
