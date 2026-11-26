"""Runs queued tasks: merge, compress, extract audio, transcribe."""
import json
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from condense.core.util import *  # noqa: F401,F403
from condense.media.losslesscut import LosslessCutHelper
from condense.pipeline.encode import EncodeMixin
from condense.pipeline.transcribe import TranscribeMixin


class PipelineWorker(EncodeMixin, TranscribeMixin, threading.Thread):
    def __init__(
        self,
        config: Dict,
        msg_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.config = config
        self.msg_queue = msg_queue
        self.stop_event = stop_event
        self.current_proc: Optional[subprocess.Popen] = None
        self.last_encode: Optional[Dict] = None  # exact encoder + command, recorded in provenance
        self.run_started: Optional[str] = None
        self._used_bases: set = set()

    def log(self, text: str, level: str = "INFO") -> None:
        self.msg_queue.put({"type": "log", "text": text, "level": level})

    def set_progress(self, percent: float, status: str) -> None:
        self.msg_queue.put(
            {"type": "progress", "percent": max(0.0, min(100.0, percent)), "status": status}
        )

    def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        payload: Dict[str, object] = {"type": "job_update", "job_id": job_id}
        if status is not None:
            payload["status"] = status
        if stage is not None:
            payload["stage"] = stage
        if progress is not None:
            payload["progress"] = max(0.0, min(100.0, float(progress)))
        if output_dir is not None:
            payload["output_dir"] = output_dir
        self.msg_queue.put(payload)

    RES_MAP = {
        "2160p (4K)": (3840, 2160),
        "1440p (2K)": (2560, 1440),
        "1080p (FHD)": (1920, 1080),
        "720p (HD)": (1280, 720),
    }

    # ----- subprocess helpers ------------------------------------------------------------------
    def run_checked(self, cmd: List[str], what: str) -> None:
        """Run a tool to completion. Honours the Stop button, and on failure raises an error that
        contains the tool's own last error lines instead of just 'exit status 1'."""
        if self.stop_event.is_set():
            raise InterruptedError("Pipeline cancelled by user.")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
        self.current_proc = proc
        err = ""
        try:
            while True:
                try:
                    _, err = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if self.stop_event.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        raise InterruptedError("Pipeline cancelled by user.")
        finally:
            self.current_proc = None
        if proc.returncode != 0:
            tail = [l for l in (err or "").splitlines() if l.strip()][-6:]
            raise RuntimeError(f"{what} failed (exit code {proc.returncode}):\n" + "\n".join(tail))

    def probe_media_signature(self, path: Path) -> Optional[Tuple]:
        """Everything that must match for a lossless concat: video size/codec/frame rate AND audio codec/rate/channels."""
        if not shutil.which("ffprobe"):
            return None
        try:
            res = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                    "-of", "json", str(path),
                ],
                capture_output=True, text=True, timeout=10,
            )
            streams = json.loads(res.stdout).get("streams", [])
        except Exception:
            return None
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not v:
            return None
        sig_v = (v.get("codec_name"), v.get("width"), v.get("height"), v.get("r_frame_rate"))
        sig_a = (a.get("codec_name"), a.get("sample_rate"), a.get("channels")) if a else None
        return (sig_v, sig_a)

    def unique_base(self, out_dir: Path, base: str, job_id: str) -> str:
        """Avoid two jobs in one run writing the same <base>.* files (same file name, shared output folder)."""
        key = (str(Path(out_dir).resolve()), base)
        if key in self._used_bases:
            base = f"{base}_{job_id}"
            key = (key[0], base)
        self._used_bases.add(key)
        return base

    # ----- stages --------------------------------------------------------------------------------
    def run_stage_merge(
        self,
        videos: List[Path],
        out_dir: Path,
        temp_dir: Path,
        compress_also_enabled: bool,
        job_ids: Optional[List[str]] = None,
    ) -> Tuple[Path, Dict]:
        job_ids = job_ids or []
        self.log(f"=== Stage 1: Merging {len(videos)} video(s) ===", "STAGE")
        self.set_progress(5.0, "Stage 1: Preparing video segments for merge...")

        prepared_videos = []
        llc_records = []
        for v in videos:
            if self.stop_event.is_set():
                raise InterruptedError("Cancelled.")
            if self.config.get("respect_llc", True):
                pv, rec = LosslessCutHelper.apply_llc_cuts_if_present(v, temp_dir, self.log, self.run_checked)
                prepared_videos.append(pv)
                if rec:
                    llc_records.append(rec)
            else:
                prepared_videos.append(v)

        container_ext = ".mkv" if self.config["hb_container"] == "MKV" else ".mp4"
        merged_name = (self.config.get("merged_filename") or "merged_output").strip() or "merged_output"

        signatures = [self.probe_media_signature(v) for v in prepared_videos]
        can_stream_copy = None not in signatures and len(set(signatures)) == 1
        if not can_stream_copy:
            self.log("Inputs differ in video or audio format; normalizing before merge (re-encode).", "INFO")

        merge_record = {
            "inputs_in_order": [str(v) for v in videos],
            "method": "stream copy (lossless)" if can_stream_copy else "normalized re-encode (libx264 CRF 16, 30 fps, AAC 48 kHz stereo)",
            "losslesscut": llc_records,
        }

        intermediate_concat = temp_dir / f"concat_stream{container_ext}"
        if can_stream_copy:
            concat_txt = temp_dir / "ffmpeg_concat_list.txt"
            concat_txt.write_text("\n".join(ffconcat_line(p) for p in prepared_videos) + "\n", encoding="utf-8")
            self.run_checked(
                ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(intermediate_concat)],
                "Merging videos (stream copy)",
            )
        else:
            first_v = signatures[0][0] if signatures[0] else (None, 1920, 1080, None)
            target_w = int(first_v[1] or 1920)
            target_h = int(first_v[2] or 1080)
            norm_parts = []
            for idx, p in enumerate(prepared_videos):
                norm_out = temp_dir / f"norm_part_{idx:03d}.mkv"
                vf = (
                    f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                    f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                )
                self.set_progress(5.0 + 12.0 * idx / max(1, len(prepared_videos)), f"Stage 1: Normalizing {p.name}...")
                self.run_checked(
                    [
                        "ffmpeg", "-hide_banner", "-y", "-i", str(p), "-vf", vf, "-r", "30",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(norm_out),
                    ],
                    f"Normalizing {p.name} for merge",
                )
                norm_parts.append(norm_out)

            concat_txt = temp_dir / "ffmpeg_norm_list.txt"
            concat_txt.write_text("\n".join(ffconcat_line(p) for p in norm_parts) + "\n", encoding="utf-8")
            self.run_checked(
                ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(intermediate_concat)],
                "Merging normalized videos",
            )

        final_merged = out_dir / f"{merged_name}{container_ext}"
        if compress_also_enabled or not can_stream_copy:
            label = "Stage 1+2: HandBrake Merge & Compress" if compress_also_enabled else "Stage 1: HandBrake Merge Finalize"
            span = 35.0 if compress_also_enabled else 25.0
            written = self.run_handbrake_encode_for_jobs(
                intermediate_concat, final_merged, label,
                base_progress=18.0, progress_span=span, job_ids=job_ids, job_base_pct=18.0, job_span_pct=span,
            )
            if Path(written) == Path(intermediate_concat):  # not worth compressing: the merged original is kept
                final_merged = final_merged.with_suffix(Path(intermediate_concat).suffix)
                shutil.move(str(intermediate_concat), str(final_merged))
            else:
                final_merged = Path(written)  # (an H.264 goal writes .mp4)
            merge_record["final_encode"] = self.compression_record()
        else:
            shutil.copy2(str(intermediate_concat), str(final_merged))

        return final_merged, merge_record

    def run_stage_extract_audio(self, video_path: Path, out_dir: Path, base_name: str) -> Path:
        self.log(f"=== Stage 3: Extracting Audio from {video_path.name} ===", "STAGE")
        fmt_choice = self.config["audio_format"]
        speech_filter = self.config.get("speech_filter", False)

        cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(video_path), "-vn"]
        if speech_filter:
            cmd.extend(["-af", "highpass=f=80,lowpass=f=8000,dynaudnorm"])

        if fmt_choice.startswith("WAV (16kHz Mono"):
            ext = ".wav"
            cmd.extend(["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"])
        elif fmt_choice.startswith("WAV (48kHz Stereo"):
            ext = ".wav"
            cmd.extend(["-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2"])
        elif fmt_choice.startswith("FLAC"):
            ext = ".flac"
            cmd.extend(["-acodec", "flac", "-ar", "16000", "-ac", "1"])
        elif fmt_choice.startswith("M4A"):
            ext = ".m4a"
            cmd.extend(["-acodec", "aac", "-b:a", "192k"])
        else:
            ext = ".mp3"
            cmd.extend(["-acodec", "libmp3lame", "-b:a", "192k"])

        audio_out = out_dir / f"{base_name}_audio{ext}"
        cmd.append(str(audio_out))
        self.run_checked(cmd, f"Extracting audio from {video_path.name}")
        return audio_out

    def run(self) -> None:
        """Run the task queue. A task is {"id", "name", "inputs": [video paths], "steps": {"compress", "audio",
        "whisper"}, "settings": {...snapshot taken when the task was created...}, "output_dir"}.
        Two or more inputs mean: merge them (in that order) first. Failures stay inside their task."""
        temp_dir = Path(tempfile.mkdtemp(prefix="condense_"))
        base_config = dict(self.config)
        tasks: List[Dict] = base_config.get("tasks", [])
        current_job_ids: List[str] = []
        self.run_started = now_iso()
        success = False
        failed_jobs = 0
        try:
            total = max(1, len(tasks))
            for idx, task in enumerate(tasks):
                if self.stop_event.is_set():
                    raise InterruptedError("Cancelled.")
                tid = task["id"]
                current_job_ids = [tid]
                steps = {"compress": False, "audio": False, "whisper": False}
                steps.update(task.get("steps") or {})
                self.config = dict(base_config, **(task.get("settings") or {}))
                self.config.update(
                    stage_compress=bool(steps["compress"]), stage_audio=bool(steps["audio"]), stage_whisper=bool(steps["whisper"]),
                    merged_filename=task.get("name") or "merged_video",
                )
                self.last_encode = None
                inputs = [Path(p) for p in task.get("inputs") or []]
                out_dir = Path(task.get("output_dir") or base_config.get("output_dir") or Path.home()).expanduser()
                label = f"Task {idx + 1}/{total}"
                job_base_overall = (idx / total) * 100.0
                job_span_overall = 100.0 / total
                try:
                    if not inputs:
                        raise RuntimeError("Task has no input videos.")
                    missing = [p for p in inputs if not p.exists()]
                    if missing:
                        raise RuntimeError(f"Input not found: {missing[0]}")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    self.log(f"=== {label}: {task.get('name')} ({len(inputs)} video{'s' if len(inputs) != 1 else ''}) ===", "STAGE")
                    self.update_job(tid, status="Running", stage="Starting…", progress=2.0, output_dir=str(out_dir))

                    if len(inputs) > 1:
                        merged, merge_record = self.run_stage_merge(
                            videos=inputs, out_dir=out_dir, temp_dir=temp_dir, compress_also_enabled=steps["compress"], job_ids=[tid]
                        )
                        base = self.unique_base(out_dir, merged.stem, tid)
                        self.run_audio_and_transcribe(merged, out_dir, temp_dir, base, [tid], inputs, {"merge": merge_record})
                    else:
                        orig_vid = inputs[0]
                        base = self.unique_base(out_dir, orig_vid.stem, tid)
                        processing: Dict = {}
                        active_vid = orig_vid
                        if self.config.get("respect_llc", True):
                            active_vid, llc_rec = LosslessCutHelper.apply_llc_cuts_if_present(orig_vid, temp_dir, self.log, self.run_checked)
                            processing["losslesscut"] = llc_rec
                            if llc_rec and not steps["compress"] and is_within(active_vid, temp_dir):
                                # the transcript is timed to the trimmed video: keep it, so it can be watched in the
                                # editor and imported into MAXQDA together with the transcript
                                kept_vid = out_dir / f"{base}_trimmed{Path(active_vid).suffix}"
                                shutil.move(str(active_vid), str(kept_vid))
                                active_vid = kept_vid
                                llc_rec["kept_video"] = kept_vid.name
                                self.log(f"Kept the trimmed video the transcript is timed to: {kept_vid.name}", "INFO")
                        if steps["compress"]:
                            container_ext = ".mkv" if self.config.get("hb_container", "MKV") == "MKV" else ".mp4"
                            out_vid = out_dir / f"{base}_compressed{container_ext}"
                            written = Path(self.run_handbrake_encode_for_jobs(
                                input_path=active_vid, output_path=out_vid, stage_label=label,
                                base_progress=job_base_overall + job_span_overall * 0.05, progress_span=job_span_overall * 0.45,
                                job_ids=[tid], job_base_pct=10.0, job_span_pct=45.0,
                            ))
                            if written == Path(active_vid):  # a copy would not be clearly smaller: the original is used
                                processing["compression"] = dict(self.compression_record(), file=None)
                                if is_within(active_vid, temp_dir):  # trimmed (LosslessCut): keep it, it's what the transcript follows
                                    kept_vid = out_dir / f"{base}_trimmed{Path(active_vid).suffix}"
                                    shutil.move(str(active_vid), str(kept_vid))
                                    active_vid = kept_vid
                                    processing["compression"]["file"] = kept_vid.name
                            else:
                                processing["compression"] = dict(self.compression_record(), file=written.name)
                                active_vid = written
                        self.run_audio_and_transcribe(active_vid, out_dir, temp_dir, base, [tid], [orig_vid], processing)

                    self.update_job(tid, status="Completed", stage="Completed ✓", progress=100.0, output_dir=str(out_dir))
                except InterruptedError:
                    raise
                except Exception as exc:
                    failed_jobs += 1
                    self.log(f"{label} ({task.get('name')}) failed: {exc}", "ERROR")
                    self.update_job(tid, status="Failed", stage=f"Error: {short_error(exc)}")
                current_job_ids = []

            if failed_jobs:
                self.set_progress(100.0, f"Finished with {failed_jobs} failed task(s) - see the console.")
            else:
                self.set_progress(100.0, "All queued tasks completed successfully!")
            success = failed_jobs == 0
        except InterruptedError as exc:
            self.log(f"Stopped: {exc}", "WARN")
            for jid in current_job_ids:
                self.update_job(jid, status="Failed", stage="Stopped by user")
        except Exception as exc:
            self.log(f"Pipeline error: {exc}", "ERROR")
            for jid in current_job_ids:
                self.update_job(jid, status="Failed", stage=f"Error: {short_error(exc)}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.msg_queue.put({"type": "done", "success": success})
