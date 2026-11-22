"""Runs queued tasks: merge, compress, extract audio, transcribe."""
import json
import math
import os
import platform
import plistlib
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from condense import __version__
from condense.asr.runner import WHISPER_RUNNER, is_harmless_output, runner_command
from condense.core.exports import RTFConfidenceWriter
from condense.core.transcript import TranscriptDocument
from condense.core.util import *  # noqa: F401,F403
from condense.media.losslesscut import LosslessCutHelper
from condense.platform import DEFAULT_BACKEND, GPU_DIARIZATION, INSTALL_HINTS, MacAppInstaller, SystemProbe, has_ffmpeg_vaapi, vaapi_device

class PipelineWorker(threading.Thread):
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

    def probe_video_duration(self, video_path: Path) -> float:
        if not shutil.which("ffprobe"):
            return 0.0
        try:
            res = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return max(0.0, float(res.stdout.strip()))
        except Exception:
            return 0.0

    RES_MAP = {
        "2160p (4K)": (3840, 2160),
        "1440p (2K)": (2560, 1440),
        "1080p (FHD)": (1920, 1080),
        "720p (HD)": (1280, 720),
    }

    def build_hw_encode_command(self, hw: str, input_path: Path, output_path: Path, encoder_choice: str) -> Tuple[List[str], str, str]:
        """ffmpeg command for a GPU encode. hw = "videotoolbox" (Apple Media Engine) or "vaapi" (Linux: AMD / Intel;
        NVIDIA's VA-API driver only decodes). Returns (command, codec, quality value)."""
        res_limit = self.config["hb_resolution"]
        fps_mode = self.config["hb_fps"]
        audio_bitrate = f"{self.config['hb_audio_bitrate']}k"
        rf = float(self.config["hb_quality"])
        cmd = ["ffmpeg", "-hide_banner", "-y"]
        h264 = "h264" in encoder_choice or "x264" in encoder_choice
        scale = (f"scale=w='min(iw,{self.RES_MAP[res_limit][0]})':h='min(ih,{self.RES_MAP[res_limit][1]})'"
                 ":force_original_aspect_ratio=decrease:force_divisible_by=2") if res_limit in self.RES_MAP else ""
        q = str(int(round(rf)))
        if hw in ("nvenc", "amf", "qsv", "cpu"):
            cmd += ["-i", str(input_path)]
            if scale:
                cmd += ["-vf", scale]
            if hw == "nvenc":    # NVIDIA: constant quality (-cq is on the same scale as x264/x265 RF)
                codec = "h264_nvenc" if h264 else "hevc_nvenc"
                cmd += ["-c:v", codec, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", q, "-b:v", "0"]
            elif hw == "amf":    # AMD: constant QP
                codec = "h264_amf" if h264 else "hevc_amf"
                cmd += ["-c:v", codec, "-quality", "quality", "-rc", "cqp", "-qp_i", q, "-qp_p", q]
                if h264:
                    cmd += ["-qp_b", q]
            elif hw == "qsv":    # Intel Quick Sync
                codec = "h264_qsv" if h264 else "hevc_qsv"
                cmd += ["-c:v", codec, "-global_quality", q]
            else:                # ffmpeg's own x264 / x265 (CPU), used when HandBrake is not installed
                codec = "libx264" if h264 else "libx265"
                cmd += ["-c:v", codec, "-crf", q, "-preset", "medium"]
            quality = q
        elif hw == "vaapi":
            codec = "av1_vaapi" if "av1" in encoder_choice else ("h264_vaapi" if "h264" in encoder_choice else "hevc_vaapi")
            # constant QP; H.264/HEVC QP is on the same 0–51 scale as x264/x265 RF, AV1 uses a 0–255 index (≈ 4 × RF)
            quality = str(int(max(1, min(255, round(rf * 4))))) if codec == "av1_vaapi" else str(int(round(rf)))
            vf = ["format=nv12", "hwupload"]
            if res_limit in self.RES_MAP:
                max_w, max_h = self.RES_MAP[res_limit]
                vf.append(f"scale_vaapi=w='min(iw,{max_w})':h='min(ih,{max_h})':force_original_aspect_ratio=decrease")
            cmd += ["-vaapi_device", vaapi_device() or "/dev/dri/renderD128", "-i", str(input_path), "-vf", ",".join(vf),
                    "-c:v", codec, "-rc_mode", "CQP", "-qp", quality]
        else:
            # Apple Media Engine encodes H.264 / HEVC only (M3/M4 decode AV1 but cannot encode it).
            codec = "h264_videotoolbox" if "h264" in encoder_choice else "hevc_videotoolbox"
            if "av1" in encoder_choice:
                self.log("Apple Silicon has no hardware AV1 encoder; using HEVC VideoToolbox instead.", "WARN")
            quality = str(SystemProbe.rf_to_videotoolbox_q(rf))
            cmd += ["-hwaccel", "videotoolbox", "-i", str(input_path)]
            if res_limit in self.RES_MAP:
                max_w, max_h = self.RES_MAP[res_limit]
                cmd += ["-vf", f"scale=w='min(iw,{max_w})':h='min(ih,{max_h})':force_original_aspect_ratio=decrease:force_divisible_by=2"]
            cmd += ["-c:v", codec, "-q:v", quality]
        if codec.startswith("hevc") or codec == "libx265":
            cmd += ["-tag:v", "hvc1"]  # makes HEVC playable in QuickTime / Finder previews
        if fps_mode != "Same as source":
            cmd += ["-r", fps_mode.split()[0]]
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, str(output_path)]
        return cmd, codec, quality

    def run_ffmpeg_videotoolbox_encode_for_jobs(self, *args, **kwargs) -> Path:
        return self.run_ffmpeg_hw_encode_for_jobs(*args, hw="videotoolbox", **kwargs)

    def run_ffmpeg_hw_encode_for_jobs(
        self,
        input_path: Path,
        output_path: Path,
        encoder_choice: str,
        stage_label: str,
        base_progress: float,
        progress_span: float,
        job_ids: List[str],
        job_base_pct: float = 0.0,
        job_span_pct: float = 100.0,
        hw: str = "videotoolbox",
    ) -> Path:
        duration = self.probe_video_duration(input_path)
        cmd, vt_codec, vt_quality = self.build_hw_encode_command(hw, input_path, output_path, encoder_choice)
        hw_name = {"vaapi": "VA-API", "videotoolbox": "VideoToolbox", "nvenc": "NVENC", "amf": "AMF", "qsv": "Quick Sync",
                   "cpu": "ffmpeg (CPU)"}.get(hw, hw)
        engine = {"videotoolbox": "Media Engine", "cpu": "CPU"}.get(hw, "GPU")
        self.log(f"[{stage_label}] Executing {hw_name} hardware encode: {' '.join(cmd)}", "CMD")
        self.last_encode = {"tool": "ffmpeg", "encoder": vt_codec, "quality": vt_quality, "command": cmd}

        self.current_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        time_regex = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
        fps_regex = re.compile(r"fps=\s*([0-9.]+)")
        tail_lines: List[str] = []

        if self.current_proc.stdout:
            for raw_line in self.current_proc.stdout:
                if self.stop_event.is_set():
                    self.current_proc.terminate()
                    raise InterruptedError("Pipeline cancelled by user.")
                line = raw_line.strip()
                if line:
                    tail_lines.append(line)
                    if len(tail_lines) > 12:
                        tail_lines.pop(0)
                t_match = time_regex.search(line)
                if t_match and duration > 0:
                    hrs, mins, secs = (
                        float(t_match.group(1)),
                        float(t_match.group(2)),
                        float(t_match.group(3)),
                    )
                    elapsed_s = hrs * 3600.0 + mins * 60.0 + secs
                    pct = max(0.0, min(100.0, (elapsed_s / duration) * 100.0))
                    fps_m = fps_regex.search(line)
                    fps_str = fps_m.group(1) if fps_m else "--"
                    overall = base_progress + (pct / 100.0) * progress_span
                    job_pct = job_base_pct + (pct / 100.0) * job_span_pct
                    self.set_progress(
                        overall,
                        f"{stage_label} ({hw_name}): {pct:.1f}% | {fps_str} FPS",
                    )
                    for jid in job_ids:
                        self.update_job(
                            jid,
                            status="Running",
                            stage=f"{engine} {pct:.0f}% ({fps_str} fps)",
                            progress=job_pct,
                        )

        ret = self.current_proc.wait()
        self.current_proc = None
        if ret != 0:
            err_tail = "\n".join(tail_lines[-6:])
            hint = ("\nThis GPU may not encode AV1 (AMD RX 7000 and Intel Arc or newer do); choose vaapi_h265."
                    if vt_codec == "av1_vaapi" else
                    "\nThe GPU or its driver may not support this encoder: choose auto_h265 or x265 (CPU) in Settings ▸ Video."
                    if hw in ("nvenc", "amf", "qsv") else "")
            raise RuntimeError(f"FFmpeg {hw_name} exited with code {ret}:\n{err_tail}{hint}")

        return output_path

    def run_handbrake_encode_for_jobs(
        self,
        input_path: Path,
        output_path: Path,
        stage_label: str,
        base_progress: float,
        progress_span: float,
        job_ids: List[str],
        job_base_pct: float = 0.0,
        job_span_pct: float = 100.0,
    ) -> Path:
        # GUI values look like "vt_h265 (Apple Media Engine)"; the first token is the encoder id.
        requested_encoder = str(self.config["hb_encoder"]).split()[0]
        vt_kwargs = dict(
            input_path=input_path,
            output_path=output_path,
            encoder_choice=requested_encoder,
            stage_label=stage_label,
            base_progress=base_progress,
            progress_span=progress_span,
            job_ids=job_ids,
            job_base_pct=job_base_pct,
            job_span_pct=job_span_pct,
        )

        if requested_encoder.startswith("ffmpeg_"):
            return self.run_ffmpeg_videotoolbox_encode_for_jobs(**vt_kwargs)
        if requested_encoder.startswith(("auto_", "nvenc_", "amf_", "qsv_")):
            kind = "h264" if "h264" in requested_encoder else "h265"
            ffcodec = "h264" if kind == "h264" else "hevc"
            wanted = ["nvenc", "amf", "qsv"] if requested_encoder.startswith("auto_") else [requested_encoder.split("_")[0]]
            for hw in wanted:
                if SystemProbe.ffmpeg_encoder_works(f"{ffcodec}_{hw}"):
                    if requested_encoder.startswith("auto_"):
                        self.log(f"Video encoder: {ffcodec}_{hw} (this computer's GPU).", "INFO")
                    return self.run_ffmpeg_hw_encode_for_jobs(hw=hw, **dict(vt_kwargs, encoder_choice=f"{hw}_{kind}"))
            fallback = "x264" if kind == "h264" else "x265"
            what = "No GPU video encoder works on this computer" if requested_encoder.startswith("auto_") else \
                f"The {requested_encoder.split('_')[0].upper()} encoder does not work on this computer (other GPU, or an old driver)"
            self.log(f"{what}; encoding with {fallback} on the CPU instead.", "WARN")
            requested_encoder = fallback
        if requested_encoder.startswith("vaapi_"):
            if has_ffmpeg_vaapi():
                return self.run_ffmpeg_hw_encode_for_jobs(hw="vaapi", **vt_kwargs)
            fallback = {"vaapi_h265": "x265", "vaapi_h264": "x264", "vaapi_av1": "svt_av1"}.get(requested_encoder, "x265")
            self.log(f"No VA-API encoder available (no /dev/dri render node, or ffmpeg/driver without VA-API). "
                     f"Encoding with HandBrake '{fallback}' on the CPU instead.", "WARN")
            requested_encoder = fallback

        hb_bin = shutil.which("HandBrakeCLI")
        if not hb_bin:
            if requested_encoder.startswith("vt_") and SystemProbe.has_ffmpeg_videotoolbox():
                self.log("HandBrakeCLI not found. Routing to FFmpeg VideoToolbox (brew install handbrake to use HandBrake).", "WARN")
                return self.run_ffmpeg_videotoolbox_encode_for_jobs(**vt_kwargs)
            if requested_encoder in ("x265", "x264", "x265_10bit") and SystemProbe.ffmpeg_encoder_works("lib" + requested_encoder[:4]):
                self.log(f"HandBrakeCLI not found; encoding with ffmpeg's {requested_encoder[:4]} instead (same quality scale).", "INFO")
                return self.run_ffmpeg_hw_encode_for_jobs(hw="cpu", **dict(vt_kwargs, encoder_choice=requested_encoder))
            raise RuntimeError(f"HandBrakeCLI is not installed. Run: {INSTALL_HINTS['handbrake']}")

        supported_hb = SystemProbe.get_supported_hb_encoders()
        encoder = requested_encoder
        if requested_encoder.startswith("vt_") and requested_encoder not in supported_hb:
            if SystemProbe.has_ffmpeg_videotoolbox() and "10bit" not in requested_encoder:
                self.log(
                    f"This HandBrakeCLI build lacks '{requested_encoder}'. Routing to FFmpeg VideoToolbox instead.",
                    "WARN",
                )
                return self.run_ffmpeg_videotoolbox_encode_for_jobs(**vt_kwargs)
            fallback_map = {
                "vt_h265": "x265",
                "vt_h265_10bit": "x265_10bit",
                "vt_h264": "x264",
            }
            encoder = fallback_map.get(requested_encoder, "x265")
            self.log(
                f"HandBrakeCLI built without '{requested_encoder}'. Falling back to '{encoder}'.",
                "WARN",
            )

        if encoder.startswith("vt_"):
            # VideoToolbox CQ uses 0-100 (higher = better), unlike x264/x265 RF.
            quality = str(SystemProbe.rf_to_videotoolbox_q(self.config["hb_quality"]))
            self.log(f"RF {self.config['hb_quality']} mapped to VideoToolbox quality {quality}.", "INFO")
        else:
            quality = str(self.config["hb_quality"])
        res_limit = self.config["hb_resolution"]
        fps_mode = self.config["hb_fps"]
        audio_bitrate = str(self.config["hb_audio_bitrate"])

        cmd = [
            hb_bin,
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-e",
            encoder,
            "-q",
            quality,
            "--aencoder",
            "av_aac",
            "-B",
            audio_bitrate,
        ]

        res_map = {
            "2160p (4K)": ("3840", "2160"),
            "1440p (2K)": ("2560", "1440"),
            "1080p (FHD)": ("1920", "1080"),
            "720p (HD)": ("1280", "720"),
        }
        if res_limit in res_map:
            max_w, max_h = res_map[res_limit]
            cmd.extend(["--maxWidth", max_w, "--maxHeight", max_h])

        if fps_mode != "Same as source":
            fps_val = fps_mode.split()[0]
            cmd.extend(["-r", fps_val, "--pfr"])

        self.log(f"[{stage_label}] Executing: {' '.join(cmd)}", "CMD")
        self.last_encode = {"tool": "HandBrakeCLI", "encoder": encoder, "quality": quality, "command": cmd}

        self.current_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        hb_regex = re.compile(
            r"Encoding:\s*task\s*\d+\s*of\s*\d+,\s*([0-9.]+)\s*%(?:\s*\(([0-9.]+)\s*fps,\s*avg\s*([0-9.]+)\s*fps,\s*ETA\s*([0-9hms]+)\))?"
        )
        error_lines: List[str] = []

        if self.current_proc.stdout:
            for raw_line in self.current_proc.stdout:
                if self.stop_event.is_set():
                    self.current_proc.terminate()
                    raise InterruptedError("Pipeline cancelled by user.")
                line = raw_line.strip()
                if any(k in line.lower() for k in ("error", "invalid", "failed", "unknown")):
                    error_lines.append(line)
                match = hb_regex.search(line)
                if match:
                    pct = float(match.group(1))
                    fps = match.group(3) or match.group(2) or "--"
                    eta = match.group(4) or "--"
                    overall = base_progress + (pct / 100.0) * progress_span
                    job_pct = job_base_pct + (pct / 100.0) * job_span_pct
                    self.set_progress(
                        overall,
                        f"{stage_label}: {pct:.1f}% | {fps} FPS | ETA {eta}",
                    )
                    for jid in job_ids:
                        self.update_job(
                            jid,
                            status="Running",
                            stage=f"HandBrake {pct:.0f}% ({fps} fps)",
                            progress=job_pct,
                        )

        ret = self.current_proc.wait()
        self.current_proc = None
        if ret != 0:
            details = f" ({'; '.join(error_lines[-3:])})" if error_lines else ""
            raise RuntimeError(f"HandBrakeCLI exited with code {ret}{details}")

        return output_path

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

    def to_wav16k(self, src: Path, dst: Path) -> Path:
        self.run_checked(
            ["ffmpeg", "-hide_banner", "-y", "-i", str(src), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)],
            f"Converting {src.name} to 16 kHz WAV",
        )
        return dst

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

    def compression_record(self) -> Dict:
        rec = {
            "requested_encoder": self.config.get("hb_encoder"),
            "quality_rf": self.config.get("hb_quality"),
            "resolution_limit": self.config.get("hb_resolution"),
            "framerate": self.config.get("hb_fps"),
            "container": self.config.get("hb_container"),
            "audio_bitrate_kbps": self.config.get("hb_audio_bitrate"),
        }
        rec.update(self.last_encode or {})
        return rec

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
            self.run_handbrake_encode_for_jobs(
                intermediate_concat, final_merged, label,
                base_progress=18.0, progress_span=span, job_ids=job_ids, job_base_pct=18.0, job_span_pct=span,
            )
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
        if extracted is not None:
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
                        if steps["compress"]:
                            container_ext = ".mkv" if self.config.get("hb_container", "MKV") == "MKV" else ".mp4"
                            out_vid = out_dir / f"{base}_compressed{container_ext}"
                            self.run_handbrake_encode_for_jobs(
                                input_path=active_vid, output_path=out_vid, stage_label=label,
                                base_progress=job_base_overall + job_span_overall * 0.05, progress_span=job_span_overall * 0.45,
                                job_ids=[tid], job_base_pct=10.0, job_span_pct=45.0,
                            )
                            processing["compression"] = dict(self.compression_record(), file=out_vid.name)
                            active_vid = out_vid
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
