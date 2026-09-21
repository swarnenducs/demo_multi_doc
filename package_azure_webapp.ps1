#Requires -Version 5.1
<#
.SYNOPSIS
  Zip THIS folder for Azure Web App zip-deploy (works as its own repo).

.EXAMPLE
  .\package_azure_webapp.ps1
  .\package_azure_webapp.ps1 C:\temp\ip-api.zip

.NOTES
  az webapp deploy -g <rg> -n <app> --src-path dist\azure-webapp.zip --type zip
#>
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$ComponentLabel = "document-processing-mcp"
$WebsitesPort = "8001"
$Startup = "python run.py"
$StartupAlt = "python run.py --transport http --host 0.0.0.0 --port 8001"
$Out = if ($args.Count -ge 1 -and $args[0]) { $args[0] } else { Join-Path $Root "dist\azure-webapp.zip" }
$Out = [System.IO.Path]::GetFullPath($Out)

@(".env.example", "Dockerfile", "pyproject.toml", "README.md", "requirements.txt", "run.py", "src", "prompts", "config") | ForEach-Object {
  if (-not (Test-Path (Join-Path $Root $_))) { throw "Missing required deployment input: $_" }
}

$Stage = Join-Path $Root "dist\.webapp-stage"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null
try {

$xd = @(".git", ".venv", "venv", "dist", "build", "tests", "data", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov")
$xf = @(".env", ".DS_Store", "Thumbs.db")
$robo = Get-Command robocopy -ErrorAction SilentlyContinue
if ($robo) {
  & robocopy.exe $Root $Stage "/E" "/NFL" "/NDL" "/NJH" "/NJS" "/nc" "/ns" "/np" "/XD" $xd "/XF" $xf
  $code = $LASTEXITCODE
    if ($code -ge 8) { throw "robocopy failed with exit $code" }
} else {
    Get-ChildItem -Force $Root | Where-Object {
        $_.Name -notin ($xd + @("dist")) -and $_.Name -ne ".env"
    } | Copy-Item -Destination $Stage -Recurse -Force
}

@"
[config]
SCM_DO_BUILD_DURING_DEPLOYMENT=true
"@ | Set-Content -Path (Join-Path $Stage ".deployment") -Encoding ascii

@"
Azure Web App zip — $ComponentLabel
This zip is only this component (safe to move to its own repo).

WEBSITES_PORT=$WebsitesPort
SCM_DO_BUILD_DURING_DEPLOYMENT=true  (also in .deployment)

Startup command:
  $Startup

Alternate:
  $StartupAlt

requirements.txt contains the pinned runtime dependencies for the flat src layout.
Do not put .env in the zip. Use App settings + Key Vault references.
"@ | Set-Content -Path (Join-Path $Stage "STARTUP.txt") -Encoding utf8

if (Test-Path $Out) { Remove-Item -Force $Out }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Out)
} finally {
  if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
}
Write-Host "Wrote $Out"
Write-Host "Deploy: az webapp deploy -g <rg> -n <app> --src-path $Out --type zip"
Write-Host "Startup: $Startup   WEBSITES_PORT=$WebsitesPort"
