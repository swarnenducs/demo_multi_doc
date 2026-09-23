#Requires -Version 5.1
<#
.SYNOPSIS
  Configure an existing Azure Web App for document-processing-mcp from
  config/azure-webapp.settings.json: managed identity, IAM role assignments
  (Key Vault + Storage Blob), blob containers, Azure SQL access (firewall +
  Key Vault secret verification), app settings, startup command, and
  (optionally) a ZIP deploy.

.DESCRIPTION
  Resource names (storage account, Key Vault, blob containers, SQL server) are DERIVED
  from the settings file so this stays in sync with the config. Role scopes and the SQL
  server resource group are resolved with `az` lookups, so you do not pass them.

  Access set up for the Web App's system-assigned managed identity:
    * Key Vault  -> 'Key Vault Secrets User' (resolves @Microsoft.KeyVault references).
    * Storage    -> 'Storage Blob Data Contributor' (blob read/write via Entra).
    * Azure SQL  -> the app authenticates as AZURE_SQL_USER using the password read from
                    Key Vault; this step ensures the SQL server firewall allows the App
                    Service and that the referenced Key Vault secret exists.

  Idempotent: safe to re-run.

.PARAMETER ResourceGroup   Resource group of the Web App (required).
.PARAMETER AppName         Web App name (required).
.PARAMETER SubscriptionId  Subscription to target (optional; sets the active sub).
.PARAMETER ConfigPath      Settings file. Default: config/azure-webapp.settings.json
.PARAMETER Deploy          Also build the ZIP (package_azure_webapp-document-processing.ps1) and deploy it.
.PARAMETER GrantCurrentUserBlob  Also grant the signed-in user Storage Blob Data Contributor (local az-login testing).
.PARAMETER SkipRoles       Skip IAM role assignments.
.PARAMETER SkipContainers  Skip blob container creation.
.PARAMETER SkipSql         Skip Azure SQL firewall rule + Key Vault secret verification.
.PARAMETER SkipSettings    Skip applying app settings.
.PARAMETER DryRun          Print planned mutating actions without executing them.

.EXAMPLE
  .\setup_azure_webapp-document-processing.ps1 -ResourceGroup my-rg -AppName app-ipp-...-document-mcp-dev -SubscriptionId <sub>

.EXAMPLE
  .\setup_azure_webapp-document-processing.ps1 -ResourceGroup my-rg -AppName app-ipp-...-document-mcp-dev -Deploy
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ResourceGroup,
  [Parameter(Mandatory = $true)][string]$AppName,
  [string]$SubscriptionId,
  [string]$ConfigPath,
  [switch]$CreateIfMissing,
  [string]$Plan = "asp-ipp-northeurope-apps-dev",
  [string]$Runtime = "PYTHON:3.14",
  [switch]$Deploy,
  [switch]$GrantCurrentUserBlob,
  [switch]$SkipRoles,
  [switch]$SkipContainers,
  [switch]$SkipSql,
  [switch]$SkipSettings,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ConfigPath) { $ConfigPath = Join-Path $ScriptRoot "config/azure-webapp.settings.json" }

# Component-specific values.
$ComponentLabel = "document-processing-mcp"
$StartupCommand = "bash startup.sh"
$DefaultPort    = "8001"
$HealthPath     = "/mcp"

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

function Invoke-Az {
  param([string[]]$AzArgs, [switch]$AllowFail, [switch]$Mutating)
  if ($DryRun -and $Mutating) { Write-Host "    [dry-run] az $($AzArgs -join ' ')" -ForegroundColor DarkGray; return "" }
  $prevEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $out = & az @AzArgs 2>&1 } finally { $ErrorActionPreference = $prevEAP }
  if ($LASTEXITCODE -ne 0) {
    if ($AllowFail) { Warn "az $($AzArgs -join ' ') -> $out"; return "" }
    throw "az $($AzArgs -join ' ') failed: $out"
  }
  return ($out -join "`n").Trim()
}

# --- 0. az present + login -------------------------------------------------
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw "Azure CLI (az) not found on PATH." }
if (-not (Test-Path $ConfigPath)) { throw "Config not found: $ConfigPath" }

Info "Azure account"
$acct = Invoke-Az @("account", "show", "-o", "json") -AllowFail
if (-not $acct) { throw "Not logged in. Run 'az login' first." }
if ($SubscriptionId) { [void](Invoke-Az @("account", "set", "--subscription", $SubscriptionId)) }
$subId = (Invoke-Az @("account", "show", "--query", "id", "-o", "tsv"))
Ok "Subscription: $subId"

# --- 1. Read config + derive resources -------------------------------------
Info "Reading $ConfigPath"
$config = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$map = @{}
foreach ($s in $config) { $map[$s.name] = "$($s.value)" }

function Has-Placeholder([string]$v) { return ($v -match "<[^>]+>") }

$storageAccount = $map["AZURE_STORAGE_ACCOUNT_NAME"]
if (-not $storageAccount -or (Has-Placeholder $storageAccount)) { throw "AZURE_STORAGE_ACCOUNT_NAME missing/placeholder in config." }

# Key Vault name: first real *.vault.azure.net host in any setting value.
$vaultName = $null
foreach ($v in $map.Values) {
  if ($v -match "https://([a-z0-9][a-z0-9-]+)\.vault\.azure\.net") {
    if (-not (Has-Placeholder $Matches[0])) { $vaultName = $Matches[1]; break }
  }
}

$containers = @()
foreach ($k in @("AZURE_BLOB_CONTAINER", "AZURE_BLOB_CUSTOM_CONTAINER", "IPP_METADATA_COLLECTION")) {
  $c = $map[$k]
  if ($c -and -not (Has-Placeholder $c)) { $containers += $c }
}
$containers = $containers | Select-Object -Unique

# Azure SQL: short server name (strip .database.windows.net) for firewall setup.
$sqlServerName = $null
$sqlServerFqdn = $map["AZURE_SQL_SERVER"]
if ($sqlServerFqdn -and -not (Has-Placeholder $sqlServerFqdn)) {
  $h = $sqlServerFqdn.Trim()
  if ($h.ToLower().StartsWith("tcp:")) { $h = $h.Substring(4) }
  $h = $h.Split(",")[0]
  $sqlServerName = $h.Split(".")[0]
}

# Key Vault secret names referenced by @Microsoft.KeyVault(...) app settings.
$kvSecretNames = @()
foreach ($v in $map.Values) {
  if ($v -match "vault\.azure\.net/secrets/([A-Za-z0-9-]+)") {
    $n = $Matches[1]
    if (-not (Has-Placeholder $n)) { $kvSecretNames += $n }
  }
}
$kvSecretNames = $kvSecretNames | Select-Object -Unique

Ok "Component      : $ComponentLabel"
Ok "Storage account: $storageAccount"
Ok "Key Vault      : $(if ($vaultName) { $vaultName } else { '(none found)' })"
Ok "Blob containers: $($containers -join ', ')"
Ok "SQL server     : $(if ($sqlServerName) { $sqlServerName } else { '(none found)' })"
Ok "KV secret refs : $(if ($kvSecretNames) { $kvSecretNames -join ', ' } else { '(none)' })"

# --- 2. Verify (or create) Web App -----------------------------------------
Info "Checking Web App $AppName"
$exists = Invoke-Az @("webapp", "show", "-g", $ResourceGroup, "-n", $AppName, "--query", "name", "-o", "tsv") -AllowFail
if (-not $exists) {
  if ($CreateIfMissing) {
    Info "Web App not found; creating it (plan '$Plan', runtime '$Runtime')"
    $planId = Invoke-Az @("appservice", "plan", "list", "--query", "[?name=='$Plan'].id | [0]", "-o", "tsv") -AllowFail
    if (-not $planId -and -not $DryRun) { throw "App Service Plan '$Plan' not found. Pass -Plan <name> of an existing Linux plan." }
    [void](Invoke-Az @("webapp", "create", "-g", $ResourceGroup, "-n", $AppName, "--plan", $planId, "--runtime", $Runtime) -Mutating)
    Ok "Created Web App '$AppName'."
  }
  elseif (-not $DryRun) {
    throw "Web App '$AppName' not found in RG '$ResourceGroup'. Re-run with -CreateIfMissing to create it, or create it manually first."
  }
}
else { Ok "Web App present" }

# --- 3. System-assigned managed identity -----------------------------------
Info "Enabling system-assigned managed identity"
[void](Invoke-Az @("webapp", "identity", "assign", "-g", $ResourceGroup, "-n", $AppName) -Mutating)
$principalId = Invoke-Az @("webapp", "identity", "show", "-g", $ResourceGroup, "-n", $AppName, "--query", "principalId", "-o", "tsv")
Ok "Principal ID: $(if ($principalId) { $principalId } else { '(dry-run)' })"

# --- 4. IAM role assignments -----------------------------------------------
function Assign-Role([string]$assignee, [string]$assigneeType, [string]$role, [string]$scope) {
  if (-not $scope) { Warn "Skipping '$role' (scope not resolved)."; return }
  $existing = Invoke-Az @("role", "assignment", "list", "--assignee", $assignee, "--role", $role, "--scope", $scope, "--query", "[0].id", "-o", "tsv") -AllowFail
  if ($existing) { Ok "Role '$role' already assigned."; return }
  [void](Invoke-Az @("role", "assignment", "create", "--assignee-object-id", $assignee, "--assignee-principal-type", $assigneeType, "--role", $role, "--scope", $scope) -AllowFail -Mutating)
  Ok "Assigned '$role'."
}

if (-not $SkipRoles) {
  Info "IAM role assignments (Web App managed identity)"
  if ($principalId) {
    if ($vaultName) {
      $vaultId = Invoke-Az @("keyvault", "show", "-n", $vaultName, "--query", "id", "-o", "tsv") -AllowFail
      Assign-Role $principalId "ServicePrincipal" "Key Vault Secrets User" $vaultId
    }
    else { Warn "No Key Vault in config; skipping Key Vault Secrets User." }

    $storageId = Invoke-Az @("storage", "account", "show", "-n", $storageAccount, "--query", "id", "-o", "tsv") -AllowFail
    Assign-Role $principalId "ServicePrincipal" "Storage Blob Data Contributor" $storageId
  }
  else { Warn "No principalId (dry-run?); skipping role assignments." }

  if ($GrantCurrentUserBlob) {
    Info "Granting signed-in user Storage Blob Data Contributor (local testing)"
    $me = Invoke-Az @("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv") -AllowFail
    $storageId = Invoke-Az @("storage", "account", "show", "-n", $storageAccount, "--query", "id", "-o", "tsv") -AllowFail
    if ($me) { Assign-Role $me "User" "Storage Blob Data Contributor" $storageId }
  }
}
else { Warn "SkipRoles set; not assigning IAM roles." }

# --- 5. Blob containers -----------------------------------------------------
if (-not $SkipContainers) {
  Info "Ensuring blob containers exist (auth-mode login)"
  foreach ($c in $containers) {
    $res = Invoke-Az @("storage", "container", "create", "--account-name", $storageAccount, "--name", $c, "--auth-mode", "login", "-o", "json") -AllowFail -Mutating
    if ($res) { Ok "Container '$c' ready." } else { Warn "Could not confirm container '$c' (RBAC may still be propagating)." }
  }
}
else { Warn "SkipContainers set." }

# --- 5b. Azure SQL access (firewall + Key Vault secret) ---------------------
if (-not $SkipSql) {
  Info "Azure SQL access for the Web App"
  if ($sqlServerName) {
    $sqlRg = Invoke-Az @("sql", "server", "list", "--query", "[?name=='$sqlServerName'].resourceGroup | [0]", "-o", "tsv") -AllowFail
    if ($sqlRg) {
      Ok "SQL server '$sqlServerName' in RG '$sqlRg'."
      $fwName = "AllowAllWindowsAzureIps"
      $existingFw = Invoke-Az @("sql", "server", "firewall-rule", "show", "-g", $sqlRg, "-s", $sqlServerName, "-n", $fwName, "--query", "name", "-o", "tsv") -AllowFail
      if ($existingFw) { Ok "Firewall rule '$fwName' already present." }
      else {
        [void](Invoke-Az @("sql", "server", "firewall-rule", "create", "-g", $sqlRg, "-s", $sqlServerName, "-n", $fwName, "--start-ip-address", "0.0.0.0", "--end-ip-address", "0.0.0.0") -AllowFail -Mutating)
        Ok "Created firewall rule '$fwName' (allow Azure services)."
      }
    }
    else { Warn "SQL server '$sqlServerName' not found in this subscription; skipping firewall." }
  }
  else { Warn "No AZURE_SQL_SERVER in config; skipping SQL firewall." }

  if ($vaultName -and $kvSecretNames.Count) {
    Info "Verifying referenced Key Vault secrets exist in '$vaultName'"
    foreach ($secret in $kvSecretNames) {
      $found = Invoke-Az @("keyvault", "secret", "show", "--vault-name", $vaultName, "--name", $secret, "--query", "attributes.enabled", "-o", "tsv") -AllowFail
      if ($found -eq "true") { Ok "Secret '$secret' present and enabled." }
      elseif ($found -eq "false") { Warn "Secret '$secret' exists but is DISABLED." }
      else { Warn "Secret '$secret' NOT found in '$vaultName' - the Key Vault reference will fail." }
    }
  }
}
else { Warn "SkipSql set." }

# --- 6. App settings --------------------------------------------------------
if (-not $SkipSettings) {
  Info "Applying app settings from config (skipping <placeholder> values)"
  $applied = @()
  $skipped = @()
  foreach ($s in $config) {
    if (Has-Placeholder "$($s.value)") { $skipped += $s.name } else { $applied += @{ name = $s.name; value = "$($s.value)"; slotSetting = $false } }
  }
  if ($skipped.Count) { Warn "Skipped placeholders: $($skipped -join ', ')" }
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("webapp-settings-" + [guid]::NewGuid().ToString() + ".json")
  ($applied | ConvertTo-Json -Depth 5) | Set-Content -Path $tmp -Encoding utf8
  try {
    [void](Invoke-Az @("webapp", "config", "appsettings", "set", "-g", $ResourceGroup, "-n", $AppName, "--settings", "@$tmp", "-o", "none") -Mutating)
    Ok "Applied $($applied.Count) settings."
  }
  finally { Remove-Item -Force $tmp -ErrorAction SilentlyContinue }

  Info "Setting startup command + port"
  [void](Invoke-Az @("webapp", "config", "set", "-g", $ResourceGroup, "-n", $AppName, "--startup-file", $StartupCommand) -Mutating)
  $port = if ($map["WEBSITES_PORT"]) { $map["WEBSITES_PORT"] } else { $DefaultPort }
  [void](Invoke-Az @("webapp", "config", "appsettings", "set", "-g", $ResourceGroup, "-n", $AppName, "--settings", "WEBSITES_PORT=$port", "-o", "none") -Mutating)
  Ok "Startup '$StartupCommand', WEBSITES_PORT=$port"
}
else { Warn "SkipSettings set." }

# --- 7. Optional deploy -----------------------------------------------------
if ($Deploy) {
  Info "Building deployment ZIP"
  $pkg = Join-Path $ScriptRoot "package_azure_webapp-document-processing.ps1"
  $zip = Join-Path $ScriptRoot "dist/azure-webapp.zip"
  if ($DryRun) { Write-Host "    [dry-run] $pkg; az webapp deploy ... $zip" -ForegroundColor DarkGray }
  else {
    if (-not (Test-Path $pkg)) { throw "package_azure_webapp-document-processing.ps1 not found next to this script." }
    & $pkg
    if (-not (Test-Path $zip)) { throw "Expected package not found: $zip" }
    Info "Deploying ZIP"
    [void](Invoke-Az @("webapp", "deploy", "-g", $ResourceGroup, "-n", $AppName, "--src-path", $zip, "--type", "zip") -Mutating)
    [void](Invoke-Az @("webapp", "restart", "-g", $ResourceGroup, "-n", $AppName) -Mutating)
    Ok "Deployed and restarted."
  }
}

# --- 8. Health hint ---------------------------------------------------------
Info "Done. Access configured for the Web App managed identity:"
Write-Host "    Key Vault -> Key Vault Secrets User" -ForegroundColor Green
Write-Host "    Storage   -> Storage Blob Data Contributor" -ForegroundColor Green
Write-Host "    Azure SQL -> firewall allows Azure services; password via Key Vault" -ForegroundColor Green
Info "Verify:"
Write-Host "    https://$AppName.azurewebsites.net$HealthPath" -ForegroundColor Green
