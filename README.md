# Condense

Local video, audio and transcript processing for qualitative research. Everything runs on your own computer: no recordings or transcripts are uploaded anywhere.

- **Tasks:** merge clips, compress video with the hardware encoder, extract audio, transcribe with Whisper. Each task keeps its own settings.
- **Transcript editor:** play/pause at any speed, follow-along highlighting, video preview, correct segments while listening, split/merge/insert segments, speakers with one-key assignment, transcription markers (`[laughs]`, `[inaudible 0:12:31]`, …), "checked" progress, a timeline, undo/redo and automatic backups.
- **Accuracy tools:** vocabulary per task, re-transcribe a passage (bigger model, forced language or translation to English), re-run speaker detection while keeping the speakers you assigned by hand.
- **Exports:** paragraphs per speaker turn (RTF/TXT, for Word or MAXQDA), SRT/VTT subtitles, plain text, and an RTF with colour-coded word confidence.
- **Traceability:** each transcript has a `<name>.transcript.json` master file with word timings, speakers, the settings, tool versions, SHA-256 checksums of the inputs and a log of every edit. All exports are regenerated from it.

## Platform status

| Platform | Whisper | Video encoding | Status |
|---|---|---|---|
| macOS, Apple Silicon | MLX (GPU) | VideoToolbox | ✅ in use |
| Windows, NVIDIA | CUDA | NVENC | 🚧 planned |
| Linux, AMD | ROCm | VAAPI / VCN | 🚧 planned |

## Install (macOS, Apple Silicon)

You need [Homebrew](https://brew.sh). Then, from a copy of this repository:

```bash
./scripts/install-mac.sh            # add --with-pyannote for neural speaker detection
```

This installs ffmpeg, HandBrake and Python via Homebrew, creates an environment in `~/.condense/env` with MLX Whisper and PyTorch, and puts **Condense.app** in `~/Applications`. The first transcription downloads the Whisper model (about 1.6 GB for large-v3-turbo).

Already have a Python environment with `mlx-whisper` (for example `~/mlx_whisper_env`)? Install into it instead:

```bash
~/mlx_whisper_env/bin/pip install -e .
~/mlx_whisper_env/bin/condense --install-app
```

Run it with `condense` (or `python -m condense`).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

The tests need `ffmpeg` but no GPU, no display and no real recordings: they generate tiny synthetic videos and use stand-ins for Tk, Whisper, HandBrake and the Keychain (`tests/stub_tk2.py`, `tests/fakes/`). Each file in `tests/scenarios/` is an end-to-end scenario (task queue, editor, player, speakers, undo/backups, re-transcription, …).

Code layout:

```
src/condense/
  core/       transcript model (transcript.py), exports, helpers   — no GUI, no GPU
  media/      LosslessCut cut lists
  asr/        the script run in the Whisper environment (transcription + speaker detection)
  pipeline/   the background worker that runs the task queue
  platform/   OS integration (macOS now; Windows and Linux next)
  gui/        Tk app: queue, settings, editor, player/timeline, video, re-runs
```

## Research data

Never commit recordings or transcripts. `.gitignore` excludes media files, `*.transcript.json` and `_backups/`. Hugging Face tokens are stored in the macOS Keychain, not in files.

## License

MIT. Condense calls ffmpeg and HandBrake as separate programs and does not bundle them.
