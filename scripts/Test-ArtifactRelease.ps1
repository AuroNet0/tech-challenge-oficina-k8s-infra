param([string]$Python = 'python')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $Python -B (Join-Path $PSScriptRoot 'artifact-release/test_release.py') -v
if ($LASTEXITCODE -ne 0) { throw 'Contratos offline de artefatos rejeitados; publicacao permanece indisponivel.' }
