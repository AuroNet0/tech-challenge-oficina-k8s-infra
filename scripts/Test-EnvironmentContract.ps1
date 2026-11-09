Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$resolver = Join-Path $PSScriptRoot 'Resolve-Environment.ps1'
$revision = 'b2e84c484c6dfb5b9fc8b502d3cc69310f52d4cf'
$checks = 0
$keys = @()
foreach ($environmentId in @('demo-a', 'demo-b')) {
    foreach ($stackName in @('infra', 'platform', 'edge')) {
        $contract = & $resolver -EnvironmentId $environmentId -Stack $stackName -GitRevision $revision
        if ($contract.Ready -or $null -ne $contract.Backend.bucket -or
            -not $contract.Backend.use_lockfile -or -not $contract.Backend.encrypt -or
            $contract.Backend.key -cne "phase4/environments/$environmentId/$stackName.tfstate" -or
            $keys -contains $contract.Backend.key) {
            throw 'Contrato inseguro ou isolamento de states incorreto.'
        }
        $keys += $contract.Backend.key
        $checks++
    }
}

foreach ($stackName in @('infra', 'platform', 'edge')) {
    $contract = & $resolver -EnvironmentId demo-a -Stack $stackName.ToUpperInvariant() -GitRevision $revision.ToUpperInvariant()
    if ($contract.Stack -cne $stackName -or
        $contract.WorkingDirectory -cne "phase4/stacks/$stackName" -or
        $contract.Backend.key -cne "phase4/environments/demo-a/$stackName.tfstate" -or
        $contract.Variables.git_revision -cnotmatch '^[0-9a-f]{40}$' -or
        $contract.Variables.git_revision -cne $revision) {
        throw 'Stack ou revisao nao atende ao contrato Terraform canonico.'
    }
    $checks++
}

$identityValidator = Join-Path $PSScriptRoot 'Assert-AwsIdentity.ps1'
& $identityValidator -ExpectedAccountId '123456789012' -ExpectedRegion 'us-east-1' `
    -ObservedAccountId '123456789012' -ObservedRegion 'us-east-1'
$checks++
foreach ($identity in @(
    @{ ObservedAccountId = '210987654321'; ObservedRegion = 'us-east-1' },
    @{ ObservedAccountId = '123456789012'; ObservedRegion = 'sa-east-1' },
    @{ ObservedAccountId = '210987654321'; ObservedRegion = 'sa-east-1' },
    @{ ObservedAccountId = ''; ObservedRegion = '' }
)) {
    $rejected = $false
    try {
        & $identityValidator -ExpectedAccountId '123456789012' -ExpectedRegion 'us-east-1' @identity
    }
    catch {
        if ($_.Exception.Message -cne 'Conta ou regiao observada diferente do cadastro.') {
            throw
        }
        $rejected = $true
    }
    if (-not $rejected) {
        throw 'Identidade AWS divergente ou ausente foi aceita.'
    }
    $checks++
}

$rejections = @(
    @{ EnvironmentId = 'production'; Stack = 'infra'; GitRevision = $revision },
    @{ EnvironmentId = '../demo-a'; Stack = 'infra'; GitRevision = $revision },
    @{ EnvironmentId = 'demo-a'; Stack = 'bootstrap'; GitRevision = $revision },
    @{ EnvironmentId = 'demo-a'; Stack = 'infra'; GitRevision = 'main' },
    @{ EnvironmentId = 'demo-a'; Stack = 'infra'; GitRevision = $revision; RequireReady = $true }
)
foreach ($arguments in $rejections) {
    $rejected = $false
    try {
        $null = & $resolver @arguments
    }
    catch {
        $rejected = $true
    }
    if (-not $rejected) {
        throw 'Entrada insegura foi aceita.'
    }
    $checks++
}
Write-Output "$checks verificacoes locais aprovadas; nenhuma chamada AWS ou Terraform executada pelo script."
