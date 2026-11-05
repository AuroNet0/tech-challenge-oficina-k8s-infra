param([string]$Python = 'python')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& $Python -B -m unittest discover -s (Join-Path $PSScriptRoot 'artifact-release') -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Contratos offline de artefatos rejeitados; publicacao permanece indisponivel.' }
