# Condense installer and updater for Windows 10/11 (64-bit).
#
# Install - paste into PowerShell (Start menu > type "PowerShell"):
#   irm https://raw.githubusercontent.com/s-hurtado26/condense/main/install.ps1 | iex
# With options (see scripts\install-windows.ps1), e.g. AMD graphics and neural speaker detection:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/s-hurtado26/condense/main/install.ps1))) -Gpu amd -WithPyannote
#
# Update: Condense > Help > Check for Updates, or
#   powershell -ExecutionPolicy Bypass -File "$HOME\.condense\install.ps1" -Update
param(
  [switch]$Update,          # update with the options used before; no winget step
  [string]$Version = "",    # e.g. v0.11.0 instead of the latest release
  [string]$Ref = "",        # a branch, for testing unreleased changes
  [switch]$Check,           # only show the installed and the latest version
  [string]$Gpu = "",
  [string]$Venv = "",
  [string]$TorchIndex = "",
  [string[]]$AmdWheels = @(),
  [switch]$ReinstallTorch,
  [switch]$WithPyannote,
  [switch]$NoApp
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell 5.1 downloads are very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = if ($env:CONDENSE_REPO) { $env:CONDENSE_REPO } else { "s-hurtado26/condense" }
$Api = if ($env:CONDENSE_API) { $env:CONDENSE_API } else { "https://api.github.com/repos/$Repo" }
$Download = if ($env:CONDENSE_DOWNLOAD) { $env:CONDENSE_DOWNLOAD } else { "https://github.com/$Repo/archive/refs" }
$CHome = if ($env:CONDENSE_HOME) { $env:CONDENSE_HOME } else { Join-Path $HOME ".condense" }
$OptionsFile = Join-Path $CHome "install-options.json"
$FromFile = [bool]$MyInvocation.MyCommand.Path   # -File (the app's updater) vs. irm | iex

function Say($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) {
  Write-Host ""
  Write-Host "Error: $text" -ForegroundColor Red
  if ($FromFile) { exit 1 }                      # the app reads the exit code
  throw $text                                    # with irm | iex, 'exit' would close the PowerShell window
}
function Get-Text([string]$Url) {
  if ($Url -like "file:*") { return [IO.File]::ReadAllText(([Uri]$Url).LocalPath) }
  return (Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers @{ "User-Agent" = "Condense-installer" }).Content
}
function Save-Url([string]$Url, [string]$Path) {
  if ($Url -like "file:*") { Copy-Item ([Uri]$Url).LocalPath $Path; return }
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path -Headers @{ "User-Agent" = "Condense-installer" }
}

if (-not [Environment]::Is64BitOperatingSystem) { Fail "Condense needs 64-bit Windows." }

# ---- which version
if ($Ref) {
  $tag = $Ref; $url = "$Download/heads/$Ref.zip"
} else {
  try {
    if ($Version) { $meta = Get-Text "$Api/releases/tags/$Version" } else { $meta = Get-Text "$Api/releases/latest" }
  } catch {
    if ($Version) { Fail "Release $Version not found." }
    $repoOk = $true
    try { Get-Text $Api | Out-Null } catch { $repoOk = $false }
    if ($repoOk) { Fail "No Condense release has been published yet: https://github.com/$Repo/releases" }
    Fail "Could not reach GitHub to find the latest release (offline?)."
  }
  $tag = ($meta | ConvertFrom-Json).tag_name
  if (-not $tag) { Fail "Could not read the release information from GitHub." }
  $url = "$Download/tags/$tag.zip"
}

$installedFile = Join-Path $CHome "installed-version"
$installed = ""
if (Test-Path $installedFile) { $installed = (Get-Content -Raw $installedFile).Trim() }
if ($Check) { Write-Host "installed: $(if ($installed) { $installed } else { 'none' })"; Write-Host "latest:    $tag"; return }

# ---- options: given now, or remembered from the first installation
$opts = @{}
foreach ($name in @("Gpu", "Venv", "TorchIndex")) { $v = Get-Variable -Name $name -ValueOnly; if ($v) { $opts[$name] = $v } }
if ($AmdWheels.Count -gt 0) { $opts["AmdWheels"] = $AmdWheels }
foreach ($name in @("WithPyannote", "NoApp")) { if ((Get-Variable -Name $name -ValueOnly).IsPresent) { $opts[$name] = $true } }
if ($Update -and $opts.Count -eq 0 -and (Test-Path $OptionsFile)) {
  $saved = Get-Content -Raw $OptionsFile | ConvertFrom-Json
  foreach ($p in $saved.PSObject.Properties) { $opts[$p.Name] = $p.Value }
}
if ($Update -and -not $installed -and -not (Test-Path (Join-Path $CHome "env")) -and -not (Test-Path $OptionsFile)) {
  Fail "No Condense installation found to update. Install it first (without -Update)."
}

# ---- download and unpack the release
Say "Downloading Condense $tag"
$releases = Join-Path $CHome "releases"
$dir = Join-Path $releases $tag
$tmp = Join-Path $releases "$tag.part"
New-Item -ItemType Directory -Force -Path $releases | Out-Null
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "release.zip"
try { Save-Url $url $zip } catch { Fail "Download of $url failed: $($_.Exception.Message)" }
# .NET's extractor: Expand-Archive in Windows PowerShell 5.1 fails on some valid archives
Add-Type -AssemblyName System.IO.Compression.FileSystem
try { [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp) } catch { Fail "Could not unpack the download: $($_.Exception.Message)" }
$top = Get-ChildItem -Directory $tmp | Select-Object -First 1
if (-not $top -or -not (Test-Path (Join-Path $top.FullName "scripts\install-windows.ps1"))) { Fail "The download does not contain scripts\install-windows.ps1." }
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
Move-Item $top.FullName $dir
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

# ---- run the Windows installer (as a script block: works under the default execution policy too)
$run = @{} + $opts
$run["Source"] = $dir
if ($Update) { $run["SkipSystem"] = $true }
if ($ReinstallTorch) { $run["ReinstallTorch"] = $true }
Say "Running the Windows installer"
try {
  $installer = [scriptblock]::Create([IO.File]::ReadAllText((Join-Path $dir "scripts\install-windows.ps1")))
  & $installer @run
} catch {
  Fail "$($_.Exception.Message)"
}

# ---- remember what was installed, and how
New-Item -ItemType Directory -Force -Path $CHome | Out-Null
if (-not $Update) { $opts | ConvertTo-Json | Set-Content -Encoding UTF8 $OptionsFile }
Set-Content -Encoding ASCII $installedFile $tag
Copy-Item -Force (Join-Path $dir "install.ps1") (Join-Path $CHome "install.ps1")   # the updater updates itself

# keep the two newest releases (the previous one is a fallback)
Get-ChildItem -Directory $releases | Where-Object { $_.Name -notlike "*.part" } | Sort-Object LastWriteTime -Descending |
  Select-Object -Skip 2 | ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }

Say "Condense $tag is installed"
if ($Update) { Write-Host "Restart Condense to use the new version." }
else { Write-Host "Start it from the Start menu or the Desktop shortcut (Condense). Later updates: Help > Check for Updates." }
