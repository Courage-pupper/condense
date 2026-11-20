"""macOS / Apple Silicon integration: environment, hardware probe, app bundle, Keychain."""
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

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Condense"
LEGACY_APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "SiliconVideoPipeline"


def setup_environment() -> None:
    # Apps launched from Finder/Dock get a minimal PATH without Homebrew; make ffmpeg/HandBrakeCLI findable.
    _HOMEBREW_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    _cur_path = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join([p for p in _HOMEBREW_PATHS if p not in _cur_path] + _cur_path)

    # Let PyTorch silently fall back to CPU for any op not yet implemented on Metal (MPS).
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    if sys.platform != "darwin":
        print("Warning: Condense currently supports macOS on Apple Silicon only (Windows and Linux are planned).", file=sys.stderr)




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

    APP_NAME = "Condense"
    BUNDLE_ID = "org.condense.app"
    _best_python_cache: Optional[str] = None

    @staticmethod
    def _python_candidates() -> List[Path]:
        home = Path.home()
        return [
            Path(sys.executable),  # the Python running Condense (the installer puts Whisper here)
            home / "mlx_whisper_env" / "bin" / "python3",
            home / ".venv" / "bin" / "python3",
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
        home = Path.home()
        app_path = home / "Applications" / f"{cls.APP_NAME}.app"
        macos_dir = app_path / "Contents" / "MacOS"
        res_dir = app_path / "Contents" / "Resources"
        macos_dir.mkdir(parents=True, exist_ok=True)
        res_dir.mkdir(parents=True, exist_ok=True)

        svg_path = res_dir / "AppIcon.svg"
        svg_path.write_text(cls.ICON_SVG, encoding="utf-8")
        has_icon = cls._build_icns(svg_path, res_dir / "AppIcon.icns")

        app_py = sys.executable  # must be the Python where Condense is installed
        log_path = home / "Library" / "Logs" / "Condense.log"
        launcher = macos_dir / "launcher"
        launcher.write_text(
            "#!/bin/bash\n"
            'export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"\n'
            "export PYTORCH_ENABLE_MPS_FALLBACK=1\n"
            f'exec "{app_py}" -m condense "$@" >> "{log_path}" 2>&1\n',
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



class KeychainSecret:
    """Stores one secret (the Hugging Face token) in the macOS login Keychain via /usr/bin/security.
    The secret is written through `security -i` on stdin, so it never appears on a command line."""

    SERVICE = "Condense"
    LEGACY_SERVICE = "SiliconVideoPipeline"  # name used before the app was called Condense
    ACCOUNT = "huggingface-token"

    @classmethod
    def available(cls) -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    @classmethod
    def _load(cls, service: str) -> str:
        try:
            res = subprocess.run(["security", "find-generic-password", "-s", service, "-a", cls.ACCOUNT, "-w"],
                                 capture_output=True, text=True, timeout=10)
            return res.stdout.strip() if res.returncode == 0 else ""
        except Exception:
            return ""

    @classmethod
    def load(cls) -> str:
        if not cls.available():
            return ""
        secret = cls._load(cls.SERVICE)
        if not secret:
            secret = cls._load(cls.LEGACY_SERVICE)
            if secret:
                cls.save(secret)  # move it to the new name
        return secret

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
