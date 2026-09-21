#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Parent = Split-Path -Parent $Here
$Helper = Join-Path $Parent "scripts\create_release_notes.ps1"
if (Test-Path -LiteralPath $Helper) {
    & $Helper document @args
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    return
}
throw "Monorepo helper missing. Run scripts\create_release_notes.ps1 document"
