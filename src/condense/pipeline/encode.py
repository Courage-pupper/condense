"""Video compression for the pipeline worker: HandBrake, or ffmpeg with a hardware or software encoder."""
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from condense.core.util import *  # noqa: F401,F403
from condense.platform import INSTALL_HINTS, SystemProbe, has_ffmpeg_vaapi, vaapi_device


class EncodeMixin:
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
