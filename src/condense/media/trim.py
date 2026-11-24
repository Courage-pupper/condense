"""Write a trimmed copy of a recording: everything except the cut parts.

exact     re-encodes (H.264 + AAC in .mp4, or AAC in .m4a for audio): cuts exactly where you cut. Default.
lossless  copies the streams: fast and without quality loss, but each kept part can only start on a keyframe, so up
          to a few seconds before it (from the cut part) stay in. The parts actually written are returned, so the
          transcript is re-timed to the real file and stays in sync either way.
"""
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from condense.core.util import ffconcat_line

Range = Tuple[float, float]


def probe(path: Path) -> dict:
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    data = json.loads(res.stdout or "{}")
    kinds = {s.get("codec_type") for s in data.get("streams", [])}
    return {"duration": float((data.get("format") or {}).get("duration") or 0.0),
            "video": "video" in kinds, "audio": "audio" in kinds}


def start_time(path: Path) -> float:
    """Where the file's clock starts. Camera files often don't start at 0 (.MTS at about 1.4 s, some MP4/MOV files later
    still), while ffmpeg's -ss and the transcript count from the start of the file."""
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=60)
    try:
        return float((res.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def video_lead(path: Path) -> float:
    """How long after the file's start its picture starts. A stream copy starts the picture at a keyframe, but some
    formats (MPEG-TS: .MTS / .ts) also keep the sound from a little before it, so the copy really starts that much
    earlier than the keyframe."""
    res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time:stream=codec_type,start_time",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(res.stdout or "{}")
        t0 = float((data.get("format") or {}).get("start_time") or 0.0)
        v = next(float(st["start_time"]) for st in data.get("streams", [])
                 if st.get("codec_type") == "video" and st.get("start_time") not in (None, "N/A"))
    except (ValueError, StopIteration, TypeError):
        return 0.0
    return max(0.0, v - t0)


def keyframes(path: Path) -> List[float]:
    """Times of the video keyframes (read from the packets, without decoding), counted from the start of the file
    like -ss and the transcript — not on the file's own clock, which made lossless clips of such files start seconds
    away from where the transcript said."""
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=3600)
    t0 = start_time(path)
    out = []
    for line in res.stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                out.append(max(0.0, float(parts[0]) - t0))
            except ValueError:
                pass
    return sorted(out)


def snap_to_keyframes(kept: Sequence[Range], keys: Sequence[float]) -> Tuple[List[Range], int]:
    """Each kept part starts at the last keyframe at or before its start (what a stream copy really writes).
    A cut that ends before that keyframe's successor cannot be removed without re-encoding: the two parts around it
    are joined (the cut stays in). Returns (parts, number of cuts that could not be applied)."""
    import bisect
    out: List[Range] = []
    dropped = 0
    for a, b in kept:
        i = bisect.bisect_right(keys, a + 1e-3) - 1
        k = keys[i] if i >= 0 else 0.0
        if out and k <= out[-1][1] + 1e-3:
            out[-1] = (out[-1][0], b)
            dropped += 1
            continue
        out.append((round(k, 3), b))
    return out, dropped


def exact_command(src: Path, dst: Path, kept: Sequence[Range], has_video: bool, has_audio: bool) -> List[str]:
    """One ffmpeg run: trim + concat filter, H.264 (veryfast, CRF 18) + AAC. A single part (an episode) seeks to it
    directly instead of decoding everything before it — frame-accurate, because the output is re-encoded."""
    if len(kept) == 1:
        a, b = kept[0]
        cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}"]
        if has_video:
            cmd += ["-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
        if dst.suffix.lower() in (".mp4", ".m4a", ".mov"):
            cmd += ["-movflags", "+faststart"]
        return cmd + ["-progress", "pipe:1", "-nostats", str(dst)]
    parts, labels = [], []
    for i, (a, b) in enumerate(kept):
        if has_video:
            parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append((f"[v{i}]" if has_video else "") + (f"[a{i}]" if has_audio else ""))
    out_labels = ("[v]" if has_video else "") + ("[a]" if has_audio else "")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(kept)}:v={int(has_video)}:a={int(has_audio)}{out_labels}"
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(src), "-filter_complex", graph]
    if has_video:
        cmd += ["-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    if dst.suffix.lower() in (".mp4", ".m4a", ".mov"):
        cmd += ["-movflags", "+faststart"]
    return cmd + ["-progress", "pipe:1", "-nostats", str(dst)]


def output_path(src: Path, out_dir: Path, base_name: str, mode: str, has_video: bool) -> Path:
    if mode == "lossless":
        return out_dir / f"{base_name}{src.suffix}"
    return out_dir / f"{base_name}{'.mp4' if has_video else '.m4a'}"


def _run(cmd: List[str], total: float, on_progress: Optional[Callable[[float], None]], stop: Optional[threading.Event],
         base: float = 0.0, span: float = 1.0, cwd: Optional[str] = None) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", cwd=cwd)
    err_lines: List[str] = []

    def read_err():
        for line in proc.stderr:
            err_lines.append(line.rstrip())
            del err_lines[:-12]

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    for line in proc.stdout:
        if stop is not None and stop.is_set():
            proc.terminate()
            proc.wait()
            raise InterruptedError("Cancelled.")
        m = re.match(r"out_time_(?:ms|us)=(\d+)", line.strip())
        if m and on_progress and total > 0:
            on_progress(min(1.0, base + span * (int(m.group(1)) / 1e6) / total))
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(err_lines[-8:]))


def write_trimmed(src: Path, dst: Path, kept: Sequence[Range], mode: str = "exact", work_dir: Optional[Path] = None,
                  on_progress: Optional[Callable[[float], None]] = None, stop: Optional[threading.Event] = None) -> dict:
    """Write dst. Returns {"kept": parts as actually written (source timeline), "not_applied": cuts a lossless copy
    could not remove (too close to the previous keyframe)}."""
    info = probe(src)
    if not kept:
        raise ValueError("Everything is cut: nothing would be left.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = sum(b - a for a, b in kept)
    if mode != "lossless":
        _run(exact_command(src, dst, kept, info["video"], info["audio"]), total, on_progress, stop)
        return {"kept": list(kept), "not_applied": 0}
    planned, not_applied = snap_to_keyframes(kept, keyframes(src)) if info["video"] else (list(kept), 0)
    actual: List[Range] = []
    work = Path(work_dir or dst.parent)
    pieces = []
    try:
        done = 0.0
        all_total = sum(b - a for a, b in planned)
        for i, (a, b) in enumerate(planned):
            piece = work / f".{dst.stem}.part{i:03d}{src.suffix}"
            # no -avoid_negative_ts make_zero: it shifted the copy by the frames' reorder delay (0.1–0.5 s) against the
            # transcript; the container's own handling keeps sound and picture where they were
            cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src), "-map", "0",
                   "-c", "copy", "-progress", "pipe:1", "-nostats", str(piece)]
            _run(cmd, all_total, on_progress, stop, base=done / all_total if all_total else 0, span=1.0)
            done += b - a
            pieces.append(piece)
            # map the transcript with what was really written: the copy may start a little before the keyframe (sound
            # kept from before it) and rarely ends exactly at b
            real = probe(piece)["duration"] or (b - a)
            start = max(0.0, a - (video_lead(piece) if info["video"] else 0.0))
            actual.append((round(start, 3), round(start + real, 3)))
        if len(pieces) == 1:
            os.replace(pieces[0], dst)
            pieces = []
        else:
            lst = work / f".{dst.stem}.concat.txt"
            lst.write_text("\n".join(ffconcat_line(p) for p in pieces) + "\n", encoding="utf-8")
            pieces.append(lst)
            _run(["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0", "-c", "copy",
                  "-progress", "pipe:1", "-nostats", str(dst)], all_total, None, stop)
    finally:
        for p in pieces:
            try:
                p.unlink()
            except OSError:
                pass
    return {"kept": actual, "not_applied": not_applied}
