Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& node (Join-Path $PSScriptRoot 'identity-contracts/contracts.test.mjs')
if ($LASTEXITCODE -ne 0) {
    throw 'Contrato de identidade ou lease invalido; nenhuma operacao remota foi executada.'
}
