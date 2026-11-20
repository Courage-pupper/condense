# Install (or update) Condense on Windows 10/11 (64-bit). Normally run by install.ps1 (the one-line installer);
# from a Condense folder you can also run it directly:
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 [options]
#
# Options:
#   -Gpu auto|nvidia|amd|cpu   which PyTorch build to install (default: detected from the graphics card)
#   -Venv PATH                 environment to use or create (default: ~\.condense\env); an existing PyTorch is kept
#   -TorchIndex URL            PyTorch package index for -Gpu nvidia / cpu (default: CUDA 12.8 / CPU builds)
#   -AmdWheels URL,URL,...     AMD's PyTorch-for-Windows wheel files to install instead of the built-in list
#   -ReinstallTorch            replace the PyTorch already in the environment
#   -WithPyannote              also install pyannote.audio (neural speaker detection; needs a Hugging Face token)
#   -SkipSystem                do not install Python / FFmpeg / HandBrake with winget (used for updates)
#   -NoApp                     do not create Start-menu and Desktop shortcuts
param(
  [string]$Source = "",
  [ValidateSet("auto", "nvidia", "amd", "cpu")][string]$Gpu = "auto",
  [string]$Venv = "",
  [string]$TorchIndex = "",
  [string[]]$AmdWheels = @(),
  [switch]$ReinstallTorch,
  [switch]$WithPyannote,
  [switch]$SkipSystem,
  [switch]$NoApp
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Say($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" }
function Run($exe) {
  # run a program with the remaining arguments; stop with a clear message if it fails
  $rest = $args
  & $exe @rest | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "'$exe $($rest -join ' ')' failed (exit code $LASTEXITCODE)." }
}
function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
  # run a program for a yes/no check: stderr is ignored (in Windows PowerShell 5.1, redirecting it while
  # $ErrorActionPreference is "Stop" would turn any warning into a fatal error)
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $Exe @Arguments 2>$null
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = (($out | Out-String).Trim()) }
  } finally { $ErrorActionPreference = $old }
}
function Invoke-Py([string]$Python, [string]$Code, [string[]]$Arguments = @(), [switch]$Quiet) {
  # Python code goes through a file: Windows PowerShell 5.1 mangles double quotes in native arguments
  $file = Join-Path $env:TEMP ("condense-" + [guid]::NewGuid().ToString("N") + ".py")
  [IO.File]::WriteAllText($file, $Code)
  try {
    if ($Quiet) { return (Invoke-Quiet $Python (@($file) + $Arguments)) }
    & $Python $file @Arguments | Out-Host
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = "" }
  } finally { Remove-Item -Force $file -ErrorAction SilentlyContinue }
}
function Refresh-Path {
  $links = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
  $env:Path = ([Environment]::GetEnvironmentVariable("Path", "Machine"), [Environment]::GetEnvironmentVariable("Path", "User"), $links) -join ";"
}

if (-not $Source) { $Source = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not (Test-Path (Join-Path $Source "pyproject.toml"))) { throw "Condense source folder not found: $Source" }
if (-not $Venv) { $Venv = Join-Path $HOME ".condense\env" }
if (-not [Environment]::Is64BitOperatingSystem) { throw "Condense needs 64-bit Windows." }

# AMD's PyTorch for Windows (ROCm 7.2.1, PyTorch 2.9.1, Python 3.12; Radeon RX 7900 / RX 9000 series, Windows 11,
# AMD Software driver 26.2.2 or newer). Newer wheels: pass -AmdWheels with the URLs from AMD's install guide.
$AmdBase = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
$AmdSdk = @("$AmdBase/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl", "$AmdBase/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
            "$AmdBase/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl", "$AmdBase/rocm-7.2.1.tar.gz")
$AmdTorch = @("$AmdBase/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl")
$AmdTorchaudio = @("$AmdBase/torchaudio-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl")

# ---------------------------------------------------------------- system tools (winget)
Refresh-Path
if (-not $SkipSystem) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is missing. Install 'App Installer' from the Microsoft Store (https://aka.ms/getwinget), then run this again."
  }
  foreach ($id in @("Python.Python.3.12", "Gyan.FFmpeg", "HandBrake.HandBrake.CLI")) {
    Say "Installing $id (winget)"
    & winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
    # winget also returns non-zero for "already installed": what matters is checked below
  }
  Refresh-Path
}
foreach ($tool in @("ffmpeg", "ffprobe")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    throw "$tool was not found. Install FFmpeg (winget install -e --id Gyan.FFmpeg), open a new PowerShell window and run this again."
  }
}
if (-not (Get-Command ffplay -ErrorAction SilentlyContinue)) { Note "ffplay not found: the transcript player needs it (part of the Gyan.FFmpeg package)." }
if (-not (Get-Command HandBrakeCLI -ErrorAction SilentlyContinue)) { Note "HandBrakeCLI not found: Condense uses ffmpeg for compression instead." }

# ---------------------------------------------------------------- GPU
$names = @()
try { $names = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch { }
if ($Gpu -eq "auto") {
  $all = ($names -join " ").ToLower()
  if ($all -match "nvidia|geforce|rtx") { $Gpu = "nvidia" }
  elseif ($all -match "radeon|amd") { $Gpu = "amd" }
  else { $Gpu = "cpu" }
  Say "Graphics: $($names -join ', ')  ->  PyTorch for: $Gpu   (override with -Gpu nvidia|amd|cpu)"
}

# ---------------------------------------------------------------- Python environment
$vpy = Join-Path $Venv "Scripts\python.exe"
if (Test-Path $vpy) {
  Say "Using the existing environment $Venv"
} else {
  $py = $null
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $r = Invoke-Quiet "py" @("-3.12", "-c", "import sys; print(sys.executable)")
    if ($r.Code -eq 0 -and $r.Out) { $py = $r.Out }
  }
  if (-not $py) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $candidate) { $py = $candidate }
  }
  if (-not $py) { throw "Python 3.12 not found. Install it (winget install -e --id Python.Python.3.12), open a new PowerShell window and run this again." }
  Say "Creating the environment $Venv"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Venv) | Out-Null
  Run $py -m venv $Venv
}
if ((Invoke-Quiet $vpy @("-c", "import tkinter")).Code -ne 0) { throw "This Python has no Tkinter. Use the python.org / winget Python 3.12 (it includes Tkinter)." }
Run $vpy -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------- PyTorch
$hasTorch = ((Invoke-Quiet $vpy @("-c", "import torch")).Code -eq 0)
if ($hasTorch -and -not $ReinstallTorch) {
  Say "Keeping the PyTorch already in this environment"
} elseif ($Gpu -eq "amd") {
  $ver = (Invoke-Quiet $vpy @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")).Out
  if ($AmdWheels.Count -eq 0 -and $ver -ne "3.12") { throw "AMD's PyTorch for Windows needs Python 3.12 (this environment has $ver). Use a new -Venv." }
  Say "Installing AMD's PyTorch for Windows (ROCm) - a large download"
  Note "Needs a supported Radeon (RX 7900 / RX 9000 series), Windows 11 and AMD Software 26.2.2 or newer."
  if ($AmdWheels.Count -gt 0) {
    Run $vpy -m pip install --no-cache-dir @AmdWheels
  } else {
    Run $vpy -m pip install --no-cache-dir @AmdSdk
    $tw = $AmdTorch
    if ($WithPyannote) { $tw = $AmdTorch + $AmdTorchaudio }
    Run $vpy -m pip install --no-cache-dir @tw
  }
} else {
  if (-not $TorchIndex) {
    if ($Gpu -eq "nvidia") { $TorchIndex = "https://download.pytorch.org/whl/cu128" } else { $TorchIndex = "https://download.pytorch.org/whl/cpu" }
  }
  $pk = @("torch")
  if ($WithPyannote) { $pk = @("torch", "torchaudio") }
  Say "Installing PyTorch ($Gpu) from $TorchIndex - a large download"
  Run $vpy -m pip install --upgrade @pk --index-url $TorchIndex
}
$torchInfo = @'
import torch
api = ("ROCm " + torch.version.hip) if getattr(torch.version, "hip", None) else (("CUDA " + torch.version.cuda) if torch.version.cuda else "CPU build")
gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU visible"
print(f"    torch {torch.__version__} - {api} - {gpu}")
'@
Invoke-Py $vpy $torchInfo | Out-Null

# pin the installed torch so that the packages below cannot replace it with another build from PyPI
$constraints = Join-Path $env:TEMP "condense-constraints.txt"
$pin = @'
import sys
from importlib.metadata import PackageNotFoundError, version
with open(sys.argv[1], "w") as fh:
    for dist in ("torch", "torchaudio", "torchvision"):
        try:
            fh.write(f"{dist}=={version(dist)}\n")
        except PackageNotFoundError:
            pass
'@
if ((Invoke-Py $vpy $pin @($constraints)).Code -ne 0) { throw "Could not record the installed PyTorch version." }

# ---------------------------------------------------------------- Condense
$extras = "windows"
if ($WithPyannote) { $extras = "windows,pyannote" }
Say "Installing Condense [$extras] into $Venv"
Run $vpy -m pip install --upgrade "$Source[$extras]" -c $constraints
Remove-Item -Force $constraints -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- check
Say "Checking"
$check = @'
import importlib.util, torch
print("    openai-whisper:", "installed" if importlib.util.find_spec("whisper") else "MISSING")
if torch.cuda.is_available():
    api = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
    print(f"    Whisper and speaker detection will run on the GPU ({api}: {torch.cuda.get_device_name(0)}).")
else:
    print("    PyTorch sees no GPU: Whisper will run on the CPU (much slower).")
    raise SystemExit(3)
'@
$checked = Invoke-Py $vpy $check
if ($checked.Code -eq 3 -and $Gpu -ne "cpu") {
  if ($Gpu -eq "amd") {
    Note "AMD: update AMD Software (Adrenalin) to 26.2.2 or newer and restart. PyTorch for Windows supports the RX 7900 and"
    Note "RX 9000 series on Windows 11; other Radeon cards run Whisper on the CPU (docs\install-windows.md, Troubleshooting)."
  } else {
    Note "NVIDIA: install or update the NVIDIA driver (GeForce Experience / NVIDIA App or nvidia.com) and restart."
  }
}
$hw = @()
foreach ($enc in @("hevc_nvenc", "hevc_amf", "hevc_qsv")) {
  $r = Invoke-Quiet "ffmpeg" @("-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "3", "-c:v", $enc, "-f", "null", "-")
  if ($r.Code -eq 0) { $hw += $enc }
}
if ($hw.Count -gt 0) { Note "GPU video encoding: $($hw -join ', ')" } else { Note "GPU video encoding: not available (Condense compresses on the CPU)" }

# ---------------------------------------------------------------- shortcuts
if (-not $NoApp) {
  Say "Creating Start-menu and Desktop shortcuts"
  Run (Join-Path $Venv "Scripts\condense.exe") --install-app
}

Write-Host ""
Write-Host "Done. Start Condense from the Start menu or the Desktop shortcut." -ForegroundColor Green
