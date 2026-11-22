# Changelog

## 0.12.0 — 2026-11-22

Workshops with groups: episodes for interviews and activity phases, projects that follow your folders, and interviews you
already cut out found again in their session by their sound. Projects stay optional — without one, Condense works file
by file as before.

### Episodes: interviews and activity phases
- An **episode** labels a stretch of a recording: the facilitator's interview with a group, or a phase such as
  *Dioramas*. Nothing is removed. Select segments and press **E**, or use *Episodes…* in the transcript toolbar.
- Episodes show as **coloured bands on the timeline** and as **headers in the transcript** (▶ … / ■ end of …), and start
  new paragraphs. Phases you only know roughly can be marked *approximate*.
- **Show ▸ In Interview / In Phase …** moves through only the segments of one kind of episode.
- **Export an episode as a clip** with a transcript timed to it (SRT, TXT, paragraphs for MAXQDA) and an `.episode.json`
  record. Interviews are saved next to the group's other interviews. Exact export now jumps straight to the episode
  instead of decoding the whole recording before it.

### Interviews you already cut out
- **Find interview clips…** (Episodes dialog or Edit menu) finds each clip from the group's interview folder in the
  session recording **by its sound** — to a few hundredths of a second, whatever tool cut it — and adds it as an
  Interview episode labelled with the clip's name. Uncertain matches are marked for checking; running it again updates
  instead of duplicating. The session's sound is read once and cached.

### Projects follow your folders
- Levels are read from folder names: `Day 1 2026-08-10/Group 1/…` gives *Day* and *Group*. Media folders inside a group
  stay part of it.
- **Consent forms** and **…Unsorted** folders are skipped: never opened or listed.
- **Project ▸ Project Settings…**: name, levels, skipped folders and episode types (Interview, Phase, Plenary …).
- The **overview is a tree** — Day ▸ Group ▸ transcript ▸ episodes — with totals per day and group; double-click an
  episode to go to it. The CSV has a column per level.

### Projects are now something you open
- A new **Project** menu: *New Project…*, *Open Project…*, *Open Recent*, *Close Project*, *Project Overview…* and
  *Project Speakers…*. A project is still just the study's folder; nothing is moved.
- The button at the top right shows the open project (click it for the overview), or **No project open** (click it to
  start or open one).
- Opening a transcript of a study opens its project. *Open Transcript* and *Search All Transcripts* start in the project's
  folder.
- The user guide's project section explains the workflow.

### Fixed
- **Opening the project overview could freeze Condense.** With no transcript open, it searched your whole home folder,
  external drives included, before showing anything. It now never searches without a project, searches in the
  background, skips hidden folders and backups, and stops on very large drives. *Search All Transcripts* uses the same
  safe search.
- **macOS `._` files on external drives** (a Mac writes one next to every file on exFAT drives such as a T7) are no
  longer added as videos by *+ Folder* or listed as broken transcripts.

## 0.11.0 — 2026-11-22

Cut recordings down to what matters, review faster, keep a whole study in view — and Condense now runs on Windows.
New here? The [user guide](https://github.com/Courage-pupper/condense/blob/main/docs/user-guide.md) walks through a
whole study, from recordings to a checked transcript in MAXQDA (also under *Help ▸ User Guide*).

### Highlights
- **Cutting:** press **X** to leave segments out of the transcript, then export a trimmed video with a transcript timed to
  it, ready for MAXQDA.
- **Trim before transcribing:** mark setup time and breaks on a filmstrip; they are skipped before Whisper runs.
- **Find & replace**, in one transcript or across every transcript of a study.
- **Notes on segments** and a **Show filter** (uncertain / not checked / with notes / cut).
- **Project overview** and **project speakers**: progress of every session at a glance, and the same number keys and
  colours for the same people in every session.
- **Clean up for Whisper:** levelling and noise reduction for quiet children and noisy rooms.
- **GPU check:** Condense tests the GPU before you transcribe, and *Help ▸ Test GPU…* shows the details.
- **Windows** (preview), with NVIDIA and AMD GPUs.

### ⚠ Changed shortcuts on Linux and Windows
- Assigning a speaker **while typing** in the segment box is now **Alt+1–9** (was Ctrl+1–9). Ctrl+1–3 switch tabs again.
- **Markers** are **Ctrl+Shift+letter** (as the menu always said). Before, Ctrl+C inserted `[crosstalk]` as well as
  copying, and Ctrl+I inserted `[inaudible]` instead of a segment.
- In the transcript itself nothing changes: **1–9** assign speakers as before. The Mac keeps ⌃1–9 and ⌃letter.

### Cutting and trimming
- **Cut while reviewing:** select segments and press **X** (or *✂ Cut / restore*). Cut segments are struck through, left
  out of every export (TXT, SRT, VTT, RTF, paragraphs), skipped while playing (*Skip cuts*) and don't count towards
  % checked. Nothing is deleted: X again restores them, *Edit ▸ Restore All Cuts…* restores everything, and ⌘Z / Ctrl+Z
  undoes.
- **Export trimmed video & transcript** (File menu or the transcript toolbar): the video without the cut parts, and a
  transcript re-timed to it, so MAXQDA links them correctly.
  - *Exact* re-encodes and cuts exactly where you cut.
  - *Lossless* is fast and keeps full quality but can only start on keyframes. The transcript is timed to what was
    really written, and cuts too short to apply are reported.
  - A `.cuts.json` record documents what was cut; the cuts can also be saved as a LosslessCut project.
- **Trim before transcribing** (*Task Details ▸ Trim…*): mark parts to leave out on a filmstrip and waveform, with a frame
  preview and 5-second playback (I / O mark a cut's start and end). Saved as `<video>-proj.llc`, which Condense applies
  before transcribing and LosslessCut opens too. Condense never deletes a LosslessCut project it did not write.
- When LosslessCut cuts are applied before transcribing, the trimmed video the transcript is timed to is now kept
  (`<name>_trimmed.<ext>`) instead of being discarded.

### Reviewing
- **Find & replace** (⌘F / Ctrl+F, or *Find…*): matches are highlighted; Return / ⇧Return step through them. *Replace* and
  *Replace all* are one undo step each, with options for match case and whole words. Word timings are kept.
- **Search all transcripts** (*Edit ▸ Search All Transcripts…*): searches every transcript in a folder and its subfolders,
  opens a hit at its segment, and can replace everywhere — for a name Whisper always gets wrong, or to swap a name for a
  pseudonym. Each changed transcript gets a backup, new exports and an entry in its edit history.
- **Notes on segments:** a ✎ Note field under the segment text. Notes show in the transcript and appear as `[Note: …]` in
  the TXT and paragraph exports.
- **Show filter:** All segments / Uncertain / Not checked / With notes / Cut. ↑/↓ then move only through those segments.
- **Clean up for Whisper** (*Settings ▸ Audio & Whisper*): *Level quiet voices* or *Reduce noise + level voices*. Only
  the audio Whisper hears is cleaned; your video and audio files are unchanged. The setting is recorded in the
  transcript's provenance.

### Projects
- **Project overview** (*File ▸ Project Overview…*): every transcript in a study folder with its length, % checked,
  % uncertain words, speakers (and how many are still unnamed), cut time, notes and last change. Double-click opens one;
  *Save as CSV* writes `condense-overview.csv`.
- **Project speakers:** the people of a study (facilitators, the children's pseudonyms) in a fixed order, saved as
  `condense-project.json` in the study folder. They come first in every transcript below it, so they have the same
  number key and colour in every session. *Add names from transcripts* collects the names you already used.

### Windows (preview)
- Windows 10/11, 64-bit. Whisper and speaker detection run on the GPU through PyTorch: CUDA on NVIDIA; AMD's ROCm for
  Windows on the Radeon RX 7900 / RX 9000 series (Windows 11, AMD Software 26.2.2 or later); otherwise the CPU.
- Video compression on the GPU: NVENC (NVIDIA), AMF (AMD) or Quick Sync (Intel). The default tests which encoder really
  works on the PC and falls back to the CPU.
- One-line install from PowerShell (see below); winget installs Python 3.12, FFmpeg and HandBrake CLI. In-app updates
  work as on macOS and Linux.
- Hugging Face token in the Windows Credential Manager; Start-menu and Desktop shortcuts; no console windows; sharp text
  on high-resolution screens.

### Linux
- **Native file chooser:** the desktop's own dialog (GNOME/GTK via `zenity`, KDE via `kdialog`) with external drives,
  bookmarks and search, instead of Tk's old dialog. It starts in the last folder used and also finds upper-case
  extensions such as `.MP4`. The installer adds `zenity`.
- **Ctrl+Enter** (save, mark checked, next segment) now also works when the transcript has the focus, and with the
  number-pad Enter.
- The harmless `amdgpu.ids: No such file or directory` message from ROCm builds of PyTorch no longer appears in the
  Console.

### All platforms
- **GPU check:** at start-up Condense runs a short calculation on the GPU instead of only asking whether one is visible
  (with ROCm a card can be visible yet unusable). The header shows the result; *Help ▸ Test GPU…* shows the details. If a
  task would transcribe on the CPU although a GPU backend is chosen, *Run* asks first. The Linux and Windows installers
  run the same test.
- **User guide** with screenshots: *Help ▸ User Guide*.
- Buttons and hints show Ctrl+S, Ctrl+Enter … on Linux and Windows instead of Mac symbols.
- Without HandBrake, the x265/x264 encoders use ffmpeg's own instead of failing.
- Whisper's output is read as UTF-8.
- Faster re-timing for long sessions with many cuts; the project overview table is readable on Linux.

### Under the hood
- Every CI run now tests the real app on macOS, Linux and Windows, including real key presses for the shortcuts, and a
  3-hour session through every core path and both trim modes.
- The largest modules (editor, pipeline worker) are split into smaller ones.

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
