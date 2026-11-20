"""Windows layer: token in the Credential Manager, Start-menu shortcut with an icon, no console windows,
GPU names. GPU work goes through PyTorch (CUDA on NVIDIA, AMD's ROCm build for Windows on Radeon); see asr/runner.py.
"""
import math
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional

from condense.platform.detect import log_file

CREATE_NO_WINDOW = 0x08000000


def popen_defaults(kwargs: dict) -> dict:
    """What every child process gets on Windows: no console window (otherwise each ffmpeg / Whisper / ffplay
    started from the windowless GUI flashes one), and text output read as UTF-8 instead of the ANSI code page,
    so an unusual character in a file name cannot crash a step."""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
        kwargs["encoding"] = "utf-8"
        kwargs.setdefault("errors", "replace")
    return kwargs


def hide_console_windows() -> None:
    if os.name != "nt" or getattr(subprocess.Popen, "_condense_no_window", False):
        return
    base = subprocess.Popen

    class _NoWindowPopen(base):  # subprocess.run & co. look up subprocess.Popen, so they use this too
        _condense_no_window = True

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **popen_defaults(kwargs))

    subprocess.Popen = _NoWindowPopen
    os.environ.setdefault("PYTHONUTF8", "1")  # child Pythons (Whisper) print UTF-8 too


def winget_links_dir() -> Path:
    """winget puts command-line tools (ffmpeg, HandBrakeCLI) here and on the user's PATH."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Microsoft" / "WinGet" / "Links"


def gpu_names() -> List[str]:
    """Display adapters as Windows reports them (works without PyTorch)."""
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
                             capture_output=True, text=True, timeout=20)
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def gpu_vendor(names: List[str]) -> str:
    """'nvidia', 'amd', 'intel' or '' — the first discrete-looking GPU wins (NVIDIA / AMD before Intel)."""
    text = " ".join(names).lower()
    if "nvidia" in text or "geforce" in text or "rtx" in text:
        return "nvidia"
    if "radeon" in text or "amd" in text:
        return "amd"
    if "intel" in text:
        return "intel"
    return ""


class CredentialManagerSecret:
    """Stores the Hugging Face token in the Windows Credential Manager (generic credential "Condense") through the
    Win32 API (advapi32), so it never touches a file or a command line."""

    LABEL = "Windows Credential Manager"
    TARGET = "Condense/huggingface-token"
    _backend = None  # tests put a stand-in here; None = the real Credential Manager

    @classmethod
    def _api(cls):
        if cls._backend is not None:
            return cls._backend
        if os.name != "nt":
            return None
        cls._backend = _Win32Credentials()
        return cls._backend

    @classmethod
    def available(cls) -> bool:
        try:
            return cls._api() is not None
        except Exception:
            return False

    @classmethod
    def load(cls) -> str:
        try:
            api = cls._api()
            return (api.read(cls.TARGET) or "") if api else ""
        except Exception:
            return ""

    @classmethod
    def save(cls, secret: str) -> bool:
        import re
        secret = secret.strip()
        if not secret or not re.fullmatch(r"[A-Za-z0-9_\-]+", secret):
            return False
        try:
            api = cls._api()
            if api is None:
                return False
            api.write(cls.TARGET, secret)
            return cls.load() == secret
        except Exception:
            return False

    @classmethod
    def delete(cls) -> None:
        try:
            api = cls._api()
            if api:
                api.delete(cls.TARGET)
        except Exception:
            pass


class _Win32Credentials:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self.ctypes, self.CREDENTIAL = ctypes, CREDENTIAL
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.CredWriteW, self.CredReadW, self.CredDeleteW, self.CredFree = adv.CredWriteW, adv.CredReadW, adv.CredDeleteW, adv.CredFree
        self.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        self.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        self.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.CredFree.argtypes = [ctypes.c_void_p]

    def write(self, target: str, secret: str) -> None:
        c = self.ctypes
        blob = secret.encode("utf-16-le")
        buf = c.create_string_buffer(blob, len(blob))
        cred = self.CREDENTIAL()
        cred.Type = 1                 # CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.UserName = "huggingface"
        cred.Comment = "Hugging Face token used by Condense for pyannote speaker detection"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = c.cast(buf, c.POINTER(c.c_char))
        cred.Persist = 2              # CRED_PERSIST_LOCAL_MACHINE: this user, this computer
        if not self.CredWriteW(c.byref(cred), 0):
            raise OSError(c.get_last_error(), "CredWriteW failed")

    def read(self, target: str) -> Optional[str]:
        c = self.ctypes
        p = c.POINTER(self.CREDENTIAL)()
        if not self.CredReadW(target, 1, 0, c.byref(p)):
            return None
        try:
            size = p.contents.CredentialBlobSize
            return c.string_at(p.contents.CredentialBlob, size).decode("utf-16-le")
        finally:
            self.CredFree(p)

    def delete(self, target: str) -> None:
        self.CredDeleteW(target, 1, 0)


# ------------------------------------------------------------------ icon (drawn here: Windows needs an .ico)
def _icon_rgba(size: int) -> bytes:
    """The Condense icon (same design as the SVG used on macOS/Linux), anti-aliased, as RGBA rows."""
    s = size / 128.0
    sub = 3 if size <= 64 else 2
    offs = [(i + 0.5) / sub for i in range(sub)]

    def lerp(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def in_rrect(x, y, r=26.0, inset=0.0):
        x0, y0, x1, y1 = inset, inset, 128 - inset, 128 - inset
        r = max(0.0, r - inset)
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return x0 <= x <= x1 and y0 <= y <= y1 and (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def in_tri(x, y):
        (ax, ay), (bx, by), (cx, cy) = (46, 36), (46, 92), (94, 64)
        d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
        d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
        d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    def seg_dist(x, y, a, b):
        (ax, ay), (bx, by) = a, b
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
        return math.hypot(x - (ax + t * (bx - ax)), y - (ay + t * (by - ay)))

    rows = bytearray()
    for py in range(size):
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for oy in offs:
                for ox in offs:
                    x, y = (px + ox) / s, (py + oy) / s
                    if not in_rrect(x, y):
                        continue
                    if not in_rrect(x, y, inset=4.0):
                        col = (225, 29, 72)                                      # border #e11d48
                    else:
                        col = lerp((24, 27, 36), (15, 17, 23), (x + y) / 256)    # background gradient
                    if in_tri(x, y):
                        col = lerp((244, 63, 94), (190, 18, 60), (x + y) / 256)  # crimson play triangle
                    if (x - 96) ** 2 + (y - 96) ** 2 <= 18 * 18:
                        col = (16, 185, 129)                                     # green check disc
                        if seg_dist(x, y, (88, 96), (94, 102)) <= 2.2 or seg_dist(x, y, (94, 102), (105, 90)) <= 2.2:
                            col = (255, 255, 255)
                    acc[0] += col[0]; acc[1] += col[1]; acc[2] += col[2]; acc[3] += 255
            n = sub * sub
            a = acc[3] / n
            if a:
                rows += bytes((int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)), int(acc[2] / (acc[3] / 255)), int(a)))
            else:
                rows += b"\0\0\0\0"
    return bytes(rows)


def _png(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_ico(sizes=(16, 32, 48, 256)) -> bytes:
    """A Windows .ico with PNG images (supported since Windows Vista)."""
    images = [_png(n, _icon_rgba(n)) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for n, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return header + entries + b"".join(images)


class WindowsShortcutInstaller:
    """Start-menu and Desktop shortcuts (Condense.lnk) that start this installation without a console window."""

    APP_NAME = "Condense"

    @staticmethod
    def gui_python() -> str:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        return str(pyw if pyw.exists() else exe)

    @classmethod
    def install_shortcut(cls) -> Dict[str, Optional[str]]:
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Condense"
        local.mkdir(parents=True, exist_ok=True)
        icon = local / "condense.ico"
        icon.write_bytes(build_ico())
        roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        start_menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Condense.lnk"
        start_menu.parent.mkdir(parents=True, exist_ok=True)
        # Paths travel in environment variables: no quoting problems with spaces or apostrophes in user names.
        script = (
            "$w = New-Object -ComObject WScript.Shell\n"
            "$targets = @($env:CONDENSE_LNK, (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Condense.lnk'))\n"
            "foreach ($t in $targets) {\n"
            "  $s = $w.CreateShortcut($t)\n"
            "  $s.TargetPath = $env:CONDENSE_TARGET\n"
            "  $s.Arguments = '-m condense'\n"
            "  $s.WorkingDirectory = $env:USERPROFILE\n"
            "  $s.IconLocation = $env:CONDENSE_ICON\n"
            "  $s.Description = 'Condense: video, audio and transcript processing for qualitative research'\n"
            "  $s.Save()\n"
            "  Write-Output $t\n"
            "}\n"
        )
        env = dict(os.environ, CONDENSE_LNK=str(start_menu), CONDENSE_TARGET=cls.gui_python(), CONDENSE_ICON=str(icon))
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                             capture_output=True, text=True, env=env, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"could not create the shortcut: {(res.stderr or res.stdout).strip()[:300]}")
        made = [line.strip() for line in res.stdout.splitlines() if line.strip().lower().endswith(".lnk")]
        desktop = next((m for m in made if Path(m) != start_menu), None)
        return {"app": str(start_menu), "desktop_icon": desktop, "log": str(log_file())}
