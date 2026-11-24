"""How much memory Condense uses: its own process and the programs it started (ffmpeg for the video preview, playback
and exports, the transcription worker). No extra packages: /proc on Linux, ps on macOS, the Windows API on Windows."""
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

GB = 1024 ** 3
HIGH_SHARE = 0.25       # of the computer's memory: worth a look
VERY_HIGH_SHARE = 0.40  # the system may start closing other programs: free memory or restart


def total_ram() -> int:
    try:
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout.strip())
        if sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys)
    except Exception:
        pass
    return 0


def _windows_self_rss() -> int:
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    h = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
    return int(pmc.WorkingSetSize)


def process_table() -> List[Dict]:
    """Every process the system lists: pid, ppid, rss (bytes), name. Empty if it can't be read."""
    out: List[Dict] = []
    if sys.platform.startswith("linux") and Path("/proc").is_dir():
        page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                stat = (d / "stat").read_text()
                name = stat[stat.index("(") + 1: stat.rindex(")")]
                ppid = int(stat[stat.rindex(")") + 2:].split()[1])
                rss = int((d / "statm").read_text().split()[1]) * page
            except (OSError, ValueError, IndexError):
                continue
            out.append({"pid": int(d.name), "ppid": ppid, "rss": rss, "name": name})
        return out
    if os.name == "nt":
        return []
    try:
        res = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss=,comm="], capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    for line in res.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            out.append({"pid": int(parts[0]), "ppid": int(parts[1]), "rss": int(parts[2]) * 1024,
                        "name": os.path.basename(parts[3].strip())})
        except ValueError:
            continue
    return out


def usage(pid: Optional[int] = None, table: Optional[List[Dict]] = None) -> Dict:
    """{"self": bytes, "children": [{"pid", "name", "rss"}], "total": bytes, "ram": bytes} for this process and every
    process it started (and those started by them)."""
    pid = pid or os.getpid()
    rows = process_table() if table is None else table
    by_pid = {r["pid"]: r for r in rows}
    me = by_pid.get(pid)
    if me is not None:
        self_rss = me["rss"]
    elif os.name == "nt":
        try:
            self_rss = _windows_self_rss()
        except Exception:
            self_rss = 0
    else:
        self_rss = 0
    kids: Dict[int, List[Dict]] = {}
    for r in rows:
        kids.setdefault(r["ppid"], []).append(r)
    children, todo = [], list(kids.get(pid, []))
    while todo:
        r = todo.pop()
        if r["pid"] == pid:
            continue
        children.append({"pid": r["pid"], "name": r["name"], "rss": r["rss"]})
        todo.extend(kids.get(r["pid"], []))
    children.sort(key=lambda c: -c["rss"])
    return {"self": self_rss, "children": children, "total": self_rss + sum(c["rss"] for c in children), "ram": total_ram()}


def level(total: int, ram: int) -> str:
    """"ok", "high" (worth a look) or "very_high" (free memory or restart)."""
    if ram <= 0:
        return "ok"
    share = total / ram
    return "very_high" if share >= VERY_HIGH_SHARE else "high" if share >= HIGH_SHARE else "ok"


def fmt(n: float) -> str:
    if n >= GB:
        return f"{n / GB:.1f} GB"
    return f"{n / 1024 ** 2:.0f} MB"


def role(name: str) -> str:
    """What a child process is, in words."""
    low = name.lower()
    if low.startswith("ffplay"):
        return "audio playback"
    if low.startswith("ffmpeg"):
        return "video preview / export (ffmpeg)"
    if low.startswith("ffprobe"):
        return "reading a recording (ffprobe)"
    if low.startswith("python") or "whisper" in low:
        return "transcription (Whisper)"
    if "handbrake" in low:
        return "compression (HandBrake)"
    return name
