"""Small helpers shared by the whole app."""
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


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_BLANK_CHARS = re.compile(r"[\s.…,;:!?\-–—♪♫*\"'()\[\]]+")


def is_system_junk(name: str) -> bool:
    """macOS bookkeeping files: '._name' (AppleDouble, written next to every file on exFAT/FAT drives such as a T7)
    and .DS_Store. They look like videos or transcripts by name but are not."""
    return name.startswith("._") or name in (".DS_Store", "Thumbs.db", "desktop.ini")


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
