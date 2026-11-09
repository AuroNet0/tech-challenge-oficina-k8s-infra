[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedAccountId,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedRegion,

    [Parameter(Mandatory = $true)]
    [AllowEmptyString()]
    [string]$ObservedAccountId,

    [Parameter(Mandatory = $true)]
    [AllowEmptyString()]
    [string]$ObservedRegion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($ObservedAccountId -cne $ExpectedAccountId -or $ObservedRegion -cne $ExpectedRegion) {
    throw 'Conta ou regiao observada diferente do cadastro.'
}
