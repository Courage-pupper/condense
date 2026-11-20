#!/usr/bin/env bash
# Install (or update) Condense on Linux. Written for Ubuntu 24.04 / Debian; other distributions: install the system
# packages yourself (see docs/install-linux.md) and pass --skip-system.
#
#   ./scripts/install-linux.sh                       # detect the GPU, new environment in ~/.condense/env
#   ./scripts/install-linux.sh --gpu rocm            # AMD (ROCm build of PyTorch)
#   ./scripts/install-linux.sh --gpu cuda            # NVIDIA (CUDA build of PyTorch)
#   ./scripts/install-linux.sh --gpu cpu             # no GPU
#   ./scripts/install-linux.sh --venv ~/rocm_whisper_env   # install into an environment whose PyTorch already works
#
# Options:
#   --gpu auto|rocm|cuda|cpu   which PyTorch build to install (default: auto-detect)
#   --venv PATH                environment to use or create (default: ~/.condense/env). If it already has
#                              PyTorch, that PyTorch is kept as it is.
#   --torch-index URL          PyTorch package index to install from (overrides --gpu's default index)
#   --reinstall-torch          replace the PyTorch already in the environment
#   --python PATH              Python used to create a new environment (default: python3; needs 3.10–3.12+)
#   --with-pyannote            also install pyannote.audio (neural speaker detection; needs a Hugging Face token)
#   --skip-system              do not install system packages with apt
#   --no-app                   do not add Condense to the app menu
set -euo pipefail

GPU="auto"
VENV="${CONDENSE_ENV:-$HOME/.condense/env}"
TORCH_INDEX=""
REINSTALL_TORCH=0
PYTHON_BIN="python3"
WITH_PYANNOTE=0
SKIP_SYSTEM=0
NO_APP=0

usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --gpu) GPU="${2:-}"; shift 2 ;;
    --venv) VENV="${2:-}"; shift 2 ;;
    --torch-index) TORCH_INDEX="${2:-}"; shift 2 ;;
    --reinstall-torch) REINSTALL_TORCH=1; shift ;;
    --python) PYTHON_BIN="${2:-}"; shift 2 ;;
    --with-pyannote) WITH_PYANNOTE=1; shift ;;
    --skip-system) SKIP_SYSTEM=1; shift ;;
    --no-app) NO_APP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$GPU" in auto|rocm|cuda|cpu) ;; *) echo "--gpu must be auto, rocm, cuda or cpu" >&2; exit 2 ;; esac
VENV="${VENV/#\~/$HOME}"

if [ "$(uname -s)" != "Linux" ]; then
  echo "This installer is for Linux. On a Mac use ./scripts/install-mac.sh" >&2; exit 1
fi
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---------------------------------------------------------------- GPU
if [ "$GPU" = "auto" ]; then
  if [ -e /dev/kfd ] || command -v rocminfo >/dev/null 2>&1; then GPU="rocm"
  elif command -v nvidia-smi >/dev/null 2>&1; then GPU="cuda"
  else GPU="cpu"; fi
  echo "==> Detected GPU type: $GPU   (override with --gpu rocm|cuda|cpu)"
fi
# Conservative defaults that are known to exist; any newer index from pytorch.org works with --torch-index.
# PyTorch's ROCm/CUDA wheels bring their own runtime libraries: only the GPU driver must be installed.
case "$GPU" in
  rocm) DEFAULT_INDEX="https://download.pytorch.org/whl/rocm6.4" ;;
  cuda) DEFAULT_INDEX="https://download.pytorch.org/whl/cu128" ;;
  cpu)  DEFAULT_INDEX="https://download.pytorch.org/whl/cpu" ;;
esac
TORCH_INDEX="${TORCH_INDEX:-$DEFAULT_INDEX}"

# ---------------------------------------------------------------- system packages
PKGS="ffmpeg handbrake-cli python3-venv python3-tk python3-pip libsecret-tools vainfo zenity"
if [ "$GPU" = "rocm" ]; then PKGS="$PKGS mesa-va-drivers"; fi   # VA-API video encoding on AMD
if [ "$SKIP_SYSTEM" = "0" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing system packages (sudo): $PKGS"
    sudo apt-get update -qq
    # shellcheck disable=SC2086
    sudo apt-get install -y $PKGS
  else
    echo "No apt-get here. Install the equivalents of: $PKGS — then re-run with --skip-system." >&2
    exit 1
  fi
fi
for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is missing (install ffmpeg)." >&2; exit 1; }
done
command -v HandBrakeCLI >/dev/null 2>&1 || echo "Note: HandBrakeCLI not found — only needed for CPU (x265/x264) compression."
command -v ffplay >/dev/null 2>&1 || echo "Note: ffplay not found — the transcript player needs it (part of Ubuntu's ffmpeg package)."

# ---------------------------------------------------------------- Python environment
if [ -x "$VENV/bin/python" ]; then
  echo "==> Using the existing environment $VENV"
else
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "Python not found: $PYTHON_BIN" >&2; exit 1; }
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "Condense needs Python 3.10 or newer ($PYTHON_BIN is older)." >&2; exit 1; }
  echo "==> Creating the environment $VENV with $("$PYTHON_BIN" --version)"
  mkdir -p "$(dirname "$VENV")"
  "$PYTHON_BIN" -m venv "$VENV"
fi
PY="$VENV/bin/python"
"$PY" -c 'import tkinter' 2>/dev/null || {
  echo "This Python has no Tkinter (the GUI toolkit): sudo apt install python3-tk" >&2
  echo "(If the environment was made from another Python, create a new one with --python /usr/bin/python3.)" >&2
  exit 1; }
"$PY" -m pip install --quiet --upgrade pip

# ---------------------------------------------------------------- PyTorch
if [ "$REINSTALL_TORCH" = "0" ] && "$PY" -c 'import torch' 2>/dev/null; then
  echo "==> Keeping the PyTorch already in this environment:"
else
  TORCH_PKGS="torch"
  if [ "$WITH_PYANNOTE" = "1" ]; then TORCH_PKGS="torch torchaudio"; fi
  echo "==> Installing PyTorch ($GPU) from $TORCH_INDEX — this is a large download"
  # shellcheck disable=SC2086
  "$PY" -m pip install --upgrade $TORCH_PKGS --index-url "$TORCH_INDEX"
fi
"$PY" - <<'EOF'
import torch
api = "ROCm " + torch.version.hip if getattr(torch.version, "hip", None) else ("CUDA " + torch.version.cuda if torch.version.cuda else "CPU build")
gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no GPU visible"
print(f"    torch {torch.__version__} · {api} · {gpu}")
EOF

# Pin the installed torch so the packages below cannot replace it with a different build from PyPI.
CONSTRAINTS="$(mktemp)"
trap 'rm -f "$CONSTRAINTS"' EXIT
"$PY" - "$CONSTRAINTS" <<'EOF'
import sys
from importlib.metadata import PackageNotFoundError, version
with open(sys.argv[1], "w") as fh:
    for dist in ("torch", "torchaudio", "torchvision"):
        try:
            fh.write(f"{dist}=={version(dist)}\n")
        except PackageNotFoundError:
            pass
EOF

# ---------------------------------------------------------------- Condense
EXTRAS="linux"
if [ "$WITH_PYANNOTE" = "1" ]; then EXTRAS="linux,pyannote"; fi
echo "==> Installing Condense [$EXTRAS] into $VENV"
"$PY" -m pip install --upgrade "$SRC_DIR[$EXTRAS]" -c "$CONSTRAINTS"

# ---------------------------------------------------------------- check
echo "==> Checking"
STATUS=0
"$PY" - <<'EOF' || STATUS=$?
import importlib.util, sys, torch
ok = importlib.util.find_spec("whisper") is not None
print(f"    openai-whisper: {'installed' if ok else 'MISSING'}")
if torch.cuda.is_available():
    api = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
    name = torch.cuda.get_device_name(0)
    try:  # visible is not enough: compute something on it
        x = torch.randn(1024, 1024, device="cuda")
        float((x @ x).sum().item()); torch.cuda.synchronize()
    except Exception as exc:
        print(f"    PyTorch sees the GPU ({api}: {name}) but a test calculation failed: {str(exc).splitlines()[0]}")
        sys.exit(4)
    print(f"    GPU test passed: Whisper and speaker detection will run on the GPU ({api}: {name}).")
else:
    print("    PyTorch sees no GPU: Whisper will run on the CPU (much slower).")
    sys.exit(3)
EOF
if [ "$STATUS" = "4" ]; then
  echo
  echo "  The GPU is visible but unusable with this PyTorch build. Usually the card is not supported by this ROCm"
  echo "  version: try a newer build (--torch-index URL --reinstall-torch) or, for RX 7000 / RX 6000 cards,"
  echo "  export HSA_OVERRIDE_GFX_VERSION=11.0.0 / 10.3.0 (docs/install-linux.md, Troubleshooting)."
fi
if [ "$STATUS" = "3" ] && [ "$GPU" != "cpu" ]; then
  echo
  echo "  The $GPU build of PyTorch is installed but sees no GPU. Common causes (docs/install-linux.md, Troubleshooting):"
  if [ "$GPU" = "rocm" ]; then
    echo "   - your user is not in the 'render' and 'video' groups:  sudo usermod -aG render,video \$USER  (then log out and in)"
    echo "   - the amdgpu driver / ROCm is not installed or the card is not supported by this ROCm version"
    echo "   - a card ROCm does not list may need e.g.  export HSA_OVERRIDE_GFX_VERSION=11.0.0  (RX 7000) / 10.3.0 (RX 6000)"
  else
    echo "   - the NVIDIA driver is not installed (nvidia-smi should list your GPU), or is older than this CUDA build needs"
  fi
fi
if command -v vainfo >/dev/null 2>&1 && vainfo 2>/dev/null | grep -q "VAEntrypointEncSlice"; then
  echo "    VA-API video encoding: available"
else
  echo "    VA-API video encoding: not available (Condense compresses on the CPU with HandBrake instead)"
fi
if ! command -v secret-tool >/dev/null 2>&1; then
  echo "    Note: secret-tool missing — install libsecret-tools to remember the Hugging Face token."
fi

# ---------------------------------------------------------------- app menu
if [ "$NO_APP" = "0" ]; then
  echo "==> Adding Condense to the app menu"
  "$VENV/bin/condense" --install-app
fi

echo
echo "Done. Start Condense from your app menu, or run: $VENV/bin/condense"
if [ "$VENV" != "$HOME/.condense/env" ]; then
  echo "(Condense is installed in $VENV; the app-menu entry starts it from there.)"
fi
