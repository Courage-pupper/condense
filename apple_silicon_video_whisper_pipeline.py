#!/usr/bin/env python3
"""
Apple Silicon (M1/M2/M3/M4) Video & Audio Pipeline for macOS
=============================================================
Port of the AMD Radeon RX 7900 XTX / Ubuntu / ROCm pipeline.

A complete GUI & CLI pipeline that integrates:
  1. Merge Videos (HandBrakeCLI / FFmpeg with optional LosslessCut .llc trim support)
  2. Compress Videos (HandBrakeCLI or FFmpeg with Apple VideoToolbox / Media Engine hardware encoding & live telemetry)
  3. Extract Audio (FFmpeg with speech-optimized WAV/FLAC/MP3/M4A & voice filtering)
  4. Transcribe Audio (Whisper on Apple MLX (GPU) or CPU with SRT, TXT & Colored RTF Confidence Intervals,
     speaker diarization on PyTorch Metal/MPS)
  5. Interactive Transcript & Speaker Editor (One-click speaker renaming, jump to low-confidence words, audio playback)

Setup (Homebrew):
  brew install ffmpeg handbrake python@3.12 python-tk@3.12
  /opt/homebrew/bin/python3.12 -m venv ~/mlx_whisper_env
  ~/mlx_whisper_env/bin/pip install mlx-whisper openai-whisper torch numpy
  # optional neural diarization:  ~/mlx_whisper_env/bin/pip install pyannote.audio
  ~/mlx_whisper_env/bin/python apple_silicon_video_whisper_pipeline.py
"""

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

# Apps launched from Finder/Dock get a minimal PATH without Homebrew; make ffmpeg/HandBrakeCLI findable.
_HOMEBREW_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
_cur_path = os.environ.get("PATH", "").split(os.pathsep)
os.environ["PATH"] = os.pathsep.join([p for p in _HOMEBREW_PATHS if p not in _cur_path] + _cur_path)

# Let PyTorch silently fall back to CPU for any op not yet implemented on Metal (MPS).
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

if sys.platform != "darwin":
    print("Warning: this build of the pipeline targets macOS on Apple Silicon.", file=sys.stderr)


class MacAppInstaller:
    ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#181b24"/>
      <stop offset="100%" stop-color="#0f1117"/>
    </linearGradient>
    <linearGradient id="crimson" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#f43f5e"/>
      <stop offset="100%" stop-color="#be123c"/>
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="26" fill="url(#bg)" stroke="#e11d48" stroke-width="4"/>
  <polygon points="46,36 46,92 94,64" fill="url(#crimson)"/>
  <circle cx="96" cy="96" r="18" fill="#10b981"/>
  <path d="M88 96 L94 102 L105 90" stroke="#ffffff" stroke-width="4" fill="none" stroke-linecap="round"/>
</svg>"""

    APP_NAME = "Silicon Video Pipeline"
    BUNDLE_ID = "local.silicon-video-pipeline"
    _best_python_cache: Optional[str] = None

    @staticmethod
    def _python_candidates() -> List[Path]:
        home = Path.home()
        script_dir = Path(__file__).resolve().parent
        return [
            home / "mlx_whisper_env" / "bin" / "python3",
            script_dir / "venv" / "bin" / "python3",
            script_dir / ".venv" / "bin" / "python3",
            home / ".venv" / "bin" / "python3",
            Path(sys.executable),
            Path("/opt/homebrew/bin/python3"),
        ]

    @classmethod
    def find_best_python(cls) -> str:
        """Prefer a Python that has mlx_whisper (GPU), then one with openai-whisper (CPU). Cached."""
        if cls._best_python_cache:
            return cls._best_python_cache
        candidates = [c for c in cls._python_candidates() if c.exists()]
        for module in ("mlx_whisper", "whisper"):
            for candidate in candidates:
                try:
                    res = subprocess.run(
                        [str(candidate), "-c", f"import {module}"],
                        capture_output=True,
                        timeout=8,
                    )
                    if res.returncode == 0:
                        cls._best_python_cache = str(candidate)
                        return cls._best_python_cache
                except Exception:
                    pass
        return sys.executable

    @staticmethod
    def _build_icns(svg_path: Path, icns_path: Path) -> bool:
        """Render the SVG with Quick Look, then build an .icns with sips + iconutil (all ship with macOS)."""
        if not all(shutil.which(t) for t in ("qlmanage", "sips", "iconutil")):
            return False
        try:
            with tempfile.TemporaryDirectory() as td:
                tdp = Path(td)
                subprocess.run(
                    ["qlmanage", "-t", "-s", "1024", "-o", str(tdp), str(svg_path)],
                    capture_output=True,
                    timeout=30,
                )
                png = tdp / f"{svg_path.name}.png"
                if not png.exists():
                    return False
                iconset = tdp / "AppIcon.iconset"
                iconset.mkdir()
                for size in (16, 32, 128, 256, 512):
                    for scale in (1, 2):
                        px = size * scale
                        suffix = "@2x" if scale == 2 else ""
                        subprocess.run(
                            ["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / f"icon_{size}x{size}{suffix}.png")],
                            capture_output=True,
                            timeout=15,
                        )
                subprocess.run(
                    ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
                    capture_output=True,
                    timeout=30,
                )
            return icns_path.exists()
        except Exception:
            return False

    @classmethod
    def install_shortcut(cls) -> Dict[str, Optional[str]]:
        """Create ~/Applications/<APP_NAME>.app (Launchpad/Spotlight/Dock-able) plus a Desktop link."""
        script_path = Path(__file__).resolve()
        os.chmod(script_path, 0o755)

        home = Path.home()
        app_path = home / "Applications" / f"{cls.APP_NAME}.app"
        macos_dir = app_path / "Contents" / "MacOS"
        res_dir = app_path / "Contents" / "Resources"
        macos_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(parents=True, exist_ok=True)

        svg_path = res_dir / "AppIcon.svg"
        svg_path.write_text(cls.ICON_SVG, encoding="utf-8")
        has_icon = cls._build_icns(svg_path, res_dir / "AppIcon.icns")

        best_py = cls.find_best_python()
        log_path = home / "Library" / "Logs" / "silicon_video_pipeline.log"
        launcher = macos_dir / "launcher"
        launcher.write_text(
            "#!/bin/bash\n"
            'export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"\n'
            "export PYTORCH_ENABLE_MPS_FALLBACK=1\n"
            f'exec "{best_py}" "{script_path}" "$@" >> "{log_path}" 2>&1\n',
            encoding="utf-8",
        )
        os.chmod(launcher, 0o755)

        info = {
            "CFBundleName": cls.APP_NAME,
            "CFBundleDisplayName": cls.APP_NAME,
            "CFBundleIdentifier": cls.BUNDLE_ID,
            "CFBundleExecutable": "launcher",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        }
        if has_icon:
            info["CFBundleIconFile"] = "AppIcon"
        with open(app_path / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump(info, fh)

        # Ask Finder / LaunchServices to notice the new bundle (and its icon).
        lsregister = Path(
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/Support/lsregister"
        )
        for cmd in (["touch", str(app_path)], [str(lsregister), "-f", str(app_path)]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=10)
            except Exception:
                pass

        desktop_link = None
        desktop_dir = home / "Desktop"
        if desktop_dir.exists():
            link = desktop_dir / f"{cls.APP_NAME}.app"
            try:
                if not link.exists() and not link.is_symlink():
                    link.symlink_to(app_path)
                desktop_link = str(link)
            except Exception:
                pass

        return {"app": str(app_path), "desktop_icon": desktop_link, "log": str(log_path)}


class SystemProbe:
    @staticmethod
    def _sysctl(key: str) -> str:
        try:
            res = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3)
            return res.stdout.strip()
        except Exception:
            return ""

    @classmethod
    def detect_gpu(cls) -> Dict[str, str]:
        chip = cls._sysctl("machdep.cpu.brand_string") or platform.processor() or "Unknown Mac"
        gpu_cores = ""
        try:
            res = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            displays = json.loads(res.stdout).get("SPDisplaysDataType", [])
            if displays:
                gpu_cores = str(displays[0].get("sppci_cores", "") or "")
        except Exception:
            pass
        gpu_name = f"{chip} ({gpu_cores}-core GPU)" if gpu_cores else chip
        is_apple_silicon = "Apple" in chip

        best_py = MacAppInstaller.find_best_python()
        mlx_ok = mps_ok = False
        py_arch = ""
        try:
            probe = (
                "import importlib.util, platform\n"
                "mlx = importlib.util.find_spec('mlx_whisper') is not None\n"
                "mps = False\n"
                "try:\n"
                "    import torch; mps = bool(torch.backends.mps.is_available())\n"
                "except Exception:\n"
                "    pass\n"
                "print(int(mlx), int(mps), platform.machine())\n"
            )
            res = subprocess.run([best_py, "-c", probe], capture_output=True, text=True, timeout=20)
            parts = res.stdout.strip().split()
            if res.returncode == 0 and len(parts) == 3:
                mlx_ok, mps_ok, py_arch = parts[0] == "1", parts[1] == "1", parts[2]
        except Exception:
            pass

        if not is_apple_silicon:
            accel_status = "Intel Mac: MLX / Metal ML acceleration unavailable (CPU only)"
        elif py_arch == "x86_64":
            accel_status = "Python runs under Rosetta (x86_64): install an arm64 Python for MLX/MPS"
        else:
            bits = [
                "MLX Whisper Ready" if mlx_ok else "MLX Whisper missing (pip install mlx-whisper)",
                "PyTorch MPS Ready" if mps_ok else "PyTorch MPS unavailable",
            ]
            accel_status = "  •  ".join(bits)

        warning = ""
        if not is_apple_silicon or py_arch == "x86_64":
            warning = accel_status
        return {
            "gpu_name": gpu_name,
            "accel_status": accel_status,
            "media_engine": "Apple VideoToolbox (H.264 / HEVC)",
            "mlx_ok": mlx_ok,
            "mps_ok": mps_ok,
            "warning": warning,
        }

    @staticmethod
    def find_losslesscut() -> Optional[str]:
        for app in (Path("/Applications/LosslessCut.app"), Path.home() / "Applications" / "LosslessCut.app"):
            if app.exists():
                return str(app)
        for cmd in ("losslesscut", "lossless-cut"):
            path = shutil.which(cmd)
            if path:
                return path
        return None

    @classmethod
    def check_dependencies(cls) -> Dict[str, Optional[str]]:
        hb_bin = shutil.which("HandBrakeCLI")
        ffmpeg_bin = shutil.which("ffmpeg")
        llc_cmd = cls.find_losslesscut()

        whisper_ok = None
        best_py = MacAppInstaller.find_best_python()
        for module, label in (("mlx_whisper", "MLX"), ("whisper", "openai-whisper")):
            try:
                res = subprocess.run([best_py, "-c", f"import {module}"], capture_output=True, timeout=8)
                if res.returncode == 0:
                    whisper_ok = f"{label} ({best_py})"
                    break
            except Exception:
                pass

        return {
            "losslesscut": llc_cmd,
            "handbrake": hb_bin,
            "ffmpeg": ffmpeg_bin,
            "whisper": whisper_ok,
        }

    @staticmethod
    def get_supported_hb_encoders() -> List[str]:
        hb_bin = shutil.which("HandBrakeCLI")
        if not hb_bin:
            return []
        try:
            res = subprocess.run(
                [hb_bin, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            known = [
                "vt_h265",
                "vt_h265_10bit",
                "vt_h264",
                "svt_av1",
                "svt_av1_10bit",
                "x265",
                "x265_10bit",
                "x264",
                "VP9",
            ]
            return [enc for enc in known if enc in output]
        except Exception:
            return []

    @staticmethod
    def has_ffmpeg_videotoolbox() -> bool:
        if not shutil.which("ffmpeg"):
            return False
        try:
            res = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            return "hevc_videotoolbox" in res.stdout or "h264_videotoolbox" in res.stdout
        except Exception:
            return False

    @staticmethod
    def rf_to_videotoolbox_q(rf: float) -> int:
        """Map an x264/x265-style RF (lower = better) to VideoToolbox's 1-100 quality (higher = better).
        Rough equivalence: RF 14 -> 80, RF 22 -> 64, RF 32 -> 44."""
        return int(max(1, min(100, round(108 - 2 * float(rf)))))


class RTFConfidenceWriter:
    @staticmethod
    def _escape_rtf(text: str) -> str:
        out = []
        for ch in text:
            code = ord(ch)
            if ch == "\\":
                out.append("\\\\")
            elif ch == "{":
                out.append("\\{")
            elif ch == "}":
                out.append("\\}")
            elif ch == "\n":
                out.append("\\line ")
            elif 32 <= code <= 126:
                out.append(ch)
            else:
                # RTF \uN takes signed 16-bit values; characters outside the BMP (e.g. emoji) need a surrogate pair.
                units = [code] if code <= 0xFFFF else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
                for u in units:
                    signed = u if u < 32768 else u - 65536
                    out.append(f"\\u{signed}?")
        return "".join(out)

    @staticmethod
    def format_timestamp(seconds: float, srt_style: bool = False) -> str:
        # Work in whole milliseconds so rounding can never produce "00:00:60,000".
        total_ms = int(round(max(0.0, float(seconds)) * 1000))
        hrs, rem = divmod(total_ms, 3_600_000)
        mins, rem = divmod(rem, 60_000)
        secs, millis = divmod(rem, 1000)
        sep = "," if srt_style else "."
        return f"{hrs:02d}:{mins:02d}:{secs:02d}{sep}{millis:03d}"

    @staticmethod
    def compute_confidence_interval(probs: List[float]) -> Tuple[float, float, float, float]:
        clean = [max(0.0, min(1.0, float(p))) for p in probs if p is not None]
        if not clean:
            return (0.0, 0.0, 0.0, 0.0)
        mean_val = statistics.mean(clean)
        if len(clean) < 2:
            return (mean_val, mean_val, mean_val, 0.0)
        stdev_val = statistics.stdev(clean)
        se = stdev_val / math.sqrt(len(clean))
        margin = 1.96 * se
        ci_low = max(0.0, mean_val - margin)
        ci_high = min(1.0, mean_val + margin)
        return (mean_val, ci_low, ci_high, margin)

    @classmethod
    def write_srt(cls, segments: List[Dict], output_path: Path) -> None:
        lines = []
        for idx, seg in enumerate((s for s in segments if not is_blank_text(s.get("text"))), start=1):
            start_ts = cls.format_timestamp(seg.get("start", 0.0), srt_style=True)
            end_ts = cls.format_timestamp(seg.get("end", 0.0), srt_style=True)
            text = (seg.get("text") or "").strip()
            speaker = seg.get("speaker")
            prefix = f"[{speaker}] " if speaker else ""
            lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{prefix}{text}\n")
        output_path.write_text("\n".join(lines), encoding="utf-8")

    @classmethod
    def write_vtt(cls, segments: List[Dict], output_path: Path) -> None:
        lines = ["WEBVTT\n"]
        for seg in segments:
            if is_blank_text(seg.get("text")):
                continue
            start_ts = cls.format_timestamp(seg.get("start", 0.0), srt_style=False)
            end_ts = cls.format_timestamp(seg.get("end", 0.0), srt_style=False)
            text = (seg.get("text") or "").strip()
            speaker = seg.get("speaker")
            prefix = f"<v {speaker}>" if speaker else ""
            lines.append(f"{start_ts} --> {end_ts}\n{prefix}{text}\n")
        output_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _hms(seconds: float) -> str:
        total = int(max(0.0, float(seconds)))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    @classmethod
    def write_paragraphs_txt(cls, paragraphs: List[Dict], output_path: Path) -> None:
        """One paragraph per speaker turn: 'Speaker: text [hh:mm:ss]'. The speaker-colon start is what
        MAXQDA's focus-group import looks for; the [hh:mm:ss] at the end of each paragraph (end of the turn)
        is a timestamp format MAXQDA links to the media file."""
        blocks = []
        for p in paragraphs:
            spk = f"{p['speaker']}: " if p.get("speaker") else ""
            blocks.append(f"{spk}{p['text']} [{cls._hms(p['end'])}]")
        output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    @classmethod
    def write_paragraphs_rtf(cls, paragraphs: List[Dict], output_path: Path, title: str) -> None:
        """Clean reading/analysis copy for Word, Pages or MAXQDA: no colours, one paragraph per turn."""
        parts = [
            r"{\rtf1\ansi\deff0{\fonttbl{\f0\fswiss Helvetica Neue;}}{\colortbl;\red110\green110\blue110;}",
            r"\viewkind4\uc1\pard\sa240\sl300\slmult1\f0\fs22",
            f"\\b\\fs28 {cls._escape_rtf(title)}\\b0\\fs22\\par",
        ]
        for p in paragraphs:
            spk = f"\\b {cls._escape_rtf(p['speaker'])}:\\b0  " if p.get("speaker") else ""
            parts.append(f"{spk}{cls._escape_rtf(p['text'])} \\cf1 [{cls._hms(p['end'])}]\\cf0\\par")
        parts.append("}")
        output_path.write_text("\n".join(parts), encoding="utf-8")

    @classmethod
    def write_txt(cls, segments: List[Dict], output_path: Path) -> None:
        out_lines = []
        last_speaker = None
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if is_blank_text(text):
                continue
            speaker = seg.get("speaker")
            if speaker:
                if speaker != last_speaker:
                    out_lines.append(f"\n[{speaker}]")
                    last_speaker = speaker
                out_lines.append(text)
            else:
                out_lines.append(text)
        output_path.write_text("\n".join(out_lines).lstrip() + "\n", encoding="utf-8")

    @classmethod
    def analyze_confidence(
        cls,
        result_data: Dict,
        high_thresh: float = 0.85,
        med_thresh: float = 0.60,
        poor_thresh: float = 0.40,
    ) -> Dict:
        """Per-word / per-segment confidence summary used by the RTF export and the transcript editor."""
        segments = result_data.get("segments", [])
        all_probs: List[float] = []
        high_count = med_count = low_count = poor_count = 0

        enriched_segments = []
        for seg in segments:
            words = seg.get("words") or []
            seg_probs = []
            if words:
                for w in words:
                    p = float(w.get("probability", w.get("prob", 0.85)))
                    seg_probs.append(p)
                    all_probs.append(p)
                    if p >= high_thresh:
                        high_count += 1
                    elif p >= med_thresh:
                        med_count += 1
                    elif p >= poor_thresh:
                        low_count += 1
                    else:
                        poor_count += 1
            else:
                avg_lp = float(seg.get("avg_logprob", -0.25))
                est_p = max(0.0, min(1.0, math.exp(avg_lp)))
                seg_probs.append(est_p)
                all_probs.append(est_p)

            enriched_segments.append(
                {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "text": (seg.get("text") or "").strip(),
                    "speaker": seg.get("speaker"),
                    "words": words,
                    "ci": cls.compute_confidence_interval(seg_probs),
                }
            )

        doc_mean, doc_low, doc_high, doc_margin = cls.compute_confidence_interval(all_probs)
        return {
            "overall_ci": (doc_mean, doc_low, doc_high, doc_margin),
            "counts": {"high": high_count, "medium": med_count, "low": low_count, "poor": poor_count},
            "segments": enriched_segments,
            "high_thresh": high_thresh,
            "med_thresh": med_thresh,
            "poor_thresh": poor_thresh,
            "diarization_method": result_data.get("diarization_method", "Disabled"),
        }

    @classmethod
    def write_rtf_with_confidence(
        cls,
        result_data: Dict,
        output_path: Path,
        source_name: str,
        model_name: str,
        high_thresh: float = 0.85,
        med_thresh: float = 0.60,
        poor_thresh: float = 0.40,
    ) -> Dict:
        report = cls.analyze_confidence(result_data, high_thresh, med_thresh, poor_thresh)
        doc_mean, doc_low, doc_high, doc_margin = report["overall_ci"]
        c = report["counts"]
        high_count, med_count, low_count, poor_count = c["high"], c["medium"], c["low"], c["poor"]
        total_words = max(1, high_count + med_count + low_count + poor_count)
        diarization_method = report["diarization_method"]
        backend_label = result_data.get("backend_label") or "Apple Silicon"

        # Color table: cf1=Slate, cf2=Green, cf3=Amber, cf4=Orange, cf5=Red, cf6=Grey, cf7=Blue, cf8=Purple
        rtf_parts = [
            r"{\rtf1\ansi\deff0",
            r"{\fonttbl{\f0\fswiss Helvetica Neue;}{\f1\fmodern Menlo;}}",
            r"{\colortbl;"
            r"\red30\green41\blue59;"
            r"\red21\green128\blue61;"
            r"\red180\green83\blue9;"
            r"\red217\green119\blue6;"
            r"\red220\green38\blue38;"
            r"\red100\green116\blue139;"
            r"\red37\green99\blue235;"
            r"\red124\green58\blue237;}",
            r"\viewkind4\uc1\pard\sa160\sl276\slmult1",
            r"\f0\fs32\b\cf1 Whisper Transcription, Diarization & Confidence Report\b0\fs22\par",
            f"\\cf6\\b Source File:\\b0  {cls._escape_rtf(source_name)}\\line "
            f"\\b Whisper Model:\\b0  {cls._escape_rtf(model_name)} ({cls._escape_rtf(backend_label)})\\line "
            f"\\b Speaker Diarization:\\b0  {cls._escape_rtf(diarization_method)}\\line "
            f"\\b Generated:\\b0  {cls._escape_rtf(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\\line "
            f"\\b Overall 95% Confidence Interval:\\b0  "
            f"\\cf7\\b {doc_mean * 100:.1f}% \\u177? {doc_margin * 100:.1f}% "
            f"[{doc_low * 100:.1f}% - {doc_high * 100:.1f}%]\\cf6\\b0\\par",
            r"\pard\sa180\sl260\slmult1\f0\fs20"
            f"\\cf1\\b Confidence Color Legend:\\b0\\line "
            f"\\cf2\\b [HIGH >= {int(high_thresh * 100)}%]\\b0  ({high_count} words / {high_count * 100 / total_words:.1f}%)   "
            f"\\cf3\\b [MEDIUM {int(med_thresh * 100)}-{int(high_thresh * 100)}%]\\b0  ({med_count} words / {med_count * 100 / total_words:.1f}%)   "
            f"\\cf4\\b [LOW {int(poor_thresh * 100)}-{int(med_thresh * 100)}%]\\b0  ({low_count} words / {low_count * 100 / total_words:.1f}%)   "
            f"\\cf5\\ul\\b [POOR < {int(poor_thresh * 100)}%]\\b0\\ulnone  ({poor_count} words / {poor_count * 100 / total_words:.1f}%)\\par",
            r"\pard\sa200\sl276\slmult1",
        ]

        for seg in report["segments"]:
            t_start = cls.format_timestamp(seg["start"], srt_style=False)
            t_end = cls.format_timestamp(seg["end"], srt_style=False)
            s_mean, s_low, s_high, s_margin = seg["ci"]
            speaker = seg.get("speaker")
            spk_rtf = f"\\cf8\\b [{cls._escape_rtf(speaker)}]\\b0  " if speaker else ""

            if s_mean >= high_thresh:
                ci_color = r"\cf2"
            elif s_mean >= med_thresh:
                ci_color = r"\cf3"
            elif s_mean >= poor_thresh:
                ci_color = r"\cf4"
            else:
                ci_color = r"\cf5"

            rtf_parts.append(
                f"\\f1\\fs18 {spk_rtf}\\cf6 [{t_start} -> {t_end}] "
                f"{ci_color}\\b (95% CI: {s_mean * 100:.1f}% \\u177? {s_margin * 100:.1f}% "
                f"[{s_low * 100:.0f}%-{s_high * 100:.0f}%])\\b0\\cf1\\f0\\fs22\\line "
            )

            words = seg["words"]
            if words:
                word_tokens_rtf = []
                for w in words:
                    escaped = cls._escape_rtf(str(w.get("word", "")))
                    prob = float(w.get("probability", w.get("prob", 0.85)))
                    if prob >= high_thresh:
                        word_tokens_rtf.append(f"\\cf2 {escaped}\\cf1")
                    elif prob >= med_thresh:
                        word_tokens_rtf.append(f"\\cf3 {escaped}\\cf1")
                    elif prob >= poor_thresh:
                        word_tokens_rtf.append(f"\\cf4 {escaped}\\cf1")
                    else:
                        word_tokens_rtf.append(
                            f"\\cf5\\ul\\b {escaped}\\b0\\ulnone\\cf1"
                        )
                rtf_parts.append("".join(word_tokens_rtf).lstrip() + r"\par")
            else:
                rtf_parts.append(f"{ci_color}{cls._escape_rtf(seg['text'])}\\cf1\\par")

        rtf_parts.append("}")
        output_path.write_text("\n".join(rtf_parts), encoding="utf-8")
        return report


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_BLANK_CHARS = re.compile(r"[\s.…,;:!?\-–—♪♫*\"'()\[\]]+")


def is_blank_text(text: Optional[str]) -> bool:
    """True for '', whitespace, or only dots/dashes/music notes ('...', '…', '♪') — what Whisper emits in silence."""
    return not _BLANK_CHARS.sub("", text or "")


def sync_text_with_words(segments: List[Dict]) -> int:
    """Make each segment's text exactly its timed words. Whisper sometimes stores segment text that doesn't
    match the words it timed (e.g. shifted by part of a segment). The words carry the audio timing, so they win;
    a differing original is kept in seg["text_whisper"]. Returns how many segments were changed."""
    changed = 0
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            continue
        joined = "".join(str(w.get("word", "")) for w in words)
        if " ".join(joined.split()) != " ".join((seg.get("text") or "").split()):
            seg.setdefault("text_whisper", seg.get("text"))
            seg["text"] = joined
            changed += 1
    return changed


def drop_empty_segments(segments: List[Dict]) -> Tuple[List[Dict], int]:
    """Remove segments that contain no actual words (Whisper's zero-length 'silence' segments)."""
    kept: List[Dict] = []
    for seg in segments:
        words = seg.get("words") or []
        if is_blank_text(seg.get("text")) and all(is_blank_text(w.get("word")) for w in words):
            continue
        kept.append(seg)
    return kept, len(segments) - len(kept)


def short_error(exc: BaseException) -> str:
    """One-line summary for the queue table: 'what failed - tool's last error line'."""
    lines = [l.strip() for l in str(exc).splitlines() if l.strip()]
    if not lines:
        return type(exc).__name__
    head = lines[0].rstrip(":")
    return head if len(lines) == 1 else f"{head} - {lines[-1]}"


def is_within(path: Path, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def ffconcat_line(path: Path) -> str:
    """One line of an ffmpeg concat list; escapes apostrophes (e.g. "O'Brien.mp4")."""
    return "file '" + Path(path).resolve().as_posix().replace("'", "'\\''") + "'"


def sha256_file(path: Path, stop_event: Optional[threading.Event] = None) -> Optional[str]:
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Pipeline cancelled by user.")
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


_TOOL_VERSION_CACHE: Dict[str, Optional[str]] = {}


def tool_version(binary: str, flag: str = "-version") -> Optional[str]:
    if binary in _TOOL_VERSION_CACHE:
        return _TOOL_VERSION_CACHE[binary]
    version = None
    exe = shutil.which(binary)
    if exe:
        try:
            res = subprocess.run([exe, flag], capture_output=True, text=True, timeout=10)
            lines = [l.strip() for l in ((res.stdout or "") + (res.stderr or "")).splitlines() if l.strip()]
            version = lines[0] if lines else None
        except Exception:
            pass
    _TOOL_VERSION_CACHE[binary] = version
    return version


# ---------------------------------------------------------------------------------------------
# Master transcript record
# ---------------------------------------------------------------------------------------------
class TranscriptDocument:
    """One transcript = one '<base>.transcript.json'. It holds the full Whisper result (word timings,
    probabilities, speakers), the export settings, how the transcript was produced (provenance) and an
    edit history. Every export (SRT/VTT/TXT/RTF) is regenerated from this file, so they never drift apart."""

    SCHEMA_VERSION = 1
    SUFFIX = ".transcript.json"
    DEFAULT_FORMATS = {"srt": True, "vtt": False, "txt": True, "rtf": True, "para_rtf": True, "para_txt": False}
    DEFAULT_THRESHOLDS = {"high": 0.85, "medium": 0.60, "poor": 0.40}

    def __init__(self, path: Path, data: Dict):
        self.path = Path(path)
        self.data = data

    # ----- construction / IO -----
    @classmethod
    def create(
        cls,
        out_dir: Path,
        base_name: str,
        whisper_result: Dict,
        model_name: str,
        export_formats: Dict[str, bool],
        thresholds: Dict[str, float],
        media: Dict[str, Optional[str]],
        provenance: Dict,
    ) -> "TranscriptDocument":
        path = Path(out_dir) / f"{base_name}{cls.SUFFIX}"
        doc = cls(
            path,
            {
                "schema": cls.SCHEMA_VERSION,
                "base_name": base_name,
                "created": now_iso(),
                "updated": now_iso(),
                "model": model_name,
                "export_formats": dict(cls.DEFAULT_FORMATS, **export_formats),
                "confidence_thresholds": dict(cls.DEFAULT_THRESHOLDS, **thresholds),
                "media": {},
                "provenance": provenance,
                "edit_history": [],
                "whisper_result": whisper_result,
            },
        )
        for key, value in media.items():
            doc.set_media(key, value)
        return doc

    @classmethod
    def load(cls, path: Path) -> "TranscriptDocument":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "whisper_result" not in data:
            raise ValueError(f"{path.name} is not a pipeline transcript file.")
        if int(data.get("schema", 1)) > cls.SCHEMA_VERSION:
            raise ValueError(f"{path.name} was written by a newer version of the pipeline.")
        return cls(path, data)

    BACKUP_EVERY_S = 300  # at most one backup per 5 minutes of editing
    BACKUPS_KEPT = 30

    @property
    def backup_dir(self) -> Path:
        return self.out_dir / "_backups"

    def list_backups(self) -> List[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob(f"{self.base_name}.*{self.SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _maybe_backup(self) -> Optional[Path]:
        """Copy the file as it is on disk (before this save) into _backups/, throttled and pruned."""
        if not self.path.exists():
            return None
        existing = self.list_backups()
        if existing and time.time() - existing[0].stat().st_mtime < self.BACKUP_EVERY_S:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dst = self.backup_dir / f"{self.base_name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}{self.SUFFIX}"
        shutil.copy2(self.path, dst)
        for old in self.list_backups()[self.BACKUPS_KEPT:]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    def save(self, backup: bool = False) -> None:
        if backup:
            try:
                self._maybe_backup()
            except Exception:
                pass  # a failed backup must never block saving the work itself
        self.data["updated"] = now_iso()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written master file

    # ----- accessors -----
    @property
    def base_name(self) -> str:
        return self.data.get("base_name") or self.path.name[: -len(self.SUFFIX)]

    @property
    def out_dir(self) -> Path:
        return self.path.parent

    @property
    def result(self) -> Dict:
        return self.data["whisper_result"]

    @property
    def segments(self) -> List[Dict]:
        return self.result.setdefault("segments", [])

    @property
    def thresholds(self) -> Dict[str, float]:
        return dict(self.DEFAULT_THRESHOLDS, **(self.data.get("confidence_thresholds") or {}))

    def set_media(self, key: str, value: Optional[str]) -> None:
        """Store media paths relative to the transcript folder when possible, so the folder can be moved."""
        if not value:
            self.data["media"][key] = None
            return
        p = Path(value)
        self.data["media"][key] = os.path.relpath(p, self.out_dir) if is_within(p, self.out_dir) else str(p.resolve())

    def media_path(self, key: str) -> Optional[Path]:
        value = (self.data.get("media") or {}).get(key)
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else (self.out_dir / p)

    def roster(self) -> List[str]:
        """All speakers of this transcript, in a stable order: the saved list (including speakers you added
        who have no segments yet), then any others in order of first appearance."""
        names = [n for n in (self.data.get("speakers") or []) if n]
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk and spk not in names:
                names.append(spk)
        return names

    def add_speaker(self, name: str) -> bool:
        name = name.strip()
        roster = self.roster()
        if not name or name in roster:
            return False
        self.data["speakers"] = roster + [name]
        self.log_edit("add_speaker", name=name)
        return True

    def remove_speaker(self, name: str) -> bool:
        """Only speakers without segments can be removed (reassign their segments first)."""
        if name in self.speakers():
            return False
        roster = self.roster()
        if name not in roster:
            return False
        self.data["speakers"] = [n for n in roster if n != name]
        self.log_edit("remove_speaker", name=name)
        return True

    def speakers(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk:
                counts[spk] = counts.get(spk, 0) + 1
        return counts

    def export_paths(self) -> Dict[str, Path]:
        b, d = self.base_name, self.out_dir
        return {
            "srt": d / f"{b}.srt",
            "vtt": d / f"{b}.vtt",
            "txt": d / f"{b}.txt",
            "rtf": d / f"{b}_confidence.rtf",
            "para_rtf": d / f"{b}_paragraphs.rtf",
            "para_txt": d / f"{b}_paragraphs.txt",
        }

    # ----- edits -----
    def log_edit(self, action: str, **details) -> None:
        self.data.setdefault("edit_history", []).append({"at": now_iso(), "action": action, **details})

    def rename_speaker(self, old: str, new: str) -> int:
        count = 0
        for seg in self.segments:
            if seg.get("speaker") == old:
                seg["speaker"] = new
                count += 1
        if count:
            self.log_edit("rename_speaker", old=old, new=new, segments=count)
        return count

    def rename_speakers(self, mapping: Dict[str, str]) -> int:
        """Apply several renames at once (so swaps like A->B, B->A work). Returns segments changed."""
        mapping = {o: n for o, n in mapping.items() if n and n != o}
        if not mapping:
            return 0
        count = 0
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk in mapping:
                seg["speaker"] = mapping[spk]
                count += 1
        roster = self.roster()
        new_roster: List[str] = []
        for n in roster:
            n = mapping.get(n, n)
            if n not in new_roster:
                new_roster.append(n)
        renamed_unused = any(o in roster and o not in self.speakers() for o in mapping)
        self.data["speakers"] = new_roster
        if count or renamed_unused:
            self.log_edit("rename_speakers", mapping=mapping, segments=count)
        return count or (1 if renamed_unused else 0)

    @staticmethod
    def _rebuild_text(seg: Dict) -> None:
        words = seg.get("words") or []
        if words:
            seg["text"] = "".join(str(w.get("word", "")) for w in words)
            seg["start"] = float(words[0].get("start", seg.get("start", 0.0)))
            seg["end"] = float(words[-1].get("end", seg.get("end", 0.0)))

    def edit_segment_text(self, index: int, new_text: str) -> bool:
        """Replace a segment's text. Words that are unchanged keep their Whisper timing and probability;
        changed/inserted words get timings interpolated inside the span they replace, probability 1.0
        (checked by a person) and "edited": true. Returns False when nothing changed."""
        import difflib

        seg = self.segments[index]
        old_text = (seg.get("text") or "").strip()
        tokens = re.findall(r"\S+", new_text)
        if " ".join(tokens) == " ".join(old_text.split()):
            return False
        old_words = seg.get("words") or []
        if not old_words:
            seg["text"] = " " + " ".join(tokens)
            seg["edited"] = True
            self.log_edit("edit_text", segment=index, old=old_text, new=" ".join(tokens))
            return True

        old_tokens = [str(w.get("word", "")).strip() for w in old_words]
        seg_start, seg_end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        new_words: List[Dict] = []
        matcher = difflib.SequenceMatcher(a=old_tokens, b=tokens, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                new_words.extend(dict(old_words[i1 + k]) for k in range(i2 - i1))
                continue
            if j2 == j1:  # pure deletion
                continue
            if i2 > i1:
                t0, t1 = float(old_words[i1]["start"]), float(old_words[i2 - 1]["end"])
            else:  # pure insertion: fit between neighbours
                t0 = float(new_words[-1]["end"]) if new_words else seg_start
                t1 = float(old_words[i1]["start"]) if i1 < len(old_words) else seg_end
                if t1 < t0:
                    t1 = t0
            n = j2 - j1
            for k in range(n):
                new_words.append({
                    "word": " " + tokens[j1 + k],
                    "start": round(t0 + (t1 - t0) * k / n, 3),
                    "end": round(t0 + (t1 - t0) * (k + 1) / n, 3),
                    "probability": 1.0,
                    "edited": True,
                })
        if not new_words:
            return False
        seg["words"] = new_words
        self._rebuild_text(seg)
        seg["edited"] = True
        self.log_edit("edit_text", segment=index, old=old_text, new=seg["text"].strip())
        return True

    def split_segment(self, index: int, word_index: int) -> bool:
        """Split before word `word_index`: words[:k] stay, words[k:] become a new segment (same speaker)."""
        seg = self.segments[index]
        words = seg.get("words") or []
        if not (0 < word_index < len(words)):
            return False
        second = {k: v for k, v in seg.items() if k not in ("words", "text", "id")}
        second["words"] = [dict(w) for w in words[word_index:]]
        seg["words"] = words[:word_index]
        self._rebuild_text(seg)
        self._rebuild_text(second)
        self.segments.insert(index + 1, second)
        self.log_edit("split_segment", segment=index, at=second["start"])
        return True

    def insert_segment(self, start: float, end: float, text: str, speaker: Optional[str] = None) -> int:
        """Add speech the transcription missed. Words are spread evenly over [start, end], marked as typed by a
        person (probability 1.0, edited). Inserted in time order; returns the new segment's index."""
        tokens = re.findall(r"\S+", text or "")
        if not tokens:
            raise ValueError("The new segment needs some text.")
        start = max(0.0, float(start))
        end = max(start + 0.1, float(end))
        n = len(tokens)
        words = [{"word": " " + t, "start": round(start + (end - start) * k / n, 3), "end": round(start + (end - start) * (k + 1) / n, 3),
                  "probability": 1.0, "edited": True} for k, t in enumerate(tokens)]
        seg: Dict = {"start": round(start, 3), "end": round(end, 3), "text": " " + " ".join(tokens), "words": words, "edited": True, "inserted": True}
        speaker = (speaker or "").strip()
        if speaker:
            seg["speaker"] = speaker
            seg["speaker_manual"] = True
            roster = self.roster()
            if speaker not in roster:
                self.data["speakers"] = roster + [speaker]
        idx = next((i for i, sg in enumerate(self.segments) if float(sg.get("start", 0.0)) > start), len(self.segments))
        self.segments.insert(idx, seg)
        self.log_edit("insert_segment", segment=idx, start=seg["start"], end=seg["end"], text=seg["text"].strip(), speaker=speaker or None)
        return idx

    def sync_text_with_words(self) -> int:
        changed = sync_text_with_words(self.segments)
        if changed:
            self.log_edit("sync_text_with_words", segments=changed,
                          note="segment text replaced by its timed words; Whisper's differing text kept as text_whisper")
        return changed

    def set_checked(self, index: int, checked: bool = True) -> bool:
        seg = self.segments[index]
        if bool(seg.get("checked")) == bool(checked):
            return False
        if checked:
            seg["checked"] = True
            seg["checked_at"] = now_iso()
        else:
            seg.pop("checked", None)
            seg.pop("checked_at", None)
        return True

    def checked_fraction(self) -> float:
        """Share of speech (by duration) in segments marked as checked."""
        total = done = 0.0
        for seg in self.segments:
            dur = max(0.1, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            total += dur
            if seg.get("checked"):
                done += dur
        return done / total if total else 0.0

    def replace_range(self, start: float, end: float, new_segments: List[Dict], info: Dict) -> Tuple[int, int]:
        """Replace the segments whose midpoint lies in [start, end] by new_segments (already on this timeline).
        New segments take the speaker they overlap most among the replaced ones (unless info['speaker'] forces one)."""
        mid = lambda sg: (float(sg.get("start", 0.0)) + float(sg.get("end", 0.0))) / 2
        idx = [i for i, sg in enumerate(self.segments) if start <= mid(sg) <= end]
        old = [self.segments[i] for i in idx]
        forced = (info.get("speaker") or "").strip()
        for sg in new_segments:
            if forced:
                sg["speaker"], sg["speaker_manual"] = forced, True
            elif old:
                def overlap(o):
                    return min(float(o["end"]), float(sg["end"])) - max(float(o["start"]), float(sg["start"]))
                best = max(old, key=overlap)
                if best.get("speaker"):
                    sg["speaker"] = best["speaker"]
                    if best.get("speaker_manual"):
                        sg["speaker_manual"] = True
            sg["retranscribed"] = {k: info.get(k) for k in ("model", "language", "task", "backend")}
            sg["retranscribed"]["at"] = now_iso()
        at = idx[0] if idx else next((i for i, sg in enumerate(self.segments) if float(sg.get("start", 0.0)) > start), len(self.segments))
        for i in reversed(idx):
            del self.segments[i]
        self.segments[at:at] = new_segments
        if forced and forced not in self.roster():
            self.data["speakers"] = self.roster() + [forced]
        self.log_edit("retranscribe", start=round(start, 3), end=round(end, 3), **{k: info.get(k) for k in ("model", "language", "task", "backend")},
                      old_text=[(o.get("text") or "").strip() for o in old], new_text=[(n.get("text") or "").strip() for n in new_segments])
        return len(old), len(new_segments)

    def apply_speaker_labels(self, labels: List[Optional[str]], keep_manual: bool, info: Dict) -> Dict[str, int]:
        """Apply new speaker labels (one per segment, same order). New labels are mapped onto the names already in
        use wherever they overlap most, so 'Robin' stays 'Robin'; hand-assigned speakers are kept if asked."""
        from collections import defaultdict

        overlap: Dict[Tuple[str, str], float] = defaultdict(float)
        for seg, lab in zip(self.segments, labels):
            if lab and seg.get("speaker"):
                overlap[(lab, seg["speaker"])] += max(0.1, float(seg.get("end", 0)) - float(seg.get("start", 0)))
        mapping: Dict[str, str] = {}
        used = set()
        for (lab, name), _w in sorted(overlap.items(), key=lambda kv: -kv[1]):
            if lab not in mapping and name not in used:
                mapping[lab] = name
                used.add(name)
        taken = set(self.roster()) | used
        for lab in sorted({l for l in labels if l}):
            if lab not in mapping:
                name, k = lab, 2
                while name in taken:
                    name, k = f"{lab}_{k}", k + 1
                mapping[lab] = name
                taken.add(name)
        changed = kept = 0
        for seg, lab in zip(self.segments, labels):
            if not lab:
                continue
            if keep_manual and seg.get("speaker_manual"):
                kept += 1
                continue
            name = mapping[lab]
            if seg.get("speaker") != name:
                seg["speaker"] = name
                seg.pop("speaker_manual", None)
                changed += 1
        roster = self.roster()
        self.data["speakers"] = roster
        self.log_edit("rerun_speaker_detection", method=info.get("method"), num_speakers=info.get("num_speakers"), changed=changed,
                      kept_manual=kept, mapping=mapping)
        return {"changed": changed, "kept": kept, "speakers": len(set(mapping.values()))}

    def remove_empty_segments(self) -> int:
        kept, removed = drop_empty_segments(self.segments)
        if removed:
            self.result["segments"] = kept
            self.log_edit("remove_empty_segments", count=removed)
        return removed

    def delete_segment(self, index: int) -> bool:
        if not (0 <= index < len(self.segments)):
            return False
        seg = self.segments.pop(index)
        self.log_edit("delete_segment", segment=index, start=seg.get("start"), text=(seg.get("text") or "").strip(), speaker=seg.get("speaker"))
        return True

    def merge_with_next(self, index: int) -> bool:
        if not (0 <= index < len(self.segments) - 1):
            return False
        a, b = self.segments[index], self.segments[index + 1]
        if (a.get("words") or []) and (b.get("words") or []):
            a["words"] = list(a["words"]) + list(b["words"])
            self._rebuild_text(a)
        else:
            a["text"] = " " + " ".join(x for x in ((a.get("text") or "").strip(), (b.get("text") or "").strip()) if x)
            a["end"] = b.get("end", a.get("end"))
        if a.get("speaker") != b.get("speaker"):
            a["speaker_merged_from"] = b.get("speaker")
        del self.segments[index + 1]
        self.log_edit("merge_segments", segment=index, merged_speaker=b.get("speaker"))
        return True

    def set_segment_speaker(self, index: int, speaker: str) -> bool:
        seg = self.segments[index]
        speaker = speaker.strip()
        if not speaker or seg.get("speaker") == speaker:
            return False
        self.log_edit("set_speaker", segment=index, old=seg.get("speaker"), new=speaker)
        roster = self.roster()  # keep the speaker being replaced in the list, even if it now has no segments
        seg["speaker"] = speaker
        seg["speaker_manual"] = True
        if speaker not in roster:
            roster.append(speaker)
        self.data["speakers"] = roster
        return True

    def paragraphs(self) -> List[Dict]:
        """Speaker turns: consecutive segments by the same speaker joined into one paragraph."""
        out: List[Dict] = []
        for i, seg in enumerate(self.segments):
            text = (seg.get("text") or "").strip()
            if is_blank_text(text):
                continue
            spk = seg.get("speaker")
            if out and out[-1]["speaker"] == spk:
                out[-1]["text"] += " " + text
                out[-1]["end"] = float(seg.get("end", out[-1]["end"]))
                out[-1]["segments"].append(i)
            else:
                out.append({"speaker": spk, "start": float(seg.get("start", 0.0)), "end": float(seg.get("end", 0.0)), "text": text, "segments": [i]})
        return out

    # ----- exports -----
    def confidence_report(self) -> Dict:
        t = self.thresholds
        return RTFConfidenceWriter.analyze_confidence(self.result, t["high"], t["medium"], t["poor"])

    def export_all(self) -> Dict[str, Path]:
        formats = dict(self.DEFAULT_FORMATS, **(self.data.get("export_formats") or {}))
        paths = self.export_paths()
        segs = self.segments
        created: Dict[str, Path] = {}
        if formats.get("srt"):
            RTFConfidenceWriter.write_srt(segs, paths["srt"])
            created["srt"] = paths["srt"]
        if formats.get("vtt"):
            RTFConfidenceWriter.write_vtt(segs, paths["vtt"])
            created["vtt"] = paths["vtt"]
        if formats.get("txt"):
            RTFConfidenceWriter.write_txt(segs, paths["txt"])
            created["txt"] = paths["txt"]
        if formats.get("rtf"):
            t = self.thresholds
            sources = (self.data.get("provenance") or {}).get("inputs") or []
            source_name = Path(sources[0]["path"]).name if sources else self.base_name
            RTFConfidenceWriter.write_rtf_with_confidence(
                self.result, paths["rtf"], source_name, self.data.get("model", ""), t["high"], t["medium"], t["poor"]
            )
            created["rtf"] = paths["rtf"]
        if formats.get("para_rtf") or formats.get("para_txt"):
            paras = self.paragraphs()
            if formats.get("para_rtf"):
                RTFConfidenceWriter.write_paragraphs_rtf(paras, paths["para_rtf"], self.base_name)
                created["para_rtf"] = paths["para_rtf"]
            if formats.get("para_txt"):
                RTFConfidenceWriter.write_paragraphs_txt(paras, paths["para_txt"])
                created["para_txt"] = paths["para_txt"]
        return created


class LosslessCutHelper:
    @staticmethod
    def find_llc_for_video(video_path: Path) -> Optional[Path]:
        candidates = [
            video_path.with_suffix(".llc"),
            Path(str(video_path) + ".llc"),
            video_path.parent / f"{video_path.stem}-proj.llc",
        ]
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    @staticmethod
    def parse_llc_segments(llc_path: Path) -> List[Tuple[float, float]]:
        content = llc_path.read_text(encoding="utf-8", errors="ignore")
        segments = []
        pattern = re.compile(
            r"start\s*:\s*([0-9.]+)\s*,\s*end\s*:\s*([0-9.]+)|"
            r'"start"\s*:\s*([0-9.]+)\s*,\s*"end"\s*:\s*([0-9.]+)'
        )
        for match in pattern.finditer(content):
            s = match.group(1) or match.group(3)
            e = match.group(2) or match.group(4)
            if s is not None and e is not None:
                segments.append((float(s), float(e)))
        return segments

    @classmethod
    def apply_llc_cuts_if_present(
        cls,
        video_path: Path,
        work_dir: Path,
        log_fn: Callable[[str, str], None],
        run_fn: Callable[[List[str], str], None],
    ) -> Tuple[Path, Optional[Dict]]:
        """Returns (video to use, record of the cuts applied or None). Cuts change the timeline, so the
        record goes into the transcript's provenance."""
        llc_file = cls.find_llc_for_video(video_path)
        if not llc_file:
            return video_path, None

        segments = cls.parse_llc_segments(llc_file)
        if not segments:
            return video_path, None

        log_fn(
            f"Found LosslessCut project ({llc_file.name}) with {len(segments)} segment(s). Trimming losslessly...",
            "INFO",
        )
        record = {
            "source": str(video_path),
            "llc_file": str(llc_file),
            "segments_s": [[round(s, 3), round(e, 3)] for s, e in segments],
            "note": "Stream-copy cuts snap to keyframes; transcript timestamps refer to the trimmed timeline.",
        }
        part_files = []
        for idx, (start_s, end_s) in enumerate(segments):
            part_out = work_dir / f"{video_path.stem}_llc_part{idx:03d}{video_path.suffix}"
            run_fn(
                [
                    "ffmpeg", "-hide_banner", "-y",
                    "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
                    "-i", str(video_path),
                    "-c", "copy", "-avoid_negative_ts", "make_zero",
                    str(part_out),
                ],
                f"LosslessCut trim of {video_path.name} (segment {idx + 1})",
            )
            part_files.append(part_out)

        if len(part_files) == 1:
            return part_files[0], record

        concat_list = work_dir / f"{video_path.stem}_llc_concat.txt"
        concat_list.write_text("\n".join(ffconcat_line(p) for p in part_files) + "\n", encoding="utf-8")
        merged_llc = work_dir / f"{video_path.stem}_llc_trimmed{video_path.suffix}"
        run_fn(
            ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(merged_llc)],
            f"Joining LosslessCut segments of {video_path.name}",
        )
        return merged_llc, record


# Script run in the Whisper Python (the one with mlx-whisper / torch), as `python -c WHISPER_RUNNER <args>`.
# Options via environment: PIPELINE_HF_TOKEN, PIPELINE_TASK, PIPELINE_PROMPT, PIPELINE_MODE, PIPELINE_SEGMENTS_JSON.
WHISPER_RUNNER = """
import json, math, os, sys, wave
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # must be set before torch is imported

import numpy as np

audio_path = sys.argv[1]
wav_16k_path = sys.argv[2]
model_name = sys.argv[3]
lang = sys.argv[4]
out_json = sys.argv[5]
diarize_mode = sys.argv[6]
num_speakers = int(sys.argv[7])
hf_token = os.environ.get("PIPELINE_HF_TOKEN", "")
backend = sys.argv[8]

# openai-whisper model names -> MLX-converted weights on Hugging Face (downloaded on first use)
MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}

kwargs = {"word_timestamps": True, "verbose": False}
if lang != "auto":
    kwargs["language"] = lang
task = os.environ.get("PIPELINE_TASK", "transcribe")
if task == "translate":
    kwargs["task"] = "translate"  # any language -> English
prompt = os.environ.get("PIPELINE_PROMPT", "").strip()
if prompt:
    kwargs["initial_prompt"] = prompt  # vocabulary: names, places, activity words
MODE = os.environ.get("PIPELINE_MODE", "full")  # "full" or "diarize_only" (speakers for existing segments)

result = None
backend_label = ""
if MODE == "diarize_only":
    with open(os.environ["PIPELINE_SEGMENTS_JSON"], encoding="utf-8") as fh:
        result = {"segments": json.load(fh)}
    backend_label = "existing transcript"
    print(f"Re-running speaker detection on {len(result['segments'])} existing segments.", flush=True)
elif backend.startswith("MLX"):
    try:
        import mlx_whisper
    except ImportError:
        mlx_whisper = None
        print("mlx-whisper is not installed in this Python; falling back to openai-whisper on CPU.", flush=True)
    if mlx_whisper is not None:
        repo = MLX_REPOS.get(model_name, model_name)
        print(f"Running MLX Whisper ({repo}) on the Apple Silicon GPU. First run downloads the model.", flush=True)
        try:
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo, hallucination_silence_threshold=2.0, **kwargs)
            kwargs["hallucination_silence_threshold"] = 2.0
        except TypeError:  # older mlx-whisper without this option
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo, **kwargs)
        backend_label = "MLX on Apple Silicon GPU"

if result is None:
    # openai-whisper's MPS support is unreliable (sparse alignment-head tensors, fp16 NaNs), so use CPU.
    import whisper
    model = whisper.load_model(model_name, device="cpu")
    try:
        result = model.transcribe(audio_path, fp16=False, hallucination_silence_threshold=2.0, **kwargs)
        kwargs["hallucination_silence_threshold"] = 2.0
    except TypeError:
        result = model.transcribe(audio_path, fp16=False, **kwargs)
    backend_label = "openai-whisper on CPU"

segments = result.get("segments", [])
result["diarization_method"] = "Disabled"
result["backend_label"] = backend_label

def pick_torch_device():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"

def run_spectral_diarization(wav_file, segs, k_Hint, dev):
    import torch
    import torch.nn.functional as F
    label = "Metal/MPS" if dev == "mps" else "CPU"
    if not segs or not os.path.exists(wav_file):
        return "Disabled"
    with wave.open(wav_file, "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)
    audio_np = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if wf.getnchannels() > 1:
        audio_np = audio_np.reshape(-1, wf.getnchannels()).mean(axis=1)

    n_fft = 1024
    hop = 256
    n_mels = 40
    win = torch.hann_window(n_fft, device=dev)
    f_min, f_max = 60.0, 7600.0
    m_min = 2595.0 * math.log10(1.0 + f_min / 700.0)
    m_max = 2595.0 * math.log10(1.0 + f_max / 700.0)
    # Filterbank / DCT are built element-by-element: do that on CPU, then move once to the GPU.
    m_pts = torch.linspace(m_min, m_max, n_mels + 2)
    hz_pts = 700.0 * (10.0 ** (m_pts / 2595.0) - 1.0)
    bins = torch.floor((n_fft + 1) * hz_pts / sr).long()
    fb = torch.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        l, c, r = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        for k in range(l, c):
            if c > l:
                fb[m - 1, k] = (k - l) / (c - l)
        for k in range(c, r):
            if r > c:
                fb[m - 1, k] = (r - k) / (r - c)
    fb = fb.to(dev)

    n_mfcc = 20
    dct_m = torch.zeros((n_mfcc, n_mels))
    for k in range(n_mfcc):
        for n in range(n_mels):
            dct_m[k, n] = math.cos(math.pi * k * (2 * n + 1) / (2 * n_mels))
    dct_m = dct_m.to(dev)

    freqs = torch.linspace(0, sr / 2.0, n_fft // 2 + 1, device=dev)
    low_band = (freqs >= 85.0) & (freqs <= 260.0)
    mid_band = (freqs > 260.0) & (freqs <= 1200.0)
    high_band = (freqs > 1200.0) & (freqs <= 4500.0)

    embeds = []
    valid_indices = []
    for idx, seg in enumerate(segs):
        s_i = max(0, int(float(seg.get("start", 0.0)) * sr))
        e_i = min(len(audio_np), int(float(seg.get("end", 0.0)) * sr))
        if e_i - s_i < int(0.15 * sr):
            s_i = max(0, s_i - int(0.15 * sr))
            e_i = min(len(audio_np), e_i + int(0.15 * sr))
        chunk = audio_np[s_i:e_i]
        if len(chunk) < n_fft:
            chunk = np.pad(chunk, (0, n_fft - len(chunk)))
        wav_t = torch.from_numpy(chunk).to(dev)
        stft = torch.stft(wav_t, n_fft=n_fft, hop_length=hop, window=win, return_complex=True)
        mag = torch.abs(stft) + 1e-7
        power = mag ** 2
        frame_energy = power.mean(dim=0)
        thresh = torch.quantile(frame_energy, 0.30) if frame_energy.numel() > 4 else 0.0
        voiced = power[:, frame_energy >= thresh]
        if voiced.shape[1] == 0:
            voiced = power

        mel_spec = torch.matmul(fb, voiced)
        log_mel = torch.log(mel_spec + 1e-7)
        mfcc = torch.matmul(dct_m, log_mel)
        mfcc_mean = mfcc.mean(dim=1)
        mfcc_std = mfcc.std(dim=1) if mfcc.shape[1] > 1 else torch.zeros_like(mfcc_mean)

        e_low = voiced[low_band, :].mean().unsqueeze(0)
        e_mid = voiced[mid_band, :].mean().unsqueeze(0)
        e_high = voiced[high_band, :].mean().unsqueeze(0)
        ratios = torch.log(torch.cat([e_low / (e_mid + 1e-7), e_mid / (e_high + 1e-7), e_low / (e_high + 1e-7)]) + 1e-7)

        feat = torch.cat([mfcc_mean, mfcc_std, ratios], dim=0)
        embeds.append(feat)
        valid_indices.append(idx)

    if len(embeds) < 2:
        for s in segs:
            s["speaker"] = "SPEAKER_01"
        return f"{label} Spectral (1 Speaker)"

    # Clustering is many tiny ops + .item() syncs; with unified memory the CPU is faster for this part.
    X = torch.stack(embeds, dim=0).to("cpu")
    X_mean = X.mean(dim=0, keepdim=True)
    X_std = X.std(dim=0, keepdim=True) + 1e-6
    X_norm = F.normalize((X - X_mean) / X_std, p=2, dim=1)

    N = X_norm.shape[0]
    if k_Hint <= 0:
        best_k = 2
        best_score = -1.0
        max_k = min(6, N - 1)
        if max_k < 2:
            best_k = 1
        else:
            sim_mat = torch.matmul(X_norm, X_norm.T)
            dist_mat = 1.0 - sim_mat
            for cand_k in range(2, max_k + 1):
                centers = [0]
                for _ in range(cand_k - 1):
                    d_to_c = dist_mat[:, centers].min(dim=1).values
                    centers.append(int(torch.argmax(d_to_c).item()))
                C = X_norm[centers].clone()
                for _ in range(25):
                    d = 1.0 - torch.matmul(X_norm, C.T)
                    labels = torch.argmin(d, dim=1)
                    for c_i in range(cand_k):
                        mask = (labels == c_i)
                        if mask.any():
                            C[c_i] = F.normalize(X_norm[mask].mean(dim=0), p=2, dim=0)
                sil_vals = []
                for i in range(N):
                    my_c = int(labels[i].item())
                    same_mask = (labels == my_c)
                    same_mask[i] = False
                    a_i = dist_mat[i, same_mask].mean().item() if same_mask.any() else 0.0
                    b_i = 1e9
                    for other_c in range(cand_k):
                        if other_c == my_c:
                            continue
                        other_mask = (labels == other_c)
                        if other_mask.any():
                            b_i = min(b_i, dist_mat[i, other_mask].mean().item())
                    if b_i < 1e8 and max(a_i, b_i) > 0:
                        sil_vals.append((b_i - a_i) / max(a_i, b_i))
                avg_sil = sum(sil_vals) / len(sil_vals) if sil_vals else -1.0
                if avg_sil > best_score:
                    best_score = avg_sil
                    best_k = cand_k
            if best_score < 0.09:
                best_k = 1
        k_final = best_k
    else:
        k_final = min(k_Hint, N)

    if k_final <= 1:
        for s in segs:
            s["speaker"] = "SPEAKER_01"
        return f"{label} Spectral (1 Speaker)"

    dist_mat = 1.0 - torch.matmul(X_norm, X_norm.T)
    centers = [0]
    for _ in range(k_final - 1):
        d_to_c = dist_mat[:, centers].min(dim=1).values
        centers.append(int(torch.argmax(d_to_c).item()))
    C = X_norm[centers].clone()
    for _ in range(30):
        d = 1.0 - torch.matmul(X_norm, C.T)
        labels = torch.argmin(d, dim=1)
        for c_i in range(k_final):
            mask = (labels == c_i)
            if mask.any():
                C[c_i] = F.normalize(X_norm[mask].mean(dim=0), p=2, dim=0)

    raw_lbls = [int(labels[i].item()) for i in range(N)]
    for i in range(1, N - 1):
        dur = float(segs[valid_indices[i]].get("end", 0)) - float(segs[valid_indices[i]].get("start", 0))
        if dur < 1.0 and raw_lbls[i - 1] == raw_lbls[i + 1] and raw_lbls[i] != raw_lbls[i - 1]:
            raw_lbls[i] = raw_lbls[i - 1]

    remap = {}
    next_id = 1
    for i, seg_idx in enumerate(valid_indices):
        c_id = raw_lbls[i]
        if c_id not in remap:
            remap[c_id] = f"SPEAKER_{next_id:02d}"
            next_id += 1
        segs[seg_idx]["speaker"] = remap[c_id]

    return f"{label} Spectral Clustering ({len(remap)} Speakers)"

if not diarize_mode.startswith("Disabled") and len(segments) > 0:
    try:
        import torch
        device = pick_torch_device()
    except ImportError:
        torch = None
        device = None
        print("PyTorch is not installed (pip install torch); skipping speaker diarization.", flush=True)

    used_pyannote = False
    if torch is not None and "Pyannote" in diarize_mode:
        try:
            from pyannote.audio import Pipeline as PyannotePipeline
            repo_id = "pyannote/speaker-diarization-3.1"
            if hf_token:
                try:
                    pipeline = PyannotePipeline.from_pretrained(repo_id, token=hf_token)  # pyannote >= 4
                except TypeError:
                    pipeline = PyannotePipeline.from_pretrained(repo_id, use_auth_token=hf_token)  # pyannote 3.x
            else:
                pipeline = PyannotePipeline.from_pretrained(repo_id)
            if pipeline is None:
                raise RuntimeError("could not load pyannote pipeline (check HF token / model licence)")
            try:
                pipeline.to(torch.device(device))
            except Exception:
                device = "cpu"
                pipeline.to(torch.device("cpu"))
            p_kwargs = {"num_speakers": num_speakers} if num_speakers > 0 else {}
            diar = pipeline(wav_16k_path, **p_kwargs)
            turns = []
            for turn, _, spk in diar.itertracks(yield_label=True):
                turns.append((turn.start, turn.end, spk))
            remap = {}
            next_id = 1
            for seg in segments:
                s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
                best_spk = "SPEAKER_01"
                best_ov = 0.0
                for t0, t1, spk in turns:
                    ov = max(0.0, min(s1, t1) - max(s0, t0))
                    if ov > best_ov:
                        best_ov = ov
                        best_spk = spk
                if best_spk not in remap:
                    remap[best_spk] = f"SPEAKER_{next_id:02d}"
                    next_id += 1
                seg["speaker"] = remap[best_spk]
            dev_lbl = "Metal/MPS" if device == "mps" else "CPU"
            result["diarization_method"] = f"pyannote.audio 3.1 {dev_lbl} ({len(remap)} Speakers)"
            used_pyannote = True
        except Exception as exc:
            print(f"pyannote diarization unavailable ({exc}); using spectral clustering.", flush=True)

    if torch is not None and not used_pyannote:
        try:
            method_str = run_spectral_diarization(wav_16k_path, segments, num_speakers, device)
        except Exception as exc:
            if device == "cpu":
                raise
            print(f"MPS diarization failed ({exc}); retrying on CPU.", flush=True)
            method_str = run_spectral_diarization(wav_16k_path, segments, num_speakers, "cpu")
        result["diarization_method"] = method_str

def _pkg_version(dist):
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return None

result["runtime"] = {
    "python": sys.version.split()[0],
    "packages": {d: _pkg_version(d) for d in ("mlx-whisper", "mlx", "openai-whisper", "torch", "pyannote.audio", "numpy")},
    "mlx_repo": MLX_REPOS.get(model_name, model_name) if backend_label.startswith("MLX") else None,
    "transcribe_options": {k: v for k, v in kwargs.items() if k != "verbose"},
}

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("WHISPER_DONE=1", flush=True)
"""


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

    def run_ffmpeg_videotoolbox_encode_for_jobs(
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
    ) -> Path:
        duration = self.probe_video_duration(input_path)
        vt_quality = str(SystemProbe.rf_to_videotoolbox_q(self.config["hb_quality"]))
        res_limit = self.config["hb_resolution"]
        fps_mode = self.config["hb_fps"]
        audio_bitrate = f"{self.config['hb_audio_bitrate']}k"

        # Apple Media Engine encodes H.264 / HEVC only (M3/M4 decode AV1 but cannot encode it).
        is_h264 = "h264" in encoder_choice
        vt_codec = "h264_videotoolbox" if is_h264 else "hevc_videotoolbox"
        if "av1" in encoder_choice:
            self.log("Apple Silicon has no hardware AV1 encoder; using HEVC VideoToolbox instead.", "WARN")

        res_map = {
            "2160p (4K)": (3840, 2160),
            "1440p (2K)": (2560, 1440),
            "1080p (FHD)": (1920, 1080),
            "720p (HD)": (1280, 720),
        }
        vf_chain: List[str] = []
        if res_limit in res_map:
            max_w, max_h = res_map[res_limit]
            vf_chain.append(
                f"scale=w='min(iw,{max_w})':h='min(ih,{max_h})':force_original_aspect_ratio=decrease:force_divisible_by=2"
            )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-hwaccel",
            "videotoolbox",
            "-i",
            str(input_path),
        ]
        if vf_chain:
            cmd.extend(["-vf", ",".join(vf_chain)])
        cmd.extend(["-c:v", vt_codec, "-q:v", vt_quality])
        if not is_h264:
            cmd.extend(["-tag:v", "hvc1"])  # makes HEVC playable in QuickTime / Finder previews

        if fps_mode != "Same as source":
            fps_val = fps_mode.split()[0]
            cmd.extend(["-r", fps_val])

        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, str(output_path)])
        self.log(f"[{stage_label}] Executing Apple VideoToolbox Hardware Encode: {' '.join(cmd)}", "CMD")
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
                        f"{stage_label} (VideoToolbox): {pct:.1f}% | {fps_str} FPS",
                    )
                    for jid in job_ids:
                        self.update_job(
                            jid,
                            status="Running",
                            stage=f"Media Engine {pct:.0f}% ({fps_str} fps)",
                            progress=job_pct,
                        )

        ret = self.current_proc.wait()
        self.current_proc = None
        if ret != 0:
            err_tail = "\n".join(tail_lines[-6:])
            raise RuntimeError(f"FFmpeg VideoToolbox exited with code {ret}:\n{err_tail}")

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

        hb_bin = shutil.which("HandBrakeCLI")
        if not hb_bin:
            if requested_encoder.startswith("vt_") and SystemProbe.has_ffmpeg_videotoolbox():
                self.log("HandBrakeCLI not found. Routing to FFmpeg VideoToolbox (brew install handbrake to use HandBrake).", "WARN")
                return self.run_ffmpeg_videotoolbox_encode_for_jobs(**vt_kwargs)
            raise RuntimeError("HandBrakeCLI is not installed. Run: brew install handbrake")

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
        script = Path(__file__).resolve()
        tools = {"ffmpeg": tool_version("ffmpeg", "-version")}
        if (processing.get("compression") or {}).get("tool") == "HandBrakeCLI" or "HandBrakeCLI" in json.dumps(processing):
            tools["HandBrakeCLI"] = tool_version("HandBrakeCLI", "--version")
        return {
            "app": {"script": str(script), "script_sha256": sha256_file(script)},
            "run_started": self.run_started,
            "transcribed_at": now_iso(),
            "system": {
                "macos": platform.mac_ver()[0] or platform.platform(),
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
        backend = self.config.get("whisper_backend", "MLX (Apple Silicon GPU)")
        self.log(f"=== Stage 4: Transcribing {media_path.name} with Whisper ({backend}) ===", "STAGE")
        self.set_progress(68.0, f"Stage 4: Loading Whisper '{self.config['whisper_model']}' ({backend})...")

        best_py = MacAppInstaller.find_best_python()
        model_name = self.config["whisper_model"]
        lang = self.config["whisper_lang"].split()[0]
        diarize_mode = self.config.get("diarize_mode", "PyTorch MPS Spectral (Offline - No Token Needed)")
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

        self.current_proc = subprocess.Popen(
            [
                best_py,
                "-c",
                runner_code,
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
                if not clean:
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
        temp_dir = Path(tempfile.mkdtemp(prefix="silicon_pipeline_"))
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


THEME = {
    "bg_root": "#0f1117",
    "bg_panel": "#171a22",
    "bg_card": "#1e222c",
    "bg_input": "#11141b",
    "fg_primary": "#e8eaef",
    "fg_secondary": "#9aa3b2",
    "fg_dim": "#6b7280",
    "accent": "#e11d48",
    "accent_hover": "#be123c",
    "ok": "#10b981",
    "warn": "#f59e0b",
    "err": "#ef4444",
    "border": "#2a3040",
}

FONT_UI = "Helvetica Neue"
FONT_MONO = "Menlo"
# name -> (family, base size, weight). Every widget uses these named fonts, so View > Larger/Smaller Text
# (⌘+ / ⌘− / ⌘0) resizes the whole window live.
FONT_SPECS = {
    "body": (FONT_UI, 10, "normal"),
    "body_bold": (FONT_UI, 10, "bold"),
    "small": (FONT_UI, 9, "normal"),
    "small_bold": (FONT_UI, 9, "bold"),
    "title": (FONT_UI, 13, "bold"),
    "section": (FONT_UI, 9, "bold"),
    "mono": (FONT_MONO, 10, "normal"),
    "mono_small": (FONT_MONO, 9, "normal"),
    "mono_small_bold": (FONT_MONO, 9, "bold"),
    "transcript": (FONT_UI, 12, "normal"),
    "marker": (FONT_UI, 12, "normal"),
}
FONT_SLANTS = {"marker": "italic"}

# Transcription markers: (menu label, text, Ctrl+Shift key). {t} = current playback time.
MARKERS = [
    ("Laughter", "[laughs]", "L"),
    ("Inaudible (with time)", "[inaudible {t}]", "I"),
    ("Unclear word", "[unclear: ]", "U"),
    ("Crosstalk", "[crosstalk]", "C"),
    ("Pause", "[pause]", "P"),
    ("German", "[German]", "G"),
    ("Non-verbal / gesture", "[gestures]", "N"),
]
SPEAKER_COLORS = ["#a78bfa", "#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#60a5fa", "#fb7185", "#a3e635"]

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "SiliconVideoPipeline"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".ts", ".m4v", ".mts"}
MEDIA_FILETYPES = [("Video Files", "*.mp4 *.mkv *.mov *.webm *.avi *.flv *.ts *.m4v *.mts"), ("All Files", "*.*")]

WHISPER_LANGUAGES = [
    "auto (Auto-Detect)", "de (German)", "en (English)", "fr (French)", "es (Spanish)", "it (Italian)",
    "nl (Dutch)", "pt (Portuguese)", "pl (Polish)", "tr (Turkish)", "ru (Russian)", "uk (Ukrainian)",
    "ar (Arabic)", "zh (Chinese)", "ja (Japanese)", "ko (Korean)", "sv (Swedish)", "da (Danish)",
]


def short_ts(seconds: float) -> str:
    """H:MM:SS.s for display (exports keep full millisecond timestamps)."""
    tenths = int(round(max(0.0, float(seconds)) * 10))
    h, rem = divmod(tenths, 36000)
    m, rem = divmod(rem, 600)
    s, t = divmod(rem, 10)
    return f"{h}:{m:02d}:{s:02d}.{t}"


def read_ppm_frame(stream) -> Optional[Tuple[int, int, bytes]]:
    """Read one binary PPM (P6) image from a pipe (ffmpeg -f image2pipe -vcodec ppm). None at end of stream."""
    magic = stream.readline()
    if not magic or magic.strip() != b"P6":
        return None
    dims = stream.readline()
    while dims.startswith(b"#"):
        dims = stream.readline()
    try:
        w, h = (int(v) for v in dims.split())
    except ValueError:
        return None
    stream.readline()  # max value (255)
    n = w * h * 3
    data = stream.read(n)
    if data is None or len(data) < n:
        return None
    return w, h, data


class KeychainSecret:
    """Stores one secret (the Hugging Face token) in the macOS login Keychain via /usr/bin/security.
    The secret is written through `security -i` on stdin, so it never appears on a command line."""

    SERVICE = "SiliconVideoPipeline"
    ACCOUNT = "huggingface-token"

    @classmethod
    def available(cls) -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    @classmethod
    def load(cls) -> str:
        if not cls.available():
            return ""
        try:
            res = subprocess.run(["security", "find-generic-password", "-s", cls.SERVICE, "-a", cls.ACCOUNT, "-w"],
                                 capture_output=True, text=True, timeout=10)
            return res.stdout.strip() if res.returncode == 0 else ""
        except Exception:
            return ""

    @classmethod
    def save(cls, secret: str) -> bool:
        secret = secret.strip()
        if not cls.available() or not secret or not re.fullmatch(r"[A-Za-z0-9_\-]+", secret):
            return False
        try:
            res = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s "{cls.SERVICE}" -a "{cls.ACCOUNT}" -w "{secret}"\n',
                                 capture_output=True, text=True, timeout=10)
            return res.returncode == 0 and cls.load() == secret
        except Exception:
            return False

    @classmethod
    def delete(cls) -> None:
        if cls.available():
            subprocess.run(["security", "delete-generic-password", "-s", cls.SERVICE, "-a", cls.ACCOUNT], capture_output=True, timeout=10)


class SettingsStore:
    """GUI settings persisted between launches (never the Hugging Face token)."""

    PATH = APP_SUPPORT_DIR / "settings.json"

    @classmethod
    def load(cls) -> Dict:
        try:
            return json.loads(cls.PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @classmethod
    def save(cls, data: Dict) -> None:
        try:
            cls.PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = cls.PATH.with_name(cls.PATH.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cls.PATH)
        except Exception:
            pass


def launch_gui() -> None:
    try:
        import tkinter as tk
        import tkinter.font as tkfont
        from tkinter import filedialog, messagebox, simpledialog, ttk
    except ImportError:
        print("Error: Python Tkinter is not installed. Run: brew install python-tk  (and use a Homebrew/python.org Python, not /usr/bin/python3)", file=sys.stderr)
        sys.exit(1)

    IS_MAC = sys.platform == "darwin"
    MOD = "Command" if IS_MAC else "Control"

    class FlowFrame(tk.Frame):
        """Lays its children out left-to-right and wraps them onto new rows when space runs out."""

        def __init__(self, master, padx: int = 6, pady: int = 4, **kw):
            super().__init__(master, **kw)
            self._items: List = []
            self._padx, self._pady = padx, pady
            self._last_width = None
            self.bind("<Configure>", self._on_configure)

        def add(self, widget):
            self._items.append(widget)
            self.after_idle(self.relayout)
            return widget

        def clear(self):
            for w in self._items:
                w.destroy()
            self._items = []
            self.relayout()

        def _on_configure(self, event):
            if event.width != self._last_width:
                self._last_width = event.width
                self.relayout()

        def relayout(self):
            width = self.winfo_width()
            if width <= 1:
                width = 10_000
            x = y = row_h = 0
            for w in self._items:
                ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
                if x > 0 and x + ww > width:
                    x, y, row_h = 0, y + row_h + self._pady, 0
                w.place(in_=self, x=x, y=y)
                x += ww + self._padx
                row_h = max(row_h, wh)
            total_h = max(1, y + row_h)
            if int(self.cget("height")) != total_h:
                self.configure(height=total_h)

    class ScrollFrame(tk.Frame):
        """Vertically scrollable container; put content into .inner."""

        def __init__(self, master, bg: str, **kw):
            super().__init__(master, bg=bg, **kw)
            self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
            self.vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
            self.inner = tk.Frame(self.canvas, bg=bg)
            self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
            self.canvas.configure(yscrollcommand=self.vsb.set)
            self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
            self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
            self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))

        def contains(self, widget) -> bool:
            path = str(widget)
            return path.startswith(str(self.canvas)) and "popdown" not in path

        def scroll(self, units: int) -> None:
            if self.inner.winfo_reqheight() > self.canvas.winfo_height():
                self.canvas.yview_scroll(units, "units")

    class SiliconPipelineApp(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Silicon Pipeline — Video, Audio & Transcripts")
            self.configure(bg=THEME["bg_root"])
            self.settings = SettingsStore.load()

            self.f: Dict[str, "tkfont.Font"] = {
                name: tkfont.Font(self, family=fam, size=size, weight=weight, slant=FONT_SLANTS.get(name, "roman"))
                for name, (fam, size, weight) in FONT_SPECS.items()
            }
            self.ui_scale = 1.0
            self._flows: List[FlowFrame] = []

            self._set_initial_geometry()
            self.minsize(900, 600)

            self.jobs: List[Dict] = []
            self.job_counter = 0
            self.msg_queue: queue.Queue = queue.Queue()
            self.stop_event = threading.Event()
            self.worker: Optional[PipelineWorker] = None
            self._persist: Dict[str, object] = {}
            self._combo_values: Dict[str, List[str]] = {}

            self.active_doc: Optional[TranscriptDocument] = None
            self._undo_stacks: Dict[str, Tuple[List, List]] = {}  # per transcript path: (undo, redo) of (label, file text)
            self.word_spans: List[Dict] = []
            self.line_spans: Dict[int, List[int]] = {}
            self.low_conf: List[int] = []
            self.current_span: Optional[int] = None
            self._play_proc: Optional[subprocess.Popen] = None

            self.gpu_info: Dict = {"gpu_name": "Detecting hardware…"}
            self.deps: Dict[str, Optional[str]] = {}

            self._configure_styles()
            self._build_menubar()
            self._build_header()
            self._build_footer()  # before the main area, so it is never pushed off-screen
            self._build_main_layout()
            self._apply_settings()
            self._set_zoom(float(self.settings.get("ui_scale", 1.0)), save=False)
            self._load_queue()

            self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
            self.bind_class("TCombobox", "<MouseWheel>", self._on_mousewheel)  # scrolling must not change values
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            try:
                self.createcommand("tk::mac::Quit", self._on_close)
                self.createcommand("tk::mac::ShowPreferences", lambda: self.notebook.select(self.tab_settings))
            except Exception:
                pass

            threading.Thread(target=self._probe_system, daemon=True).start()
            self.after(100, self._poll_queue)

        # ================================================================== styles, fonts, zoom
        def _set_initial_geometry(self):
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            saved = str(self.settings.get("geometry") or "")
            m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", saved)
            if m:
                w, h, x, y = (int(v) for v in m.groups())
                if 900 <= w <= sw and 600 <= h <= sh and 0 <= x < sw - 100 and 0 <= y < sh - 100:
                    self.geometry(saved)
                    return
            w = max(900, min(1360, int(sw * 0.85)))
            h = max(600, min(860, int(sh * 0.82)))
            w, h = min(w, sw), min(h, sh - 60)
            self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(25, (sh - h) // 2 - 20)}")

        def _configure_styles(self):
            T, f = THEME, self.f
            style = ttk.Style(self)
            style.theme_use("clam")
            self.style = style
            style.configure(".", background=T["bg_root"], foreground=T["fg_primary"], font=f["body"])
            style.configure("TFrame", background=T["bg_panel"])
            style.configure("TLabel", background=T["bg_panel"], foreground=T["fg_primary"], font=f["body"])

            style.configure("Primary.TButton", background=T["accent"], foreground="#ffffff", borderwidth=0, font=f["body_bold"], padding=(12, 5))
            style.map("Primary.TButton", background=[("active", T["accent_hover"]), ("disabled", T["bg_card"])], foreground=[("disabled", T["fg_dim"])])
            style.configure("Tool.TButton", background=T["bg_card"], foreground=T["fg_primary"], borderwidth=1, bordercolor=T["border"],
                            lightcolor=T["bg_card"], darkcolor=T["bg_card"], font=f["small_bold"], padding=(8, 3))
            style.map("Tool.TButton", background=[("active", "#2b3348")], foreground=[("disabled", T["fg_dim"])])

            style.configure("TNotebook", background=T["bg_root"], borderwidth=0, tabmargins=(0, 0, 0, 0))
            style.configure("TNotebook.Tab", background=T["bg_card"], foreground=T["fg_secondary"], padding=(12, 4), font=f["small_bold"], borderwidth=0)
            style.map("TNotebook.Tab", background=[("selected", T["accent"])], foreground=[("selected", "#ffffff")])

            style.configure("TCombobox", fieldbackground=T["bg_input"], background=T["bg_card"], foreground=T["fg_primary"], arrowcolor=T["fg_secondary"],
                            bordercolor=T["border"], lightcolor=T["bg_input"], darkcolor=T["bg_input"], padding=(4, 1))
            style.map("TCombobox", fieldbackground=[("readonly", T["bg_input"])], foreground=[("readonly", T["fg_primary"])],
                      selectbackground=[("readonly", T["bg_input"])], selectforeground=[("readonly", T["fg_primary"])])
            self.option_add("*TCombobox*Listbox.background", T["bg_card"])
            self.option_add("*TCombobox*Listbox.foreground", T["fg_primary"])
            self.option_add("*TCombobox*Listbox.selectBackground", T["accent"])
            self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
            self.option_add("*TCombobox*Listbox.font", f["body"])

            style.configure("Queue.Treeview", background=T["bg_input"], foreground=T["fg_primary"], fieldbackground=T["bg_input"], borderwidth=0, font=f["body"])
            style.configure("Queue.Treeview.Heading", background=T["bg_card"], foreground=T["fg_secondary"], font=f["small_bold"], relief="flat", padding=(4, 3))
            style.map("Queue.Treeview", background=[("selected", "#2b3348")], foreground=[("selected", "#ffffff")])

            style.configure("Horizontal.TProgressbar", troughcolor=T["bg_input"], background=T["accent"], thickness=6, borderwidth=0)
            for orient in ("Vertical", "Horizontal"):
                style.configure(f"{orient}.TScrollbar", background=T["bg_card"], troughcolor=T["bg_panel"], arrowcolor=T["fg_dim"],
                                bordercolor=T["bg_panel"], lightcolor=T["bg_card"], darkcolor=T["bg_card"])

        def _set_zoom(self, scale: float, save: bool = True):
            self.ui_scale = min(1.8, max(0.7, round(scale, 2)))
            for name, (_fam, base, _w) in FONT_SPECS.items():
                self.f[name].configure(size=max(7, int(round(base * self.ui_scale))))
            self.style.configure("Queue.Treeview", rowheight=int(self.f["body"].metrics("linespace") * 1.45) + 4)
            for flow in self._flows:
                self.after_idle(flow.relayout)
            if save:
                self.settings["ui_scale"] = self.ui_scale
                SettingsStore.save(self.settings)

        def _flow(self, parent, **kw) -> FlowFrame:
            flow = FlowFrame(parent, **kw)
            self._flows.append(flow)
            return flow

        # ================================================================== small widget helpers
        def _label(self, parent, text: str, font: str = "body", fg: Optional[str] = None, bg: Optional[str] = None, **kw):
            return tk.Label(parent, text=text, bg=bg or THEME["bg_card"], fg=fg or THEME["fg_primary"], font=self.f[font], **kw)

        def _wrapping_label(self, parent, text: str, **kw):
            lbl = self._label(parent, text, justify=tk.LEFT, anchor="w", **kw)
            lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(120, e.width - 4)))
            return lbl

        def _check(self, parent, text: str, var, bg: Optional[str] = None, fg: Optional[str] = None, font: str = "body"):
            bg = bg or THEME["bg_card"]
            fg = fg or THEME["fg_primary"]
            style_name = f"C{bg[1:]}{fg[1:]}{font}.TCheckbutton"
            self.style.configure(style_name, background=bg, foreground=fg, font=self.f[font], indicatorbackground=THEME["bg_input"],
                                 indicatorforeground="#ffffff", focuscolor=bg, padding=(0, 1))
            self.style.map(style_name, background=[("active", bg)], foreground=[("active", fg)],
                           indicatorbackground=[("selected", THEME["accent"]), ("active", THEME["bg_input"])])
            return ttk.Checkbutton(parent, text=text, variable=var, style=style_name)

        def _tool(self, parent, text: str, cmd, primary: bool = False):
            return ttk.Button(parent, text=text, style="Primary.TButton" if primary else "Tool.TButton", command=cmd)

        def _entry(self, parent, var, show: str = "", width: int = 20, font: str = "mono_small"):
            return tk.Entry(parent, textvariable=var, bg=THEME["bg_input"], fg=THEME["fg_primary"], insertbackground=THEME["fg_primary"], relief=tk.FLAT,
                            highlightthickness=1, highlightbackground=THEME["border"], highlightcolor=THEME["accent"], font=self.f[font], show=show, width=width)

        def _separator(self, parent):
            return tk.Frame(parent, bg=THEME["border"], width=1, height=self.f["small"].metrics("linespace") + 8)

        # ================================================================== menu bar
        def _build_menubar(self):
            mb = tk.Menu(self)
            if IS_MAC:
                app_menu = tk.Menu(mb, name="apple", tearoff=0)
                app_menu.add_command(label="Setup & Status…", command=self._show_setup_modal)
                app_menu.add_command(label="Install App in ~/Applications", command=self._install_desktop_shortcut)
                mb.add_cascade(menu=app_menu)

            acc = "Command-" if IS_MAC else "Ctrl+"
            file_menu = tk.Menu(mb, tearoff=0)
            file_menu.add_command(label="Add Videos…", accelerator=f"{acc}O", command=self._add_videos)
            file_menu.add_command(label="New Merge Task…", command=self._add_merge_task)
            file_menu.add_command(label="Add Folder…", command=self._add_folder)
            file_menu.add_separator()
            file_menu.add_command(label="Open Transcript…", accelerator=f"{acc}Shift-O" if IS_MAC else "Ctrl+Shift+O", command=self._open_transcript)
            file_menu.add_command(label="Show Output Folder", command=self._open_selected_job_folder)
            file_menu.add_command(label="Re-export Open Transcript", command=self._reexport_open_transcript)
            if not IS_MAC:
                file_menu.add_separator()
                file_menu.add_command(label="Setup & Status…", command=self._show_setup_modal)
            mb.add_cascade(label="File", menu=file_menu)

            em = tk.Menu(mb, tearoff=0)
            em.add_command(label="Undo Transcript Change", accelerator=f"{acc}Z", command=self._undo)
            em.add_command(label="Redo Transcript Change", accelerator=f"{acc}Shift-Z" if IS_MAC else "Ctrl+Shift+Z", command=self._redo)
            em.add_separator()
            em.add_command(label="Mark Segment Checked & Next", accelerator=f"{acc}Return" if IS_MAC else "Ctrl+Enter", command=self._check_and_next)
            em.add_command(label="Toggle Segment Checked", command=self._toggle_checked)
            em.add_separator()
            mm = tk.Menu(em, tearoff=0)
            for label, text, key in MARKERS:
                mm.add_command(label=f"{label}   {text.replace('{t}', 't')}", accelerator=f"Control-Shift-{key}" if IS_MAC else f"Ctrl+Shift+{key}",
                               command=lambda t=text: self._insert_marker(t))
            em.add_cascade(label="Insert Marker", menu=mm)
            self.marker_menu = mm
            em.add_separator()
            em.add_command(label="Re-transcribe Passage…", accelerator=f"{acc}Shift-R" if IS_MAC else "Ctrl+Shift+R", command=self._retranscribe_dialog)
            em.add_command(label="Re-run Speaker Detection…", command=self._rediarize_dialog)
            em.add_separator()
            em.add_command(label="Restore Backup…", command=self._restore_backup)
            em.add_command(label="Show Backups Folder", command=self._show_backups)
            mb.add_cascade(label="Edit", menu=em)

            q = tk.Menu(mb, tearoff=0)
            q.add_command(label="Run Pending Tasks", accelerator=f"{acc}R", command=self._start_pipeline)
            q.add_command(label="Stop", accelerator=f"{acc}.", command=self._stop_pipeline)
            q.add_separator()
            q.add_command(label="Move Up", command=self._move_up)
            q.add_command(label="Move Down", command=self._move_down)
            q.add_command(label="Remove Selected", command=self._remove_selected)
            q.add_command(label="Combine Selected into One Merge Task", command=self._combine_selected)
            q.add_command(label="Split Merge Task into Single Tasks", command=self._split_merge_task)
            q.add_command(label="Apply Current Settings to Selected", command=self._task_use_current_settings)
            q.add_command(label="Reset Selected to Pending", command=self._reset_selected_jobs)
            q.add_command(label="Clear Completed", command=self._clear_completed_jobs)
            mb.add_cascade(label="Queue", menu=q)
            self.queue_menu = q

            v = tk.Menu(mb, tearoff=0)
            v.add_command(label="Larger Text", accelerator=f"{acc}+", command=lambda: self._set_zoom(self.ui_scale + 0.1))
            v.add_command(label="Smaller Text", accelerator=f"{acc}-", command=lambda: self._set_zoom(self.ui_scale - 0.1))
            v.add_command(label="Actual Size", accelerator=f"{acc}0", command=lambda: self._set_zoom(1.0))
            v.add_separator()
            self.var_show_pct = tk.BooleanVar(value=False)
            v.add_checkbutton(label="Show % on Uncertain Words", variable=self.var_show_pct, command=self._rerender)
            self.var_show_video = tk.BooleanVar(value=True)
            v.add_checkbutton(label="Show Video Preview", variable=self.var_show_video, command=self._toggle_video_panel)
            v.add_separator()
            v.add_command(label="Settings", accelerator=f"{acc}1", command=lambda: self.notebook.select(self.tab_settings))
            v.add_command(label="Console", accelerator=f"{acc}2", command=lambda: self.notebook.select(self.tab_console))
            v.add_command(label="Transcript", accelerator=f"{acc}3", command=lambda: self.notebook.select(self.tab_preview))
            mb.add_cascade(label="View", menu=v)

            pm = tk.Menu(mb, tearoff=0)
            pm.add_command(label="Play / Pause", accelerator="Esc", command=self._toggle_play)
            pm.add_command(label="Back 5 Seconds", accelerator=f"{acc}[", command=lambda: self._seek_rel(-5))
            pm.add_command(label="Forward 5 Seconds", accelerator=f"{acc}]", command=lambda: self._seek_rel(+5))
            pm.add_separator()
            pm.add_command(label="Faster", accelerator=f"{acc}Shift-." if IS_MAC else "Ctrl+Shift+.", command=lambda: self._speed_step(+1))
            pm.add_command(label="Slower", accelerator=f"{acc}Shift-," if IS_MAC else "Ctrl+Shift+,", command=lambda: self._speed_step(-1))
            pm.add_command(label="Normal Speed", command=lambda: (self.var_speed.set("1×"), self._on_speed_change()))
            pm.add_separator()
            pm.add_command(label="Save Segment Edit", accelerator=f"{acc}S", command=self._save_segment_edit)
            pm.add_command(label="Insert Missing Segment…", accelerator=f"{acc}I", command=self._insert_segment_dialog)
            pm.add_command(label="Next Uncertain Word", command=lambda: self._jump_low_conf(+1))
            pm.add_command(label="Previous Uncertain Word", command=lambda: self._jump_low_conf(-1))
            mb.add_cascade(label="Playback", menu=pm)
            self.configure(menu=mb)

            keys = {
                "o": self._add_videos, "O": self._open_transcript, "r": self._start_pipeline, "period": self._stop_pipeline,
                "equal": lambda: self._set_zoom(self.ui_scale + 0.1), "plus": lambda: self._set_zoom(self.ui_scale + 0.1),
                "minus": lambda: self._set_zoom(self.ui_scale - 0.1), "0": lambda: self._set_zoom(1.0),
                "1": lambda: self.notebook.select(self.tab_settings), "2": lambda: self.notebook.select(self.tab_console),
                "3": lambda: self.notebook.select(self.tab_preview),
                "bracketleft": lambda: self._seek_rel(-5), "bracketright": lambda: self._seek_rel(+5),
                "s": self._save_segment_edit,
                "i": self._insert_segment_dialog,
            }
            # Use the <Mod-Key-x> form: in Tk "<Command-1>" would mean Command + mouse button 1.
            for key, fn in keys.items():
                self.bind_all(f"<{MOD}-Key-{key}>", lambda e, fn=fn: (fn(), "break")[-1])
            self.bind_all("<Escape>", self._toggle_play)  # works while typing in the segment editor
            self.bind_all(f"<{MOD}-Key-z>", self._undo_key)
            self.bind_all(f"<{MOD}-Key-R>", lambda e: (self._retranscribe_dialog(), "break")[-1])
            self.bind_all(f"<{MOD}-Shift-Key-r>", lambda e: (self._retranscribe_dialog(), "break")[-1])
            self.bind_all(f"<{MOD}-Shift-Key-z>", self._redo_key)
            self.bind_all(f"<{MOD}-Key-Z>", self._redo_key)
            for _label, text, key in MARKERS:
                for seq in (f"<Control-Shift-Key-{key}>", f"<Control-Key-{key}>"):  # with Shift, Tk reports the capital letter
                    self.bind_all(seq, lambda e, t=text: (self._insert_marker(t), "break")[-1])
            # Shift turns . and , into > and < — bind both spellings so the shortcut works on every keyboard layout
            for seq, d in ((f"<{MOD}-Key-greater>", +1), (f"<{MOD}-Shift-Key-period>", +1), (f"<{MOD}-Key-less>", -1), (f"<{MOD}-Shift-Key-comma>", -1)):
                self.bind_all(seq, lambda e, d=d: (self._speed_step(d), "break")[-1])

        # ================================================================== header & footer
        def _build_header(self):
            header = tk.Frame(self, bg=THEME["bg_panel"], padx=14, pady=7)
            header.pack(fill=tk.X, side=tk.TOP)
            self._tool(header, "Setup & Status", self._show_setup_modal).pack(side=tk.RIGHT)
            self._label(header, "Silicon Pipeline", font="title", bg=THEME["bg_panel"]).pack(side=tk.LEFT)
            self.sub_lbl = self._wrapping_label(header, "", font="mono_small", fg=THEME["fg_secondary"], bg=THEME["bg_panel"])
            self.sub_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 10))
            self._update_header()

        def _update_header(self):
            g = self.gpu_info
            if g.get("warning"):
                self.sub_lbl.configure(text=f"{g.get('gpu_name')}  ·  ⚠ {g['warning']}", fg=THEME["warn"])
                return
            parts = [str(g.get("gpu_name"))]
            if "mlx_ok" in g:
                parts.append(f"MLX {'✓' if g['mlx_ok'] else '✗'}")
                parts.append(f"Metal {'✓' if g['mps_ok'] else '✗'}")
                parts.append("VideoToolbox ✓")
            ok = g.get("mlx_ok", True)
            self.sub_lbl.configure(text="  ·  ".join(parts), fg=THEME["fg_secondary"] if ok else THEME["warn"])

        def _build_footer(self):
            footer = tk.Frame(self, bg=THEME["bg_panel"], padx=14, pady=8)
            footer.pack(fill=tk.X, side=tk.BOTTOM)
            row = tk.Frame(footer, bg=THEME["bg_panel"])
            row.pack(fill=tk.X)
            self.btn_stop = self._tool(row, "■ Stop", self._stop_pipeline)
            self.btn_stop.configure(state=tk.DISABLED)
            self.btn_stop.pack(side=tk.RIGHT, padx=(6, 0))
            self.btn_run = self._tool(row, "▶  Run Pending Tasks", self._start_pipeline, primary=True)
            self.btn_run.pack(side=tk.RIGHT)
            self.lbl_status = self._wrapping_label(row, "Ready. Add videos (⌘O) or a merge task, check the task details, then Run (⌘R).",
                                                   font="small", fg=THEME["fg_secondary"], bg=THEME["bg_panel"])
            self.lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
            self.progress_var = tk.DoubleVar(value=0.0)
            ttk.Progressbar(footer, variable=self.progress_var, maximum=100.0, style="Horizontal.TProgressbar").pack(fill=tk.X, pady=(6, 0))

        # ================================================================== main split
        def _build_main_layout(self):
            self.paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.FLAT, bd=0, bg=THEME["bg_root"], showhandle=False)
            self.paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))
            left = tk.Frame(self.paned, bg=THEME["bg_panel"], padx=10, pady=8)
            right = tk.Frame(self.paned, bg=THEME["bg_root"])
            self.paned.add(left, minsize=300, stretch="always")
            self.paned.add(right, minsize=480, stretch="always")
            self.after(80, self._restore_sash)

            self._build_queue_panel(left)

            self.notebook = ttk.Notebook(right)
            self.notebook.pack(fill=tk.BOTH, expand=True)
            self.tab_settings = tk.Frame(self.notebook, bg=THEME["bg_panel"])
            self.tab_console = tk.Frame(self.notebook, bg=THEME["bg_panel"], padx=8, pady=8)
            self.tab_preview = tk.Frame(self.notebook, bg=THEME["bg_panel"], padx=10, pady=8)
            self.notebook.add(self.tab_settings, text="Settings")
            self.notebook.add(self.tab_console, text="Console")
            self.notebook.add(self.tab_preview, text="Transcript")
            self._build_settings_tab()
            self._build_console_tab()
            self._build_preview_tab()

        def _restore_sash(self):
            if self.paned.winfo_width() <= 1:
                self.after(100, self._restore_sash)
                return
            try:
                frac = min(0.6, max(0.22, float(self.settings.get("sash_fraction", 0.36))))
                self.paned.sash_place(0, int(self.paned.winfo_width() * frac), 0)
            except Exception:
                pass

        # ------------------------------------------------------------------ task queue
        def _build_queue_panel(self, panel):
            T = THEME
            top = tk.Frame(panel, bg=T["bg_panel"])
            top.pack(fill=tk.X)
            self._label(top, "TASKS", font="section", fg=T["fg_dim"], bg=T["bg_panel"]).pack(side=tk.LEFT)
            self.lbl_queue_counts = self._label(top, "empty", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
            self.lbl_queue_counts.pack(side=tk.RIGHT)

            bar = self._flow(panel, padx=4, pady=4, bg=T["bg_panel"])
            bar.pack(fill=tk.X, pady=(6, 2))
            bar.add(self._tool(bar, "+ Videos", self._add_videos, primary=True))
            bar.add(self._tool(bar, "+ Merge Task", self._add_merge_task))
            bar.add(self._tool(bar, "+ Folder", self._add_folder))
            bar.add(self._separator(bar))
            bar.add(self._tool(bar, "↑", self._move_up))
            bar.add(self._tool(bar, "↓", self._move_down))
            bar.add(self._tool(bar, "Remove", self._remove_selected))
            bar.add(self._tool(bar, "⋯", self._show_queue_menu_button))
            self._wrapping_label(panel, "+ Videos: one task per video  ·  + Merge Task: several videos → one merged video and transcript",
                                 font="small", fg=T["fg_dim"], bg=T["bg_panel"]).pack(fill=tk.X, pady=(0, 6))

            self._build_task_details(panel)

            tree_frame = tk.Frame(panel, bg=T["bg_input"])
            tree_frame.pack(fill=tk.BOTH, expand=True)
            tree_frame.rowconfigure(0, weight=1)
            tree_frame.columnconfigure(0, weight=1)
            cols = ("name", "steps", "status", "stage")
            self.queue_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="Queue.Treeview", selectmode="extended")
            for c, w, mw, stretch, heading in [
                ("name", 160, 90, True, "Task"),
                ("steps", 170, 90, True, "Steps"),
                ("status", 74, 60, False, "Status"),
                ("stage", 140, 70, True, "Stage"),
            ]:
                self.queue_tree.heading(c, text=heading, anchor="w")
                self.queue_tree.column(c, width=w, minwidth=mw, stretch=stretch, anchor="w")
            self.queue_tree.tag_configure("Pending", foreground=T["fg_primary"])
            self.queue_tree.tag_configure("Running", foreground="#38bdf8")
            self.queue_tree.tag_configure("Completed", foreground=T["ok"])
            self.queue_tree.tag_configure("Failed", foreground=T["err"])
            self.queue_tree.bind("<<TreeviewSelect>>", self._on_job_selected)
            self.queue_tree.bind("<Double-Button-1>", self._on_task_double_click)
            for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
                self.queue_tree.bind(seq, self._show_queue_context_menu)
            vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.queue_tree.yview)
            self.queue_tree.configure(yscrollcommand=vsb.set)
            self.queue_tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")

            self.ctx_menu = tk.Menu(self, tearoff=0)
            self.ctx_menu.add_command(label="Open Transcript", command=self._open_selected_transcript)
            self.ctx_menu.add_command(label="Show in Finder", command=self._open_selected_job_folder)
            self.ctx_menu.add_separator()
            self.ctx_menu.add_command(label="Combine Selected into One Merge Task", command=self._combine_selected)
            self.ctx_menu.add_command(label="Split Merge Task into Single Tasks", command=self._split_merge_task)
            self.ctx_menu.add_command(label="Use Current Settings", command=self._task_use_current_settings)
            self.ctx_menu.add_separator()
            self.ctx_menu.add_command(label="Reset to Pending", command=self._reset_selected_jobs)
            self.ctx_menu.add_command(label="Remove", command=self._remove_selected)
            self.ctx_menu.add_command(label="Clear Completed", command=self._clear_completed_jobs)

        def _build_task_details(self, panel):
            """Shows (and, while Pending, edits) exactly what the selected task will do."""
            T = THEME
            card = tk.Frame(panel, bg=T["bg_card"], padx=10, pady=8)
            card.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
            self.lbl_task_title = self._label(card, "TASK DETAILS — select a task", font="section", fg=T["accent"])
            self.lbl_task_title.pack(anchor="w", pady=(0, 4))
            form = tk.Frame(card, bg=T["bg_card"])
            form.pack(fill=tk.X)
            form.columnconfigure(1, weight=1)
            self._loading_details = False
            lab = lambda r, t: self._label(form, t, font="small", fg=T["fg_secondary"]).grid(row=r, column=0, sticky="nw", padx=(0, 8), pady=2)

            lab(0, "Name")
            self.var_task_name = tk.StringVar(value="")
            self.ent_task_name = self._entry(form, self.var_task_name, font="small")
            self.ent_task_name.grid(row=0, column=1, sticky="ew", pady=2)
            self.var_task_name.trace_add("write", lambda *a: self._on_task_name())

            lab(1, "Videos")
            vids = tk.Frame(form, bg=T["bg_card"])
            vids.grid(row=1, column=1, sticky="ew", pady=2)
            vids.columnconfigure(0, weight=1)
            self.lst_inputs = tk.Listbox(vids, height=3, bg=T["bg_input"], fg=T["fg_primary"], selectbackground=T["accent"], selectforeground="#ffffff",
                                         font=self.f["small"], activestyle="none", highlightthickness=1, highlightbackground=T["border"], relief=tk.FLAT,
                                         exportselection=False)
            self.lst_inputs.grid(row=0, column=0, sticky="ew")
            vbtn = tk.Frame(vids, bg=T["bg_card"])
            vbtn.grid(row=0, column=1, sticky="n", padx=(4, 0))
            self._task_buttons = []
            for text, cmd in (("↑", lambda: self._task_input_move(-1)), ("↓", lambda: self._task_input_move(+1)),
                              ("+", self._task_input_add), ("−", self._task_input_remove)):
                b = self._tool(vbtn, text, cmd)
                b.pack(side=tk.LEFT if text in "↑↓" else tk.LEFT, padx=(0, 2))
                self._task_buttons.append(b)

            lab(2, "Steps")
            self.var_task_compress = tk.BooleanVar(value=False)
            self.var_task_audio = tk.BooleanVar(value=False)
            self.var_task_whisper = tk.BooleanVar(value=False)
            steps = self._flow(form, padx=10, pady=2, bg=T["bg_card"])
            steps.grid(row=2, column=1, sticky="ew", pady=2)
            self.lbl_task_merge = self._label(steps, "", font="small_bold", fg=T["fg_primary"])
            steps.add(self.lbl_task_merge)
            self._task_checks = []
            for text, var in (("Compress", self.var_task_compress), ("Extract audio", self.var_task_audio), ("Transcribe", self.var_task_whisper)):
                chk = self._check(steps, text, var, font="small")
                steps.add(chk)
                self._task_checks.append(chk)
                var.trace_add("write", lambda *a: self._on_task_steps())

            lab(3, "Settings")
            srow = tk.Frame(form, bg=T["bg_card"])
            srow.grid(row=3, column=1, sticky="ew", pady=2)
            srow.columnconfigure(0, weight=1)
            self.lbl_task_settings = self._wrapping_label(srow, "", font="small", fg=T["fg_secondary"])
            self.lbl_task_settings.grid(row=0, column=0, sticky="ew")
            self.btn_task_settings = self._tool(srow, "Use current Settings", self._task_use_current_settings)
            self.btn_task_settings.grid(row=0, column=1, sticky="ne", padx=(6, 0))

            lab(4, "Output")
            orow = tk.Frame(form, bg=T["bg_card"])
            orow.grid(row=4, column=1, sticky="ew", pady=2)
            orow.columnconfigure(0, weight=1)
            self.lbl_task_output = self._wrapping_label(orow, "", font="small", fg=T["fg_secondary"])
            self.lbl_task_output.grid(row=0, column=0, sticky="ew")
            self.btn_task_output = self._tool(orow, "Change…", self._task_change_output)
            self.btn_task_output.grid(row=0, column=1, sticky="ne", padx=(6, 0))

            self.lbl_task_note = self._wrapping_label(card, "", font="small", fg=T["fg_dim"])
            self.lbl_task_note.pack(fill=tk.X, pady=(4, 0))
            self._show_task_details(None)

        def _show_queue_context_menu(self, event):
            row = self.queue_tree.identify_row(event.y)
            if row and row not in self.queue_tree.selection():
                self.queue_tree.selection_set(row)
            self.ctx_menu.tk_popup(event.x_root, event.y_root)

        def _show_queue_menu_button(self):
            self.ctx_menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

        # ------------------------------------------------------------------ settings
        def _card(self, parent, title: str):
            card = tk.Frame(parent, bg=THEME["bg_card"], padx=12, pady=10)
            self._label(card, title, font="section", fg=THEME["accent"]).pack(anchor="w", pady=(0, 6))
            form = tk.Frame(card, bg=THEME["bg_card"])
            form.pack(fill=tk.X)
            form.columnconfigure(1, weight=1)
            card.form = form
            card.row = 0
            return card

        def _form_label(self, card, text: str):
            self._label(card.form, text, font="small", fg=THEME["fg_secondary"]).grid(row=card.row, column=0, sticky="w", padx=(0, 10), pady=3)

        def _row_widget(self, card, label: str, widget):
            self._form_label(card, label)
            widget.grid(row=card.row, column=1, sticky="ew", pady=3)
            card.row += 1
            return widget

        def _row_combo(self, card, label: str, var, values: List[str], key: str):
            cb = ttk.Combobox(card.form, textvariable=var, values=values, state="readonly", font=self.f["body"], width=18)
            self._persist[key] = var
            self._combo_values[key] = list(values)
            return self._row_widget(card, label, cb)

        def _row_full(self, card, widget, pady=(3, 3)):
            widget.grid(row=card.row, column=0, columnspan=2, sticky="ew", pady=pady)
            card.row += 1
            return widget

        @staticmethod
        def _quality_text(v) -> str:
            rf = int(float(v))
            return f"RF {rf}  ·  VideoToolbox q{SystemProbe.rf_to_videotoolbox_q(rf)}"

        def _build_settings_tab(self):
            self.settings_scroll = ScrollFrame(self.tab_settings, bg=THEME["bg_panel"])
            self.settings_scroll.pack(fill=tk.BOTH, expand=True)
            self.cards_frame = tk.Frame(self.settings_scroll.inner, bg=THEME["bg_panel"], padx=10, pady=10)
            self.cards_frame.pack(fill=tk.BOTH, expand=True)
            T = THEME

            # --- Output
            out = self._card(self.cards_frame, "NEW TASKS  ·  DEFAULTS")
            self.var_stage_compress = tk.BooleanVar(value=True)
            self.var_stage_audio = tk.BooleanVar(value=True)
            self.var_stage_whisper = tk.BooleanVar(value=True)
            self._persist.update(stage_compress=self.var_stage_compress, stage_audio=self.var_stage_audio, stage_whisper=self.var_stage_whisper)
            dsteps = self._flow(out.form, padx=12, bg=T["bg_card"])
            for label, var in (("Compress", self.var_stage_compress), ("Extract audio", self.var_stage_audio), ("Transcribe", self.var_stage_whisper)):
                dsteps.add(self._check(dsteps, label, var))
            self._row_widget(out, "Steps", dsteps)
            self._row_full(out, self._wrapping_label(out.form, "New tasks start with these steps and a copy of all Settings below. Change a single task "
                                                               "under Task Details; 'Use current Settings' updates a pending task.", font="small", fg=T["fg_dim"]), pady=(0, 6))
            self.var_respect_llc = tk.BooleanVar(value=True)
            self.var_auto_subfolder = tk.BooleanVar(value=True)
            self._persist.update(respect_llc=self.var_respect_llc, auto_subfolder=self.var_auto_subfolder)
            self._row_full(out, self._check(out.form, "Save results in a subfolder next to the (first) video", self.var_auto_subfolder))
            self._row_full(out, self._wrapping_label(out.form, "Interviews/P01.mp4 → Interviews/P01/P01.srt, P01.transcript.json …", font="small", fg=T["fg_dim"]), pady=(0, 4))
            self._row_full(out, self._check(out.form, "Apply LosslessCut (.llc) trims if present", self.var_respect_llc))
            folder = tk.Frame(out.form, bg=T["bg_card"])
            folder.columnconfigure(0, weight=1)
            self.var_output_dir = tk.StringVar(value=str(Path.home() / "Movies" / "SiliconPipelineOut"))
            self._persist["output_dir"] = self.var_output_dir
            self._entry(folder, self.var_output_dir).grid(row=0, column=0, sticky="ew", ipady=2)
            self._tool(folder, "Browse…", self._browse_output).grid(row=0, column=1, padx=(6, 0))
            self._row_widget(out, "Output folder", folder)
            self._row_full(out, self._wrapping_label(out.form, "Used when subfolders are off (merge tasks get their own subfolder inside it).", font="small", fg=T["fg_dim"]), pady=(0, 0))

            # --- Video
            hb = self._card(self.cards_frame, "VIDEO  ·  STAGES 1–2")
            self.var_hb_encoder = tk.StringVar(value="vt_h265 (Apple Media Engine)")
            self._row_combo(hb, "Encoder", self.var_hb_encoder, [
                "vt_h265 (Apple Media Engine)",
                "vt_h265_10bit (Apple Media Engine)",
                "vt_h264 (Apple Media Engine)",
                "ffmpeg_hevc_videotoolbox (Media Engine, no HandBrake)",
                "ffmpeg_h264_videotoolbox (Media Engine, no HandBrake)",
                "svt_av1 (CPU - no AV1 HW encoder on Apple Silicon)",
                "x265 (CPU)",
                "x264 (CPU)",
            ], "hb_encoder")
            self.var_hb_quality = tk.IntVar(value=22)
            self._persist["hb_quality"] = self.var_hb_quality
            qf = tk.Frame(hb.form, bg=T["bg_card"])
            qf.columnconfigure(0, weight=1)
            self.lbl_hb_q = self._label(qf, self._quality_text(22), font="mono_small", fg=T["fg_secondary"])
            tk.Scale(qf, from_=14, to=32, orient=tk.HORIZONTAL, variable=self.var_hb_quality, showvalue=False, bg=T["bg_card"], fg=T["fg_primary"],
                     highlightthickness=0, troughcolor=T["bg_input"], sliderlength=16, width=10, bd=0,
                     command=lambda v: self.lbl_hb_q.config(text=self._quality_text(v))).grid(row=0, column=0, sticky="ew")
            self.lbl_hb_q.grid(row=0, column=1, padx=(8, 0))
            self._row_widget(hb, "Quality", qf)
            self.var_hb_res = tk.StringVar(value="Original")
            self._row_combo(hb, "Max resolution", self.var_hb_res, ["Original", "2160p (4K)", "1440p (2K)", "1080p (FHD)", "720p (HD)"], "hb_res")
            self.var_hb_fps = tk.StringVar(value="Same as source")
            self._row_combo(hb, "Frame rate", self.var_hb_fps, ["Same as source", "60 fps", "30 fps", "24 fps"], "hb_fps")
            self.var_hb_container = tk.StringVar(value="MKV")
            self._row_combo(hb, "Container", self.var_hb_container, ["MKV", "MP4"], "hb_container")
            self.var_hb_abitrate = tk.StringVar(value="192")
            self._row_combo(hb, "Audio kbps", self.var_hb_abitrate, ["128", "160", "192", "256", "320"], "hb_abitrate")

            # --- Audio & Whisper
            aw = self._card(self.cards_frame, "AUDIO & WHISPER  ·  STAGES 3–4")
            self.var_audio_format = tk.StringVar(value="WAV (16kHz Mono - Optimal for Whisper)")
            self._row_combo(aw, "Audio format", self.var_audio_format, ["WAV (16kHz Mono - Optimal for Whisper)", "WAV (48kHz Stereo)", "MP3 (192kbps)", "FLAC", "M4A"], "audio_format")
            self.var_speech_filter = tk.BooleanVar(value=False)
            self._persist["speech_filter"] = self.var_speech_filter
            self._row_full(aw, self._check(aw.form, "Voice bandpass filter (80 Hz – 8 kHz)", self.var_speech_filter))
            self.var_whisper_backend = tk.StringVar(value="MLX (Apple Silicon GPU)")
            self._row_combo(aw, "Backend", self.var_whisper_backend, ["MLX (Apple Silicon GPU)", "openai-whisper (CPU)"], "whisper_backend")
            self.var_whisper_model = tk.StringVar(value="large-v3-turbo")
            self._row_combo(aw, "Model", self.var_whisper_model, ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"], "whisper_model")
            self.var_whisper_lang = tk.StringVar(value="auto (Auto-Detect)")
            self._row_combo(aw, "Language", self.var_whisper_lang, WHISPER_LANGUAGES, "whisper_lang")
            self.var_whisper_prompt = tk.StringVar(value="")
            self._persist["whisper_prompt"] = self.var_whisper_prompt
            self._row_widget(aw, "Vocabulary", self._entry(aw.form, self.var_whisper_prompt, font="small"))
            self._row_full(aw, self._wrapping_label(aw.form, "Names, places, activity words Whisper should expect, comma-separated "
                                                              "(e.g. Robin, Sam, Riverside Club, tablet). Keep it short; it is saved per task.",
                                                    font="small", fg=T["fg_dim"]), pady=(0, 4))
            self._row_full(aw, self._wrapping_label(aw.form, "Set the language for single-language interviews; auto-detect only listens to the first 30 s.",
                                                    font="small", fg=T["fg_dim"]), pady=(2, 0))

            # --- Speakers & exports
            se = self._card(self.cards_frame, "SPEAKERS & EXPORTS")
            self.var_diarize_mode = tk.StringVar(value="PyTorch MPS Spectral (Offline - No Token Needed)")
            self._row_combo(se, "Diarization", self.var_diarize_mode, [
                "PyTorch MPS Spectral (Offline - No Token Needed)",
                "Pyannote.audio 3.1 Neural (Requires HF Token)",
                "Disabled (No Speaker Labels)",
            ], "diarize_mode")
            self.var_num_speakers = tk.IntVar(value=0)
            self._persist["num_speakers"] = self.var_num_speakers
            sp = tk.Frame(se.form, bg=T["bg_card"])
            tk.Spinbox(sp, from_=0, to=12, increment=1, textvariable=self.var_num_speakers, width=4, bg=T["bg_input"], fg=T["fg_primary"],
                       buttonbackground=T["bg_card"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"], font=self.f["body"]).pack(side=tk.LEFT)
            self._label(sp, "0 = detect automatically", font="small", fg=T["fg_dim"]).pack(side=tk.LEFT, padx=(8, 0))
            self._row_widget(se, "Speakers", sp)
            self.var_hf_token = tk.StringVar(value="")
            self.var_remember_token = tk.BooleanVar(value=True)
            self._persist["remember_token"] = self.var_remember_token
            self._stored_token = ""
            tok = tk.Frame(se.form, bg=T["bg_card"])
            tok.columnconfigure(0, weight=1)
            self._entry(tok, self.var_hf_token, show="•").grid(row=0, column=0, sticky="ew", ipady=2)
            self._check(tok, "Remember in Keychain", self.var_remember_token, font="small").grid(row=0, column=1, padx=(8, 0))
            self.var_remember_token.trace_add("write", lambda *a: self.after_idle(self._sync_token))
            self._row_widget(se, "HF token", tok)
            note = ("Only needed for pyannote. Stored in your macOS login Keychain, never in the settings file."
                    if KeychainSecret.available() else "Only needed for pyannote. Keychain not available here, so it is not saved.")
            self._row_full(se, self._wrapping_label(se.form, note, font="small", fg=T["fg_dim"]), pady=(0, 6))
            fmt = self._flow(se.form, padx=12, bg=T["bg_card"])
            self.var_fmt_srt = tk.BooleanVar(value=True)
            self.var_fmt_vtt = tk.BooleanVar(value=False)
            self.var_fmt_txt = tk.BooleanVar(value=True)
            self.var_fmt_rtf = tk.BooleanVar(value=True)
            self.var_fmt_para_rtf = tk.BooleanVar(value=True)
            self.var_fmt_para_txt = tk.BooleanVar(value=False)
            self._persist.update(fmt_srt=self.var_fmt_srt, fmt_vtt=self.var_fmt_vtt, fmt_txt=self.var_fmt_txt, fmt_rtf=self.var_fmt_rtf,
                                 fmt_para_rtf=self.var_fmt_para_rtf, fmt_para_txt=self.var_fmt_para_txt)
            for t, var in [("Paragraphs RTF (Word / MAXQDA)", self.var_fmt_para_rtf), ("Paragraphs TXT", self.var_fmt_para_txt),
                           ("SRT subtitles", self.var_fmt_srt), ("VTT subtitles", self.var_fmt_vtt), ("Segments TXT", self.var_fmt_txt),
                           ("RTF + confidence colours", self.var_fmt_rtf)]:
                fmt.add(self._check(fmt, t, var))
            self._row_widget(se, "Exports", fmt)
            self._row_full(se, self._wrapping_label(se.form, "Paragraphs = one block per speaker turn, for reading and coding. Subtitles (SRT/VTT) keep the "
                                                              "short timed segments for video. A <name>.transcript.json master copy is always saved; "
                                                              "all exports are regenerated from it after every edit.", font="small", fg=T["fg_dim"]), pady=(4, 0))

            self._cards = [out, hb, aw, se]
            self._card_cols = None
            self.cards_frame.bind("<Configure>", self._layout_cards)
            self._layout_cards()

        def _layout_cards(self, event=None):
            width = event.width if event else self.cards_frame.winfo_width()
            cols = 2 if width >= 760 else 1
            if cols == self._card_cols:
                return
            self._card_cols = cols
            for c in self._cards:
                c.grid_forget()
            for i in range(2):
                self.cards_frame.columnconfigure(i, weight=1 if i < cols else 0, uniform="cards" if i < cols else "")
            for i, card in enumerate(self._cards):
                r, c = (i // 2, i % 2) if cols == 2 else (i, 0)
                padx = ((0, 5) if c == 0 else (5, 0)) if cols == 2 else (0, 0)
                card.grid(row=r, column=c, sticky="nsew", padx=padx, pady=(0, 10))

        def _on_mousewheel(self, event):
            try:
                widget = self.winfo_containing(event.x_root, event.y_root)
            except Exception:
                widget = None
            if widget is not None and self.settings_scroll.contains(widget):
                d = event.delta
                units = (-d if abs(d) < 120 else -int(d / 120)) if IS_MAC else (-int(d / 120) if d else 0)
                if units:
                    self.settings_scroll.scroll(units)
                return "break"
            if "combobox" in str(event.widget) and "popdown" not in str(event.widget):
                return "break"
            return None

        # ------------------------------------------------------------------ console
        def _build_console_tab(self):
            frame = tk.Frame(self.tab_console, bg=THEME["bg_panel"])
            frame.pack(fill=tk.BOTH, expand=True)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            self.console_text = tk.Text(frame, bg="#0a0c10", fg=THEME["fg_primary"], font=self.f["mono_small"], relief=tk.FLAT, padx=10, pady=8,
                                        wrap=tk.WORD, highlightthickness=0, spacing1=1)
            sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.console_text.yview)
            self.console_text.configure(yscrollcommand=sb.set)
            self.console_text.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            self.console_text.tag_config("INFO", foreground="#c9ced8")
            self.console_text.tag_config("STAGE", foreground="#38bdf8", font=self.f["mono_small_bold"])
            self.console_text.tag_config("CMD", foreground="#8b80c9")
            self.console_text.tag_config("SUCCESS", foreground=THEME["ok"], font=self.f["mono_small_bold"])
            self.console_text.tag_config("WARN", foreground=THEME["warn"])
            self.console_text.tag_config("ERROR", foreground=THEME["err"], font=self.f["mono_small_bold"])

        def _append_log(self, msg: str, level: str = "INFO"):
            ts = datetime.now().strftime("%H:%M:%S")
            at_bottom = self.console_text.yview()[1] >= 0.999
            self.console_text.insert(tk.END, f"{ts}  {msg}\n", level)
            lines = int(self.console_text.index("end-1c").split(".")[0])
            if lines > 8000:
                self.console_text.delete("1.0", f"{lines - 6000}.0")
            if at_bottom:
                self.console_text.see(tk.END)

        # ------------------------------------------------------------------ transcript editor
        PLAYER_LATENCY_S = 0.25
        EDIT_HINT = ("The box shows exactly the highlighted segment. Unchanged words keep their exact timing; corrected words are "
                     "timed within the words they replace and marked as checked. Every change is logged in the .transcript.json.")  # ffplay start-up delay; keeps the follow-along highlight in sync

        def _build_preview_tab(self):
            T = THEME
            tab = self.tab_preview

            bar = self._flow(tab, padx=5, pady=5, bg=T["bg_panel"])
            bar.pack(fill=tk.X)
            bar.add(self._tool(bar, "Open…", self._open_transcript))
            bar.add(self._tool(bar, "Speakers…", self._show_speakers_dialog))
            bar.add(self._tool(bar, "Detect speakers…", self._rediarize_dialog))
            bar.add(self._separator(bar))
            self.var_view_mode = tk.StringVar(value="Paragraphs")
            self._persist["view_mode"] = self.var_view_mode
            self._combo_values["view_mode"] = ["Paragraphs", "Segments"]
            cb = ttk.Combobox(bar, textvariable=self.var_view_mode, values=["Paragraphs", "Segments"], state="readonly", width=10, font=self.f["small"])
            cb.bind("<<ComboboxSelected>>", lambda e: self._rerender())
            bar.add(cb)
            bar.add(self._separator(bar))
            bar.add(self._tool(bar, "◀ Uncertain", lambda: self._jump_low_conf(-1)))
            bar.add(self._tool(bar, "Uncertain ▶", lambda: self._jump_low_conf(+1)))
            self.var_autoplay = tk.BooleanVar(value=True)
            self._persist["autoplay"] = self.var_autoplay
            bar.add(self._check(bar, "Play snippet on jump", self.var_autoplay, bg=T["bg_panel"], font="small"))
            self._persist["show_pct"] = self.var_show_pct

            player = self._flow(tab, padx=5, pady=5, bg=T["bg_card"])
            player.pack(fill=tk.X, pady=(4, 0), ipady=3)
            player.add(self._tool(player, "⟲ 5 s", lambda: self._seek_rel(-5)))
            self.btn_play = self._tool(player, "▶ Play", self._toggle_play, primary=True)
            player.add(self.btn_play)
            player.add(self._tool(player, "5 s ⟳", lambda: self._seek_rel(+5)))
            self.var_speed = tk.StringVar(value="1×")
            self._persist["speed"] = self.var_speed
            self._combo_values["speed"] = ["0.5×", "0.6×", "0.75×", "0.9×", "1×", "1.1×", "1.25×", "1.4×", "1.5×", "1.75×", "2×"]
            sp = ttk.Combobox(player, textvariable=self.var_speed, values=self._combo_values["speed"], state="readonly", width=6, font=self.f["small"])
            sp.bind("<<ComboboxSelected>>", lambda e: self._on_speed_change())
            player.add(sp)
            self.lbl_time = self._label(player, "0:00:00 / 0:00:00", font="mono_small", fg=T["fg_secondary"])
            player.add(self.lbl_time)
            self.var_follow = tk.BooleanVar(value=True)
            self._persist["follow"] = self.var_follow
            player.add(self._check(player, "Follow text", self.var_follow, font="small"))
            self._persist["show_video"] = self.var_show_video
            player.add(self._check(player, "Video", self.var_show_video, font="small"))
            self.var_show_video.trace_add("write", lambda *a: self.after_idle(self._toggle_video_panel))
            player.add(self._tool(player, "QuickTime ↗", self._open_in_quicktime))
            player.add(self._label(player, "Esc play/pause · ⌘[ ⌘] ±5 s · ⌘⇧. ⌘⇧, speed", font="small", fg=T["fg_dim"]))

            self.timeline = tk.Canvas(tab, height=30, bg=T["bg_input"], highlightthickness=0, bd=0, cursor="hand2")
            self.timeline.pack(fill=tk.X, pady=(4, 0))
            self.timeline.bind("<Configure>", lambda e: self._timeline_redraw())
            self.timeline.bind("<Button-1>", self._timeline_click)
            self.timeline.bind("<B1-Motion>", self._timeline_click)
            self._tl_playhead = None

            info = tk.Frame(tab, bg=T["bg_panel"])
            info.pack(fill=tk.X, pady=(6, 4))
            self.lbl_ci_summary = self._wrapping_label(info, "No transcript open. Select a finished job or use Open… (⇧⌘O).", font="small_bold", bg=T["bg_panel"])
            self.lbl_ci_summary.pack(fill=tk.X)
            self.lbl_word = self._wrapping_label(info, "", font="mono_small", fg=T["fg_secondary"], bg=T["bg_panel"])
            self.lbl_word.pack(fill=tk.X)

            # --- segment editor (bottom) ---
            ep = tk.Frame(tab, bg=T["bg_card"], padx=10, pady=8)
            ep.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
            row = self._flow(ep, padx=6, pady=4, bg=T["bg_card"])
            row.pack(fill=tk.X)
            self.edit_row = row
            self.lbl_edit_title = self._label(row, "Click a word to edit its segment", font="small_bold")
            row.add(self.lbl_edit_title)
            row.add(self._label(row, "Speaker", font="small", fg=T["fg_secondary"]))
            self.var_seg_speaker = tk.StringVar(value="")
            self.cmb_seg_speaker = ttk.Combobox(row, textvariable=self.var_seg_speaker, values=[], width=16, font=self.f["small"])
            row.add(self.cmb_seg_speaker)
            row.add(self._tool(row, "Split before word", self._split_at_current_word))
            row.add(self._tool(row, "Merge with next", self._merge_with_next))
            self.btn_checked = self._tool(row, "Mark checked", self._toggle_checked)
            row.add(self.btn_checked)
            row.add(self._tool(row, "Markers ▾", self._show_marker_menu))
            row.add(self._tool(row, "+ Segment", self._insert_segment_dialog))
            row.add(self._tool(row, "Re-transcribe…", self._retranscribe_dialog))
            row.add(self._tool(row, "Delete", self._delete_segment))
            row.add(self._tool(row, "Revert", self._revert_segment_edit))
            row.add(self._tool(row, "Save  ⌘S", self._save_segment_edit))
            row.add(self._tool(row, "✓ Save & next  ⌘↩", self._check_and_next, primary=True))
            self.edit_text = tk.Text(ep, height=3, wrap=tk.WORD, font=self.f["transcript"], bg=T["bg_input"], fg=T["fg_primary"],
                                     insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                                     highlightcolor=T["accent"], padx=8, pady=6, undo=True)
            self.spk_chips = self._flow(ep, padx=4, pady=4, bg=T["bg_card"])
            self.spk_chips.pack(fill=tk.X, pady=(6, 0))
            self.edit_text.pack(fill=tk.X, pady=(6, 2))
            self.edit_text.bind(f"<{MOD}-Return>", lambda e: (self._check_and_next(), "break")[-1])
            for d in range(1, 10):
                self.bind_all(f"<Control-Key-{d}>", lambda e, d=d: (self._assign_speaker_number(d), "break")[-1])
            self.lbl_edit_hint = self._wrapping_label(ep, self.EDIT_HINT, font="small", fg=T["fg_dim"])
            self.lbl_edit_hint.pack(fill=tk.X)
            self.edit_seg_idx: Optional[int] = None
            self.edit_original = ("", "")

            self.tx_paned = tk.PanedWindow(tab, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.FLAT, bd=0, bg=T["bg_panel"], showhandle=False)
            self.tx_paned.pack(fill=tk.BOTH, expand=True)
            frame = tk.Frame(self.tx_paned, bg=T["bg_panel"])
            self.tx_paned.add(frame, minsize=300, stretch="always")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            self._build_video_panel()
            self.preview_text = tk.Text(frame, bg="#0b0d13", fg=T["fg_primary"], font=self.f["transcript"], relief=tk.FLAT, padx=16, pady=12,
                                        wrap=tk.WORD, highlightthickness=0, cursor="arrow", spacing1=2, spacing3=2, state=tk.DISABLED)
            sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.preview_text.yview)
            self.preview_text.configure(yscrollcommand=sb.set)
            self.preview_text.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            pt = self.preview_text
            pt.tag_config("SEG_HDR", foreground=T["fg_dim"], font=self.f["mono_small"], spacing1=12)
            for i, color in enumerate(SPEAKER_COLORS):
                pt.tag_config(f"SPK{i}", foreground=color, font=self.f["mono_small_bold"], spacing1=12)
            pt.tag_config("HIGH", foreground=T["fg_primary"])
            pt.tag_config("MED", foreground="#f5c56b")
            pt.tag_config("LOW", foreground="#fb923c")
            pt.tag_config("POOR", foreground="#f87171", underline=True)
            pt.tag_config("PCT", foreground="#f87171", font=self.f["mono_small"])
            pt.tag_config("EDITSEG", background="#1c2a40")
            pt.tag_config("PLAYING", background="#1d3a2e")
            pt.tag_config("CURRENT", background="#3b4a63")
            pt.tag_config("MARKER", foreground="#8fb3ff", font=self.f["marker"])
            pt.tag_config("CHECKMARK", foreground=T["ok"], font=self.f["small_bold"])
            pt.bind("<ButtonRelease-1>", self._on_text_click)
            pt.bind("<Double-Button-1>", lambda e: (self._on_text_click(e), self._play_from_current_word(), "break")[-1])
            pt.bind("<Right>", lambda e: (self._jump_low_conf(+1), "break")[-1])
            pt.bind("<Left>", lambda e: (self._jump_low_conf(-1), "break")[-1])
            pt.bind("<space>", lambda e: (self._toggle_play(), "break")[-1])

            # player state
            self.playing = False
            self.play_pos = 0.0
            self._play_from = 0.0
            self._play_t0 = 0.0
            self._play_until: Optional[float] = None
            self._play_speed = 1.0
            self._tick_job = None
            self.span_starts: List[float] = []
            self.span_lookup: Dict[Tuple[int, int], int] = {}
            self.seg_ranges: Dict[int, Tuple[str, str]] = {}

        # ----- rendering -----
        def _rerender(self, keep_view: bool = True):
            if self.active_doc is None:
                return
            keep = None
            if self.current_span is not None and self.current_span < len(self.word_spans):
                sp = self.word_spans[self.current_span]
                keep = (sp["seg"], sp["w"])
            top = self.preview_text.yview()[0] if keep_view else 0.0
            self._render_doc(self.active_doc, reset_editor=False)
            try:
                self.preview_text.yview_moveto(top)
            except Exception:
                pass
            if keep is not None:
                idx = self.span_lookup.get(keep) or self.span_lookup.get((keep[0], 0))
                if idx is not None:
                    self._select_span(idx, scroll=False, load_editor=False)
            self._highlight_edit_segment()

        def _render_doc(self, doc: TranscriptDocument, reset_editor: bool = True):
            if reset_editor and self.active_doc is not None and doc is not self.active_doc:
                self._remember_position()
                self._autosave_edit(rerender=False)  # the old document is about to be replaced
                self._player_pause()
            new_doc = doc is not self.active_doc
            self.active_doc = doc
            if new_doc:
                synced = doc.sync_text_with_words()
                removed = doc.remove_empty_segments()
                if synced or removed:
                    try:
                        doc.save()
                        doc.export_all()
                        parts = []
                        if synced:
                            parts.append(f"matched the text of {synced} segment(s) to their timed words")
                        if removed:
                            parts.append(f"removed {removed} empty segment(s)")
                        self._append_log(f"{doc.base_name}: {' and '.join(parts)}; exports regenerated, logged in the edit history.", "INFO")
                    except Exception as exc:
                        self._append_log(f"Could not save cleanup: {exc}", "WARN")
            report = doc.confidence_report()
            high_t, med_t, poor_t = report["high_thresh"], report["med_thresh"], report["poor_thresh"]
            show_pct = bool(self.var_show_pct.get())
            speakers = doc.speakers()
            roster = doc.roster()
            spk_tag = {spk: f"SPK{i % len(SPEAKER_COLORS)}" for i, spk in enumerate(roster)}
            rsegs = report["segments"]
            if self.var_view_mode.get() == "Paragraphs":
                groups = [p["segments"] for p in doc.paragraphs()]
            else:
                groups = [[i] for i in range(len(rsegs)) if not is_blank_text(rsegs[i].get("text"))]

            txt = self.preview_text
            txt.configure(state=tk.NORMAL)
            txt.delete("1.0", tk.END)
            spans: List[Dict] = []
            line_spans: Dict[int, List[int]] = {}
            low: List[int] = []
            seg_ranges: Dict[int, Tuple[str, str]] = {}
            pos = {"line": 1, "col": 0}

            def put(s: str, *tags):
                if tags:
                    txt.insert(tk.END, s, tags)
                else:
                    txt.insert(tk.END, s)
                n = s.count("\n")
                if n:
                    pos["line"] += n
                    pos["col"] = len(s.rsplit("\n", 1)[1])
                else:
                    pos["col"] += len(s)

            for group in groups:
                first, last = rsegs[group[0]], rsegs[group[-1]]
                spk = first.get("speaker")
                if spk:
                    put(f"{spk}  ", spk_tag.get(spk, "SPK0"))
                put(f"{short_ts(first['start'])} – {short_ts(last['end'])}\n", "SEG_HDR")
                for si in group:
                    seg = rsegs[si]
                    seg_start = f"{pos['line']}.{pos['col']}"
                    words = seg["words"]
                    if doc.segments[si].get("checked"):
                        put((" " if pos["col"] > 0 else "") + "✓", "CHECKMARK")
                    in_marker = False
                    if words:
                        for wi, w in enumerate(words):
                            w_str = str(w.get("word", ""))
                            if wi == 0 and pos["col"] > 0 and not w_str.startswith(" "):
                                put(" ")
                            prob = float(w.get("probability", w.get("prob", 0.85)))
                            tag = "HIGH" if prob >= high_t else ("MED" if prob >= med_t else ("LOW" if prob >= poor_t else "POOR"))
                            token = w_str.strip()
                            if token.startswith("["):
                                in_marker = True
                            if in_marker:
                                tag = "MARKER"
                                if token.endswith("]"):
                                    in_marker = False
                            c0 = pos["col"] + (len(w_str) - len(w_str.lstrip()))
                            put(w_str, tag)
                            c1 = pos["col"]
                            if show_pct and prob < poor_t:
                                put(f"{int(round(prob * 100))}%", "PCT")
                            spans.append({"line": pos["line"], "c0": c0, "c1": c1, "start": float(w.get("start", seg["start"])),
                                          "end": float(w.get("end", seg["end"])), "prob": prob, "word": w_str.strip(),
                                          "speaker": seg.get("speaker"), "seg": si, "w": wi})
                            line_spans.setdefault(pos["line"], []).append(len(spans) - 1)
                            if prob < med_t:
                                low.append(len(spans) - 1)
                    else:
                        put((" " if pos["col"] > 0 else "") + (seg["text"] or "…"), "HIGH")
                    seg_ranges[si] = (seg_start, f"{pos['line']}.{pos['col']}")
                put("\n")
            txt.configure(state=tk.DISABLED)
            for tag in ("EDITSEG", "PLAYING", "CURRENT"):
                txt.tag_raise(tag)

            self.word_spans, self.line_spans, self.low_conf, self.current_span = spans, line_spans, low, None
            self.span_starts = [sp["start"] for sp in spans]
            self.span_lookup = {(sp["seg"], sp["w"]): i for i, sp in enumerate(spans)}
            self.seg_ranges = seg_ranges
            total = max(1, len(spans))
            edited = sum(1 for s in doc.segments if s.get("edited"))
            self.lbl_ci_summary.config(
                text=f"{doc.base_name}   ·   {doc.checked_fraction() * 100:.0f}% checked   ·   {len(spans)} words   ·   "
                     f"{len(low)} uncertain ({len(low) * 100 / total:.0f}%)   ·   mean word probability {report['overall_ci'][0] * 100:.0f}%   ·   "
                     f"{len(roster)} speaker(s)" + (f"   ·   {edited} edited" if edited else "")
            )
            self.cmb_seg_speaker.configure(values=roster)
            self._refresh_speaker_chips()
            if new_doc:
                self.play_pos = 0.0
                self._clear_editor()
                self._update_task_progress(doc)
                media = self._media()
                if media:
                    self.lbl_word.config(text="Click a word to select it and edit its segment · double-click plays from there · ←/→ uncertain words",
                                         fg=THEME["fg_dim"])
                else:
                    self.lbl_word.config(text="⚠ Playback media not found (moved or deleted?) — see 'media' in the .transcript.json", fg=THEME["warn"])
                self._resume_position(doc)
            self._update_time_label()
            self._timeline_redraw()
            if new_doc:
                self._video_stop()
                self._toggle_video_panel()
                self._video_still(self.play_pos)

        def _select_span(self, idx: int, autoplay: bool = False, scroll: bool = True, load_editor: bool = True):
            if not (0 <= idx < len(self.word_spans)):
                return
            sp = self.word_spans[idx]
            if load_editor and sp["seg"] != self.edit_seg_idx and self._editor_dirty():
                target = (sp["start"], sp["word"])  # by time + text: segment numbers may shift if the edit deleted a segment
                self._save_segment_edit(reload=False)  # never lose an edit by clicking elsewhere
                idx = next((i for i, x in enumerate(self.word_spans) if x["start"] == target[0] and x["word"] == target[1]), None)
                if idx is None:
                    return
                sp = self.word_spans[idx]
            self.current_span = idx
            a, b = f"{sp['line']}.{sp['c0']}", f"{sp['line']}.{sp['c1']}"
            self.preview_text.tag_remove("CURRENT", "1.0", tk.END)
            self.preview_text.tag_add("CURRENT", a, b)
            if scroll:
                self.preview_text.see(a)
            pos = f"   ·   uncertain {self.low_conf.index(idx) + 1} of {len(self.low_conf)}" if idx in self.low_conf else ""
            spk = f"   ·   {sp['speaker']}" if sp.get("speaker") else ""
            self.lbl_word.config(text=f"“{sp['word']}”   {short_ts(sp['start'])}   ·   p {sp['prob'] * 100:.0f}%{spk}{pos}", fg=THEME["fg_secondary"])
            if load_editor and sp["seg"] != self.edit_seg_idx:
                self._load_segment_into_editor(sp["seg"])
            if not self.playing:
                self.play_pos = max(0.0, sp["start"] - 0.5)
                self._update_time_label()
                self._video_still(sp["start"])
            if autoplay:
                self._player_start(max(0.0, sp["start"] - 1.0), duration=max(3.0, sp["end"] - sp["start"] + 2.5))

        def _on_text_click(self, event):
            self.preview_text.focus_set()
            index = self.preview_text.index(f"@{event.x},{event.y}")
            line, col = (int(v) for v in index.split("."))
            for i in self.line_spans.get(line, []):
                sp = self.word_spans[i]
                if sp["c0"] <= col < max(sp["c1"], sp["c0"] + 1):
                    self._select_span(i)
                    if self.playing:
                        self._player_start(max(0.0, sp["start"] - 0.5))
                    return

        def _jump_low_conf(self, direction: int):
            if not self.low_conf:
                self.lbl_word.config(text="No uncertain words in this transcript.")
                return
            cur = self.current_span if self.current_span is not None else (-1 if direction > 0 else len(self.word_spans))
            if direction > 0:
                nxt = next((i for i in self.low_conf if i > cur), self.low_conf[0])
            else:
                nxt = next((i for i in reversed(self.low_conf) if i < cur), self.low_conf[-1])
            self._select_span(nxt, autoplay=bool(self.var_autoplay.get()))

        # ----- segment editor -----
        def _clear_editor(self):
            self.edit_seg_idx = None
            self.edit_original = ("", "")
            self.edit_text.delete("1.0", tk.END)
            self.var_seg_speaker.set("")
            self.lbl_edit_title.config(text="Click a word to edit its segment")
            self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)

        def _editor_dirty(self) -> bool:
            if self.edit_seg_idx is None:
                return False
            text = " ".join(self.edit_text.get("1.0", "end-1c").split())
            return text != " ".join(self.edit_original[0].split()) or self.var_seg_speaker.get().strip() != self.edit_original[1]

        def _load_segment_into_editor(self, seg_idx: int):
            doc = self.active_doc
            if doc is None or not (0 <= seg_idx < len(doc.segments)):
                return
            seg = doc.segments[seg_idx]
            words = seg.get("words") or []
            # exactly what is highlighted in the transcript: the timed words
            text = ("".join(str(w.get("word", "")) for w in words) if words else (seg.get("text") or "")).strip()
            speaker = seg.get("speaker") or ""
            self.edit_seg_idx = seg_idx
            self.edit_original = (text, speaker)
            self.edit_text.delete("1.0", tk.END)
            self.edit_text.insert("1.0", text)
            self.edit_text.edit_reset()
            self.var_seg_speaker.set(speaker)
            self._refresh_speaker_chips()
            mark = ("  ✎" if seg.get("edited") else "") + ("  ✓" if seg.get("checked") else "")
            self.btn_checked.configure(text="✓ Checked" if seg.get("checked") else "Mark checked")
            self.lbl_edit_title.config(text=f"Segment {seg_idx + 1}/{len(doc.segments)}  ·  {short_ts(seg.get('start', 0))} – {short_ts(seg.get('end', 0))}{mark}")
            self.after_idle(self.edit_row.relayout)  # the title's width changed
            orig = seg.get("text_whisper")
            rt = seg.get("retranscribed") or {}
            if rt and not seg.get("edited"):
                what = "translated to English" if rt.get("task") == "translate" else "re-transcribed"
                self.lbl_edit_hint.config(text=f"This segment was {what} with Whisper {rt.get('model')} (language: {rt.get('language')}). "
                                               "Undo (⌘Z) brings back the previous version.", fg="#8fb3ff")
            elif orig and " ".join(str(orig).split()) != " ".join(text.split()) and not seg.get("edited"):
                self.lbl_edit_hint.config(text=f"Whisper's own text for this segment differed: “{str(orig).strip()[:200]}” — listen and correct if needed.",
                                          fg=THEME["warn"])
            else:
                self.lbl_edit_hint.config(text=self.EDIT_HINT, fg=THEME["fg_dim"])
            self._highlight_edit_segment()

        def _highlight_edit_segment(self):
            self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)
            rng = self.seg_ranges.get(self.edit_seg_idx) if self.edit_seg_idx is not None else None
            if rng:
                self.preview_text.tag_add("EDITSEG", *rng)

        def _commit(self, message: str, undoable: bool = True) -> Optional[Dict[str, Path]]:
            """Save the master file (with a throttled backup) and regenerate every export.
            The file as it was before is pushed on the undo stack. Returns the files written, or None."""
            doc = self.active_doc
            try:
                previous = doc.path.read_text(encoding="utf-8") if (undoable and doc.path.exists()) else None
                doc.save(backup=True)
                created = doc.export_all()
            except Exception as exc:
                messagebox.showerror("Could not save transcript", str(exc), parent=self)
                return None
            if previous is not None:
                undo, redo = self._undo_stacks.setdefault(str(doc.path), ([], []))
                undo.append((message, previous))
                del undo[:-100]
                redo.clear()
            self.lbl_status.config(text=message)
            self._update_task_progress(doc)
            self._timeline_redraw()
            return created

        # ----- undo / redo / backups -----
        def _text_input_focused(self) -> bool:
            w = self.focus_get()
            return w is not None and w is not self.preview_text and w.winfo_class() in ("Text", "Entry", "TEntry", "TCombobox", "Spinbox")

        def _undo_key(self, event=None):
            if self._text_input_focused():
                return None  # let the text field undo its own typing
            self._undo()
            return "break"

        def _redo_key(self, event=None):
            if self._text_input_focused():
                return None
            self._redo()
            return "break"

        def _swap_state(self, from_stack: List, to_stack: List, verb: str):
            doc = self.active_doc
            label, text = from_stack.pop()
            to_stack.append((label, doc.path.read_text(encoding="utf-8")))
            keep_seg = self.edit_seg_idx
            doc.data = json.loads(text)
            try:
                doc.save(backup=True)
                doc.export_all()
            except Exception as exc:
                messagebox.showerror("Could not save transcript", str(exc), parent=self)
                return
            self.edit_original = ("", "")
            self._rerender()
            if doc.segments:
                self._load_segment_into_editor(min(keep_seg or 0, len(doc.segments) - 1))
            self._update_task_progress(doc)
            self.lbl_status.config(text=f"{verb}: {label}")
            self._append_log(f"{verb}: {label}", "INFO")

        def _undo(self):
            doc = self.active_doc
            if doc is None:
                return
            if self._editor_dirty():
                self._revert_segment_edit()
                self.lbl_status.config(text="Undid the unsaved edit in the segment box.")
                return
            undo, redo = self._undo_stacks.get(str(doc.path), ([], []))
            if not undo:
                self.lbl_status.config(text="Nothing to undo for this transcript (since it was opened).")
                return
            self._swap_state(undo, redo, "Undid")

        def _redo(self):
            doc = self.active_doc
            if doc is None:
                return
            undo, redo = self._undo_stacks.get(str(doc.path), ([], []))
            if not redo:
                self.lbl_status.config(text="Nothing to redo.")
                return
            self._swap_state(redo, undo, "Redid")

        def _restore_backup(self):
            doc = self.active_doc
            if doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return
            if not doc.list_backups():
                messagebox.showinfo("Backups", "No backups yet for this transcript. They are made automatically while you edit "
                                               f"(at most every {doc.BACKUP_EVERY_S // 60} minutes, the last {doc.BACKUPS_KEPT} are kept).", parent=self)
                return
            path = filedialog.askopenfilename(title="Restore which backup?", initialdir=str(doc.backup_dir),
                                              filetypes=[("Transcript backup", f"*{TranscriptDocument.SUFFIX}")])
            if not path:
                return
            try:
                data = TranscriptDocument.load(Path(path)).data
            except Exception as exc:
                messagebox.showerror("Could not read backup", str(exc), parent=self)
                return
            self._autosave_edit()
            data["base_name"] = doc.base_name
            doc.data = data
            doc.log_edit("restore_backup", file=Path(path).name)
            if self._commit(f"Restored backup {Path(path).name}") is not None:
                self.edit_original = ("", "")
                self._rerender()

        def _show_backups(self):
            doc = self.active_doc
            if doc is not None:
                folder = doc.backup_dir if doc.backup_dir.exists() else doc.out_dir
                subprocess.Popen(["open", str(folder)])

        # ----- resume where you left off -----
        def _remember_position(self):
            doc = self.active_doc
            if doc is None:
                return
            positions = self.settings.setdefault("positions", {})
            positions[str(doc.path)] = {"pos": round(self._current_pos(), 2), "seg": self.edit_seg_idx}
            if len(positions) > 300:
                for k in list(positions)[: len(positions) - 300]:
                    positions.pop(k, None)

        def _resume_position(self, doc: TranscriptDocument):
            saved = (self.settings.get("positions") or {}).get(str(doc.path)) or {}
            pos = float(saved.get("pos", 0.0) or 0.0)
            seg = saved.get("seg")
            self._pending_resume = (pos, seg) if (pos or seg is not None) else None
            self.after_idle(self._apply_resume)

        def _apply_resume(self):
            pending = self.__dict__.get("_pending_resume")
            self._pending_resume = None
            doc = self.active_doc
            if not pending or doc is None:
                return
            pos, seg = pending
            if seg is not None and 0 <= seg < len(doc.segments):
                idx = self.span_lookup.get((seg, 0))
                if idx is not None:
                    self._select_span(idx)
                else:
                    self._load_segment_into_editor(seg)
            self.play_pos = pos
            self._update_time_label()
            if self._media() is not None:
                self.lbl_word.config(text=f"Resumed where you left off: {short_ts(pos)[:-2]} (segment {seg + 1 if seg is not None else '–'}).",
                                     fg=THEME["fg_secondary"])

        # ----- checked marks -----
        def _update_task_progress(self, doc: TranscriptDocument):
            pct = round(doc.checked_fraction() * 100)
            changed = False
            for t in self.jobs:
                if t.get("doc_path") == str(doc.path):
                    t["checked_pct"] = pct
                    if t["status"] == "Completed":
                        t["stage"] = f"Done ✓ · {pct}% checked"
                    self._update_job_row(t)
                    changed = True
            if changed:
                self._save_queue()

        def _toggle_checked(self):
            doc, idx = self.active_doc, self.edit_seg_idx
            if doc is None or idx is None:
                self.lbl_status.config(text="Click a word to pick a segment first.")
                return
            self._autosave_edit(rerender=False)
            now = not bool(doc.segments[idx].get("checked"))
            doc.set_checked(idx, now)
            if self._commit(f"Segment {idx + 1} marked {'checked' if now else 'not checked'}.") is not None:
                self._rerender()
                self._load_segment_into_editor(idx)

        def _check_and_next(self):
            """Save the segment (text + speaker), mark it checked and move to the next one: the review loop."""
            doc, idx = self.active_doc, self.edit_seg_idx
            if doc is None or idx is None:
                self.lbl_status.config(text="Click a word to pick a segment first.")
                return
            text = self.edit_text.get("1.0", "end-1c")
            if is_blank_text(text) and not is_blank_text(self.edit_original[0]):
                self._delete_segment()
                return
            doc.edit_segment_text(idx, text)
            doc.set_segment_speaker(idx, self.var_seg_speaker.get())
            doc.set_checked(idx, True)
            if self._commit(f"Checked segment {idx + 1}.") is None:
                return
            self.edit_original = ("", "")
            self._rerender()
            nxt = next((i for i in range(idx + 1, len(doc.segments)) if not is_blank_text(doc.segments[i].get("text"))), None)
            if nxt is None:
                self._load_segment_into_editor(idx)
                self.lbl_status.config(text=f"Checked segment {idx + 1} — that was the last one. {doc.checked_fraction() * 100:.0f}% checked.")
                return
            first = self.span_lookup.get((nxt, 0))
            if first is not None:
                self._select_span(first)
            else:
                self._load_segment_into_editor(nxt)
            self.edit_text.focus_set()

        # ----- markers -----
        def _show_marker_menu(self):
            self.marker_menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

        def _insert_marker(self, template: str):
            if self.active_doc is None or self.edit_seg_idx is None:
                self.lbl_status.config(text="Click a word to open its segment, then insert the marker where it belongs.")
                return
            text = template.replace("{t}", short_ts(self._current_pos())[:-2])
            box = self.edit_text
            box.focus_set()
            before = box.get("insert-1c", "insert")
            if before and not before.isspace():
                text = " " + text
            box.insert("insert", text + ("" if text.endswith(": ]") else " "))
            if text.endswith(": ]"):  # [unclear: |] — put the cursor where the guess goes
                box.mark_set("insert", "insert-1c")

        # ----- timeline strip -----
        @staticmethod
        def _dim(color: str, f: float = 0.45, bg: str = "#11141b") -> str:
            c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{int(b[k] + (c[k] - b[k]) * f):02x}" for k in range(3))

        def _timeline_items(self, width: int, height: int = 30) -> List[Tuple]:
            """Shapes to draw: ('band', x0, x1, colour), ('check', x0, x1), ('tick', x). Pure geometry, so it can be tested."""
            doc = self.active_doc
            dur = self._duration()
            if doc is None or dur <= 0 or width <= 2:
                return []
            sx = width / dur
            roster = doc.roster()
            items: List[Tuple] = []
            for seg in doc.segments:
                x0, x1 = float(seg.get("start", 0.0)) * sx, max(float(seg.get("start", 0.0)) * sx + 1, float(seg.get("end", 0.0)) * sx)
                spk = seg.get("speaker")
                color = SPEAKER_COLORS[roster.index(spk) % len(SPEAKER_COLORS)] if spk in roster else "#64748b"
                items.append(("band", x0, x1, color))
                if seg.get("checked"):
                    items.append(("check", x0, x1))
            for i in self.low_conf:
                items.append(("tick", self.word_spans[i]["start"] * sx))
            return items

        def _timeline_redraw(self):
            tl = self.__dict__.get("timeline")
            if tl is None:
                return
            tl.delete("all")
            width, height = tl.winfo_width(), int(tl.cget("height") or 30)
            for item in self._timeline_items(width, height):
                if item[0] == "band":
                    _, x0, x1, color = item
                    tl.create_rectangle(x0, 9, x1, height - 7, fill=self._dim(color), outline="")
                elif item[0] == "check":
                    _, x0, x1 = item
                    tl.create_rectangle(x0, height - 5, x1, height - 2, fill=THEME["ok"], outline="")
                else:
                    tl.create_line(item[1], 1, item[1], 7, fill="#f87171")
            self._tl_playhead = tl.create_line(0, 0, 0, height, fill="#ffffff", width=2)
            self._timeline_playhead()

        def _timeline_playhead(self):
            tl, ph = self.__dict__.get("timeline"), self.__dict__.get("_tl_playhead")
            dur = self._duration() if self.active_doc else 0
            if tl is None or ph is None or dur <= 0:
                return
            x = self._current_pos() / dur * tl.winfo_width()
            tl.coords(ph, x, 0, x, int(tl.cget("height") or 30))

        def _timeline_click(self, event):
            dur = self._duration()
            width = self.timeline.winfo_width()
            if self.active_doc is None or dur <= 0 or width <= 2:
                return
            import bisect

            pos = max(0.0, min(dur, event.x / width * dur))
            i = max(0, bisect.bisect_right(self.span_starts, pos) - 1) if self.span_starts else None
            if i is not None and i < len(self.word_spans):
                self._select_span(i)
            if self.playing:
                self._player_start(pos)
            else:
                self.play_pos = pos
                self._update_time_label()
                self._video_still(pos)



        @staticmethod
        def _parse_time(text: str) -> Optional[float]:
            """'83.4', '1:23.4' or '0:01:23.4' -> seconds."""
            text = (text or "").strip().replace(",", ".")
            if not text:
                return None
            try:
                parts = [float(p) for p in text.split(":")]
            except ValueError:
                return None
            if len(parts) > 3 or any(p < 0 for p in parts):
                return None
            total = 0.0
            for p in parts:
                total = total * 60 + p
            return total

        def _insert_defaults(self) -> Tuple[float, float, str]:
            """Start = where you paused; end = start of the next segment (if close) or +3 s; speaker = the one speaking before."""
            doc = self.active_doc
            pos = self._current_pos()
            starts = sorted(float(sg.get("start", 0.0)) for sg in doc.segments)
            nxt = next((t for t in starts if t > pos + 0.05), None)
            end = nxt if nxt is not None and nxt - pos <= 15 else pos + 3.0
            before = [sg for sg in doc.segments if float(sg.get("start", 0.0)) <= pos and sg.get("speaker")]
            if self.edit_seg_idx is not None and self.edit_seg_idx < len(doc.segments) and doc.segments[self.edit_seg_idx].get("speaker"):
                speaker = doc.segments[self.edit_seg_idx]["speaker"]
            else:
                speaker = before[-1]["speaker"] if before else (doc.roster()[0] if doc.roster() else "")
            return pos, end, speaker

        def _insert_segment_dialog(self):
            doc = self.active_doc
            if doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return
            self._autosave_edit()
            if self.playing:
                self._player_pause()
            start, end, speaker = self._insert_defaults()
            T = THEME
            win = tk.Toplevel(self)
            win.title("Insert missing segment")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            win.transient(self)
            self._label(win, "Add speech the transcript missed", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
            self._label(win, "Start is where you paused. Keep listening with Esc (play/pause) and press 'End = now' when the missing part ends.",
                        font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=440, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
            var_start = tk.StringVar(value=short_ts(start))
            var_end = tk.StringVar(value=short_ts(end))
            var_spk = tk.StringVar(value=speaker)
            for r, (label, var, which) in enumerate((("Start", var_start, "start"), ("End", var_end, "end")), start=2):
                self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
                self._entry(win, var, width=12, font="mono_small").grid(row=r, column=1, sticky="w", pady=3, padx=(10, 6))
                self._tool(win, f"{label} = now", lambda v=var: v.set(short_ts(self._current_pos()))).grid(row=r, column=2, sticky="w")
            self._label(win, "Speaker", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=4, column=0, sticky="w", pady=3)
            ttk.Combobox(win, textvariable=var_spk, values=doc.roster(), width=20, font=self.f["small"]).grid(row=4, column=1, columnspan=2, sticky="w", pady=3, padx=(10, 0))
            self._label(win, "Text", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=5, column=0, sticky="nw", pady=3)
            text_box = tk.Text(win, height=4, width=48, wrap=tk.WORD, font=self.f["transcript"], bg=T["bg_input"], fg=T["fg_primary"],
                               insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                               highlightcolor=T["accent"], padx=6, pady=4, undo=True)
            text_box.grid(row=5, column=1, columnspan=2, sticky="nsew", pady=3, padx=(10, 0))
            win.columnconfigure(2, weight=1)
            win.rowconfigure(5, weight=1)
            msg = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
            msg.grid(row=6, column=0, columnspan=3, sticky="w")
            btns = tk.Frame(win, bg=T["bg_panel"])
            btns.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))

            def do_insert():
                error = self._do_insert_segment(var_start.get(), var_end.get(), var_spk.get(), text_box.get("1.0", "end-1c"))
                if error:
                    msg.configure(text=error)
                else:
                    win.destroy()
                return "break"

            self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
            self._tool(btns, "Insert  ⌘↩", do_insert, primary=True).pack(side=tk.RIGHT)
            text_box.bind(f"<{MOD}-Return>", lambda e: do_insert())
            text_box.focus_set()

        def _do_insert_segment(self, start_text: str, end_text: str, speaker: str, text: str) -> Optional[str]:
            """Validate and insert; returns an error message for the dialog, or None on success."""
            doc = self.active_doc
            if doc is None:
                return "No transcript open."
            start, end = self._parse_time(start_text), self._parse_time(end_text)
            if start is None or (end is None and (end_text or "").strip()):
                return "Times must look like 1:23.4 or 0:01:23.4."
            end = end if end is not None else start  # End is optional
            if is_blank_text(text):
                return "Type the missing words first."
            if end <= start:
                end = start + max(1.0, 0.35 * len(text.split()))  # sensible length if End wasn't set
            idx = doc.insert_segment(start, end, text, speaker)
            if self._commit(f"Inserted segment {idx + 1} at {short_ts(start)}.") is None:
                return "Could not save the transcript."
            self._rerender()
            self._load_segment_into_editor(idx)
            first = self.span_lookup.get((idx, 0))
            if first is not None:
                self._select_span(first, load_editor=False)
            self._append_log(f"Inserted a missed segment at {short_ts(start)} ({speaker or 'no speaker'}); logged in the edit history.", "SUCCESS")
            return None

        def _delete_segment(self, ask: bool = True):
            doc, idx = self.active_doc, self.edit_seg_idx
            if doc is None or idx is None or idx >= len(doc.segments):
                return
            text = (doc.segments[idx].get("text") or "").strip()
            if ask and not is_blank_text(text) and not messagebox.askyesno(
                    "Delete segment?", f"Delete segment {idx + 1}?\n\n“{text[:160]}”\n\nThe deletion is logged in the transcript's edit history.", parent=self):
                return
            self.edit_original = ("", "")  # nothing left to autosave
            self.edit_text.delete("1.0", tk.END)
            if doc.delete_segment(idx) and self._commit(f"Deleted segment {idx + 1}.") is not None:
                self._clear_editor()
                self._rerender()
                if doc.segments:
                    self._load_segment_into_editor(min(idx, len(doc.segments) - 1))

        def _save_segment_edit(self, rerender: bool = True, reload: bool = True):
            doc, idx = self.active_doc, self.edit_seg_idx
            if doc is None or idx is None or idx >= len(doc.segments):
                return
            if is_blank_text(self.edit_text.get("1.0", "end-1c")) and not is_blank_text(self.edit_original[0]):
                self._delete_segment()  # saving an emptied segment = delete it (asks first)
                return
            changed = doc.edit_segment_text(idx, self.edit_text.get("1.0", "end-1c"))
            changed = doc.set_segment_speaker(idx, self.var_seg_speaker.get()) or changed
            if not changed:
                return
            if self._commit(f"Saved segment {idx + 1} — transcript and exports updated.") is not None:
                self.edit_original = ((doc.segments[idx].get("text") or "").strip(), doc.segments[idx].get("speaker") or "")
                if rerender:
                    self._rerender()
                if reload:
                    self._load_segment_into_editor(idx)

        def _autosave_edit(self, rerender: bool = True):
            if self._editor_dirty():
                self._save_segment_edit(rerender=rerender, reload=rerender)

        def _revert_segment_edit(self):
            if self.edit_seg_idx is None:
                return
            self.edit_text.delete("1.0", tk.END)
            self.edit_text.insert("1.0", self.edit_original[0])
            self.var_seg_speaker.set(self.edit_original[1])

        def _split_at_current_word(self):
            doc = self.active_doc
            if doc is None or self.current_span is None:
                self.lbl_status.config(text="Click the word that should start the new segment, then Split.")
                return
            self._autosave_edit()
            if self.current_span is None:
                return
            sp = self.word_spans[self.current_span]
            if doc.split_segment(sp["seg"], sp["w"]) and self._commit(f"Split segment {sp['seg'] + 1} before “{sp['word']}”.") is not None:
                self._rerender()
                self._load_segment_into_editor(sp["seg"] + 1)
            else:
                self.lbl_status.config(text="Pick a word in the middle of a segment to split before it.")

        def _merge_with_next(self):
            doc, idx = self.active_doc, self.edit_seg_idx
            if doc is None or idx is None:
                return
            self._autosave_edit()
            if doc.merge_with_next(idx) and self._commit(f"Merged segments {idx + 1} and {idx + 2}.") is not None:
                self._rerender()
                self._load_segment_into_editor(idx)

        # ----- player -----
        def _media(self) -> Optional[Path]:
            doc = self.active_doc
            media = doc.media_path("playback") if doc else None
            return media if media and media.exists() else None

        def _speed(self) -> float:
            try:
                return float(self.var_speed.get().rstrip("×"))
            except Exception:
                return 1.0

        def _duration(self) -> float:
            segs = self.active_doc.segments if self.active_doc else []
            return max((float(s.get("end", 0.0)) for s in segs), default=0.0)

        def _kill_player(self):
            if self._play_proc is not None and self._play_proc.poll() is None:
                self._play_proc.terminate()
            self._play_proc = None

        def _player_start(self, pos: float, duration: Optional[float] = None):
            media = self._media()
            if media is None:
                self.lbl_word.config(text="⚠ Playback media not found (moved or deleted?) — see 'media' in the .transcript.json", fg=THEME["warn"])
                return
            self._kill_player()
            pos = max(0.0, pos)
            speed = self._speed()
            tempo = ["-af", f"atempo={speed}"] if abs(speed - 1.0) > 1e-3 else []
            if shutil.which("ffplay"):
                cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{pos:.2f}"]
                if duration:
                    cmd += ["-t", f"{duration:.2f}"]
                cmd += tempo + [str(media)]
            elif shutil.which("ffmpeg"):
                chunk = Path(tempfile.gettempdir()) / "silicon_pipeline_play.wav"
                length = duration or 600.0  # afplay can't seek: play up to 10 min, Pause/Play continues from there
                try:
                    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{pos:.2f}", "-t", f"{length:.2f}", "-i", str(media)]
                                   + tempo + [str(chunk)], capture_output=True, timeout=60, check=True)
                except Exception as exc:
                    self._append_log(f"Could not prepare audio for playback: {exc}", "ERROR")
                    return
                cmd = ["afplay", str(chunk)]
            else:
                self._append_log("ffmpeg/ffplay not found (brew install ffmpeg).", "ERROR")
                return
            self._play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.playing = True
            self._play_from = pos
            self._play_speed = speed
            self._play_t0 = time.monotonic() + self.PLAYER_LATENCY_S
            self._play_until = pos + duration if duration else None
            self.btn_play.configure(text="❚❚ Pause")
            self._video_start(pos)
            self._schedule_tick()

        def _current_pos(self) -> float:
            if self.playing:
                return self._play_from + max(0.0, time.monotonic() - self._play_t0) * self._play_speed
            return self.play_pos

        def _player_pause(self):
            if self.playing:
                self.play_pos = self._current_pos()
                if self._play_until is not None:
                    self.play_pos = min(self.play_pos, self._play_until)
            was_streaming = self._video_proc is not None
            self.playing = False
            self._kill_player()
            self._video_stop()
            if was_streaming:
                self._video_still(self.play_pos)
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
                self._tick_job = None
            self.btn_play.configure(text="▶ Play")
            self._update_time_label()

        def _toggle_play(self, event=None):
            if event is not None and "popdown" in str(getattr(event, "widget", "")):
                return None
            if self.active_doc is None:
                return "break"
            if self.playing:
                self._player_pause()
            else:
                self._player_start(self.play_pos)
            return "break"

        def _seek_rel(self, delta: float):
            pos = max(0.0, min(self._duration(), self._current_pos() + delta))
            if self.playing:
                self._player_start(pos)
            else:
                self.play_pos = pos
                self._follow(pos)
                self._update_time_label()
                self._video_still(pos)

        def _play_from_current_word(self):
            if self.current_span is None:
                return
            self._player_start(max(0.0, self.word_spans[self.current_span]["start"] - 1.0))

        def _on_speed_change(self):
            if self.playing:
                self._player_start(self._current_pos())
            self._update_time_label()

        def _speed_step(self, direction: int):
            """⌘⇧. faster / ⌘⇧, slower — through the steps in the speed menu."""
            steps = self._combo_values["speed"]
            cur = self._speed()
            values = [float(v.rstrip("×")) for v in steps]
            i = min(range(len(values)), key=lambda k: abs(values[k] - cur))
            j = max(0, min(len(steps) - 1, i + direction))
            if j != i:
                self.var_speed.set(steps[j])
                self._on_speed_change()
                self.lbl_status.config(text=f"Playback speed {steps[j]}")

        def _schedule_tick(self):
            if self._tick_job is not None:
                try:
                    self.after_cancel(self._tick_job)
                except Exception:
                    pass
            self._tick_job = self.after(150, self._player_tick)

        def _player_tick(self):
            self._tick_job = None
            if not self.playing:
                return
            pos = self._current_pos()
            ended = self._play_proc is None or self._play_proc.poll() is not None
            if ended or (self._play_until is not None and pos >= self._play_until):
                self._player_pause()
                return
            self._follow(pos)
            self._update_time_label()
            self._schedule_tick()

        def _follow(self, pos: float):
            import bisect

            if not self.word_spans:
                return
            i = max(0, bisect.bisect_right(self.span_starts, pos) - 1)
            sp = self.word_spans[i]
            self.preview_text.tag_remove("PLAYING", "1.0", tk.END)
            self.preview_text.tag_add("PLAYING", f"{sp['line']}.{sp['c0']}", f"{sp['line']}.{sp['c1']}")
            if self.var_follow.get():
                self.preview_text.see(f"{sp['line']}.{sp['c0']}")

        def _update_time_label(self):
            speed = self._speed()
            extra = f"  ·  {self.var_speed.get()}" if abs(speed - 1.0) > 1e-3 else ""
            self.lbl_time.configure(text=f"{short_ts(self._current_pos())[:-2]} / {short_ts(self._duration())[:-2]}{extra}")
            self._timeline_playhead()

        def _open_transcript(self):
            initial = self.settings.get("last_transcript_dir") or str(Path.home())
            path = filedialog.askopenfilename(title="Open transcript", initialdir=initial,
                                              filetypes=[("Pipeline transcript", "*.transcript.json"), ("JSON", "*.json")])
            if not path:
                return
            try:
                doc = TranscriptDocument.load(Path(path))
            except Exception as exc:
                messagebox.showerror("Could not open transcript", str(exc), parent=self)
                return
            self.settings["last_transcript_dir"] = str(Path(path).parent)
            self._render_doc(doc)
            self.notebook.select(self.tab_preview)

        def _reexport_open_transcript(self):
            doc = self.active_doc
            if doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return
            self._autosave_edit()
            doc.data["export_formats"] = self._export_formats()
            created = self._commit(f"Re-exported {doc.base_name} with the current export settings.", undoable=False)
            if created is not None:
                self._append_log(f"Re-exported {', '.join(p.name for p in created.values()) or 'nothing (all formats off)'}", "SUCCESS")

        def _show_speakers_dialog(self):
            """Rename, add and remove speakers. Renames apply to every segment and export; swaps work."""
            doc = self.active_doc
            if doc is None:
                self.lbl_word.config(text="Open a transcript first.")
                return
            self._autosave_edit()
            T = THEME
            counts = doc.speakers()
            roster = doc.roster()
            win = tk.Toplevel(self)
            win.title("Speakers")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            win.transient(self)
            self._label(win, "Speakers in this transcript", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=4, sticky="w")
            self._label(win, "Rename by typing a new name (an existing name merges the two). Add speakers that detection missed, then assign them "
                             "in the segment editor with the speaker chips or ⌃1–⌃9.", font="small", fg=T["fg_dim"], bg=T["bg_panel"],
                        wraplength=460, justify=tk.LEFT).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))
            entries: Dict[str, tk.StringVar] = {}
            remove_vars: Dict[str, tk.BooleanVar] = {}
            r = 2
            for i, spk in enumerate(roster):
                color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
                self._label(win, f"⌃{i + 1}" if i < 9 else "", font="mono_small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 6))
                self._label(win, spk, font="mono_small_bold", fg=color, bg=T["bg_panel"]).grid(row=r, column=1, sticky="w", pady=3)
                var = tk.StringVar(value=spk)
                self._entry(win, var, width=22, font="body").grid(row=r, column=2, sticky="ew", pady=3, padx=(10, 6))
                entries[spk] = var
                n = counts.get(spk, 0)
                if n:
                    self._label(win, f"{n} segment{'s' if n != 1 else ''}", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=3, sticky="w")
                else:
                    rv = tk.BooleanVar(value=False)
                    remove_vars[spk] = rv
                    self._check(win, "unused · remove", rv, bg=T["bg_panel"], font="small").grid(row=r, column=3, sticky="w")
                r += 1
            self._label(win, "Add speaker", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
            var_new = tk.StringVar(value="")
            new_entry = self._entry(win, var_new, width=22, font="body")
            new_entry.grid(row=r, column=2, sticky="ew", pady=(10, 0), padx=(10, 6))
            self._label(win, "e.g. P03, Facilitator", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=3, sticky="w", pady=(10, 0))
            win.columnconfigure(2, weight=1)
            btns = tk.Frame(win, bg=T["bg_panel"])
            btns.grid(row=r + 1, column=0, columnspan=4, sticky="e", pady=(14, 0))

            def apply():
                mapping = {old: v.get().strip() for old, v in entries.items() if v.get().strip() and v.get().strip() != old}
                removals = [n for n, v in remove_vars.items() if v.get() and n not in mapping]
                new_names = [n.strip() for n in re.split(r"[,;]", var_new.get()) if n.strip()]
                win.destroy()
                self._apply_speaker_changes(mapping, removals, new_names)
                return "break"

            self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
            self._tool(btns, "Apply", apply, primary=True).pack(side=tk.RIGHT)
            win.bind("<Return>", lambda e: apply())
            win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
            if not roster:
                new_entry.focus_set()

        def _apply_speaker_changes(self, mapping: Dict[str, str], removals: List[str], new_names: List[str]):
            doc = self.active_doc
            if doc is None:
                return
            changed = []
            if mapping and doc.rename_speakers(mapping):
                changed.append(", ".join(f"{o} → {n}" for o, n in mapping.items()))
            for name in removals:
                if doc.remove_speaker(name):
                    changed.append(f"removed {name}")
            for name in new_names:
                if doc.add_speaker(name):
                    changed.append(f"added {name}")
            if not changed:
                return
            summary = "; ".join(changed)
            if self._commit(f"Speakers: {summary}.") is not None:
                self._rerender()
                if self.edit_seg_idx is not None:
                    self._load_segment_into_editor(self.edit_seg_idx)
                self._append_log(f"Speakers: {summary}. Transcript and exports updated.", "SUCCESS")

        # ----- re-run Whisper on a passage / speakers on a transcript -----
        LANG_NAMES = {w.split()[0]: w.split("(", 1)[1].rstrip(")") for w in WHISPER_LANGUAGES if "(" in w}

        def _rerun_busy(self) -> bool:
            if self.worker is not None and self.worker.is_alive():
                messagebox.showinfo("Busy", "The task queue is running. Re-runs use the same GPU, so wait until it has finished.", parent=self)
                return True
            if self.__dict__.get("_rerun_thread") is not None and self._rerun_thread.is_alive():
                messagebox.showinfo("Busy", "Another re-run is still in progress.", parent=self)
                return True
            return False

        def _doc_setting(self, key: str, default: str) -> str:
            w = ((self.active_doc.data.get("provenance") or {}).get("whisper") or {}) if self.active_doc else {}
            return str(w.get(key) or default)

        def _start_rerun(self, kind: str, params: Dict, clip: Optional[Tuple[float, float]], segments_json: Optional[List[Dict]] = None):
            """Run WHISPER_RUNNER in the background on (a clip of) the transcript's playback audio."""
            doc = self.active_doc
            media = self._media()
            if doc is None or media is None:
                messagebox.showerror("No audio", "The playback media of this transcript was not found, so nothing can be re-run.", parent=self)
                return
            best_py = MacAppInstaller.find_best_python()
            token = self.var_hf_token.get().strip()
            params = dict(params, doc_path=str(doc.path))
            msgq = self.msg_queue

            def job():
                tmp = Path(tempfile.mkdtemp(prefix="silicon_rerun_"))
                try:
                    wav = tmp / "clip.wav"
                    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                    if clip:
                        cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1] - clip[0]:.3f}"]
                    cmd += ["-i", str(media), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav)]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                    if res.returncode != 0:
                        raise RuntimeError("Could not cut the audio: " + (res.stderr or "").strip()[-300:])
                    out = tmp / "out.json"
                    env = os.environ.copy()
                    env.update(PYTORCH_ENABLE_MPS_FALLBACK="1", PYTHONUNBUFFERED="1", PIPELINE_HF_TOKEN=token,
                               PIPELINE_TASK=params.get("task", "transcribe"), PIPELINE_PROMPT=params.get("prompt", ""),
                               PIPELINE_MODE="diarize_only" if segments_json is not None else "full")
                    if segments_json is not None:
                        seg_file = tmp / "segments.json"
                        seg_file.write_text(json.dumps(segments_json), encoding="utf-8")
                        env["PIPELINE_SEGMENTS_JSON"] = str(seg_file)
                    proc = subprocess.Popen([best_py, "-c", WHISPER_RUNNER, str(wav), str(wav), params.get("model", "large-v3"),
                                             params.get("language", "auto"), str(out), params.get("diarize_mode", "Disabled"),
                                             str(params.get("num_speakers", 0)), params.get("backend", "MLX (Apple Silicon GPU)")],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                    self._rerun_proc = proc
                    tail: List[str] = []
                    for line in proc.stdout:
                        line = line.strip()
                        if line:
                            tail = (tail + [line])[-6:]
                            msgq.put({"type": "rerun_progress", "kind": kind, "text": line})
                    if proc.wait() != 0 or not out.exists():
                        raise RuntimeError("Whisper failed:\n" + "\n".join(tail))
                    msgq.put({"type": "rerun_done", "kind": kind, "params": params, "result": json.loads(out.read_text(encoding="utf-8"))})
                except Exception as exc:
                    msgq.put({"type": "rerun_failed", "kind": kind, "error": str(exc)})
                finally:
                    self._rerun_proc = None
                    shutil.rmtree(tmp, ignore_errors=True)

            self._rerun_thread = threading.Thread(target=job, daemon=True)
            self._rerun_thread.start()

        def _handle_rerun_message(self, msg: Dict):
            kind = msg.get("kind")
            label = "Re-transcribing" if kind == "retranscribe" else "Detecting speakers"
            if msg["type"] == "rerun_progress":
                text = msg["text"]
                m = re.search(r"(\d+)%\|", text)
                self.lbl_status.config(text=f"{label}… {m.group(1)}%" if m else f"{label}… {text[:120]}")
                if not m:
                    self._append_log(f"[{label}] {text}", "INFO")
                return
            if msg["type"] == "rerun_failed":
                self.lbl_status.config(text=f"{label} failed — see the console.")
                self._append_log(f"{label} failed: {msg['error']}", "ERROR")
                messagebox.showerror(f"{label} failed", msg["error"][-800:], parent=self)
                return
            p = msg["params"]
            doc = self.active_doc
            if doc is None or str(doc.path) != p["doc_path"]:
                messagebox.showinfo("Transcript changed", "You switched to another transcript while this was running; the result was not applied.", parent=self)
                return
            self._autosave_edit(rerender=False)
            if kind == "retranscribe":
                self._apply_retranscription(doc, msg["result"], p)
            else:
                self._apply_rediarization(doc, msg["result"], p)

        # --- re-transcribe a passage
        def _retranscribe_dialog(self):
            doc = self.active_doc
            if doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return
            if self._rerun_busy():
                return
            self._autosave_edit()
            if self.playing:
                self._player_pause()
            T = THEME
            if self.edit_seg_idx is not None and self.edit_seg_idx < len(doc.segments):
                seg = doc.segments[self.edit_seg_idx]
                start, end = float(seg["start"]), float(seg["end"])
            else:
                start = self._current_pos()
                end = start + 10.0
            win = tk.Toplevel(self)
            win.title("Re-transcribe passage")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            win.transient(self)
            self._label(win, "Run Whisper again on part of this recording", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
            self._label(win, "The segments in this time range are replaced by the new result. Your other edits stay untouched, and ⌘Z undoes it. "
                             "Useful for mumbled speech (bigger model), a language switch (force the language) or an English version (translate).",
                        font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
            v_start, v_end = tk.StringVar(value=short_ts(start)), tk.StringVar(value=short_ts(end))
            r = 2
            for label, var in (("From", v_start), ("To", v_end)):
                self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
                self._entry(win, var, width=12, font="mono_small").grid(row=r, column=1, sticky="w", padx=(10, 6), pady=3)
                self._tool(win, f"{label} = now", lambda v=var: v.set(short_ts(self._current_pos()))).grid(row=r, column=2, sticky="w")
                r += 1

            def extend(direction: int):
                a, b = self._parse_time(v_start.get()), self._parse_time(v_end.get())
                if a is None or b is None:
                    return
                if direction < 0:
                    prev = [sg for sg in doc.segments if float(sg["end"]) <= a + 0.05]
                    if prev:
                        v_start.set(short_ts(float(prev[-1]["start"])))
                else:
                    nxt = [sg for sg in doc.segments if float(sg["start"]) >= b - 0.05]
                    if nxt:
                        v_end.set(short_ts(float(nxt[0]["end"])))

            ext = tk.Frame(win, bg=T["bg_panel"])
            ext.grid(row=r, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(0, 6))
            self._tool(ext, "+ previous segment", lambda: extend(-1)).pack(side=tk.LEFT, padx=(0, 6))
            self._tool(ext, "+ next segment", lambda: extend(+1)).pack(side=tk.LEFT)
            r += 1
            v_model = tk.StringVar(value="large-v3")
            lang_default = self._doc_setting("language_setting", "auto")
            v_lang = tk.StringVar(value=next((w for w in WHISPER_LANGUAGES if w.split()[0] == lang_default), WHISPER_LANGUAGES[0]))
            v_task = tk.StringVar(value="Transcribe (keep the spoken language)")
            v_spk = tk.StringVar(value="(keep the speakers of the replaced segments)")
            v_prompt = tk.StringVar(value=self.var_whisper_prompt.get())
            for label, var, values in (
                ("Model", v_model, ["large-v3", "large-v3-turbo", "medium", "small"]),
                ("Language", v_lang, WHISPER_LANGUAGES),
                ("Output", v_task, ["Transcribe (keep the spoken language)", "Translate to English"]),
                ("Speaker", v_spk, ["(keep the speakers of the replaced segments)"] + doc.roster()),
            ):
                self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
                ttk.Combobox(win, textvariable=var, values=values, state="readonly", width=36, font=self.f["small"]).grid(row=r, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=3)
                r += 1
            self._label(win, "Vocabulary", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
            self._entry(win, v_prompt, width=40, font="small").grid(row=r, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=3)
            r += 1
            self._label(win, "large-v3 is the most accurate (first use downloads ~3 GB); large-v3-turbo can't translate well.",
                        font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
            msg = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
            msg.grid(row=r + 1, column=0, columnspan=3, sticky="w")
            btns = tk.Frame(win, bg=T["bg_panel"])
            btns.grid(row=r + 2, column=0, columnspan=3, sticky="e", pady=(10, 0))

            def run():
                err = self._launch_retranscription(v_start.get(), v_end.get(), v_model.get(), v_lang.get(), v_task.get(), v_spk.get(), v_prompt.get())
                if err:
                    msg.configure(text=err)
                else:
                    win.destroy()

            self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
            self._tool(btns, "Re-transcribe", run, primary=True).pack(side=tk.RIGHT)

        def _launch_retranscription(self, start_text, end_text, model, lang_label, task_label, speaker_label, prompt) -> Optional[str]:
            start, end = self._parse_time(start_text), self._parse_time(end_text)
            if start is None or end is None or end <= start:
                return "Check the times: From must be before To (e.g. 1:23.4)."
            if end - start > 30 * 60:
                return "Please pick at most 30 minutes at a time."
            translate = task_label.startswith("Translate")
            if translate and model == "large-v3-turbo":
                model = "large-v3"  # turbo was not trained for translation
            lang = lang_label.split()[0]
            pad = 0.3
            params = {
                "start": start, "end": end, "clip_start": max(0.0, start - pad), "model": model, "language": lang,
                "task": "translate" if translate else "transcribe", "prompt": prompt.strip(),
                "speaker": "" if speaker_label.startswith("(") else speaker_label,
                "backend": self._doc_setting("backend_setting", self.var_whisper_backend.get()), "diarize_mode": "Disabled",
            }
            self._start_rerun("retranscribe", params, clip=(params["clip_start"], end + pad))
            what = "Translating" if translate else "Re-transcribing"
            self.lbl_status.config(text=f"{what} {short_ts(start)[:-2]}–{short_ts(end)[:-2]} with Whisper {model}…")
            self._append_log(f"{what} {short_ts(start)}–{short_ts(end)} with Whisper {model} (language {lang}).", "STAGE")
            return None

        def _apply_retranscription(self, doc: TranscriptDocument, result: Dict, p: Dict):
            off, start, end = float(p["clip_start"]), float(p["start"]), float(p["end"])
            new: List[Dict] = []
            for sg in result.get("segments") or []:
                words = []
                for w in sg.get("words") or []:
                    w = dict(w, start=round(float(w.get("start", 0)) + off, 3), end=round(float(w.get("end", 0)) + off, 3))
                    if start - 0.15 <= (w["start"] + w["end"]) / 2 <= end + 0.15:  # drop words from the padding
                        words.append(w)
                if not words:
                    continue
                seg = {"start": words[0]["start"], "end": words[-1]["end"], "words": words, "text": "".join(str(w.get("word", "")) for w in words)}
                for k in ("avg_logprob", "no_speech_prob"):
                    if k in sg:
                        seg[k] = sg[k]
                new.append(seg)
            new, _ = drop_empty_segments(new)
            if not new:
                messagebox.showinfo("Nothing recognised", "Whisper found no speech in that range, so the transcript was left unchanged.", parent=self)
                self.lbl_status.config(text="Re-transcription found no speech; nothing changed.")
                return
            if p.get("task") == "translate" and p.get("language") != "en":
                src = self.LANG_NAMES.get(p.get("language"), "another language") if p.get("language") != "auto" else "another language"
                note = f"[translated from {src}]"
                for seg in new:  # keep translations identifiable in every export
                    seg["words"].insert(0, {"word": " " + note, "start": seg["start"], "end": seg["start"], "probability": 1.0, "edited": True})
                    seg["text"] = "".join(str(w.get("word", "")) for w in seg["words"])
            n_old, n_new = doc.replace_range(start, end, new, p)
            if self._commit(f"Re-transcribed {short_ts(start)[:-2]}–{short_ts(end)[:-2]}: {n_old} segment(s) → {n_new}.") is None:
                return
            self.edit_original = ("", "")
            self._rerender()
            first = next((i for i, sg in enumerate(doc.segments) if sg is new[0]), None)
            if first is not None:
                self._load_segment_into_editor(first)  # always reload: the editor may still show the old text of this segment
                idx = self.span_lookup.get((first, 0))
                if idx is not None:
                    self._select_span(idx, load_editor=False)
            self._append_log(f"Re-transcribed {short_ts(start)}–{short_ts(end)} with {p['model']} ({p['task']}): {n_old} → {n_new} segment(s). ⌘Z undoes it.", "SUCCESS")

        # --- re-run speaker detection
        def _rediarize_dialog(self):
            doc = self.active_doc
            if doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return
            if self._rerun_busy():
                return
            self._autosave_edit()
            T = THEME
            win = tk.Toplevel(self)
            win.title("Re-run speaker detection")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            win.transient(self)
            manual = sum(1 for sg in doc.segments if sg.get("speaker_manual"))
            self._label(win, "Detect speakers again for this transcript", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=2, sticky="w")
            self._label(win, "Your text stays as it is; only speaker labels change. New labels are matched to the names you already use "
                             "wherever they overlap most. ⌘Z undoes the whole re-run.", font="small", fg=T["fg_dim"], bg=T["bg_panel"],
                        wraplength=440, justify=tk.LEFT).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
            v_method = tk.StringVar(value=self.var_diarize_mode.get() if not self.var_diarize_mode.get().startswith("Disabled")
                                    else "PyTorch MPS Spectral (Offline - No Token Needed)")
            v_n = tk.IntVar(value=max(2, len(doc.roster())))
            v_keep = tk.BooleanVar(value=True)
            self._label(win, "Method", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=2, column=0, sticky="w", pady=3)
            ttk.Combobox(win, textvariable=v_method, values=["PyTorch MPS Spectral (Offline - No Token Needed)", "Pyannote.audio 3.1 Neural (Requires HF Token)"],
                         state="readonly", width=40, font=self.f["small"]).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=3)
            self._label(win, "Number of speakers", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=3, column=0, sticky="w", pady=3)
            tk.Spinbox(win, from_=0, to=12, textvariable=v_n, width=4, bg=T["bg_input"], fg=T["fg_primary"], buttonbackground=T["bg_card"], relief=tk.FLAT,
                       highlightthickness=1, highlightbackground=T["border"], font=self.f["body"]).grid(row=3, column=1, sticky="w", padx=(10, 0), pady=3)
            self._label(win, "Counting everyone who speaks, including you (0 = guess).", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=4, column=1, sticky="w", padx=(10, 0))
            self._check(win, f"Keep speakers I assigned by hand ({manual} segment{'s' if manual != 1 else ''})", v_keep, bg=T["bg_panel"], font="small").grid(
                row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
            btns = tk.Frame(win, bg=T["bg_panel"])
            btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))

            def run():
                try:
                    n = int(v_n.get())
                except Exception:
                    n = 0
                win.destroy()
                self._launch_rediarization(v_method.get(), n, bool(v_keep.get()))

            self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
            self._tool(btns, "Detect speakers", run, primary=True).pack(side=tk.RIGHT)

        def _launch_rediarization(self, method: str, num_speakers: int, keep_manual: bool):
            doc = self.active_doc
            if doc is None:
                return
            if method.startswith("Pyannote") and not self.var_hf_token.get().strip():
                messagebox.showinfo("Token needed", "pyannote needs your Hugging Face token (Settings ▸ Speakers & Exports). "
                                                    "Without it, the offline spectral method is used.", parent=self)
            segs = [{"start": float(sg.get("start", 0)), "end": float(sg.get("end", 0)), "text": sg.get("text", "")} for sg in doc.segments]
            params = {"method": method, "diarize_mode": method, "num_speakers": num_speakers, "keep_manual": keep_manual,
                      "n_segments": len(segs), "fingerprint": [(round(s["start"], 2), round(s["end"], 2)) for s in segs]}
            self._start_rerun("rediarize", params, clip=None, segments_json=segs)
            self.lbl_status.config(text=f"Detecting speakers ({'pyannote' if method.startswith('Pyannote') else 'spectral'}, "
                                        f"{num_speakers or 'auto'} speakers)…")
            self._append_log("Re-running speaker detection on the whole recording.", "STAGE")

        def _apply_rediarization(self, doc: TranscriptDocument, result: Dict, p: Dict):
            segs = result.get("segments") or []
            current = [(round(float(sg.get("start", 0)), 2), round(float(sg.get("end", 0)), 2)) for sg in doc.segments]
            if len(segs) != len(doc.segments) or current != [tuple(x) for x in p["fingerprint"]]:
                messagebox.showinfo("Transcript changed", "Segments were edited while speakers were being detected, so the result "
                                                          "was not applied. Please run it again.", parent=self)
                return
            labels = [sg.get("speaker") for sg in segs]
            if not any(labels):
                messagebox.showinfo("No speakers found", f"Speaker detection returned no labels ({result.get('diarization_method')}).", parent=self)
                return
            stats = doc.apply_speaker_labels(labels, p["keep_manual"], p)
            if self._commit(f"Speakers re-detected: {stats['changed']} segment(s) changed, {stats['kept']} kept.") is None:
                return
            self.edit_original = ("", "")
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(min(self.edit_seg_idx, len(doc.segments) - 1))
            self._append_log(f"Speaker detection ({result.get('diarization_method')}): {stats['changed']} segment(s) relabelled, "
                             f"{stats['kept']} hand-assigned kept, {stats['speakers']} speaker(s). Rename new ones under Speakers…; ⌘Z undoes it.", "SUCCESS")

        # ----- speaker chips in the segment editor -----
        def _refresh_speaker_chips(self):
            chips = self.__dict__.get("spk_chips")
            if chips is None:
                return
            chips.clear()
            doc = self.active_doc
            if doc is None:
                return
            T = THEME
            current = self.var_seg_speaker.get().strip()
            chips.add(self._label(chips, "Assign:", font="small", fg=T["fg_secondary"]))
            for i, name in enumerate(doc.roster()):
                color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
                active = name == current
                chip = tk.Label(chips, text=(f"⌃{i + 1} " if i < 9 else "") + name, font=self.f["small_bold"], padx=8, pady=2, cursor="hand2",
                                bg=color if active else T["bg_input"], fg=T["bg_root"] if active else color,
                                highlightthickness=1, highlightbackground=color)
                chip.bind("<Button-1>", lambda e, n=name: self._assign_speaker(n))
                chips.add(chip)
            add = tk.Label(chips, text="+ Speaker", font=self.f["small"], padx=8, pady=2, cursor="hand2", bg=T["bg_input"], fg=T["fg_secondary"],
                           highlightthickness=1, highlightbackground=T["border"])
            add.bind("<Button-1>", lambda e: self._add_speaker_quick())
            chips.add(add)

        def _assign_speaker(self, name: str):
            if self.active_doc is None or self.edit_seg_idx is None:
                self.lbl_status.config(text="Click a word in the transcript first, then pick the speaker for that segment.")
                return
            self.var_seg_speaker.set(name)
            self._save_segment_edit()
            self._refresh_speaker_chips()

        def _assign_speaker_number(self, number: int):
            doc = self.active_doc
            if doc is None:
                return
            roster = doc.roster()
            if 1 <= number <= len(roster):
                self._assign_speaker(roster[number - 1])

        def _add_speaker_quick(self):
            doc = self.active_doc
            if doc is None:
                return
            name = simpledialog.askstring("Add speaker", "Name of the new speaker (e.g. P03, Facilitator):", parent=self)
            if not name or not name.strip():
                return
            if doc.add_speaker(name) and self._commit(f"Added speaker {name.strip()}.") is not None:
                self._rerender()
                if self.edit_seg_idx is not None:
                    self._assign_speaker(name.strip())

        # ----- video preview -----
        VIDEO_FPS = 10

        def _build_video_panel(self):
            T = THEME
            self.video_frame = tk.Frame(self.tx_paned, bg="#000000")
            self.video_label = tk.Label(self.video_frame, bg="#000000", fg=T["fg_dim"], font=self.f["small"], text="No video",
                                        justify=tk.CENTER, wraplength=260)
            self.video_label.pack(fill=tk.BOTH, expand=True)
            self.lbl_video_caption = self._label(self.video_frame, "", font="mono_small", fg=T["fg_dim"], bg="#000000")
            self.lbl_video_caption.pack(fill=tk.X)
            self._video_proc: Optional[subprocess.Popen] = None
            self._video_gen = 0
            self._video_q: "queue.Queue" = queue.Queue(maxsize=6)
            self._video_next = None
            self._still_q: "queue.Queue" = queue.Queue()
            self._still_req = 0
            self._video_img = None
            self._video_panel_shown = False
            self.after(40, self._video_pump)
            self.after_idle(self._toggle_video_panel)

        def _toggle_video_panel(self):
            want = bool(self.var_show_video.get())
            if want and not self._video_panel_shown:
                self.tx_paned.add(self.video_frame, minsize=200, width=420, stretch="never")
                self._video_panel_shown = True
                if not self.__dict__.get("playing", False):
                    self._video_still(self.__dict__.get("play_pos", 0.0))
            elif not want and self._video_panel_shown:
                self._video_stop()
                self.tx_paned.forget(self.video_frame)
                self._video_panel_shown = False

        def _video_media(self) -> Optional[Path]:
            """A video on the transcript's timeline: the one recorded at transcription time, else (older transcripts)
            the compressed/merged output next to the transcript, else the original if no LosslessCut cuts were applied."""
            doc = self.active_doc
            if doc is None:
                return None
            p = doc.media_path("video")
            if p and p.exists():
                return p
            if "video" in (doc.data.get("media") or {}):
                return None  # recorded as "no video on this timeline"
            for ext in (".mkv", ".mp4", ".mov"):
                for name in (f"{doc.base_name}_compressed{ext}", f"{doc.base_name}{ext}"):
                    cand = doc.out_dir / name
                    if cand.exists():
                        return cand
            prov = doc.data.get("provenance") or {}
            proc = prov.get("processing") or {}
            inputs = prov.get("inputs") or []
            if len(inputs) == 1 and not proc.get("losslesscut") and not proc.get("merge"):
                cand = Path(inputs[0]["path"])
                if cand.exists():
                    return cand
            return None

        def _video_width(self) -> int:
            w = self.video_label.winfo_width()
            w = w if w > 40 else 420
            return max(160, min(1280, w)) // 2 * 2

        def _video_ffmpeg(self, media: Path, pos: float, single: bool) -> List[str]:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
            if IS_MAC:
                cmd += ["-hwaccel", "videotoolbox"]
            cmd += ["-ss", f"{max(0.0, pos):.3f}", "-i", str(media), "-an", "-sn"]
            scale = f"scale='min({self._video_width()},iw)':-2"  # never upscale small videos
            vf = scale if single else f"fps={self.VIDEO_FPS},{scale}"
            cmd += ["-vf", vf] + (["-frames:v", "1"] if single else []) + ["-f", "image2pipe", "-vcodec", "ppm", "-"]
            return cmd

        def _video_start(self, pos: float):
            self._video_stop()
            media = self._video_media()
            if media is None or not self._video_panel_shown or not shutil.which("ffmpeg"):
                return
            self._video_gen += 1
            gen = self._video_gen
            self._video_q = queue.Queue(maxsize=6)
            q = self._video_q
            try:
                proc = subprocess.Popen(self._video_ffmpeg(media, pos, single=False), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception:
                return
            self._video_proc = proc

            def reader():
                i = 0
                while self._video_gen == gen:
                    frame = read_ppm_frame(proc.stdout)
                    if frame is None:
                        break
                    item = (gen, pos + i / self.VIDEO_FPS, frame)
                    i += 1
                    while self._video_gen == gen:  # bounded queue = ffmpeg is paced by playback
                        try:
                            q.put(item, timeout=0.2)
                            break
                        except queue.Full:
                            continue

            threading.Thread(target=reader, daemon=True).start()

        def _video_stop(self):
            self._video_gen = self.__dict__.get("_video_gen", 0) + 1
            proc = self.__dict__.get("_video_proc")
            if proc is not None and proc.poll() is None:
                proc.terminate()
            self._video_proc = None
            self._video_next = None

        def _video_still(self, pos: float):
            if not self.__dict__.get("_video_panel_shown"):
                return
            media = self._video_media()
            if media is None:
                self._show_no_video()
                return
            self._still_req += 1
            req = self._still_req
            cmd = self._video_ffmpeg(media, pos, single=True)

            def grab():
                try:
                    res = subprocess.run(cmd, capture_output=True, timeout=20)
                    import io
                    frame = read_ppm_frame(io.BytesIO(res.stdout))
                except Exception:
                    frame = None
                if frame is not None:
                    self._still_q.put((req, pos, frame))

            threading.Thread(target=grab, daemon=True).start()

        def _show_no_video(self):
            self._video_img = None
            self.video_label.configure(image="", text="No video on this transcript's timeline.\n\nAudio-only, or LosslessCut-trimmed without the "
                                                       "Compress step (the trimmed file isn't kept).")
            self.lbl_video_caption.configure(text="")

        def _show_frame(self, pos: float, frame: Tuple[int, int, bytes]):
            w, h, data = frame
            try:
                img = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (w, h) + data, format="ppm")
            except Exception:
                return
            self._video_img = img  # keep a reference, or Tk drops the image
            self.video_label.configure(image=img, text="")
            self.lbl_video_caption.configure(text=f"{short_ts(pos)[:-2]}  ·  {self._video_media().name if self._video_media() else ''}")

        def _video_pump(self):
            try:
                if self._video_proc is not None and self.playing:
                    now = self._current_pos()
                    latest = None
                    while True:
                        item = self._video_next
                        self._video_next = None
                        if item is None:
                            try:
                                item = self._video_q.get_nowait()
                            except queue.Empty:
                                break
                        if item[0] != self._video_gen:
                            continue
                        if item[1] <= now + 0.05:
                            latest = item
                        else:
                            self._video_next = item  # not due yet
                            break
                    if latest is not None:
                        self._show_frame(latest[1], latest[2])
                while True:
                    try:
                        req, pos, frame = self._still_q.get_nowait()
                    except queue.Empty:
                        break
                    if req == self._still_req and not (self.playing and self._video_proc is not None):
                        self._show_frame(pos, frame)
            finally:
                self.after(40, self._video_pump)

        def _open_in_quicktime(self):
            media = self._video_media() or (self._media() if self.active_doc else None)
            if media is None:
                self.lbl_status.config(text="No video for this transcript.")
                return
            pos = self._current_pos()
            self._player_pause()
            path = str(media).replace("\\", "\\\\").replace('"', '\\"')
            script = (f'tell application "QuickTime Player"\nactivate\nset d to open (POSIX file "{path}")\n'
                      f"set current time of d to {pos:.2f}\nend tell")
            try:
                subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                self._append_log(f"Could not open QuickTime: {exc}", "ERROR")

        def _apply_speaker_mapping(self, mapping: Dict[str, str]):
            doc = self.active_doc
            if doc is None:
                return
            count = doc.rename_speakers(mapping)
            if not count:
                return
            pairs = ", ".join(f"{o} → {n}" for o, n in mapping.items())
            if self._commit(f"Renamed {pairs}.") is not None:
                self._rerender()
                if self.edit_seg_idx is not None:
                    self._load_segment_into_editor(self.edit_seg_idx)
                self._append_log(f"Renamed {pairs} ({count} segments); transcript and exports updated.", "SUCCESS")

        # ================================================================== task handling
        @property
        def _queue_path(self) -> Path:
            return APP_SUPPORT_DIR / "queue.json"

        def _save_queue(self):
            try:
                APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
                tmp = self._queue_path.with_name("queue.json.tmp")
                tmp.write_text(json.dumps({"counter": self.job_counter, "tasks": self.jobs}, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self._queue_path)
            except Exception as exc:
                self._append_log(f"Could not save the task queue: {exc}", "WARN")

        def _load_queue(self):
            try:
                data = json.loads(self._queue_path.read_text(encoding="utf-8"))
            except Exception:
                return
            self.job_counter = int(data.get("counter", 0))
            self.jobs = []
            for t in data.get("tasks") or []:
                if not isinstance(t, dict) or not t.get("inputs"):
                    continue
                if t.get("status") == "Running":  # app was closed mid-run
                    t["status"], t["stage"], t["progress"] = "Pending", "Interrupted — will run again", 0.0
                t.setdefault("steps", self._default_steps())
                t.setdefault("settings", self._settings_snapshot())
                self.jobs.append(t)
            self._refresh_queue_tree()
            if self.jobs:
                self._append_log(f"Restored {len(self.jobs)} task(s) from the last session.", "INFO")

        def _settings_snapshot(self) -> Dict:
            try:
                num_speakers = int(self.var_num_speakers.get())
            except Exception:
                num_speakers = 0
            return {
                "respect_llc": self.var_respect_llc.get(),
                "hb_encoder": self.var_hb_encoder.get(), "hb_quality": self.var_hb_quality.get(), "hb_resolution": self.var_hb_res.get(),
                "hb_fps": self.var_hb_fps.get(), "hb_container": self.var_hb_container.get(), "hb_audio_bitrate": self.var_hb_abitrate.get(),
                "audio_format": self.var_audio_format.get(), "speech_filter": self.var_speech_filter.get(),
                "whisper_backend": self.var_whisper_backend.get(), "whisper_model": self.var_whisper_model.get(),
                "whisper_lang": self.var_whisper_lang.get(), "diarize_mode": self.var_diarize_mode.get(), "num_speakers": num_speakers,
                "whisper_prompt": self.var_whisper_prompt.get().strip(),
                **{f"fmt_{k}": v for k, v in self._export_formats().items()},
            }

        def _default_steps(self) -> Dict[str, bool]:
            return {"compress": bool(self.var_stage_compress.get()), "audio": bool(self.var_stage_audio.get()),
                    "whisper": bool(self.var_stage_whisper.get())}

        def _default_output_dir(self, inputs: List[str], name: str) -> str:
            if self.var_auto_subfolder.get():
                first = Path(inputs[0])
                return str(first.parent / (name if len(inputs) > 1 else first.stem))
            return str(Path(self.var_output_dir.get()).expanduser() / name) if len(inputs) > 1 else str(Path(self.var_output_dir.get()).expanduser())

        @staticmethod
        def _merge_name(inputs: List[str]) -> str:
            stems = [Path(p).stem for p in inputs]
            prefix = os.path.commonprefix(stems).rstrip(" _-.")
            return f"{prefix}_merged" if len(prefix) >= 3 else f"{stems[0]}_merged"

        def _new_task(self, inputs: List[str], name: Optional[str] = None) -> Dict:
            self.job_counter += 1
            name = name or (self._merge_name(inputs) if len(inputs) > 1 else Path(inputs[0]).stem)
            return {
                "id": f"task_{self.job_counter:04d}", "name": name, "inputs": list(inputs), "steps": self._default_steps(),
                "settings": self._settings_snapshot(), "output_dir": self._default_output_dir(inputs, name), "output_auto": True,
                "status": "Pending", "stage": "Queued", "progress": 0.0, "info": "", "doc_path": None, "created": now_iso(),
            }

        def _probe_quick_media_info(self, video_path: Path) -> str:
            if not shutil.which("ffprobe"):
                return ""
            try:
                res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(video_path)],
                                     capture_output=True, text=True, timeout=10)
                data = json.loads(res.stdout)
                streams = data.get("streams", [])
                h = streams[0].get("height") if streams else None
                dur_s = float((data.get("format") or {}).get("duration", 0.0) or 0.0)
                hrs, rem = divmod(int(dur_s), 3600)
                mins, secs = divmod(rem, 60)
                dur_str = (f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}") if dur_s > 0 else ""
                return " · ".join(x for x in (f"{h}p" if h else "", dur_str) if x)
            except Exception:
                return ""

        def _probe_tasks_async(self, tasks: List[Dict]):
            todo = [(t["id"], list(t["inputs"])) for t in tasks]

            def probe():
                for tid, inputs in todo:
                    infos = [self._probe_quick_media_info(Path(p)) for p in inputs[:20]]
                    self.msg_queue.put({"type": "job_info", "job_id": tid, "info": infos[0] if len(infos) == 1 else f"{len(inputs)} videos"})

            threading.Thread(target=probe, daemon=True).start()

        def _add_tasks(self, tasks: List[Dict], select: bool = True):
            if not tasks:
                return
            self.jobs.extend(tasks)
            self._refresh_queue_tree()
            self._save_queue()
            self._probe_tasks_async(tasks)
            if select:
                self.queue_tree.selection_set(tasks[-1]["id"])
                self._on_job_selected()

        def _enqueue_video_paths(self, paths: List[str]):
            queued = {p for t in self.jobs if t["status"] in ("Pending", "Running") for p in t["inputs"]}
            files = [str(Path(f)) for f in paths if Path(f).is_file() and str(Path(f)) not in queued]
            tasks = [self._new_task([f]) for f in files]
            self._add_tasks(tasks)
            if tasks:
                self._append_log(f"Added {len(tasks)} task(s), one per video.", "INFO")

        def _ask_videos(self, title: str) -> List[str]:
            files = filedialog.askopenfilenames(title=title, initialdir=self.settings.get("last_add_dir") or str(Path.home()), filetypes=MEDIA_FILETYPES)
            if files:
                self.settings["last_add_dir"] = str(Path(files[0]).parent)
            return list(files or [])

        def _add_videos(self):
            files = self._ask_videos("Add videos (one task per video)")
            if files:
                self._enqueue_video_paths(files)

        def _add_merge_task(self):
            files = self._ask_videos("Choose the videos to merge (you can reorder them afterwards)")
            if not files:
                return
            files = sorted(files, key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).name)])
            task = self._new_task(files)
            self._add_tasks([task])
            self._append_log(f"Added merge task '{task['name']}' with {len(files)} video(s). Check the order under Task Details.", "INFO")
            self.ent_task_name.focus_set()

        def _add_folder(self):
            folder = filedialog.askdirectory(title="Add every video in a folder (one task per video)", initialdir=self.settings.get("last_add_dir") or str(Path.home()))
            if folder:
                self.settings["last_add_dir"] = folder
                found = sorted(str(p) for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
                if found:
                    self._enqueue_video_paths(found)
                else:
                    self._append_log(f"No video files found in {folder}.", "WARN")

        @staticmethod
        def _steps_text(task: Dict) -> str:
            st = task.get("steps") or {}
            parts = [f"Merge {len(task['inputs'])}"] if len(task.get("inputs") or []) > 1 else []
            parts += [label for key, label in (("compress", "Compress"), ("audio", "Audio"), ("whisper", "Transcribe")) if st.get(key)]
            return " → ".join(parts) or "nothing selected"

        @staticmethod
        def _settings_summary(task: Dict) -> str:
            s, st = task.get("settings") or {}, task.get("steps") or {}
            parts = []
            if st.get("compress") or len(task.get("inputs") or []) > 1:
                parts.append(f"Video: {str(s.get('hb_encoder', '')).split()[0]} RF {s.get('hb_quality')}, {s.get('hb_container')}")
            if st.get("audio"):
                parts.append(f"Audio: {str(s.get('audio_format', '')).split(' (')[0]}")
            if st.get("whisper"):
                backend = "MLX" if str(s.get("whisper_backend", "")).startswith("MLX") else "CPU"
                diar = str(s.get("diarize_mode", "")).split()[0].replace("PyTorch", "spectral").replace("Pyannote.audio", "pyannote")
                lang = str(s.get("whisper_lang", "auto")).split()[0]
                fmts = [n for k, n in (("fmt_para_rtf", "¶RTF"), ("fmt_para_txt", "¶TXT"), ("fmt_srt", "SRT"), ("fmt_vtt", "VTT"),
                                       ("fmt_txt", "TXT"), ("fmt_rtf", "RTF")) if s.get(k)]
                vocab = [v for v in str(s.get("whisper_prompt") or "").split(",") if v.strip()]
                parts.append(f"Whisper {s.get('whisper_model')} ({backend}), language {lang}, speakers {diar}"
                             + (f", vocabulary {len(vocab)} term{'s' if len(vocab) != 1 else ''}" if vocab else "")
                             + f"; exports {', '.join(fmts) or 'none'}")
            return "  ·  ".join(parts) or "—"

        def _job_values(self, task: Dict) -> Tuple:
            st = task["status"]
            status = {"Pending": "Pending", "Completed": "✓ Done", "Failed": "✖ Failed"}.get(st, f"{task['progress']:.0f}%" if st == "Running" else st)
            name = task["name"] + (f"  ({task['info']})" if task.get("info") else "")
            return (name, self._steps_text(task), status, task["stage"])

        def _refresh_queue_tree(self):
            sel = set(self.queue_tree.selection())
            self.queue_tree.delete(*self.queue_tree.get_children())
            for task in self.jobs:
                self.queue_tree.insert("", tk.END, iid=task["id"], values=self._job_values(task), tags=(task["status"],))
                if task["id"] in sel:
                    self.queue_tree.selection_add(task["id"])
            self._update_queue_counts()

        def _update_job_row(self, task: Dict):
            if not self.queue_tree.exists(task["id"]):
                self._refresh_queue_tree()
                return
            self.queue_tree.item(task["id"], values=self._job_values(task), tags=(task["status"],))
            self._update_queue_counts()

        def _update_queue_counts(self):
            if not self.jobs:
                self.lbl_queue_counts.config(text="empty")
                return
            pending = sum(j["status"] == "Pending" for j in self.jobs)
            done = sum(j["status"] == "Completed" for j in self.jobs)
            failed = sum(j["status"] == "Failed" for j in self.jobs)
            parts = [f"{len(self.jobs)} task{'s' if len(self.jobs) != 1 else ''}"]
            parts += [f"{n} {label}" for n, label in ((pending, "pending"), (done, "done"), (failed, "failed")) if n]
            self.lbl_queue_counts.config(text=" · ".join(parts))

        def _get_selected_indices(self) -> List[int]:
            sel = set(self.queue_tree.selection())
            return [i for i, j in enumerate(self.jobs) if j["id"] in sel]

        def _selected_task(self) -> Optional[Dict]:
            ind = self._get_selected_indices()
            return self.jobs[ind[0]] if len(ind) == 1 else None

        def _move_up(self):
            ind = self._get_selected_indices()
            if not ind or ind[0] == 0:
                return
            for i in ind:
                self.jobs[i - 1], self.jobs[i] = self.jobs[i], self.jobs[i - 1]
            self._refresh_queue_tree()
            self._save_queue()

        def _move_down(self):
            ind = self._get_selected_indices()
            if not ind or ind[-1] >= len(self.jobs) - 1:
                return
            for i in reversed(ind):
                self.jobs[i + 1], self.jobs[i] = self.jobs[i], self.jobs[i + 1]
            self._refresh_queue_tree()
            self._save_queue()

        def _remove_selected(self):
            sel = set(self.queue_tree.selection())
            self.jobs = [j for j in self.jobs if j["id"] not in sel or j["status"] == "Running"]
            self._refresh_queue_tree()
            self._show_task_details(None)
            self._save_queue()

        def _reset_selected_jobs(self):
            sel = set(self.queue_tree.selection())
            for j in self.jobs:
                if (not sel or j["id"] in sel) and j["status"] != "Running":
                    j["status"], j["stage"], j["progress"] = "Pending", "Queued", 0.0
            self._refresh_queue_tree()
            self._on_job_selected()
            self._save_queue()

        def _clear_completed_jobs(self):
            self.jobs = [j for j in self.jobs if j["status"] != "Completed"]
            self._refresh_queue_tree()
            self._show_task_details(self._selected_task())
            self._save_queue()

        def _combine_selected(self):
            """Turn several selected (pending) tasks into one merge task, in queue order."""
            ind = self._get_selected_indices()
            chosen = [self.jobs[i] for i in ind if self.jobs[i]["status"] == "Pending"]
            if len(chosen) < 2:
                self.lbl_status.config(text="Select two or more pending tasks to combine (⌘-click or ⇧-click).")
                return
            inputs = [p for t in chosen for p in t["inputs"]]
            task = self._new_task(inputs)
            task["steps"] = dict(chosen[0]["steps"])
            task["settings"] = dict(chosen[0]["settings"])
            first = ind[0]
            ids = {t["id"] for t in chosen}
            self.jobs = [j for j in self.jobs if j["id"] not in ids]
            self.jobs.insert(min(first, len(self.jobs)), task)
            self._refresh_queue_tree()
            self._save_queue()
            self._probe_tasks_async([task])
            self.queue_tree.selection_set(task["id"])
            self._on_job_selected()
            self._append_log(f"Combined {len(chosen)} tasks into merge task '{task['name']}'.", "INFO")

        def _split_merge_task(self):
            task = self._selected_task()
            if task is None or len(task["inputs"]) < 2 or task["status"] == "Running":
                self.lbl_status.config(text="Select one merge task to split it into single-video tasks.")
                return
            pos = self.jobs.index(task)
            singles = []
            for p in task["inputs"]:
                t = self._new_task([p])
                t["steps"], t["settings"] = dict(task["steps"]), dict(task["settings"])
                singles.append(t)
            self.jobs[pos:pos + 1] = singles
            self._refresh_queue_tree()
            self._save_queue()
            self._probe_tasks_async(singles)
            self._show_task_details(None)

        # ----- task details panel -----
        def _show_task_details(self, task: Optional[Dict]):
            self._loading_details = True
            try:
                editable = task is not None and task["status"] == "Pending"
                state = tk.NORMAL if editable else tk.DISABLED
                self.lst_inputs.delete(0, tk.END)
                if task is None:
                    n = len(self._get_selected_indices()) if self.__dict__.get("queue_tree") is not None else 0  # tree is built after this panel
                    self.lbl_task_title.config(text=f"{n} TASKS SELECTED — right-click to combine" if n > 1 else "TASK DETAILS — select a task")
                    self.var_task_name.set("")
                    for v in (self.var_task_compress, self.var_task_audio, self.var_task_whisper):
                        v.set(False)
                    self.lbl_task_merge.config(text="")
                    self.lbl_task_settings.config(text="")
                    self.lbl_task_output.config(text="")
                    self.lbl_task_note.config(text="")
                else:
                    merge = len(task["inputs"]) > 1
                    self.lbl_task_title.config(text=("MERGE TASK" if merge else "TASK") + f"  ·  {task['status'].upper()}")
                    self.var_task_name.set(task["name"])
                    for p in task["inputs"]:
                        self.lst_inputs.insert(tk.END, Path(p).name)
                    st = task.get("steps") or {}
                    self.var_task_compress.set(bool(st.get("compress")))
                    self.var_task_audio.set(bool(st.get("audio")))
                    self.var_task_whisper.set(bool(st.get("whisper")))
                    self.lbl_task_merge.config(text=f"Merge {len(task['inputs'])} videos  →" if merge else "")
                    self.lbl_task_settings.config(text=self._settings_summary(task))
                    self.lbl_task_output.config(text=task["output_dir"])
                    if task["status"] == "Failed":
                        self.lbl_task_note.config(text=task["stage"], fg=THEME["err"])
                    elif not editable:
                        self.lbl_task_note.config(text="Finished tasks can't be edited — ↻ Reset to Pending (right-click) to run again with changes.", fg=THEME["fg_dim"])
                    elif merge:
                        self.lbl_task_note.config(text="Videos are merged top to bottom. Name = merged file and transcript name.", fg=THEME["fg_dim"])
                    else:
                        self.lbl_task_note.config(text="+ adds another video and turns this into a merge task.", fg=THEME["fg_dim"])
                self.ent_task_name.configure(state=state if (task and len(task["inputs"]) > 1) else tk.DISABLED)
                self.lst_inputs.configure(state=tk.NORMAL)
                for w in (*self._task_buttons, *self._task_checks, self.btn_task_settings, self.btn_task_output):
                    w.configure(state=state)
            finally:
                self._loading_details = False

        def _editable_task(self) -> Optional[Dict]:
            task = self._selected_task()
            return task if task is not None and task["status"] == "Pending" else None

        def _task_changed(self, task: Dict, refresh_details: bool = False):
            self._update_job_row(task)
            self._save_queue()
            if refresh_details:
                self._show_task_details(task)
            else:
                self.lbl_task_settings.config(text=self._settings_summary(task))

        def _on_task_name(self):
            if self._loading_details:
                return
            task = self._editable_task()
            name = self.var_task_name.get().strip()
            if task is None or not name or len(task["inputs"]) < 2:
                return
            task["name"] = re.sub(r'[\\/:*?"<>|]', "_", name)
            if task.get("output_auto", True):
                task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
                self.lbl_task_output.config(text=task["output_dir"])
            self._task_changed(task)

        def _on_task_steps(self):
            if self._loading_details:
                return
            task = self._editable_task()
            if task is None:
                return
            task["steps"] = {"compress": bool(self.var_task_compress.get()), "audio": bool(self.var_task_audio.get()),
                             "whisper": bool(self.var_task_whisper.get())}
            self._task_changed(task)

        def _task_input_move(self, direction: int):
            task = self._editable_task()
            sel = self.lst_inputs.curselection()
            if task is None or not sel:
                return
            i = sel[0]
            j = i + direction
            if not (0 <= j < len(task["inputs"])):
                return
            task["inputs"][i], task["inputs"][j] = task["inputs"][j], task["inputs"][i]
            self._task_changed(task, refresh_details=True)
            self.lst_inputs.selection_set(j)

        def _task_input_add(self):
            task = self._editable_task()
            if task is None:
                return
            files = [f for f in self._ask_videos("Add videos to this task (they will be merged)") if f not in task["inputs"]]
            if not files:
                return
            was_single = len(task["inputs"]) == 1
            task["inputs"].extend(files)
            if was_single:
                task["name"] = self._merge_name(task["inputs"])
            if task.get("output_auto", True):
                task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
            task["info"] = f"{len(task['inputs'])} videos"
            self._task_changed(task, refresh_details=True)

        def _task_input_remove(self):
            task = self._editable_task()
            sel = self.lst_inputs.curselection()
            if task is None or not sel or len(task["inputs"]) <= 1:
                return
            del task["inputs"][sel[0]]
            if len(task["inputs"]) == 1:
                task["name"] = Path(task["inputs"][0]).stem
                task["info"] = ""
                self._probe_tasks_async([task])
            else:
                task["info"] = f"{len(task['inputs'])} videos"
            if task.get("output_auto", True):
                task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
            self._task_changed(task, refresh_details=True)

        def _task_use_current_settings(self):
            ind = self._get_selected_indices()
            tasks = [self.jobs[i] for i in ind if self.jobs[i]["status"] == "Pending"]
            if not tasks:
                return
            snap = self._settings_snapshot()
            for t in tasks:
                t["settings"] = dict(snap)
                self._update_job_row(t)
            self._save_queue()
            self._show_task_details(self._selected_task())
            self.lbl_status.config(text=f"Applied the current Settings to {len(tasks)} task(s).")

        def _task_change_output(self):
            task = self._editable_task()
            if task is None:
                return
            f = filedialog.askdirectory(title="Output folder for this task", initialdir=task["output_dir"])
            if f:
                task["output_dir"] = f
                task["output_auto"] = False
                self._task_changed(task, refresh_details=True)

        # ----- selection / opening -----
        def _open_selected_job_folder(self):
            task = self._selected_task()
            if task is not None:
                p = Path(task["output_dir"])
            elif self.active_doc is not None:
                p = self.active_doc.out_dir
            else:
                p = Path(self.var_output_dir.get()).expanduser()
            while not p.exists() and p != p.parent:
                p = p.parent
            subprocess.Popen(["open", str(p)])

        def _open_selected_transcript(self):
            task = self._selected_task()
            if task is None or not task.get("doc_path"):
                self.lbl_status.config(text="This task has no transcript yet.")
                return
            try:
                self._render_doc(TranscriptDocument.load(Path(task["doc_path"])))
                self.notebook.select(self.tab_preview)
            except Exception as exc:
                self._append_log(f"Could not load transcript: {exc}", "ERROR")

        def _on_task_double_click(self, event=None):
            task = self._selected_task()
            if task is not None and task.get("doc_path"):
                self._open_selected_transcript()

        def _on_job_selected(self, _event=None):
            task = self._selected_task()
            self._show_task_details(task)
            if task is None:
                return
            doc_path = task.get("doc_path")
            if doc_path and Path(doc_path).exists() and (self.active_doc is None or str(self.active_doc.path) != doc_path):
                try:
                    self._render_doc(TranscriptDocument.load(Path(doc_path)))
                except Exception as exc:
                    self._append_log(f"Could not load transcript: {exc}", "ERROR")

        def _browse_output(self):
            f = filedialog.askdirectory(title="Default output folder", initialdir=self.var_output_dir.get())
            if f:
                self.var_output_dir.set(f)

        def _export_formats(self) -> Dict[str, bool]:
            return {"srt": bool(self.var_fmt_srt.get()), "vtt": bool(self.var_fmt_vtt.get()), "txt": bool(self.var_fmt_txt.get()),
                    "rtf": bool(self.var_fmt_rtf.get()), "para_rtf": bool(self.var_fmt_para_rtf.get()), "para_txt": bool(self.var_fmt_para_txt.get())}

        def _sync_token(self):
            """Keep the Keychain in step with the token field and the 'Remember' checkbox."""
            if not KeychainSecret.available():
                return
            token = self.var_hf_token.get().strip()
            if self.var_remember_token.get():
                if token and token != self._stored_token:
                    if KeychainSecret.save(token):
                        self._stored_token = token
                        self._append_log("Hugging Face token saved in the macOS Keychain.", "SUCCESS")
                    else:
                        self._append_log("Could not save the token in the Keychain (unexpected characters?).", "WARN")
            elif self._stored_token:
                KeychainSecret.delete()
                self._stored_token = ""
                self._append_log("Hugging Face token removed from the Keychain.", "INFO")

        # ================================================================== running
        def _start_pipeline(self):
            if self.worker is not None and self.worker.is_alive():
                self._append_log("Pipeline is already running.", "WARN")
                return
            pending = [j for j in self.jobs if j["status"] == "Pending"]
            if not pending:
                self.lbl_status.config(text="No pending tasks. Add videos (⌘O) or a merge task first.")
                return
            empty = [t["name"] for t in pending if len(t["inputs"]) < 2 and not any((t.get("steps") or {}).values())]
            if empty:
                self.lbl_status.config(text=f"'{empty[0]}' has no steps ticked — choose at least one step in Task Details.")
                return
            config = {
                "tasks": json.loads(json.dumps(pending)),
                "output_dir": self.var_output_dir.get(),
                "hf_token": self.var_hf_token.get(),
                "conf_high": 0.85, "conf_med": 0.60, "conf_poor": 0.40,
            }
            self._sync_token()
            self._save_settings()
            self._save_queue()
            self.stop_event.clear()
            self.worker = PipelineWorker(config, self.msg_queue, self.stop_event)
            self.worker.start()
            self.btn_stop.config(state=tk.NORMAL)
            self.btn_run.config(state=tk.DISABLED)
            self.notebook.select(self.tab_console)

        def _stop_pipeline(self):
            if self.worker is not None and self.worker.is_alive():
                self.stop_event.set()
                self._append_log("Stopping pipeline…", "WARN")

        def _poll_queue(self):
            try:
                for _ in range(500):
                    msg = self.msg_queue.get_nowait()
                    mtype = msg.get("type")
                    if mtype == "log":
                        self._append_log(msg["text"], msg.get("level", "INFO"))
                    elif mtype == "progress":
                        self.progress_var.set(msg["percent"])
                        self.lbl_status.config(text=msg["status"])
                    elif mtype in ("job_update", "job_info"):
                        for j in self.jobs:
                            if j["id"] == msg.get("job_id"):
                                status_changed = "status" in msg and msg["status"] != j["status"]
                                for k in ("status", "stage", "progress", "output_dir", "info"):
                                    if k in msg:
                                        j[k] = msg[k]
                                self._update_job_row(j)
                                if status_changed or mtype == "job_info":
                                    self._save_queue()
                                    if self._selected_task() is j:
                                        self._show_task_details(j)
                    elif mtype == "transcript_ready":
                        names = []
                        for j in self.jobs:
                            if j["id"] in (msg.get("job_ids") or []):
                                j["doc_path"] = msg["doc_path"]
                                names.append(j["name"])
                        self._save_queue()
                        if self.active_doc is None:
                            try:
                                self._render_doc(TranscriptDocument.load(Path(msg["doc_path"])))
                            except Exception as exc:
                                self._append_log(f"Could not load transcript: {exc}", "ERROR")
                        else:
                            # Don't pull the editor away from what you're working on.
                            self._append_log(f"✓ Transcript ready: {', '.join(names) or Path(msg['doc_path']).name} — double-click it in the queue to open.", "SUCCESS")
                    elif mtype in ("rerun_progress", "rerun_done", "rerun_failed"):
                        self._handle_rerun_message(msg)
                    elif mtype == "probe":
                        self.gpu_info, self.deps = msg["gpu"], msg["deps"]
                        self._update_header()
                        missing = [k for k in ("ffmpeg", "whisper") if not self.deps.get(k)]
                        if missing:
                            self._append_log(f"Missing: {', '.join(missing)}. See Setup & Status.", "WARN")
                    elif mtype == "done":
                        self.btn_stop.config(state=tk.DISABLED)
                        self.btn_run.config(state=tk.NORMAL)
                        self._save_queue()
                        ok = msg.get("success")
                        self._append_log("Pipeline finished." if ok else "Pipeline finished with errors or was stopped.", "SUCCESS" if ok else "ERROR")
            except queue.Empty:
                pass
            self.after(100, self._poll_queue)

        def _probe_system(self):
            try:
                gpu = SystemProbe.detect_gpu()
                deps = SystemProbe.check_dependencies()
            except Exception as exc:
                gpu, deps = {"gpu_name": "Unknown", "warning": f"Hardware probe failed: {exc}"}, {}
            self.msg_queue.put({"type": "probe", "gpu": gpu, "deps": deps})

        # ================================================================== settings persistence
        def _apply_settings(self):
            for key, value in (self.settings.get("vars") or {}).items():
                var = self._persist.get(key)
                if var is None or (key in self._combo_values and value not in self._combo_values[key]):
                    continue
                try:
                    var.set(value)
                except Exception:
                    pass
            self.lbl_hb_q.config(text=self._quality_text(self.var_hb_quality.get()))
            if self.var_remember_token.get():
                self._stored_token = KeychainSecret.load()
                if self._stored_token:
                    self.var_hf_token.set(self._stored_token)

        def _save_settings(self):
            values = {}
            for key, var in self._persist.items():
                try:
                    values[key] = var.get()
                except Exception:
                    pass
            self.settings["vars"] = values
            self.settings["ui_scale"] = self.ui_scale
            self._remember_position()
            try:
                self.settings["geometry"] = self.geometry()
                self.settings["sash_fraction"] = round(self.paned.sash_coord(0)[0] / max(1, self.paned.winfo_width()), 3)
            except Exception:
                pass
            SettingsStore.save(self.settings)

        def _on_close(self):
            if self.worker is not None and self.worker.is_alive():
                if not messagebox.askyesno("Quit?", "A pipeline run is in progress. Stop it and quit?", parent=self):
                    return
                self.stop_event.set()
                proc = self.worker.current_proc
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            try:
                self._autosave_edit(rerender=False)
            except Exception:
                pass
            self._sync_token()
            self._save_settings()
            self._save_queue()
            self._player_pause()
            self._video_stop()
            proc = self.__dict__.get("_rerun_proc")
            if proc is not None and proc.poll() is None:
                proc.terminate()
            self.destroy()

        # ================================================================== dialogs
        def _install_desktop_shortcut(self):
            try:
                paths = MacAppInstaller.install_shortcut()
                self._append_log(f"Installed {paths['app']} (Launchpad / Spotlight; drag it to the Dock).", "SUCCESS")
                if paths.get("desktop_icon"):
                    self._append_log(f"Desktop link: {paths['desktop_icon']}", "INFO")
                self._append_log(f"App launches log to {paths['log']}", "INFO")
            except Exception as exc:
                self._append_log(f"Failed to install app: {exc}", "ERROR")
            self.notebook.select(self.tab_console)

        def _show_setup_modal(self):
            win = tk.Toplevel(self)
            win.title("Setup & Status")
            win.geometry("720x520")
            win.minsize(480, 320)
            win.configure(bg=THEME["bg_panel"])
            bar = tk.Frame(win, bg=THEME["bg_panel"])
            bar.pack(fill=tk.X, padx=14, pady=(12, 0))
            self._tool(bar, "Install App in ~/Applications", lambda: (win.destroy(), self._install_desktop_shortcut())).pack(side=tk.LEFT)
            txt = tk.Text(win, bg="#0b0c10", fg="#a7f3d0", font=self.f["mono_small"], padx=12, pady=12, wrap=tk.WORD, highlightthickness=0)
            txt.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
            status = ["# Status"]
            if self.deps:
                for name, label in (("ffmpeg", "ffmpeg"), ("handbrake", "HandBrakeCLI"), ("whisper", "Whisper"), ("losslesscut", "LosslessCut")):
                    status.append(f"  {'✓' if self.deps.get(name) else '✗'} {label}: {self.deps.get(name) or 'not found'}")
            else:
                status.append("  (still detecting…)")
            status.append(f"  Python for Whisper: {MacAppInstaller.find_best_python()}")
            status.append(f"  Settings file: {SettingsStore.PATH}")
            txt.insert("1.0", "\n".join(status) + "\n\n" + (
                "# Keyboard\n"
                "  ⌘O add videos · ⇧⌘O open transcript · ⌘R run · ⌘. stop\n"
                "  ⌘+ / ⌘− / ⌘0 text size · ⌘1 / ⌘2 / ⌘3 tabs\n"
                "  Transcript: click a word to edit its segment · Esc play/pause (also while typing)\n"
                "  ⌘[ / ⌘] back/forward 5 s · ⌘⇧. faster · ⌘⇧, slower · ⌘S save segment · ⌘↩ save + mark checked + next · ⌘I insert a missed segment\n"
                "  ⇧⌘R re-transcribe the selected segment / a passage (bigger model, other language or translate)\n"
                "  ⌘Z / ⇧⌘Z undo / redo transcript changes · ⌃⇧L laughs · ⌃⇧I inaudible · ⌃⇧U unclear · ⌃⇧C crosstalk · ⌃⇧P pause · ⌃⇧G German\n"
                "  double-click a word to play from there\n"
                "  ←/→ previous/next uncertain word (when the transcript has focus) · Space play/pause\n\n"
                "# 1. Homebrew tools (https://brew.sh)\n"
                "brew install ffmpeg handbrake python@3.12 python-tk@3.12\n\n"
                "# 2. Python env with MLX Whisper (Apple GPU) + PyTorch (MPS diarization)\n"
                "/opt/homebrew/bin/python3.12 -m venv ~/mlx_whisper_env\n"
                "~/mlx_whisper_env/bin/pip install -U mlx-whisper openai-whisper torch numpy\n\n"
                "# 3. Optional: neural diarization (needs a Hugging Face token)\n"
                "~/mlx_whisper_env/bin/pip install pyannote.audio\n\n"
                "# 4. Optional: LosslessCut\n"
                "brew install --cask losslesscut\n"
            ))
            txt.config(state=tk.DISABLED)

    app = SiliconPipelineApp()
    app.mainloop()


if __name__ == "__main__":
    if "--install-desktop" in sys.argv or "--install-app" in sys.argv:
        info = MacAppInstaller.install_shortcut()
        print(f"App installed: {info['app']}")
        sys.exit(0)
    launch_gui()
