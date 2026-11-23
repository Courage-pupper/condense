# Installing Condense on Windows

> **Preview.** Windows support is new in Condense 0.11.0. Whisper and speaker detection run on the GPU through
> PyTorch — CUDA on NVIDIA, AMD's ROCm build for Windows on supported Radeon cards — and video is compressed on the
> GPU (NVENC, AMF or Quick Sync). It is covered by automated tests on Windows but has seen little real-world use yet —
> please [report problems](https://github.com/s-hurtado26/condense/issues).

Nothing is uploaded: recordings and transcripts stay on your computer.

## Quick install

1. Update your graphics driver (see [step 1](#1-update-the-graphics-driver)).
2. Open **PowerShell**: Start menu → type `PowerShell` → open *Windows PowerShell* (no administrator needed).
3. Paste this line and press Enter:

   ```powershell
   irm https://raw.githubusercontent.com/s-hurtado26/condense/main/install.ps1 | iex
   ```

It installs Python 3.12, FFmpeg and HandBrake with **winget**, detects your graphics card, installs the matching
PyTorch, and puts **Condense** in the Start menu and on the Desktop. Windows may ask once whether the Python installer
may make changes — allow it. The whole installation takes 10–30 minutes, mostly downloads.

With options (they are remembered for updates):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/s-hurtado26/condense/main/install.ps1))) -Gpu amd -WithPyannote
```

Updates: Condense offers new versions itself (see [Updating](#updating)).

## What you need

| | |
|---|---|
| Windows | Windows 10 or 11, 64-bit. **AMD GPU transcription needs Windows 11.** |
| GPU (recommended) | **NVIDIA**: a GeForce or RTX card with a current driver (very old cards may not be supported by current PyTorch). **AMD**: a Radeon supported by AMD's PyTorch for Windows — currently the **RX 7900 series and RX 9000 series** (plus Radeon PRO W7900 and some Ryzen AI processors) — with AMD Software (Adrenalin) **26.2.2 or newer**. Other GPUs: transcription runs on the CPU (slower), video compression can still use the GPU. |
| winget | Built into Windows 11 and current Windows 10 ("App Installer" in the Microsoft Store). |
| Disk space | about 8–10 GB: PyTorch with CUDA or ROCm is large, plus ~1.5 GB for the default Whisper model. |
| Internet | for the installation and the first transcription (model download). After that Condense works offline. |

What runs where:

| Step | NVIDIA | AMD RX 7900 / 9000 | Other AMD, Intel, no GPU |
|---|---|---|---|
| Transcription (openai-whisper) | GPU (CUDA) | GPU (ROCm) | CPU |
| Speaker detection | GPU (CUDA) | GPU (ROCm) | CPU |
| Video compression | GPU (NVENC) | GPU (AMF) | GPU (AMF / Quick Sync) where available, else CPU |

## 1. Update the graphics driver

- **NVIDIA:** install the latest driver with the *NVIDIA App* or from [nvidia.com/drivers](https://www.nvidia.com/drivers).
- **AMD:** install the latest *AMD Software: Adrenalin Edition* from [amd.com/support](https://www.amd.com/en/support)
  (26.2.2 or newer for GPU transcription), then restart.
- **Intel or no separate GPU:** Windows Update is enough.

## 2. Run the installer

Use the [quick install](#quick-install) line. From a downloaded release folder you can also run the Windows installer
directly: `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1`.

The installer

1. installs **Python 3.12**, **FFmpeg** (with ffplay) and **HandBrake CLI** with winget,
2. detects the graphics card (NVIDIA → CUDA, AMD → ROCm, otherwise CPU),
3. creates a private Python environment in `%USERPROFILE%\.condense\env` and installs the matching **PyTorch** into it —
   from [pytorch.org](https://pytorch.org/get-started/locally/) for NVIDIA (CUDA 12.8) or from
   [AMD](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html)
   for Radeon (ROCm 7.2.1, PyTorch 2.9.1),
4. installs Condense with openai-whisper, keeping that PyTorch fixed so nothing replaces it,
5. checks what PyTorch sees and which GPU video encoders work, and
6. creates **Start-menu and Desktop shortcuts**.

Options:

| Option | What it does |
|---|---|
| `-Gpu nvidia` / `-Gpu amd` / `-Gpu cpu` | choose the PyTorch build instead of detecting it |
| `-Venv PATH` | use (or create) another environment; a PyTorch already in it is kept — see below |
| `-TorchIndex URL` | another PyTorch index for NVIDIA/CPU (e.g. `https://download.pytorch.org/whl/cu126` for older drivers) |
| `-AmdWheels URL,URL,…` | newer AMD wheels: the URLs from AMD's install guide, all in one list |
| `-ReinstallTorch` | replace the PyTorch already in the environment |
| `-WithPyannote` | also install pyannote.audio (neural speaker detection) |
| `-NoApp` | no Start-menu / Desktop shortcuts |

### Reusing a PyTorch environment that already works

If you already have a Python 3.12 environment where PyTorch uses your GPU, install Condense into it — its PyTorch stays
as it is:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/s-hurtado26/condense/main/install.ps1))) -Venv C:\path\to\env
```

To keep Condense separate but run Whisper from that environment, set a user environment variable
`CONDENSE_WHISPER_PYTHON` to `C:\path\to\env\Scripts\python.exe` (Settings → System → About → Advanced system
settings → Environment Variables).

## 3. First start

Start **Condense** from the Start menu or the Desktop (right-click → *Pin to taskbar* to keep it there).

- The header shows the GPU, `CUDA ✓` or `ROCm ✓`, and the video encoder (`NVENC ✓`, `AMF ✓` or `Quick Sync ✓`). A warning
  there means PyTorch does not see the GPU — see [Troubleshooting](#troubleshooting).
- **Help ▸ Test GPU…** checks, before you transcribe anything, whether Whisper can use the GPU (a short calculation on
  it, with the GPU, its memory, the PyTorch / CUDA or ROCm version and the result). The same test runs at every start,
  and **Run** asks first if a transcription would fall back to the CPU.
- **File ▸ Setup & Status** lists the tools Condense found, the Python that runs Whisper and where settings live.
- Settings defaults on Windows: Whisper backend **PyTorch GPU**, video encoder **auto_h265**, which tests NVENC, AMF and
  Quick Sync and uses the first that works on this PC (otherwise the CPU).
- The **first transcription downloads the Whisper model** (about 1.5 GB for `large-v3-turbo`).
- Shortcuts use **Ctrl** (Ctrl+O add videos, Ctrl+R run, Ctrl+/ list of all shortcuts).

## Optional: neural speaker detection (pyannote)

1. Install with `-WithPyannote` (see the option line above).
2. Create a free account at <https://huggingface.co> and accept the conditions on
   [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and
   [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
3. Create a **Read** token at <https://huggingface.co/settings/tokens>.
4. In Condense, Settings ▸ Speakers & Exports: choose *Pyannote.audio 3.1*, paste the token into **HF token**, keep
   **Remember** ticked. It is stored in the **Windows Credential Manager** (entry `Condense/huggingface-token`), never
   in a settings file or transcript.

## Updating

Condense checks once a day and shows *Update available* with what is new; click **Update now**, then **Restart**.
Check any time with Help ▸ Check for Updates…. From PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\.condense\install.ps1" -Update
```

Updates reuse your options, keep your PyTorch, skip winget and need no administrator rights. Settings, the task queue
and transcripts are kept.

## Where things are

| What | Where |
|---|---|
| Condense and its Python packages | `%USERPROFILE%\.condense\env` (or the `-Venv` you chose) |
| The updater, downloaded releases, your install options | `%USERPROFILE%\.condense\install.ps1`, `…\releases\`, `…\install-options.json` |
| Settings and the task queue | `%APPDATA%\Condense\` |
| Log of Start-menu launches, the icon | `%LOCALAPPDATA%\Condense\Logs\condense.log`, `%LOCALAPPDATA%\Condense\condense.ico` |
| Shortcuts | Start menu (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Condense.lnk`) and Desktop |
| Hugging Face token | Windows Credential Manager, `Condense/huggingface-token` |
| Whisper models | `%USERPROFILE%\.cache\whisper\` (pyannote: `%USERPROFILE%\.cache\huggingface\`) |
| Your results | next to each video in a subfolder (or the output folder in Settings, default `Videos\Condense`); edit backups in `_backups\` there |

## Uninstalling

In PowerShell:

```powershell
Remove-Item -Recurse -Force "$HOME\.condense", "$env:APPDATA\Condense", "$env:LOCALAPPDATA\Condense"
Remove-Item -Force "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Condense.lnk", "$([Environment]::GetFolderPath('Desktop'))\Condense.lnk"
cmdkey /delete:Condense/huggingface-token        # the saved token, if any
```

Python, FFmpeg and HandBrake stay installed (Settings → Apps removes them, or `winget uninstall Gyan.FFmpeg`, …).
Downloaded models are in `%USERPROFILE%\.cache`; other programs may share them. Your transcripts and recordings are
never touched.

## Troubleshooting

**The header says PyTorch sees no GPU, or the GPU test fails.** Help ▸ Test GPU… shows the error. Check what PyTorch sees:

```powershell
& "$HOME\.condense\env\Scripts\python.exe" -c "import torch; print(torch.__version__, torch.version.hip or torch.version.cuda, torch.cuda.is_available())"
```

- `+cpu` in the version or `None` as second value: a CPU build is installed. Re-run the installer with `-Gpu nvidia -ReinstallTorch`
  (or `-Gpu amd -ReinstallTorch`).
- NVIDIA, `False`: update the driver and restart. With an older driver use `-TorchIndex https://download.pytorch.org/whl/cu126 -ReinstallTorch`.
- AMD, `False`: AMD's PyTorch for Windows needs Windows 11, a supported card (RX 7900 / RX 9000 series) and AMD
  Software 26.2.2 or newer. Other Radeon cards transcribe on the CPU; video compression still uses AMF.

**"winget is not recognized".** Install *App Installer* from the Microsoft Store (<https://aka.ms/getwinget>), then open a
new PowerShell window.

**"ffmpeg was not found" right after installing.** Windows only updates the PATH for new windows: close PowerShell,
open a new one and run the install line again (it skips what is already there).

**"Running scripts is disabled on this system".** Use the `irm … | iex` line above, or add `-ExecutionPolicy Bypass`
when running a `.ps1` file; nothing needs to be changed permanently.

**Compression says no GPU video encoder works.** Update the graphics driver. With several GPUs, choose the encoder
yourself in Settings ▸ Video (`nvenc_h265`, `amf_h265` or `qsv_h265`).

**No sound in the transcript player.** The player uses `ffplay` from the FFmpeg package; check with `Get-Command ffplay`.

**Condense does not start from the Start menu.** Look at `%LOCALAPPDATA%\Condense\Logs\condense.log`, or start it in
PowerShell with `& "$HOME\.condense\env\Scripts\condense.exe"` to see the error.

Still stuck? Open an [issue](https://github.com/s-hurtado26/condense/issues) — describe the problem with invented
example text and never attach recordings or transcripts.
