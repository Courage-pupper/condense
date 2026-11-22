# Changelog

## 0.11.0 — 2026-11-20

### Reviewing
- **Find & replace** (⌘F / Ctrl+F, or *Find…* in the transcript toolbar): matching segments are highlighted, ◀/▶ (Return /
  ⇧Return) step through them, *Replace* and *Replace all* are one undo step each; options for match case and whole words.
  Word timings are kept; only changed words are marked as edited.
- **Search all transcripts** (Edit menu or *All transcripts…* in the find bar): searches every transcript in a folder
  (and its subfolders), opens a hit at its segment, and can replace everywhere — each transcript gets a backup, new
  exports and an entry in its edit history.
- **Notes on segments:** a ✎ Note field under the segment text. Notes appear in the transcript and in the TXT and paragraph
  exports as `[Note: …]`.
- **Show filter** in the transcript toolbar: All segments / Uncertain / Not checked / With notes / Cut. ↑/↓ move within the
  filtered segments, so you can work through only the uncertain or unchecked ones.
- **Project overview** (File ▸ Project Overview…): every transcript in a study folder with its length, % checked,
  % uncertain words, speakers (and how many are still unnamed), cut time, notes and last change. Double-click opens one;
  *Save as CSV* writes `condense-overview.csv` for your records.
- **Project speakers:** a `condense-project.json` in the study folder lists the people of the study (facilitators, the
  children's pseudonyms). They come first in every transcript under that folder, so they keep the same number key and
  colour in every session. Edit the list from the overview (*Project speakers…*); *Add names from transcripts* collects the
  names you already used (not `SPEAKER_xx`).
- **Clean up for Whisper** (Settings ▸ Audio & Whisper): *Level quiet voices* or *Reduce noise + level voices* is applied to
  the audio Whisper hears — useful for quiet children and noisy rooms. The kept audio and video are unchanged; the setting is
  recorded in the transcript's provenance.

### Cutting
- **Cut while reviewing:** select segments and press **X** (or ✂ Cut / restore). Cut segments are struck through, left out of
  every export (TXT, SRT, VTT, RTF, paragraphs), skipped while playing ("Skip cuts") and don't count towards % checked.
  Nothing is deleted: X again restores them, and ⌘Z / Ctrl+Z undoes.
- **Export trimmed video & transcript** (File menu or the transcript toolbar): writes the video without the cut parts and
  a transcript re-timed to it, so MAXQDA links them correctly. *Exact* re-encodes and cuts precisely; *Lossless* copies
  the streams (fast) but can only start on keyframes — the transcript is timed to what was really written, and cuts too
  short to apply are reported. A `.cuts.json` record documents what was cut. Optionally saves the cuts as a LosslessCut project.
- **Trim before transcribing** (Task Details ▸ Trim…): mark parts to leave out (setup, breaks) on a filmstrip and waveform
  with a frame preview and 5-second playback; saved as `<video>-proj.llc`, which Condense applies before transcribing and
  LosslessCut opens too. Condense never deletes a LosslessCut project it did not write.
- When LosslessCut cuts are applied before transcribing, the trimmed video the transcript is timed to is now kept
  (`<name>_trimmed.<ext>`) instead of being discarded with the temporary files.

### Windows (preview)
- New Windows platform layer (Windows 10/11, 64-bit). Whisper and speaker detection run on the GPU through PyTorch: CUDA on NVIDIA, AMD's ROCm build of PyTorch for Windows on the Radeon RX 7900 / RX 9000 series (Windows 11, AMD Software 26.2.2+), otherwise the CPU.
- Video compression on the GPU with ffmpeg: NVENC (NVIDIA), AMF (AMD) and Quick Sync (Intel). The default `auto_h265` tests which encoder really works on the PC (an encoder listed by ffmpeg is not necessarily usable) and falls back to the CPU.
- One-line install from PowerShell: `irm https://raw.githubusercontent.com/Courage-pupper/condense/main/install.ps1 | iex` — winget installs Python 3.12, FFmpeg and HandBrake CLI; options (`-Gpu`, `-Venv`, `-WithPyannote`, …) are remembered; in-app updates work as on macOS and Linux.
- Hugging Face token in the Windows Credential Manager, Start-menu and Desktop shortcuts with an icon, no console windows, sharp text on high-DPI screens, `%APPDATA%` / `%LOCALAPPDATA%` locations.

### Linux
- **Ctrl+Enter** (save, mark checked, next segment) now works when the transcript has the focus, not only in the text box — on Linux and Windows menu shortcuts are not active by themselves as they are on macOS. The number-pad Enter works too. It acts only on the Transcript tab.
- The harmless message `amdgpu.ids: No such file or directory` (from the libdrm inside ROCm builds of PyTorch, which looks for AMD's GPU name table under /opt/amdgpu) no longer appears in the Console.
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
