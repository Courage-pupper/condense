"""Which platform layer Condense uses, and what each platform offers.

`CONDENSE_PLATFORM=mac|linux` overrides the detection (the test suite uses it to run the macOS and the Linux
paths on any machine). Window-system details such as the ⌘/Ctrl key follow the real OS instead (gui/widgets.py).
"""
import os
import sys
from pathlib import Path
from typing import List


def _detect() -> str:
    forced = os.environ.get("CONDENSE_PLATFORM", "").strip().lower()
    if forced in ("mac", "linux"):
        return forced
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


PLATFORM = _detect()
IS_MAC_PLATFORM = PLATFORM == "mac"
IS_LINUX = PLATFORM == "linux"


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value) if value else Path.home() / default


def app_support_dir() -> Path:
    """Settings, task queue. macOS: ~/Library/Application Support/Condense; Linux: ~/.config/condense."""
    if IS_MAC_PLATFORM:
        return Path.home() / "Library" / "Application Support" / "Condense"
    return _xdg("XDG_CONFIG_HOME", ".config") / "condense"


def log_file() -> Path:
    """Where the app launcher writes the console output."""
    if IS_MAC_PLATFORM:
        return Path.home() / "Library" / "Logs" / "Condense.log"
    return _xdg("XDG_STATE_HOME", ".local/state") / "condense" / "condense.log"


def default_output_dir() -> Path:
    return Path.home() / ("Movies" if IS_MAC_PLATFORM else "Videos") / "Condense"


def open_command(path) -> List[str]:
    """Command that opens a file or folder with the desktop's default app (Finder, Files, the video player…)."""
    return ["open", str(path)] if IS_MAC_PLATFORM else ["xdg-open", str(path)]


# ----- what the Settings tab offers on each platform (the first entry is the default) -----
if IS_MAC_PLATFORM:
    WHISPER_BACKENDS = ["MLX (Apple Silicon GPU)", "openai-whisper (CPU)"]
    VIDEO_ENCODERS = [
        "vt_h265 (Apple Media Engine)",
        "vt_h265_10bit (Apple Media Engine)",
        "vt_h264 (Apple Media Engine)",
        "ffmpeg_hevc_videotoolbox (Media Engine, no HandBrake)",
        "ffmpeg_h264_videotoolbox (Media Engine, no HandBrake)",
        "svt_av1 (CPU - no AV1 HW encoder on Apple Silicon)",
        "x265 (CPU)",
        "x264 (CPU)",
    ]
    GPU_DIARIZATION = "PyTorch MPS Spectral (Offline - No Token Needed)"
    HW_QUALITY_NAME = "VideoToolbox"
    INSTALL_HINTS = {"handbrake": "brew install handbrake", "ffmpeg": "brew install ffmpeg"}
else:
    WHISPER_BACKENDS = ["PyTorch GPU (ROCm / CUDA)", "openai-whisper (CPU)"]
    VIDEO_ENCODERS = [
        "vaapi_h265 (AMD / Intel GPU via VA-API, no HandBrake)",
        "vaapi_av1 (AMD RX 7000 / Intel Arc and newer, VA-API)",
        "vaapi_h264 (AMD / Intel GPU via VA-API, no HandBrake)",
        "x265 (CPU — use this on NVIDIA until NVENC support)",
        "x264 (CPU)",
        "svt_av1 (CPU)",
    ]
    GPU_DIARIZATION = "PyTorch GPU Spectral (Offline - No Token Needed)"
    HW_QUALITY_NAME = "VA-API qp"
    INSTALL_HINTS = {"handbrake": "sudo apt install handbrake-cli", "ffmpeg": "sudo apt install ffmpeg"}

DEFAULT_BACKEND = WHISPER_BACKENDS[0]
DEFAULT_ENCODER = VIDEO_ENCODERS[0]
