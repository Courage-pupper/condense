"""Linux layer: token in the desktop keyring (Secret Service), app-menu launcher, VA-API detection.

GPU work on Linux goes through PyTorch (ROCm on AMD, CUDA on NVIDIA) in the Whisper Python; see asr/runner.py.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from condense.platform.detect import log_file


class SecretServiceSecret:
    """Stores the Hugging Face token in the desktop keyring (GNOME Keyring, KWallet via Secret Service) with
    `secret-tool` (package libsecret-tools). The secret goes through stdin, never onto a command line."""

    LABEL = "system keyring"
    ATTRS = ["service", "Condense", "account", "huggingface-token"]

    @classmethod
    def available(cls) -> bool:
        return shutil.which("secret-tool") is not None

    @classmethod
    def load(cls) -> str:
        if not cls.available():
            return ""
        try:
            res = subprocess.run(["secret-tool", "lookup", *cls.ATTRS], capture_output=True, text=True, timeout=10)
            return res.stdout.strip() if res.returncode == 0 else ""
        except Exception:
            return ""

    @classmethod
    def save(cls, secret: str) -> bool:
        secret = secret.strip()
        if not cls.available() or not secret or not re.fullmatch(r"[A-Za-z0-9_\-]+", secret):
            return False
        try:
            res = subprocess.run(["secret-tool", "store", "--label=Condense: Hugging Face token", *cls.ATTRS],
                                 input=secret, capture_output=True, text=True, timeout=20)
            return res.returncode == 0 and cls.load() == secret
        except Exception:
            return False

    @classmethod
    def delete(cls) -> None:
        if cls.available():
            subprocess.run(["secret-tool", "clear", *cls.ATTRS], capture_output=True, timeout=10)


class LinuxDesktopInstaller:
    """Adds Condense to the desktop's app menu (GNOME, KDE, …) with a .desktop file and an icon."""

    APP_NAME = "Condense"

    @staticmethod
    def _data_home() -> Path:
        value = os.environ.get("XDG_DATA_HOME", "").strip()
        return Path(value) if value else Path.home() / ".local" / "share"

    @classmethod
    def install_shortcut(cls) -> Dict[str, Optional[str]]:
        from condense.platform.mac import MacAppInstaller  # the icon is shared

        data = cls._data_home()
        icon = data / "icons" / "hicolor" / "scalable" / "apps" / "condense.svg"
        icon.parent.mkdir(parents=True, exist_ok=True)
        icon.write_text(MacAppInstaller.ICON_SVG, encoding="utf-8")

        log = log_file()
        log.parent.mkdir(parents=True, exist_ok=True)
        launcher = data / "condense" / "launcher.sh"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        # sys.executable is the Python Condense is installed in, so the menu entry always runs this installation
        launcher.write_text(
            "#!/bin/sh\n"
            "# Written by `condense --install-app`. Starts Condense and appends its console output to the log.\n"
            f"exec {shlex.quote(sys.executable)} -m condense \"$@\" >> {shlex.quote(str(log))} 2>&1\n",
            encoding="utf-8",
        )
        os.chmod(launcher, 0o755)

        def q(p: Path) -> str:  # .desktop Exec quoting: double quotes, with \ " ` $ escaped
            s = str(p)
            return '"' + re.sub(r'([\\"`$])', r"\\\\\1", s) + '"' if re.search(r'[\s"\'\\`$]', s) else s

        apps = data / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        desktop = apps / "condense.desktop"
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Condense\n"
            "GenericName=Interview transcription\n"
            "Comment=Video, audio and transcript processing for qualitative research\n"
            f"Exec={q(launcher)}\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Video;Education;Science;\n"
            "Keywords=transcript;whisper;interview;subtitles;\n"
            "StartupWMClass=Condense\n",
            encoding="utf-8",
        )
        if shutil.which("update-desktop-database"):
            try:
                subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=15)
            except Exception:
                pass
        return {"app": str(desktop), "desktop_icon": None, "log": str(log)}


def vaapi_device() -> Optional[str]:
    """First DRM render node (the GPU that VA-API encodes on), e.g. /dev/dri/renderD128."""
    override = os.environ.get("CONDENSE_VAAPI_DEVICE", "").strip()
    if override:
        return override if Path(override).exists() else None
    nodes = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else []
    return str(nodes[0]) if nodes else None


def has_ffmpeg_vaapi() -> bool:
    if not shutil.which("ffmpeg") or vaapi_device() is None:
        return False
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=8)
        return "hevc_vaapi" in res.stdout or "h264_vaapi" in res.stdout
    except Exception:
        return False
