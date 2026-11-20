#!/usr/bin/env bash
# Install (or update) Condense on an Apple Silicon Mac.
#
#   ./scripts/install-mac.sh                    # everything below
#   ./scripts/install-mac.sh --with-pyannote    # + neural speaker detection (needs a Hugging Face token)
#
# Installs ffmpeg, HandBrake and Python 3.12 with Homebrew, creates ~/.condense/env with MLX Whisper and
# PyTorch, and puts Condense.app into ~/Applications. Safe to re-run: that is also how you update.
#
# Options:
#   --with-pyannote   also install pyannote.audio
#   --no-app          do not create ~/Applications/Condense.app
# Environment: CONDENSE_ENV=/path/to/env to use another environment folder.
set -euo pipefail

WITH_PYANNOTE=0
NO_APP=0
for arg in "$@"; do
  case "$arg" in
    --with-pyannote) WITH_PYANNOTE=1 ;;
    --no-app) NO_APP=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer is for macOS. On Linux use ./scripts/install-linux.sh" >&2; exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "Warning: this is not an Apple Silicon Mac; Whisper will run on the CPU (slow)." >&2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh (then open a new Terminal window and run this again)" >&2; exit 1
fi

echo "==> Installing ffmpeg, HandBrakeCLI and Python 3.12 (Homebrew)"
brew install ffmpeg handbrake python@3.12 python-tk@3.12

PY="$(brew --prefix)/bin/python3.12"
ENV_DIR="${CONDENSE_ENV:-$HOME/.condense/env}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Preparing the Python environment in $ENV_DIR"
"$PY" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --quiet --upgrade pip

EXTRAS="mac"
if [ "$WITH_PYANNOTE" = "1" ]; then EXTRAS="mac,pyannote"; fi
echo "==> Installing Condense [$EXTRAS] (MLX Whisper, PyTorch — this can take a few minutes)"
"$ENV_DIR/bin/python" -m pip install --upgrade "$SRC_DIR[$EXTRAS]"

echo "==> Checking"
"$ENV_DIR/bin/python" - <<'EOF'
import importlib.util, platform
mlx = importlib.util.find_spec("mlx_whisper") is not None
try:
    import torch
    mps = bool(torch.backends.mps.is_available())
except Exception:
    mps = False
print(f"    MLX Whisper (GPU transcription): {'ready' if mlx else 'MISSING'}")
print(f"    PyTorch Metal (speaker detection): {'ready' if mps else 'not available (CPU)'}")
if platform.machine() != "arm64":
    print("    This Python is not arm64 (Rosetta?): MLX and Metal need an arm64 Python.")
EOF

if [ "$NO_APP" = "0" ]; then
  echo "==> Creating Condense.app in ~/Applications"
  "$ENV_DIR/bin/condense" --install-app
fi

echo
echo "Done. Open Condense from Launchpad or Spotlight, or run: $ENV_DIR/bin/condense"
