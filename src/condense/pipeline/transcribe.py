"""Transcription for the pipeline worker: audio for Whisper, the Whisper run, speakers, provenance."""
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from condense import __version__
from condense.asr.runner import WHISPER_RUNNER, is_harmless_output, runner_command
from condense.core.transcript import TranscriptDocument
from condense.core.util import *  # noqa: F401,F403
from condense.platform import DEFAULT_BACKEND, GPU_DIARIZATION, MacAppInstaller, SystemProbe


class TranscribeMixin:
    # What Whisper hears can be cleaned up; the kept audio/video files are not changed by this.
    AUDIO_CLEANUP = {
        "Off": None,
        "Level quiet voices": "dynaudnorm=f=150:g=15:p=0.9",
        "Reduce noise + level voices": "highpass=f=80,lowpass=f=8000,afftdn=nf=-25:tn=1,dynaudnorm=f=150:g=15:p=0.9",
    }

    def to_wav16k(self, src: Path, dst: Path, audio_filter: Optional[str] = None) -> Path:
        cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(src), "-vn"]
        if audio_filter:
            cmd += ["-af", audio_filter]
        self.run_checked(cmd + ["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)],
                         f"Converting {src.name} to 16 kHz WAV" + (" (with audio cleanup)" if audio_filter else ""))
        return dst

    def persistent_playback_media(self, candidates: List[Optional[Path]], temp_dir: Path, out_dir: Path, base_name: str) -> Optional[Path]:
        """The transcript editor needs media on the SAME timeline as the transcript that survives the run.
        Use the extracted audio or the (compressed/original) video if they are kept; otherwise save a small M4A."""
        existing = [c for c in candidates if c and Path(c).exists()]
        for c in existing:
            if not is_within(c, temp_dir):
                return Path(c)
        if not existing:
            return None
        dst = out_dir / f"{base_name}_playback.m4a"
        self.run_checked(
            ["ffmpeg", "-hide_banner", "-y", "-i", str(existing[0]), "-vn", "-c:a", "aac", "-b:a", "96k", str(dst)],
            "Saving playback audio for the transcript editor",
        )
        return dst

    def build_provenance(self, source_media: List[Path], transcribed: Path, processing: Dict, result: Dict, settings: Dict) -> Dict:
        self.log("Recording provenance (SHA-256 of inputs, tool versions, settings)...", "INFO")
        inputs = []
        for src in source_media:
            src = Path(src)
            inputs.append(
                {
                    "path": str(src),
                    "size_bytes": src.stat().st_size if src.exists() else None,
                    "duration_s": round(self.probe_video_duration(src), 3) or None,
                    "sha256": sha256_file(src, self.stop_event),
                }
            )
        tools = {"ffmpeg": tool_version("ffmpeg", "-version")}
        if (processing.get("compression") or {}).get("tool") == "HandBrakeCLI" or "HandBrakeCLI" in json.dumps(processing):
            tools["HandBrakeCLI"] = tool_version("HandBrakeCLI", "--version")
        return {
            "app": {"name": "Condense", "version": __version__},
            "run_started": self.run_started,
            "transcribed_at": now_iso(),
            "system": {
                "os": platform.platform(),
                "macos": platform.mac_ver()[0] or None,
                "machine": platform.machine(),
                "chip": SystemProbe._sysctl("machdep.cpu.brand_string") or None,
            },
            "tools": tools,
            "inputs": inputs,
            "transcribed_audio": {"file": transcribed.name, "sha256": sha256_file(transcribed, self.stop_event)},
            "processing": processing,
            "whisper": dict(settings, **{
                "backend_used": result.get("backend_label"),
                "detected_language": result.get("language"),
                "diarization_method": result.get("diarization_method"),
                "runtime": result.get("runtime"),
            }),
        }

    def run_stage_whisper(
        self,
        media_path: Path,
        out_dir: Path,
        temp_dir: Path,
        base_name: str,
        source_media: List[Path],
        playback_media: Optional[Path],
        processing: Dict,
        job_ids: Optional[List[str]] = None,
    ) -> Dict[str, Path]:
        job_ids = job_ids or []
        backend = self.config.get("whisper_backend", DEFAULT_BACKEND)
        self.log(f"=== Stage 4: Transcribing {media_path.name} with Whisper ({backend}) ===", "STAGE")
        self.set_progress(68.0, f"Stage 4: Loading Whisper '{self.config['whisper_model']}' ({backend})...")

        best_py = MacAppInstaller.find_best_python()
        model_name = self.config["whisper_model"]
        lang = self.config["whisper_lang"].split()[0]
        diarize_mode = self.config.get("diarize_mode", GPU_DIARIZATION)
        num_speakers = int(self.config.get("num_speakers", 0))
        hf_token = (self.config.get("hf_token") or "").strip()
        raw_json_path = temp_dir / f"{base_name}_{job_ids[0] if job_ids else 'x'}_whisper_raw.json"

        if media_path.suffix.lower() == ".wav":
            diar_wav_path = media_path
        else:
            diar_wav_path = temp_dir / f"{base_name}_16k_mono.wav"
            try:
                self.to_wav16k(media_path, diar_wav_path)
            except InterruptedError:
                raise
            except Exception as exc:
                self.log(f"Could not prepare audio for diarization ({exc}); speakers may be missing.", "WARN")

        runner_code = WHISPER_RUNNER
        env = os.environ.copy()
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["PIPELINE_HF_TOKEN"] = hf_token  # via environment, not argv (argv is visible to other users in `ps`)
        env["PIPELINE_TASK"] = "transcribe"
        env["PIPELINE_PROMPT"] = str(self.config.get("whisper_prompt") or "")
        env["PIPELINE_MODE"] = "full"

        env["PYTHONIOENCODING"] = "utf-8"
        self.current_proc = subprocess.Popen(
            [
                *runner_command(best_py, temp_dir),
                str(media_path),
                str(diar_wav_path),
                model_name,
                lang,
                str(raw_json_path),
                diarize_mode,
                str(num_speakers),
                backend,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        pct_regex = re.compile(r"(\d+)%\|")
        last_logged_pct = -100.0
        tail_lines: List[str] = []
        if self.current_proc.stdout:
            for line in self.current_proc.stdout:
                if self.stop_event.is_set():
                    self.current_proc.terminate()
                    raise InterruptedError("Cancelled.")
                clean = line.strip()
                if not clean or is_harmless_output(clean):
                    continue
                tail_lines = (tail_lines + [clean])[-8:]
                m = pct_regex.search(clean)
                if m:
                    w_pct = float(m.group(1))
                    # Progress bars print constantly; log only every ~10% to keep the console readable.
                    if abs(w_pct - last_logged_pct) >= 10 or w_pct >= 100:
                        self.log(f"[Whisper] {clean}", "INFO")
                        last_logged_pct = w_pct
                    for jid in job_ids:
                        self.update_job(jid, status="Running", stage=f"Stage 4: Transcribing {w_pct:.0f}%", progress=70.0 + (w_pct * 0.22))
                else:
                    self.log(f"[Whisper] {clean}", "INFO")

        ret = self.current_proc.wait()
        self.current_proc = None
        if ret != 0 or not raw_json_path.exists():
            raise RuntimeError("Whisper transcription failed:\n" + "\n".join(tail_lines[-5:]))

        result_data = json.loads(raw_json_path.read_text(encoding="utf-8"))
        synced = sync_text_with_words(result_data.get("segments") or [])
        kept, removed = drop_empty_segments(result_data.get("segments") or [])
        result_data["segments"] = kept
        if removed or synced:
            result_data["cleanup"] = {"empty_segments_removed": removed, "segments_text_synced_to_words": synced}
        if removed:
            self.log(f"Removed {removed} empty segment(s) Whisper produced in silent passages.", "INFO")
        if synced:
            self.log(f"Matched the text of {synced} segment(s) to their timed words (Whisper's text differed).", "INFO")

        whisper_settings = {
            "backend_setting": backend,
            "model": model_name,
            "language_setting": lang,
            "diarization_setting": diarize_mode,
            "num_speakers_hint": num_speakers,
            "pyannote_token_supplied": bool(hf_token),
            "vocabulary_prompt": str(self.config.get("whisper_prompt") or "") or None,
        }
        provenance = self.build_provenance(source_media, media_path, processing, result_data, whisper_settings)
        doc = TranscriptDocument.create(
            out_dir=out_dir,
            base_name=base_name,
            whisper_result=result_data,
            model_name=model_name,
            export_formats={
                "srt": bool(self.config.get("fmt_srt", True)),
                "vtt": bool(self.config.get("fmt_vtt", False)),
                "txt": bool(self.config.get("fmt_txt", True)),
                "rtf": bool(self.config.get("fmt_rtf", True)),
                "para_rtf": bool(self.config.get("fmt_para_rtf", True)),
                "para_txt": bool(self.config.get("fmt_para_txt", False)),
            },
            thresholds={
                "high": float(self.config.get("conf_high", 0.85)),
                "medium": float(self.config.get("conf_med", 0.60)),
                "poor": float(self.config.get("conf_poor", 0.40)),
            },
            media={
                "playback": str(playback_media) if playback_media else None,
                "video": str(self.current_video_media) if getattr(self, "current_video_media", None) else None,
                "transcribed_audio": str(media_path) if not is_within(media_path, temp_dir) else None,
            },
            provenance=provenance,
        )
        doc.save()
        created = doc.export_all()
        created["json"] = doc.path
        self.log(f"Saved {', '.join(p.name for p in created.values())} in {out_dir}", "SUCCESS")
        self.msg_queue.put({"type": "transcript_ready", "job_ids": job_ids, "doc_path": str(doc.path)})
        return created

    def run_audio_and_transcribe(
        self,
        active_vid: Path,
        out_dir: Path,
        temp_dir: Path,
        base: str,
        job_ids: List[str],
        source_media: List[Path],
        processing: Dict,
    ) -> None:
        do_audio = self.config["stage_audio"]
        do_whisper = self.config["stage_whisper"]
        extracted = None
        if do_audio:
            extracted = self.run_stage_extract_audio(active_vid, out_dir, base)
            processing["audio_extraction"] = {
                "format": self.config.get("audio_format"),
                "speech_bandpass_filter": bool(self.config.get("speech_filter")),
                "file": extracted.name,
            }
        if not do_whisper:
            return
        cleanup = self.config.get("audio_cleanup") or "Off"
        audio_filter = self.AUDIO_CLEANUP.get(cleanup)
        if audio_filter:
            src = extracted if extracted is not None else active_vid
            self.log(f"Audio cleanup for Whisper: {cleanup}.", "INFO")
            whisper_input = self.to_wav16k(src, temp_dir / f"{base}_{job_ids[0]}_whisper_input.wav", audio_filter)
            processing["whisper_audio_cleanup"] = {"setting": cleanup, "ffmpeg_filter": audio_filter}
        elif extracted is not None:
            whisper_input = extracted
        else:
            whisper_input = self.to_wav16k(active_vid, temp_dir / f"{base}_{job_ids[0]}_whisper_input.wav")
        playback = self.persistent_playback_media([extracted, active_vid, whisper_input], temp_dir, out_dir, base)
        # Video for the editor's preview: only one that is kept AND on the transcript's timeline
        # (original, compressed or merged video; a LosslessCut-trimmed temp file is not kept).
        self.current_video_media = active_vid if (Path(active_vid).exists() and not is_within(active_vid, temp_dir)) else None
        self.run_stage_whisper(
            media_path=whisper_input,
            out_dir=out_dir,
            temp_dir=temp_dir,
            base_name=base,
            source_media=source_media,
            playback_media=playback,
            processing=processing,
            job_ids=job_ids,
        )
