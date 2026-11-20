# Changelog

## Unreleased

- Speakers by number key: in the transcript, 1–9 assign speaker 1–9 and 0 adds a new one (⌃1–9 still work while typing). The speaker chips show their number.
- Select several segments — drag across them, ⇧-click, ⇧↑/⇧↓ or ⌘A — and assign a speaker (key, chip or the editor's speaker list) or mark them checked in one go. One undo step reverts the whole change.
- More transcript keys: ↑/↓ previous/next segment, R replays the open segment, C toggles checked, Return edits the segment, Tab in the segment box saves and returns to the transcript.
- View ▸ Keyboard Shortcuts (⌘/) lists every shortcut.
- Single-key shortcuts ignore ⌘/Ctrl/Alt, so ⌘C copies, ⌘1–3 switch tabs and ⌘R starts the queue from the transcript as before.
- Public-release housekeeping: third-party notices, citation file, security policy, contributing guide, issue/PR templates with privacy reminders, Dependabot for GitHub Actions.

## 0.9.0 — 2026-11-20

First version in this repository, restructured from the single-file Apple Silicon app into the `condense` package. Behaviour is unchanged; the previous settings, task queue and Keychain token (stored under "SiliconVideoPipeline") are picked up automatically.

- Task queue: one task per video or merge tasks, per-task steps and settings, remembered between launches.
- Hardware: MLX Whisper on the Apple GPU, PyTorch/Metal speaker detection, VideoToolbox encoding.
- Editor: paragraphs/segments views, play/pause/seek/speed, follow-along highlight, video preview, segment editing with timing kept, split/merge/insert/delete, speaker list with ⌃1–⌃9, markers, checked marks and progress, timeline, undo/redo, automatic backups, resume position.
- Accuracy: vocabulary per task, re-transcribe passage (incl. translate to English), re-run speaker detection keeping hand-assigned speakers.
- Exports: paragraph RTF/TXT, SRT, VTT, TXT, confidence RTF; master `.transcript.json` with provenance and edit history.
- Tests: end-to-end scenarios runnable with pytest (no GPU, display or real recordings needed).
