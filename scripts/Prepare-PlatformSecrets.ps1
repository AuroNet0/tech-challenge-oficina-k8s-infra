[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('demo-a', 'demo-b')][string]$EnvironmentId,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$GitRevision,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9]{12}$')][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$ContractPath,
    [string]$Python = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$contract = & (Join-Path $PSScriptRoot 'Resolve-Environment.ps1') -EnvironmentId $EnvironmentId -Stack platform -GitRevision $GitRevision
& (Join-Path $PSScriptRoot 'Assert-AwsIdentity.ps1') -ExpectedAccountId $AwsAccountId -ExpectedRegion $contract.Variables.aws_region -ObservedAccountId $AwsAccountId -ObservedRegion $AwsRegion
$env:PYTHONDONTWRITEBYTECODE = '1'
& $Python (Join-Path $PSScriptRoot 'platform-secrets/platform_secrets.py') `
    --catalog (Join-Path $PSScriptRoot '../phase4/environments/catalog.json') `
    --contract $ContractPath --environment $EnvironmentId --account $AwsAccountId --region $AwsRegion --revision $GitRevision
if ($LASTEXITCODE -ne 0) { throw 'Platform Secrets dry-run rejected. No operational transport was enabled.' }
