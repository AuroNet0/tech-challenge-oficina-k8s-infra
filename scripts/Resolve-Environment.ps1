[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z][a-z0-9-]{2,11}$')]
    [string]$EnvironmentId,

    [Parameter(Mandatory = $true)]
    [ValidateSet('infra', 'platform', 'edge')]
    [string]$Stack,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$GitRevision,

    [switch]$RequireReady,
    [string]$ObservedAccountId,
    [string]$ObservedRegion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Stack = $Stack.ToLowerInvariant()
$GitRevision = $GitRevision.ToLowerInvariant()
$catalogPath = Join-Path $PSScriptRoot '../phase4/environments/catalog.json'
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($catalog.schema_version -ne 1) {
    throw 'Versao de catalogo desconhecida.'
}
if ($catalog.environments.PSObject.Properties.Name -cnotcontains $EnvironmentId) {
    throw 'Ambiente desconhecido. Cadastre e revise o mapeamento antes de operar.'
}

$allKeys = @()
foreach ($entry in $catalog.environments.PSObject.Properties) {
    $settings = $entry.Value
    if ($entry.Name -cnotmatch '^[a-z][a-z0-9-]{2,11}$' -or
        $settings.project_name -cnotmatch '^[a-z][a-z0-9-]{1,15}$' -or
        $settings.resource_prefix -cnotmatch '^[a-z][a-z0-9-]{1,7}$' -or
        $settings.owner -cnotmatch '^[a-z][a-z0-9-]{2,62}$' -or
        $settings.aws_region -cnotmatch '^[a-z]{2}-[a-z]+-[0-9]+$' -or
        $settings.enabled -isnot [bool]) {
        throw 'Identificadores, regiao, ownership ou habilitacao invalidos no catalogo.'
    }
    if (@($settings.state_keys.PSObject.Properties).Count -ne 3) {
        throw 'O catalogo deve mapear exatamente tres stacks efemeras.'
    }
    foreach ($stackName in @('infra', 'platform', 'edge')) {
        $stateKey = $settings.state_keys.$stackName
        if ($stateKey -cne "phase4/environments/$($entry.Name)/$stackName.tfstate") {
            throw 'State fora do namespace controlado. Bootstrap e legado sao proibidos.'
        }
        if ($allKeys -ccontains $stateKey) {
            throw 'Chave de state duplicada.'
        }
        $allKeys += $stateKey
    }
}

$environment = $catalog.environments.$EnvironmentId
$ready = $environment.enabled -and
    $environment.aws_account_id -cmatch '^[0-9]{12}$' -and
    $environment.backend_bucket -cmatch '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'
if ($RequireReady) {
    if (-not $ready) {
        throw 'Ambiente bloqueado: habilitacao, conta e bucket precisam de revisao posterior.'
    }
    & (Join-Path $PSScriptRoot 'Assert-AwsIdentity.ps1') `
        -ExpectedAccountId $environment.aws_account_id -ExpectedRegion $environment.aws_region `
        -ObservedAccountId $ObservedAccountId -ObservedRegion $ObservedRegion
}

[pscustomobject]@{
    EnvironmentId = $EnvironmentId
    Stack = $Stack
    Ready = [bool]$ready
    TerraformVersion = $catalog.terraform_version
    WorkingDirectory = "phase4/stacks/$Stack"
    Backend = [ordered]@{
        bucket = $environment.backend_bucket
        key = $environment.state_keys.$Stack
        region = $environment.aws_region
        allowed_account_ids = @($environment.aws_account_id)
        encrypt = $true
        use_lockfile = $true
    }
    Variables = [ordered]@{
        environment_id = $EnvironmentId
        aws_account_id = $environment.aws_account_id
        aws_region = $environment.aws_region
        git_revision = $GitRevision
    }
}
