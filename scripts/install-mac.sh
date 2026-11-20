#!/usr/bin/env bash
# Install Condense on an Apple Silicon Mac.
#   ./scripts/install-mac.sh [--with-pyannote]
# Installs ffmpeg, HandBrake and Python (Homebrew), creates ~/.condense/env with MLX Whisper + PyTorch,
# and puts Condense.app into ~/Applications. Safe to re-run to update.
set -euo pipefail

WITH_PYANNOTE=0
for arg in "$@"; do
  case "$arg" in
    --with-pyannote) WITH_PYANNOTE=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer is for macOS." >&2; exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "Warning: this is not an Apple Silicon Mac; Whisper will run on the CPU (slow)." >&2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2; exit 1
fi

echo "==> Installing ffmpeg, HandBrakeCLI and Python 3.12 (Homebrew)"
brew install ffmpeg handbrake python@3.12 python-tk@3.12

PY="$(brew --prefix)/bin/python3.12"
ENV_DIR="${CONDENSE_ENV:-$HOME/.condense/env}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Creating the Python environment in $ENV_DIR"
"$PY" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --upgrade pip

EXTRAS="mac"
if [ "$WITH_PYANNOTE" = "1" ]; then EXTRAS="mac,pyannote"; fi
echo "==> Installing Condense [$EXTRAS] (MLX Whisper, PyTorch — this can take a few minutes)"
"$ENV_DIR/bin/python" -m pip install --upgrade "$SRC_DIR[$EXTRAS]"

echo "==> Creating Condense.app in ~/Applications"
"$ENV_DIR/bin/condense" --install-app

echo
echo "Done. Open Condense from Launchpad or Spotlight, or run: $ENV_DIR/bin/condense"
