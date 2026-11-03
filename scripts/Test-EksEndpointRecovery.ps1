param([string]$Python = 'python')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $Python -B (Join-Path $PSScriptRoot 'eks-endpoint-recovery/test_recovery.py') -v
if ($LASTEXITCODE -ne 0) {
    throw 'Testes offline de recuperacao falharam; abertura permanece bloqueada.'
}
