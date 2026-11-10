# Checksums do provider AWS na Fase 4

As quatro roots versionadas da Fase 4 fixam `hashicorp/aws` em `5.100.0`:
`bootstrap/identities`, `stacks/infra`, `stacks/platform` e `stacks/edge`.
Seus locks devem conter os checksums dos pacotes Windows e Linux amd64.

O lock anterior continha o `h1` Windows e os `zh` oficiais, inclusive o ZIP Linux,
mas faltava o `h1` Linux. `init -lockfile=readonly` pode verificar o ZIP usando
`zh` sem persistir o `h1` do pacote extraído. A verificação posterior do pacote
extraído em `validate` precisa do `h1` correspondente à plataforma.

Checksums de conteúdo obtidos com Terraform 1.15.8, usando os pacotes oficiais
identificados por `providers lock` como assinados pela HashiCorp:

| Plataforma | Checksum |
|---|---|
| windows_amd64 | `h1:H3mU/7URhP0uCRGK8jeQRKxx2XFzEqLiOq/L2Bbiaxs=` |
| linux_amd64 | `h1:edXOJWE4ORX8Fm+dpVpICzMZJat4AX0VRCAy/xkcOc0=` |

Para regenerar os checksums pelo registry oficial, sem inicializar o backend:

```sh
terraform -chdir=phase4/bootstrap/identities providers lock -platform=windows_amd64 -platform=linux_amd64
```

Repetir nas três stacks, preservando a seleção `5.100.0` e revisando o diff.
Usar uma configuração CLI com instalação direta, sem mirrors ou caches locais
não verificados. Não usar `-upgrade` nem opções que ignorem integridade.

Em uma cópia limpa Linux amd64, sem `.terraform`, sem cache global e sem
credenciais AWS, verificar sem backend remoto:

```sh
terraform -chdir=phase4/bootstrap/identities init -backend=false -input=false -lockfile=readonly
terraform -chdir=phase4/bootstrap/identities validate -json
```

O lock deve permanecer idêntico após essa validação. `.terraform/` permanece
ignorado; pacotes, binários e caches não devem ser versionados. O lock local de
`phase4/modules/endpoint-recovery` também é ignorado e não é distribuído pelo Git;
como módulo filho, ele usa a seleção de providers da root.

Referências: [dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock),
[providers lock](https://developer.hashicorp.com/terraform/cli/commands/providers/lock) e
[SHA256SUMS oficiais AWS 5.100.0](https://releases.hashicorp.com/terraform-provider-aws/5.100.0/terraform-provider-aws_5.100.0_SHA256SUMS).
