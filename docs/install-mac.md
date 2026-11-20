# Installing Condense on a Mac

Condense runs on Macs with Apple Silicon (M1 or newer). Transcription runs on the Mac's GPU with
[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), speaker detection on the GPU with PyTorch
(Metal), and video compression on the Media Engine (VideoToolbox). Nothing is uploaded: recordings and transcripts
stay on your Mac.

Setting up takes about 15 minutes, most of it downloads.

## Quick install

Open **Terminal** (Applications ▸ Utilities), paste this line and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/Courage-pupper/condense/main/install.sh | bash
```

That is all: it installs Homebrew if needed (it asks for your Mac password once — typing shows no dots, that is
normal), then everything described in steps 1–3 below, and puts **Condense** in Launchpad. Continue with
[First start](#4-first-start). For neural speaker detection add `-s -- --with-pyannote` at the end of the line.

Updates: Condense offers new versions itself (see [Updating](#updating)).

The rest of this page explains each step, for manual installation and when something goes wrong.

## What you need

| | |
|---|---|
| Mac | Apple Silicon (M1, M2, M3, M4, …). Intel Macs work, but only on the CPU and very slowly. |
| macOS | 14 (Sonoma) or newer — required by MLX. |
| Disk space | about 5 GB: ~3 GB for the Python environment, ~1.5 GB for the default Whisper model, more for larger models. |
| Memory | 8 GB works with the default model (`large-v3-turbo`); 16 GB or more is more comfortable for long recordings or `large-v3`. |
| Internet | for the installation and the first transcription (model download). After that Condense works offline. |

## 1. Install Homebrew

[Homebrew](https://brew.sh) installs the command-line tools Condense uses (ffmpeg, HandBrake, Python). Open
**Terminal** (Applications ▸ Utilities) and paste the command shown on <https://brew.sh>. At the end, Homebrew prints
"Next steps" with two or three commands that add `brew` to your shell; run them, then close and reopen Terminal.

Check it worked:

```bash
brew --version
```

## 2. Get Condense

**Option A — download a release.** On the [Releases page](https://github.com/Courage-pupper/condense/releases),
download **Source code (zip)** of the latest release and double-click it to unzip. Move the folder somewhere permanent,
for example your home folder, and open Terminal in it:

```bash
cd ~/condense-0.10.0        # the unzipped folder
```

**Option B — clone with git** (makes updating a one-liner):

```bash
xcode-select --install      # only if git is not installed yet
git clone https://github.com/Courage-pupper/condense.git ~/condense
cd ~/condense
```

## 3. Run the installer

```bash
./scripts/install-mac.sh
```

The installer

1. installs **ffmpeg**, **HandBrakeCLI**, **Python 3.12** and **Tk** with Homebrew,
2. creates a private Python environment in `~/.condense/env` (nothing is installed into the system Python),
3. installs Condense with MLX Whisper, openai-whisper (CPU fallback) and PyTorch into it,
4. checks that MLX and Metal work, and
5. creates **Condense.app** in `~/Applications` (plus a link on your Desktop).

Options:

| Option | What it does |
|---|---|
| `--with-pyannote` | also installs pyannote.audio for neural speaker detection (see [below](#optional-neural-speaker-detection-pyannote)) |
| `--no-app` | skips creating Condense.app |
| `CONDENSE_ENV=/path ./scripts/install-mac.sh` | uses another folder for the Python environment |

## 4. First start

Open **Condense** from Launchpad, Spotlight or `~/Applications` (drag it to the Dock to keep it there). You can also
start it from Terminal with `~/.condense/env/bin/condense`.

- The header shows your chip and `MLX ✓ · Metal ✓ · VideoToolbox ✓`; **Help ▸ Test GPU…** shows the details, and
  **Run** asks first if MLX is missing and a transcription would fall back to the CPU. **Setup & Status** (in the Condense menu)
  lists every tool Condense found and where your settings live.
- When you first open videos in your Desktop, Documents or Downloads folder, macOS asks whether Condense (or
  "python3.12") may access that folder — allow it.
- The **first transcription downloads the Whisper model** (about 1.5 GB for `large-v3-turbo`) from Hugging Face. Later
  runs use the downloaded copy and work offline.

A quick test: add a short video (⌘O), check the task details, and press **Run** (⌘R). The transcript opens in the
Transcript tab when it is done.

## Optional: neural speaker detection (pyannote)

Condense's built-in speaker detection works offline without an account. For difficult recordings (many speakers,
overlapping speech) the pyannote model is usually better. It is free but **gated**: you need a Hugging Face account
and must accept its conditions.

1. Install it: `./scripts/install-mac.sh --with-pyannote`
2. Create a free account at <https://huggingface.co>.
3. Open both model pages and accept the conditions (you share your name and affiliation with the model authors):
   [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
   [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
4. Create an access token of type **Read** at <https://huggingface.co/settings/tokens>.
5. In Condense, Settings ▸ Speakers & Exports: choose *Pyannote.audio 3.1*, paste the token into **HF token**, keep
   **Remember** ticked. The token is stored in your macOS Keychain, never in a settings file or transcript.

The model is downloaded on its first use; the audio itself is processed on your Mac.

## Optional: LosslessCut

To cut out the parts of a recording you do not want transcribed, edit it in
[LosslessCut](https://github.com/mifi/lossless-cut) and save the project (`.llc`) next to the video. Condense applies
those cuts automatically ("Apply LosslessCut (.llc) trims if present" in Settings).

```bash
brew install --cask losslesscut
```

## Updating

Your settings, task queue and transcripts are kept when you update.

- **Installed with the quick install line:** Condense checks once a day and shows *Update available* with what is
  new; click **Update now**, then **Restart**. You can also check any time: Condense ▸ Check for Updates… (or
  Help ▸ Check for Updates…). From Terminal: `~/.condense/install.sh --update`.
- **Installed from a release folder:** download the new release, unzip it, and run `./scripts/install-mac.sh` in the new folder.
- **Installed with git:**

  ```bash
  cd ~/condense && git pull && ./scripts/install-mac.sh
  ```

`~/.condense/env/bin/condense --version` shows the installed version.

## Using a Python environment you already have

If you already have an environment with `mlx-whisper` (for example `~/mlx_whisper_env`), install Condense into it
instead of running the installer:

```bash
~/mlx_whisper_env/bin/pip install ".[mac]"      # from the Condense folder
~/mlx_whisper_env/bin/condense --install-app
```

Condense.app then starts from that environment. To run Whisper from a *different* environment than Condense itself,
set `CONDENSE_WHISPER_PYTHON=/path/to/env/bin/python` before starting Condense.

## Where things are

| What | Where |
|---|---|
| Condense and its Python packages | `~/.condense/env` |
| The updater, downloaded releases, the options you installed with | `~/.condense/install.sh`, `~/.condense/releases/`, `~/.condense/install-options` |
| The app | `~/Applications/Condense.app` (and a link on the Desktop) |
| Settings and the task queue | `~/Library/Application Support/Condense/` |
| Console log of app launches | `~/Library/Logs/Condense.log` |
| Hugging Face token | macOS Keychain, item "Condense" |
| Whisper models | `~/.cache/huggingface/hub/` (MLX), `~/.cache/whisper/` (CPU fallback) |
| Your results | next to each video in a subfolder (or the output folder set in Settings); edit backups in `_backups/` there |

## Uninstalling

```bash
rm -rf ~/.condense ~/Applications/Condense.app ~/Desktop/Condense.app
rm -rf ~/Library/Application\ Support/Condense ~/Library/Logs/Condense.log
security delete-generic-password -s Condense -a huggingface-token   # the saved token, if any
```

Downloaded models are in `~/.cache/huggingface` and `~/.cache/whisper`; other programs may share them, so delete
those only if you know nothing else uses them. Homebrew tools stay installed (`brew uninstall ffmpeg handbrake` removes
them). Your transcripts and recordings are never touched.

## Troubleshooting

**Condense.app does not open, or closes immediately.** Look at `~/Library/Logs/Condense.log`, or start it from
Terminal (`~/.condense/env/bin/condense`) to see the error.

**"Python Tkinter is not installed".** Run `brew install python-tk@3.12` and re-run the installer. Condense needs
Homebrew's Python, not `/usr/bin/python3`.

**The header shows `MLX ✗`, or transcription is slow.** Check that Terminal is not running under Rosetta: `uname -m`
must print `arm64`. If it prints `x86_64`, untick "Open using Rosetta" in Terminal's Get Info window, then run the
installer again.

**"HandBrakeCLI is not installed".** `brew install handbrake`, or choose an `ffmpeg_…_videotoolbox` encoder in
Settings ▸ Video, which needs only ffmpeg.

**No sound when playing a transcript.** The player uses `ffplay`, which comes with Homebrew's ffmpeg. Check with
`which ffplay`; reinstall with `brew reinstall ffmpeg`.

**Everything is too small or too large.** View ▸ Larger / Smaller Text (⌘+ / ⌘− / ⌘0) scales the whole window.

Still stuck? Open an [issue](https://github.com/Courage-pupper/condense/issues) — describe the problem with invented
example text and never attach recordings or transcripts.
