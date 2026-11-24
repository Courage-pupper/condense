"""What Condense keeps on disk besides your recordings and transcripts, and clearing what is safe to clear.

  backups      _backups/ next to each transcript: copies made while you edit (at most one per 5 minutes, the last 30).
               A 5-hour transcript is ~6.5 MB, so 30 backups are ~200 MB. Thinning keeps the newest few per transcript.
  leftovers    hidden part files of exports that were interrupted (.<name>.part.mp4, .<name>.part000.mp4, *.tmp).
  audio cache  the sound of recordings, read once for finding interview clips and linking videos; made again when needed.

Only these are ever deleted. Videos Condense made (compressed, trimmed, subtitled, playback audio) are listed for
information only: they are results, and some are what a transcript plays.
"""
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

BACKUP_RE = re.compile(r"^(?P<base>.+)\.(?P<stamp>\d{8}-\d{6})\.transcript\.json$")
LEFTOVER_RE = re.compile(r"^\..+\.(part\d*|concat)\.[A-Za-z0-9]+$|.+\.transcript\.json\.tmp$")
MADE_RE = re.compile(r".+_(compressed|trimmed|subtitled|playback)\.[A-Za-z0-9]+$")
LEFTOVER_AGE_S = 600
SKIP = {"node_modules", "__pycache__", "$RECYCLE.BIN", "System Volume Information", "lost+found"}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def human(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def scan(folder: Path, cache_dir: Optional[Path] = None, max_dirs: int = 20000, stop=None) -> Dict:
    """Everything under folder (and the audio cache). Reads only names and sizes."""
    rep = {"folder": Path(folder), "backups": [], "leftovers": [], "made": [], "transcripts": 0, "transcript_bytes": 0,
           "cache": [], "truncated": False}
    seen = 0
    now = time.time()
    for root, dirs, files in os.walk(folder, followlinks=False, onerror=lambda e: None):
        seen += 1
        if seen > max_dirs or (stop is not None and stop.is_set()):
            rep["truncated"] = True
            break
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        here = Path(root)
        in_backups = here.name == "_backups"
        for f in files:
            p = here / f
            if in_backups and BACKUP_RE.match(f):
                rep["backups"].append(p)
            elif LEFTOVER_RE.match(f):
                if now - _mtime(p) > LEFTOVER_AGE_S:  # not an export that is running right now
                    rep["leftovers"].append(p)
            elif f.endswith(".transcript.json") and not f.startswith("."):
                rep["transcripts"] += 1
                rep["transcript_bytes"] += _size(p)
            elif MADE_RE.match(f) and not f.startswith("."):
                rep["made"].append(p)
    if cache_dir is not None and Path(cache_dir).is_dir():
        rep["cache"] = [p for p in Path(cache_dir).rglob("*") if p.is_file()]
    for key in ("backups", "leftovers", "made", "cache"):
        rep[f"{key}_bytes"] = sum(_size(p) for p in rep[key])
    return rep


def backups_to_remove(backups: Sequence[Path], keep: int) -> List[Path]:
    """The backups beyond the newest `keep` of each transcript (keep=0: all)."""
    groups: Dict[tuple, List[tuple]] = {}
    for p in backups:
        m = BACKUP_RE.match(p.name)
        if m:
            groups.setdefault((str(p.parent), m.group("base")), []).append((m.group("stamp"), p))
    out: List[Path] = []
    for items in groups.values():
        items.sort(reverse=True)  # newest first (the stamp sorts as time)
        out += [p for _, p in items[max(0, keep):]]
    return sorted(out)


def delete(paths: Sequence[Path]) -> Dict:
    """Delete the files; returns {"deleted", "bytes", "failed"}. Empty _backups folders are removed too."""
    deleted, freed, failed = 0, 0, []
    parents = set()
    for p in paths:
        size = _size(p)
        try:
            Path(p).unlink()
            deleted += 1
            freed += size
            parents.add(Path(p).parent)
        except OSError as exc:
            failed.append(f"{Path(p).name}: {exc}")
    for d in parents:
        if d.name == "_backups":
            try:
                d.rmdir()  # only if empty
            except OSError:
                pass
    return {"deleted": deleted, "bytes": freed, "failed": failed}
