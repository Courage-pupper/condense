# Condense

[![tests](https://github.com/Courage-pupper/condense/actions/workflows/ci.yml/badge.svg)](https://github.com/Courage-pupper/condense/actions/workflows/ci.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Local video, audio and transcript processing for qualitative research. Everything runs on your own computer: no recordings or transcripts are uploaded anywhere.

- **Tasks:** merge clips, compress video with the hardware encoder, extract audio, transcribe with Whisper. Each task keeps its own settings.
- **Transcript editor:** play/pause at any speed, follow-along highlighting, video preview, correct segments while listening, split/merge/insert segments, speakers assigned with the number keys — to one segment or to a whole selected stretch at once (drag, ⇧-click or ⇧↓), transcription markers (`[laughs]`, `[inaudible 0:12:31]`, …), "checked" progress, a timeline, undo/redo and automatic backups.
- **Keyboard-driven review:** ↑/↓ through segments, 1–9 speakers, R replays, C marks checked, ⌘↩ saves and moves on; View ▸ Keyboard Shortcuts (⌘/, Ctrl+/ on Linux) lists them all.
- **Accuracy tools:** vocabulary per task, re-transcribe a passage (bigger model, forced language or translation to English), re-run speaker detection while keeping the speakers you assigned by hand.
- **Exports:** paragraphs per speaker turn (RTF/TXT, for Word or MAXQDA), SRT/VTT subtitles, plain text, and an RTF with colour-coded word confidence.
- **Traceability:** each transcript has a `<name>.transcript.json` master file with word timings, speakers, the settings, tool versions, SHA-256 checksums of the inputs and a log of every edit. All exports are regenerated from it.

## Platforms

| Platform | Transcription & speakers | Video compression | Status |
|---|---|---|---|
| macOS, Apple Silicon | MLX Whisper and PyTorch on the GPU (Metal) | Media Engine (VideoToolbox) | ✅ main platform |
| Linux, AMD GPU | openai-whisper and PyTorch on the GPU (ROCm) | GPU (VA-API) | 🧪 preview |
| Linux, NVIDIA GPU | openai-whisper and PyTorch on the GPU (CUDA) | CPU (HandBrake); NVENC planned | 🧪 preview |
| Linux, no GPU | CPU | CPU | 🧪 preview (slow) |
| Windows, NVIDIA GPU | openai-whisper and PyTorch on the GPU (CUDA) | GPU (NVENC) | 🧪 preview |
| Windows, AMD RX 7900 / RX 9000 | openai-whisper and PyTorch on the GPU (AMD's ROCm for Windows, Windows 11) | GPU (AMF) | 🧪 preview |
| Windows, other GPUs / no GPU | CPU | GPU (AMF / Quick Sync) where available, else CPU | 🧪 preview (slow) |

## Install

**Windows:** open **PowerShell** (Start menu → type "PowerShell"), paste this line and press Enter — then see the
[Windows guide](docs/install-windows.md):

```powershell
irm https://raw.githubusercontent.com/Courage-pupper/condense/main/install.ps1 | iex
```

**Mac and Linux:** open **Terminal** (Mac) or a terminal (Linux), paste this line and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/Courage-pupper/condense/main/install.sh | bash
```

It downloads the latest release and installs everything Condense needs — on a Mac also [Homebrew](https://brew.sh) if
it is missing (it asks for your password once). Condense then appears in Launchpad (Mac) or your app menu (Linux).

**Updating:** Condense checks once a day whether a new version is out and offers to install it with one click
(Help ▸ Check for Updates). Your settings, tasks and transcripts are kept.

Options go after `bash -s --`, e.g. neural speaker detection or a specific Linux GPU type:

```bash
curl -fsSL https://raw.githubusercontent.com/Courage-pupper/condense/main/install.sh | bash -s -- --with-pyannote
curl -fsSL https://raw.githubusercontent.com/Courage-pupper/condense/main/install.sh | bash -s -- --gpu rocm --venv ~/rocm_whisper_env    # Linux: reuse a PyTorch environment that already works
```

The guides cover requirements, GPU drivers, the Hugging Face token, manual installation, uninstalling and
troubleshooting:

- **[Installing on a Mac](docs/install-mac.md)**
- **[Installing on Linux](docs/install-linux.md)**
- **[Installing on Windows](docs/install-windows.md)**

The first transcription downloads the Whisper model (about 1.5 GB for `large-v3-turbo`); after that Condense works offline.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

The tests need `ffmpeg` but no GPU, no display and no real recordings: they generate tiny synthetic videos and use stand-ins for Tk, Whisper, PyTorch, HandBrake, the Keychain and the Linux keyring (`tests/stub_tk2.py`, `tests/fakes/`). `CONDENSE_PLATFORM=mac|linux|windows` selects which platform layer runs, so all three are tested on any machine; CI also runs on real macOS, Ubuntu and Windows. Each file in `tests/scenarios/` is an end-to-end scenario (task queue, editor, player, speakers, undo/backups, re-transcription, …).

Code layout:

```
src/condense/
  core/       transcript model (transcript.py), exports, helpers   — no GUI, no GPU
  media/      LosslessCut cut lists
  asr/        the script run in the Whisper environment (transcription + speaker detection)
  pipeline/   the background worker that runs the task queue
  platform/   OS integration: detect.py (what each platform offers), mac.py, linux.py, windows.py
  gui/        Tk app: queue, settings, editor, player/timeline, video, re-runs
```

## Responsible use

- **Automatic transcripts contain errors.** Whisper can mishear, skip or invent words, and speaker detection can mix people up. Check every passage you analyse or quote; Condense's "uncertain word" highlighting, checked marks and edit log are there to help, not to replace that check.
- **Translations are machine translations.** Passages translated with *Re-transcribe → Translate to English* are marked `[translated from …]` in every export; have them checked before quoting.
- **You are responsible for your data.** Condense keeps everything on your computer, but consent, ethics approval, pseudonymisation, storage and deletion (e.g. under the GDPR) remain your responsibility and your institution's rules apply. The vocabulary field and speaker names are saved in the `.transcript.json`; use pseudonyms there if files leave your computer.
- **Update check:** once a day Condense asks GitHub whether a new release exists. That request contains no information about you or your recordings (GitHub sees your IP address, as with any web request). Turn it off with Help ▸ Check for Updates Automatically.
- **pyannote models are gated:** to use neural speaker detection, accept the model conditions on Hugging Face with your own account and token.

Never commit recordings or transcripts: `.gitignore` excludes media files, `*.transcript.json` and `_backups/`, and the test suite uses only synthetic media and invented text. See [SECURITY.md](SECURITY.md).

## Citing

If Condense helps your research, please cite it — see [CITATION.cff](CITATION.cff) (GitHub shows a "Cite this repository" button).

## Licence

Condense is MIT-licensed ([LICENSE](LICENSE)). It uses, but does not bundle, Whisper, MLX, PyTorch, pyannote, ffmpeg and HandBrake; their licences and conditions are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Condense is not affiliated with OpenAI, Apple, or any of the other projects and companies named here.
