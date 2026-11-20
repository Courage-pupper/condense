# Changelog

## 0.11.0 — 2026-11-20

### Windows (preview)
- New Windows platform layer (Windows 10/11, 64-bit). Whisper and speaker detection run on the GPU through PyTorch: CUDA on NVIDIA, AMD's ROCm build of PyTorch for Windows on the Radeon RX 7900 / RX 9000 series (Windows 11, AMD Software 26.2.2+), otherwise the CPU.
- Video compression on the GPU with ffmpeg: NVENC (NVIDIA), AMF (AMD) and Quick Sync (Intel). The default `auto_h265` tests which encoder really works on the PC (an encoder listed by ffmpeg is not necessarily usable) and falls back to the CPU.
- One-line install from PowerShell: `irm https://raw.githubusercontent.com/Courage-pupper/condense/main/install.ps1 | iex` — winget installs Python 3.12, FFmpeg and HandBrake CLI; options (`-Gpu`, `-Venv`, `-WithPyannote`, …) are remembered; in-app updates work as on macOS and Linux.
- Hugging Face token in the Windows Credential Manager, Start-menu and Desktop shortcuts with an icon, no console windows, sharp text on high-DPI screens, `%APPDATA%` / `%LOCALAPPDATA%` locations.

### Linux
- **Native file chooser:** opening videos, folders and transcripts now uses the desktop's own dialog — GNOME/GTK through `zenity`, KDE through `kdialog` — with external drives, bookmarks, recent places and search, instead of Tk's old Motif-style dialog. It starts in the last folder used (or the home folder if that drive is no longer connected) and also matches upper-case extensions such as `.MP4`. The installer adds `zenity`; `CONDENSE_FILE_DIALOG=tk|zenity|kdialog` chooses explicitly.

### All platforms
- **GPU check before transcribing:** at start-up Condense now runs a short calculation on the GPU instead of only asking whether one is visible (with ROCm a card can be visible yet unusable). The header shows the result with the GPU's memory; **Help ▸ Test GPU…** shows the details (PyTorch, ROCm/CUDA version, GPU architecture, test result, video encoder). If a task would transcribe on the CPU although a GPU backend is chosen, **Run** asks first. The Linux and Windows installers run the same test at the end.
- Without HandBrake, the x265/x264 encoders now use ffmpeg's own x265/x264 instead of failing.
- Whisper's output is read as UTF-8.

## 0.10.0 — 2026-11-20

### Installing and updating
- One-line installer for macOS and Linux: `curl -fsSL https://raw.githubusercontent.com/Courage-pupper/condense/main/install.sh | bash`. It downloads the latest release, installs Homebrew on a Mac if needed, runs the platform installer and remembers its options.
- Updates from inside the app: a daily check (Help ▸ Check for Updates Automatically, can be turned off) shows what is new; **Update now** installs it without a password and offers a restart. "Skip this version" is remembered. Also Help ▸ Check for Updates… and `~/.condense/install.sh --update`.
- Help menu: installation guide, release notes, report a problem, About.

### Linux (preview)
- New Linux platform layer. Whisper (openai-whisper) and speaker detection run on the GPU through PyTorch — ROCm on AMD, CUDA on NVIDIA — or on the CPU. The GPU and API used are recorded in each transcript's provenance.
- Video compression with VA-API on AMD and Intel GPUs (`vaapi_h265`, `vaapi_av1`, `vaapi_h264`); without a VA-API encoder Condense falls back to HandBrake on the CPU and says so.
- Hugging Face token in the desktop keyring (GNOME Keyring / KWallet via `secret-tool`), app-menu entry (`condense --install-app`), XDG locations (`~/.config/condense`, `~/.local/state/condense`), `xdg-open` for folders and the video player, X11 mouse-wheel scrolling, Linux fonts.
- `scripts/install-linux.sh`: apt packages, the matching PyTorch build (`--gpu rocm|cuda|cpu`, `--torch-index`), or reuse of an environment whose PyTorch already works (`--venv`); PyTorch is pinned so later packages cannot replace it.
- `CONDENSE_WHISPER_PYTHON` runs Whisper from another Python environment; `CONDENSE_VAAPI_DEVICE` picks the render node.

### Editor
- Speakers by number key: in the transcript, 1–9 assign speaker 1–9 and 0 adds a new one (⌃1–9 still work while typing). The speaker chips show their number.
- Select several segments — drag across them, ⇧-click, ⇧↑/⇧↓ or ⌘A — and assign a speaker (key, chip or the editor's speaker list) or mark them checked in one go. One undo step reverts the whole change.
- More transcript keys: ↑/↓ previous/next segment, R replays the open segment, C toggles checked, Return edits the segment, Tab in the segment box saves and returns to the transcript.
- View ▸ Keyboard Shortcuts (⌘/) lists every shortcut.
- Single-key shortcuts ignore ⌘/Ctrl/Alt, so ⌘C copies, ⌘1–3 switch tabs and ⌘R starts the queue from the transcript as before.

### Project
- Install guides for macOS and Linux (`docs/`), release workflow, install-script checks in CI.
- The Mac installer checks MLX and Metal at the end and has `--no-app`; Condense.app requires macOS 14 (as MLX does).
- Public-release housekeeping: third-party notices, citation file, security policy, contributing guide, issue/PR templates with privacy reminders, Dependabot for GitHub Actions.

## 0.9.0 — 2026-11-20

First version in this repository, restructured from the single-file Apple Silicon app into the `condense` package. Behaviour is unchanged; the previous settings, task queue and Keychain token (stored under "SiliconVideoPipeline") are picked up automatically.

- Task queue: one task per video or merge tasks, per-task steps and settings, remembered between launches.
- Hardware: MLX Whisper on the Apple GPU, PyTorch/Metal speaker detection, VideoToolbox encoding.
- Editor: paragraphs/segments views, play/pause/seek/speed, follow-along highlight, video preview, segment editing with timing kept, split/merge/insert/delete, speaker list with ⌃1–⌃9, markers, checked marks and progress, timeline, undo/redo, automatic backups, resume position.
- Accuracy: vocabulary per task, re-transcribe passage (incl. translate to English), re-run speaker detection keeping hand-assigned speakers.
- Exports: paragraph RTF/TXT, SRT, VTT, TXT, confidence RTF; master `.transcript.json` with provenance and edit history.
- Tests: end-to-end scenarios runnable with pytest (no GPU, display or real recordings needed).
