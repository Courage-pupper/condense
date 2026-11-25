# Installing Condense on Linux

> **Linux with an AMD GPU is a main platform**, used for real studies alongside the Mac. Whisper and speaker
> detection run on the GPU through PyTorch (ROCm), and video is compressed with VA-API. **NVIDIA** (CUDA) and
> **CPU-only** setups are still a preview: covered by the automated tests, but with less real-world use — please
> [report problems](https://github.com/s-hurtado26/condense/issues).

Nothing is uploaded: recordings and transcripts stay on your computer. The instructions use **Ubuntu 24.04**; other
distributions work if you install the same packages with their package manager.

## Quick install

Prepare the GPU driver first if you have an AMD or NVIDIA card ([step 1](#1-prepare-the-gpu-driver)), then paste
this into a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/s-hurtado26/condense/main/install.sh | bash
```

It asks for your password once (for `apt`), installs everything described in steps 2–3 below with the PyTorch build for
your GPU, and adds **Condense** to your app menu. Add options after `-s --`, for example:

```bash
curl -fsSL https://raw.githubusercontent.com/s-hurtado26/condense/main/install.sh | bash -s -- --gpu rocm --venv ~/rocm_whisper_env   # reuse a PyTorch environment that already works
curl -fsSL https://raw.githubusercontent.com/s-hurtado26/condense/main/install.sh | bash -s -- --with-pyannote                         # + neural speaker detection
```

Options you give are remembered for updates. Updates: Condense offers new versions itself (see [Updating](#updating)).

## What you need

| | |
|---|---|
| Linux | Ubuntu 24.04 or similar (Debian 12+, Linux Mint 22, Pop!_OS …) with a desktop (X11 or Wayland). |
| Python | 3.10 or newer with Tk (Ubuntu 24.04 ships 3.12). |
| GPU (recommended) | **AMD**: a Radeon card supported by ROCm — the RX 7900 series is; other RX 7000 and RX 6000 cards often work, sometimes with an override (see [Troubleshooting](#troubleshooting) and AMD's compatibility list). **NVIDIA**: a GeForce/RTX card with the NVIDIA driver. **No GPU**: works, but transcription is much slower. |
| Disk space | about 8–10 GB: PyTorch with ROCm or CUDA is large (4–7 GB), plus ~1.5 GB for the default Whisper model. |
| Internet | for the installation and the first transcription (model download). After that Condense works offline. |

What runs where on Linux:

| Step | AMD | NVIDIA | No GPU |
|---|---|---|---|
| Transcription (openai-whisper) | GPU (ROCm) | GPU (CUDA) | CPU |
| Speaker detection | GPU (ROCm) | GPU (CUDA) | CPU |
| Video compression | GPU (VA-API) | CPU (HandBrake x265; NVENC is planned) | CPU |

## 1. Prepare the GPU driver

**AMD.** Ubuntu's kernel already contains the `amdgpu` driver, and PyTorch's ROCm packages bring their own ROCm
libraries, so on supported cards no separate ROCm installation is usually needed. Your user must be allowed to use the
GPU:

```bash
sudo usermod -aG render,video $USER      # then log out and back in
```

If PyTorch later does not see the GPU, install AMD's driver and ROCm as described in
[AMD's ROCm installation guide](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/) and check the
[compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html) for your card.

**NVIDIA.** Install the proprietary driver and reboot:

```bash
sudo ubuntu-drivers install
nvidia-smi        # should list your GPU after the reboot
```

**No GPU.** Nothing to do.

## 2. Get Condense

**Option A — download a release.** On the [Releases page](https://github.com/s-hurtado26/condense/releases),
download **Source code (tar.gz)** of the latest release, unpack it somewhere permanent and open a terminal there:

```bash
tar xzf condense-0.10.0.tar.gz -C ~ && cd ~/condense-0.10.0
```

**Option B — clone with git** (makes updating a one-liner):

```bash
sudo apt install git
git clone https://github.com/s-hurtado26/condense.git ~/condense
cd ~/condense
```

## 3. Run the installer

```bash
./scripts/install-linux.sh
```

The installer asks for your password once (for `apt`) and then

1. installs **ffmpeg** (with ffplay), **HandBrakeCLI**, Python's **venv** and **Tk**, **libsecret-tools** (keyring
   access), **zenity** (the desktop's file chooser) and **vainfo** — plus **mesa-va-drivers** on AMD,
2. detects the GPU type (AMD → ROCm, NVIDIA → CUDA, otherwise CPU),
3. creates a private Python environment in `~/.condense/env` and installs the matching **PyTorch** build into it,
4. installs Condense with openai-whisper, keeping that PyTorch build fixed so nothing replaces it with another one,
5. checks what PyTorch sees and whether VA-API encoding is available, and
6. adds **Condense** to your app menu.

Options:

| Option | What it does |
|---|---|
| `--gpu rocm` / `--gpu cuda` / `--gpu cpu` | choose the PyTorch build instead of auto-detecting |
| `--venv PATH` | use (or create) another environment — see [Reusing a PyTorch environment](#reusing-a-pytorch-environment-that-already-works) |
| `--torch-index URL` | install PyTorch from another package index (e.g. a newer ROCm or CUDA version from [pytorch.org](https://pytorch.org/get-started/locally/)) |
| `--reinstall-torch` | replace the PyTorch already in the environment |
| `--python /usr/bin/python3.12` | Python used to create a new environment |
| `--with-pyannote` | also install pyannote.audio (neural speaker detection) |
| `--skip-system` | do not run `apt` (for other distributions — install the packages listed in point 1 above yourself) |
| `--no-app` | do not add an app-menu entry |

By default PyTorch comes from `https://download.pytorch.org/whl/rocm6.4` (AMD), `…/whl/cu128` (NVIDIA) or
`…/whl/cpu`. These are deliberately conservative versions that are known to exist. PyTorch's GPU packages include their
own ROCm/CUDA runtime, so they only need a recent enough driver. Newer cards may need a newer build: pick it on
[pytorch.org](https://pytorch.org/get-started/locally/) (Linux · Pip · ROCm or CUDA) and pass its `--index-url` value as
`--torch-index`, or use the wheels from [AMD's PyTorch guide](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/frameworks/pytorch/install.html)
in your own environment and reuse it as described below.

### Reusing a PyTorch environment that already works

If you already have a Python environment where PyTorch uses your GPU (for example `~/rocm_whisper_env`), install
Condense into it. The installer keeps that PyTorch exactly as it is:

```bash
./scripts/install-linux.sh --venv ~/rocm_whisper_env
```

The app-menu entry then starts Condense from that environment. Two things the environment needs: a Python with Tk
(`python -c "import tkinter"`), and a PyTorch where `torch.cuda.is_available()` is `True` (ROCm builds of PyTorch use the
same `torch.cuda` interface as CUDA).

To keep Condense in its own environment but run Whisper from another one, set `CONDENSE_WHISPER_PYTHON` before
starting Condense, e.g. in `~/.profile`:

```bash
export CONDENSE_WHISPER_PYTHON=~/rocm_whisper_env/bin/python
```

## 4. First start

Start **Condense** from your app menu (pin it to the dock from there), or run `~/.condense/env/bin/condense`.

- The header shows the GPU and `ROCm ✓` (or `CUDA ✓`) and `VA-API ✓`. A warning there means PyTorch does not see the GPU
  — see [Troubleshooting](#troubleshooting).
- **Help ▸ Test GPU…** checks, before you transcribe anything, whether Whisper can use the GPU: it runs a short
  calculation on it and shows the GPU, its memory, the PyTorch / ROCm or CUDA version and the result. Condense runs the
  same test at every start (the result is in the header), and **Run** asks first if a transcription would fall back to
  the CPU.
- **File ▸ Setup & Status** lists the tools Condense found, the Python that runs Whisper, and where settings live.
- Settings defaults on Linux: Whisper backend **PyTorch GPU (ROCm / CUDA)**, video encoder **vaapi_h265**. Without a
  VA-API encoder Condense compresses with HandBrake on the CPU instead and says so in the Console. On NVIDIA choose
  **x265 (CPU)**.
- The **first transcription downloads the Whisper model** (about 1.5 GB for `large-v3-turbo`). Later runs use the
  downloaded copy and work offline.
- Shortcuts use **Ctrl** where the Mac uses ⌘ (Ctrl+O add videos, Ctrl+R run, Ctrl+/ shortcut list).

A quick test: add a short video (Ctrl+O), check the task details, and press **Run** (Ctrl+R).

## Optional: neural speaker detection (pyannote)

The built-in speaker detection works offline without an account. pyannote is usually better for difficult
recordings; it is free but **gated**.

1. Install it: `./scripts/install-linux.sh --with-pyannote` (this also installs `torchaudio` from the same PyTorch
   index, so the builds match).
2. Create a free account at <https://huggingface.co> and accept the conditions on both
   [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
   [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
3. Create an access token of type **Read** at <https://huggingface.co/settings/tokens>.
4. In Condense, Settings ▸ Speakers & Exports: choose *Pyannote.audio 3.1*, paste the token into **HF token** and keep
   **Remember in keyring** ticked. The token is stored in your desktop keyring (GNOME Keyring or KWallet, through
   `secret-tool`), never in a settings file or transcript. Without a keyring (e.g. a server without a desktop) it is
   not saved and you paste it each time.

## Optional: LosslessCut

To cut parts out before transcription, edit the recording in [LosslessCut](https://github.com/mifi/lossless-cut)
(AppImage, Flatpak or Snap from its website) and save the project (`.llc`) next to the video. Condense applies the cuts
("Apply LosslessCut (.llc) trims if present" in Settings).

## Updating

Settings, the task queue and transcripts are kept.

- **Installed with the quick install line:** Condense checks once a day and shows *Update available* with what is
  new; click **Update now**, then **Restart**. Check any time with Help ▸ Check for Updates…, or from a terminal:
  `~/.condense/install.sh --update`. Updates reuse your options (`--venv`, `--gpu` …), keep your PyTorch and need no
  password.
- **Installed from a release folder:** unpack the new release and run `./scripts/install-linux.sh` in it (add the same
  `--venv` you used before, if any).
- **Installed with git:**

  ```bash
  cd ~/condense && git pull && ./scripts/install-linux.sh
  ```

Updating keeps the PyTorch already in the environment; add `--reinstall-torch` to change it.
`~/.condense/env/bin/condense --version` shows the installed version.

## Where things are

| What | Where |
|---|---|
| Condense and its Python packages | `~/.condense/env` (or the `--venv` you chose) |
| The updater, downloaded releases, the options you installed with | `~/.condense/install.sh`, `~/.condense/releases/`, `~/.condense/install-options` |
| App-menu entry | `~/.local/share/applications/condense.desktop`, launcher `~/.local/share/condense/launcher.sh`, icon in `~/.local/share/icons/hicolor/scalable/apps/` |
| Settings and the task queue | `~/.config/condense/` |
| Console log of app-menu launches | `~/.local/state/condense/condense.log` |
| Hugging Face token | desktop keyring, entry "Condense: Hugging Face token" |
| Whisper models | `~/.cache/whisper/` |
| Your results | next to each video in a subfolder (or the output folder set in Settings, default `~/Videos/Condense`); edit backups in `_backups/` there |

## Uninstalling

```bash
rm -rf ~/.condense ~/.config/condense ~/.local/state/condense ~/.local/share/condense
rm -f ~/.local/share/applications/condense.desktop ~/.local/share/icons/hicolor/scalable/apps/condense.svg
secret-tool clear service Condense account huggingface-token      # the saved token, if any
```

If you installed into your own environment with `--venv`, remove Condense from it with `pip uninstall condense`.
Downloaded models are in `~/.cache/whisper` (and `~/.cache/huggingface` for pyannote); other programs may share them.
System packages stay installed. Your transcripts and recordings are never touched.

## Troubleshooting

**The header says PyTorch sees no GPU, or the GPU test fails.** Help ▸ Test GPU… shows the error. "Sees the GPU but a
test calculation failed" (often `HIP error: invalid device function`) means the card is not supported by the installed
ROCm build — see the override below, or install a newer PyTorch build with `--torch-index … --reinstall-torch`.
Check what the Whisper Python sees (Setup & Status shows which Python that is):

```bash
~/.condense/env/bin/python -c "import torch; print(torch.__version__, torch.version.hip or torch.version.cuda, torch.cuda.is_available())"
```

- The version ends in `+cpu`, or the second value is `None`: a CPU build is installed. Re-run the installer with
  `--gpu rocm --reinstall-torch` (or `--gpu cuda`).
- AMD, `False`: make sure you are in the `render` and `video` groups (`groups` lists them; log out and in after
  `usermod`). `rocminfo` (from AMD's ROCm packages) should list your card as an agent.
- AMD card not on ROCm's support list: setting an override sometimes helps. For RX 7000 cards try
  `export HSA_OVERRIDE_GFX_VERSION=11.0.0`, for RX 6000 cards `10.3.0`, before starting Condense (or in `~/.profile`).
  Officially supported cards such as the RX 7900 XTX do not need it.
- NVIDIA, `False`: `nvidia-smi` must work. If the driver is old, either update it or install an older CUDA build with
  `--torch-index https://download.pytorch.org/whl/cu126 --reinstall-torch`.

**"VA-API: not available", or compression falls back to the CPU.** Run `vainfo`: it should list `VAEntrypointEncSlice`
lines. On AMD install `mesa-va-drivers`, on Intel `intel-media-va-driver`. With several GPUs, choose the render device
with `export CONDENSE_VAAPI_DEVICE=/dev/dri/renderD129`. AV1 encoding needs an RX 7000 or Intel Arc (or newer) GPU;
choose `vaapi_h265` otherwise.

**"Python Tkinter is not installed".** `sudo apt install python3-tk`. If you reuse your own environment, it must have
been created from a Python that has Tk.

**pip fails while building `numba` or `llvmlite`** (dependencies of openai-whisper). Your Python may be newer than
those packages support yet. Create the environment with Python 3.12: `--python /usr/bin/python3.12`.

**No sound in the transcript player.** The player uses `ffplay` (part of Ubuntu's `ffmpeg` package). Check
`which ffplay` and that sound works in other apps (PipeWire/PulseAudio).

**Condense does not appear in the app menu.** Log out and back in, or start it once with
`~/.condense/env/bin/condense --install-app`. Launch errors are in `~/.local/state/condense/condense.log`.

**The Console showed `/opt/amdgpu/share/libdrm/amdgpu.ids: No such file or directory`** (Condense 0.10). Harmless:
the libdrm inside ROCm builds of PyTorch looks for AMD's list of GPU product names where AMD's own driver packages put it;
Ubuntu keeps it in `/usr/share/libdrm/`. Only the card's display name is affected, not the computation. Condense 0.11
hides the message.

**The file dialog looks old (no sidebar, no external drives).** Condense uses the desktop's own file chooser through
`zenity` (GNOME and most desktops) or `kdialog` (KDE) and falls back to Tk's basic dialog when neither is installed:
`sudo apt install zenity` (or `kdialog` on KDE). To force one: `export CONDENSE_FILE_DIALOG=zenity` (or `kdialog`, `tk`).

**Text is tiny on a high-resolution screen.** Tk does not follow the desktop's scaling everywhere. Use View ▸ Larger
Text (Ctrl++) — the setting is remembered.

Still stuck? Open an [issue](https://github.com/s-hurtado26/condense/issues) — describe the problem with invented
example text and never attach recordings or transcripts.
