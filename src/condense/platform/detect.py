"""Which platform layer Condense uses, and what each platform offers.

`CONDENSE_PLATFORM=mac|linux|windows` overrides the detection (the test suite uses it to run the macOS and the Linux
paths on any machine). Window-system details such as the ⌘/Ctrl key follow the real OS instead (gui/widgets.py).
"""
import os
import sys
from pathlib import Path
from typing import List


def _detect() -> str:
    forced = os.environ.get("CONDENSE_PLATFORM", "").strip().lower()
    if forced in ("mac", "linux", "windows"):
        return forced
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    return "other"


PLATFORM = _detect()
IS_MAC_PLATFORM = PLATFORM == "mac"
IS_LINUX = PLATFORM == "linux"
IS_WINDOWS = PLATFORM == "windows"


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value) if value else Path.home() / default


def _win_dir(var: str, default: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value) if value else Path.home() / default


def app_support_dir() -> Path:
    """Settings, task queue. macOS: ~/Library/Application Support/Condense; Linux: ~/.config/condense;
    Windows: %APPDATA%\\Condense."""
    if IS_MAC_PLATFORM:
        return Path.home() / "Library" / "Application Support" / "Condense"
    if IS_WINDOWS:
        return _win_dir("APPDATA", "AppData/Roaming") / "Condense"
    return _xdg("XDG_CONFIG_HOME", ".config") / "condense"


def log_file() -> Path:
    """Where the app launcher writes the console output."""
    if IS_MAC_PLATFORM:
        return Path.home() / "Library" / "Logs" / "Condense.log"
    if IS_WINDOWS:
        return _win_dir("LOCALAPPDATA", "AppData/Local") / "Condense" / "Logs" / "condense.log"
    return _xdg("XDG_STATE_HOME", ".local/state") / "condense" / "condense.log"


def default_output_dir() -> Path:
    return Path.home() / ("Movies" if IS_MAC_PLATFORM else "Videos") / "Condense"


def open_command(path) -> List[str]:
    """Command that opens a file or folder with the desktop's default app (Finder, Files, the video player…)."""
    if IS_MAC_PLATFORM:
        return ["open", str(path)]
    if IS_WINDOWS:
        return ["explorer", str(path)]  # folders open in Explorer, files in their default app
    return ["xdg-open", str(path)]


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
elif IS_WINDOWS:
    WHISPER_BACKENDS = ["PyTorch GPU (ROCm / CUDA)", "openai-whisper (CPU)"]
    VIDEO_ENCODERS = [
        "auto_h265 (this PC's GPU: NVIDIA, AMD or Intel; else CPU)",
        "nvenc_h265 (NVIDIA GPU, no HandBrake)",
        "amf_h265 (AMD GPU, no HandBrake)",
        "qsv_h265 (Intel GPU, no HandBrake)",
        "nvenc_h264 (NVIDIA GPU, no HandBrake)",
        "amf_h264 (AMD GPU, no HandBrake)",
        "x265 (CPU)",
        "x264 (CPU)",
        "svt_av1 (CPU)",
    ]
    GPU_DIARIZATION = "PyTorch GPU Spectral (Offline - No Token Needed)"
    HW_QUALITY_NAME = "GPU qp"
    INSTALL_HINTS = {"handbrake": "winget install -e --id HandBrake.HandBrake.CLI", "ffmpeg": "winget install -e --id Gyan.FFmpeg"}
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
