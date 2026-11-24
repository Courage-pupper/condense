"""A video with the transcript as subtitles.

Two ways:
  track   the subtitles go into the file as a subtitle track (mov_text in MP4/MOV, SubRip in MKV). Nothing is
          re-encoded, so it takes seconds; viewers switch them on and off in the player (VLC, QuickTime, IINA …).
  burn    the subtitles are drawn into the picture (ffmpeg's libass 'subtitles' filter). Always visible, in every player
          and wherever the file is uploaded; the video is re-encoded (H.264), so it takes a while.

Whisper's segments can be long, so the subtitles are made from the words: at most two lines and a few seconds each,
broken at sentence ends where possible.
"""
import functools
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from condense.core.util import is_blank_text
from condense.media import trim as trimlib

Cue = Tuple[float, float, str]
MAX_CHARS = 84      # two lines of ~42 characters
MAX_SECONDS = 6.0
MIN_SECONDS = 1.0
LINE = 42
LANG3 = {"en": "eng", "de": "ger", "fr": "fre", "es": "spa", "it": "ita", "nl": "dut", "pt": "por", "sv": "swe", "da": "dan",
         "no": "nor", "fi": "fin", "pl": "pol", "cs": "cze", "tr": "tur", "el": "gre", "ru": "rus", "ja": "jpn", "zh": "chi", "ar": "ara"}


def _wrap(text: str) -> str:
    """Two balanced lines when it is longer than one."""
    if len(text) <= LINE:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def cues(segments: Sequence[Dict], speakers: bool = True) -> List[Cue]:
    """Subtitle cues from the segments' words (segments without word times: one cue per segment). With speakers, a
    cue starts with "Name: " when the speaker changes."""
    out: List[Cue] = []
    last_speaker = None
    for seg in segments:
        if is_blank_text(seg.get("text")):
            continue
        spk = seg.get("speaker") if speakers else None
        prefix = f"{spk}: " if spk and spk != last_speaker else ""
        last_speaker = spk if speakers else None
        words = [w for w in seg.get("words") or [] if str(w.get("word", "")).strip() and "start" in w and "end" in w]
        if not words:
            out.append((float(seg["start"]), float(seg["end"]), _wrap(prefix + str(seg.get("text", "")).strip())))
            continue
        chunk: List[Dict] = []

        def flush():
            nonlocal prefix
            if chunk:
                text = prefix + " ".join(str(w["word"]).strip() for w in chunk)
                out.append((float(chunk[0]["start"]), float(chunk[-1]["end"]), _wrap(text)))
                prefix = ""
                chunk.clear()

        for w in words:
            token = str(w["word"]).strip()
            text_len = len(prefix) + sum(len(str(x["word"]).strip()) + 1 for x in chunk) + len(token)
            if chunk and (text_len > MAX_CHARS or float(w["end"]) - float(chunk[0]["start"]) > MAX_SECONDS):
                flush()
            chunk.append(w)
            if token.endswith((".", "?", "!")) and float(w["end"]) - float(chunk[0]["start"]) > 1.5:
                flush()  # a sentence ends: start the next subtitle with the next sentence
        flush()
    # in time order; each shown for at least MIN_SECONDS where the next one leaves room; never overlapping (players
    # would stack them)
    out.sort(key=lambda c: c[0])
    fixed: List[Cue] = []
    for i, (a, b, t) in enumerate(out):
        nxt = out[i + 1][0] if i + 1 < len(out) else None
        b = max(b, a + MIN_SECONDS)
        if nxt is not None and b > nxt:
            b = max(a + 0.2, nxt)
        fixed.append((round(a, 3), round(b, 3), t))
    return fixed


def _ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(cs: Sequence[Cue]) -> str:
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(cs, start=1))


@functools.lru_cache(maxsize=1)
def can_burn() -> bool:
    """ffmpeg was built with libass (the 'subtitles' filter)."""
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return any(line.split()[1:2] == ["subtitles"] for line in res.stdout.splitlines() if len(line.split()) > 1)


def output_path(video: Path, out_dir: Path, base_name: str, mode: str) -> Path:
    """<base>_subtitled.mp4 when burning; for a track the container keeps the video as it is (MKV for formats whose
    container cannot hold a text track)."""
    if mode == "burn":
        ext = ".mp4"
    else:
        ext = Path(video).suffix.lower()
        ext = ext if ext in (".mp4", ".m4v", ".mov", ".mkv") else ".mkv"
    return Path(out_dir) / f"{base_name}_subtitled{ext}"


def command(video: Path, srt_name: str, dst: Path, mode: str, language: Optional[str] = None) -> List[str]:
    """The ffmpeg command, to run in the folder that holds srt_name (the subtitles filter's path syntax is fragile:
    a plain file name in the working folder avoids escaping colons, quotes and backslashes in real paths)."""
    base = ["ffmpeg", "-hide_banner", "-y", "-nostdin"]
    lang = LANG3.get((language or "").lower()[:2])
    if mode == "burn":
        style = "Fontsize=20,Outline=1.2,Shadow=0,MarginV=22,BorderStyle=1"
        return base + ["-i", str(video), "-map", "0:v:0", "-map", "0:a?", "-vf", f"subtitles={srt_name}:force_style='{style}'",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                       "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(dst)]
    codec = "srt" if dst.suffix.lower() == ".mkv" else "mov_text"
    cmd = base + ["-i", str(video), "-i", srt_name, "-map", "0:v", "-map", "0:a?", "-map", "1:0", "-c", "copy", "-c:s", codec,
                  "-disposition:s:0", "default"]
    if lang:
        cmd += ["-metadata:s:s:0", f"language={lang}"]
    return cmd + ["-progress", "pipe:1", "-nostats", str(dst)]


def write(video: Path, segments: Sequence[Dict], dst: Path, mode: str = "track", speakers: bool = True,
          language: Optional[str] = None, on_progress: Optional[Callable[[float], None]] = None,
          stop: Optional[threading.Event] = None) -> Dict:
    """Write dst. Returns {"dst", "cues", "mode"}."""
    if mode == "burn" and not can_burn():
        raise RuntimeError("This ffmpeg cannot draw subtitles into the picture (it was built without libass). "
                           "Use a subtitle track instead.")
    cs = cues(segments, speakers)
    if not cs:
        raise ValueError("The transcript has no text to show as subtitles.")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = trimlib.probe(Path(video))["duration"] or max(b for _, b, _ in cs)
    work = Path(tempfile.mkdtemp(prefix="condense_subs_"))
    tmp = dst.with_name(f".{dst.stem}.part{dst.suffix}")
    try:
        (work / "subs.srt").write_text(srt_text(cs), encoding="utf-8")
        cmd = command(Path(video).resolve(), "subs.srt", tmp.resolve(), mode, language)
        trimlib._run(cmd, total, on_progress, stop, cwd=str(work))
        tmp.replace(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {"dst": dst, "cues": len(cs), "mode": mode}
