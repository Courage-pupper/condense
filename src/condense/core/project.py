"""Projects: a folder of transcripts from one study.

A `condense-project.json` in that folder (or a parent) holds the project's speaker list, so the same people get the same
number keys and colours in every session. The overview summarises every transcript in the folder.
"""
import csv
import fnmatch
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_FILE = "condense-project.json"
GENERIC_SPEAKER = ("SPEAKER_", "Speaker ")


def find_project_file(start: Path, levels: int = 7) -> Optional[Path]:
    """The nearest condense-project.json in start or one of its parents."""
    p = Path(start).resolve()
    if p.is_file():
        p = p.parent
    for _ in range(levels + 1):
        cand = p / PROJECT_FILE
        if cand.is_file():
            return cand
        if p.parent == p:
            break
        p = p.parent
    return None


def load_project(path: Path) -> Dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("speakers", [])
    return data


def save_project(folder: Path, speakers: List[str], name: Optional[str] = None) -> Path:
    path = Path(folder) / PROJECT_FILE
    data = load_project(path) if path.exists() else {"created": datetime.now().isoformat(timespec="seconds")}
    clean: List[str] = []
    for s in speakers:
        s = " ".join(str(s).split())
        if s and s not in clean:
            clean.append(s)
    data["speakers"] = clean
    if name:
        data["name"] = name
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


DEFAULT_SKIP = ["Consent*", "*Unsorted*", "_backups"]


def update_project(folder: Path, **fields) -> Path:
    """Change project settings (name, levels, skip, episode_types …) and keep everything else."""
    path = Path(folder) / PROJECT_FILE
    data = load_project(path) if path.exists() else {"created": datetime.now().isoformat(timespec="seconds")}
    data.update(fields)
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def project_settings(folder: Path) -> Dict:
    data = load_project(Path(folder) / PROJECT_FILE)
    from condense.core.episodes import DEFAULT_TYPES
    from condense.core.structure import from_project
    st = from_project(data)
    levels = st.levels if st is not None and st.levels else list(data.get("levels") or [])
    return {"name": data.get("name") or Path(folder).name, "levels": levels, "structure": st,
            "skip": list(data.get("skip") if data.get("skip") is not None else DEFAULT_SKIP),
            "episode_types": list(data.get("episode_types") or DEFAULT_TYPES), "speakers": list(data["speakers"])}


def skipped(name: str, patterns) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat.lower()) for pat in patterns or [])


def _level_word(names: List[str]) -> Optional[str]:
    """'Day 1 2026-08-10', 'Day 2 …' -> 'Day'; 'Group 1', 'Group 2' -> 'Group'. None if the names share no leading word."""
    words = [re.split(r"[\s_\-]+|(?<=[A-Za-z])(?=\d)", n.strip())[0] for n in names if n.strip()]
    if len(words) == 1 and re.match(r"^[A-Za-z]+[\s_\-]*\d", names[0].strip()):
        return words[0].capitalize()  # a single "Day 1 …" or "Group 3" is a level too
    if len(words) < 2:
        return None
    word, count = Counter(w.capitalize() for w in words if w and not w[0].isdigit()).most_common(1)[0] if any(
        w and not w[0].isdigit() for w in words) else (None, 0)
    if not word or count < max(2, round(0.6 * len(words))):
        return None
    # levels are numbered folders (Day 1, Group 3, Participant 07); "Session Recordings" in every group is not a level
    numbered = [n for n in names if n.strip().lower().startswith(word.lower()) and re.search(r"\d", n)]
    return word if len(numbered) >= max(1, round(0.6 * count)) else None


def detect_levels(folder: Path, skip=None, depth: int = 3) -> List[str]:
    """Names for the folder levels of a study, read from its folder names: Day 1 / Day 2 … then Group 1 / Group 2 …"""
    skip = DEFAULT_SKIP if skip is None else skip
    levels: List[str] = []
    current = [Path(folder)]
    for _ in range(depth):
        subs = []
        for d in current:
            try:
                subs += [c for c in d.iterdir() if c.is_dir() and not c.name.startswith(".") and not skipped(c.name, skip)]
            except OSError:
                pass
        word = _level_word([c.name for c in subs])
        if not word:
            break
        levels.append(word)
        current = [c for c in subs if c.name.lower().startswith(word.lower())]
    return levels


def level_path(project: Path, path: Path, levels: List[str]) -> List[Tuple[str, str]]:
    """[('Day', 'Day 1 2026-08-10'), ('Group', 'Group 1')] for a file below project/Day 1 …/Group 1/…"""
    try:
        parts = Path(path).resolve().relative_to(Path(project).resolve()).parts[:-1]
    except ValueError:
        return []
    return list(zip(levels, parts))


def level_folder(project: Path, path: Path, levels: List[str]) -> Optional[Path]:
    """The deepest level folder (e.g. the group's) that a file lies in."""
    lp = level_path(project, path, levels)
    return Path(project).resolve().joinpath(*[name for _, name in lp]) if lp else None


def group_key(project: Path, path: Path) -> Optional[str]:
    """The group a file belongs to: the name of its deepest level folder ("Group 1"). The same group on different days
    is the same case, so its speakers are shared across days."""
    levels = project_settings(project)["levels"]
    lp = level_path(project, path, levels)
    return lp[-1][1] if lp and len(lp) == len(levels) else None


def groups_in(project: Path) -> List[str]:
    """All deepest-level folder names (Group 1, Group 2 …) in the project, in natural order."""
    cfg = project_settings(project)
    if not cfg["levels"]:
        return []
    current = [Path(project)]
    for lv in cfg["levels"]:
        nxt = []
        for d in current:
            try:
                nxt += [c for c in d.iterdir() if c.is_dir() and c.name.lower().startswith(lv.lower()) and not skipped(c.name, cfg["skip"])]
            except OSError:
                pass
        current = nxt
    names = sorted({c.name for c in current}, key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)])
    return names


def group_speakers(project: Path, group: str) -> List[str]:
    return list((load_project(Path(project) / PROJECT_FILE).get("group_speakers") or {}).get(group) or [])


def save_group_speakers(project: Path, group: str, speakers: List[str]) -> Path:
    data = load_project(Path(project) / PROJECT_FILE)
    gs = dict(data.get("group_speakers") or {})
    clean: List[str] = []
    for n in speakers:
        n = " ".join(str(n).split())
        if n and n not in clean:
            clean.append(n)
    if clean:
        gs[group] = clean
    else:
        gs.pop(group, None)
    return update_project(project, group_speakers=gs)


def project_speakers_for(transcript_path: Path) -> List[str]:
    """Speakers offered first for a transcript, in key order: the project's (e.g. facilitators, in every group), then
    those of the transcript's group (its children)."""
    f = find_project_file(Path(transcript_path).parent)
    if not f:
        return []
    out = list(load_project(f)["speakers"])
    g = group_key(f.parent, transcript_path)
    if g:
        out += [n for n in group_speakers(f.parent, g) if n not in out]
    return out


def effective_roster(doc_roster: List[str], project: List[str]) -> List[str]:
    """Project speakers first (stable order across sessions), then this transcript's other speakers."""
    return list(project) + [s for s in doc_roster if s not in project]


SKIP_DIRS = {"_backups", "node_modules", "__pycache__", "$RECYCLE.BIN", "System Volume Information", "lost+found"}


def transcripts_under(folder: Path, stop=None, max_dirs: int = 20000, skip=None) -> List[Path]:
    """Transcripts in folder and its subfolders. Skips hidden folders, backups and symbolic links, and gives up after
    max_dirs folders, so pointing it at a home folder or a whole drive cannot hang the app."""
    from condense.core.transcript import TranscriptDocument
    found: List[Path] = []
    seen = 0
    for root, dirs, files in os.walk(folder, followlinks=False, onerror=lambda e: None):
        seen += 1
        if seen > max_dirs or (stop is not None and stop.is_set()):
            break
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS and not skipped(d, skip))
        found += [Path(root) / f for f in files if f.endswith(TranscriptDocument.SUFFIX) and not f.startswith(".")]
    return sorted(found)


def suggest_settings(folder: Path, skip=None) -> Dict:
    """Levels and folder structure for an existing study, read from its folders."""
    from condense.core.structure import suggest
    skip = list(DEFAULT_SKIP) if skip is None else skip
    levels = detect_levels(folder, skip)
    return {"levels": levels, "skip": skip, "structure": suggest(folder, levels)}


def create_project(folder: Path, name: Optional[str] = None, speakers: Optional[List[str]] = None) -> Path:
    """Make folder a project (created if needed). An existing project file keeps its speakers unless new ones are given."""
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    existing = folder / PROJECT_FILE
    keep = load_project(existing)["speakers"] if existing.exists() else []
    return save_project(folder, speakers if speakers else keep, name=name or folder.name)


def project_name(project_folder: Path) -> str:
    f = Path(project_folder) / PROJECT_FILE
    return str(load_project(f).get("name") or Path(project_folder).name) if f.exists() else Path(project_folder).name


def overview_row(path: Path) -> Dict:
    from condense.core.transcript import TranscriptDocument
    doc = TranscriptDocument.load(path)
    segs = doc.segments
    kept = doc.kept_segments()
    med = doc.thresholds["medium"]
    words = [w for s in kept for w in (s.get("words") or [])]
    uncertain = sum(1 for w in words if float(w.get("probability", w.get("prob", 0.85))) < med)
    length = max((float(s.get("end", 0.0)) for s in segs), default=0.0)
    cut = sum(b - a for a, b in doc.cuts)
    return {
        "path": path,
        "name": doc.base_name,
        "folder": str(path.parent),
        "length_s": round(length, 1),
        "checked_pct": round(doc.checked_fraction() * 100),
        "uncertain_pct": round(uncertain * 100 / len(words)) if words else 0,
        "speakers": len({s.get("speaker") for s in kept if s.get("speaker")}),
        "generic_speakers": sum(1 for n in {s.get("speaker") or "" for s in kept} if n.startswith(GENERIC_SPEAKER)),
        "cut_s": round(cut, 1),
        "notes": sum(1 for s in segs if s.get("note")),
        "edits": len(doc.data.get("edit_history") or []),
        "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "episodes": doc.episodes,
        "media_paths": [m for m in [(doc.data.get("media") or {}).get("playback")]
                        + [i.get("path") for i in ((doc.data.get("provenance") or {}).get("inputs") or []) if isinstance(i, dict)] if m],
    }


def overview(folder: Path, stop=None) -> List[Dict]:
    """One row per transcript, with the level folders it lies in (Day, Group …) when folder is a project."""
    settings = project_settings(folder) if (Path(folder) / PROJECT_FILE).is_file() else {"levels": [], "skip": DEFAULT_SKIP}
    rows = []
    for p in transcripts_under(folder, stop=stop, skip=settings["skip"]):
        if stop is not None and stop.is_set():
            break
        try:
            row = overview_row(p)
        except Exception as exc:
            row = {"path": p, "name": p.name, "error": str(exc)}
        row["levels"] = level_path(folder, p, settings["levels"])
        rows.append(row)
    return rows


def write_overview_csv(rows: List[Dict], target: Path) -> Path:
    level_names = []
    for r in rows:
        for lv, _ in r.get("levels") or []:
            if lv not in level_names:
                level_names.append(lv)
    cols = level_names + ["name", "folder", "length_s", "checked_pct", "uncertain_pct", "speakers", "generic_speakers", "cut_s", "notes",
            "edits", "modified"]
    with open(target, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            if "error" not in r:
                lv = dict(r.get("levels") or [])
                w.writerow([lv.get(c, "") if c in level_names else r.get(c, "") for c in cols])
    return target


MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm", ".wav", ".mp3", ".m4a", ".aac", ".flac"}


def inventory(folder: Path, stop=None, max_dirs: int = 20000) -> List[Dict]:
    """Everything a study holds, by level folder: each recording (transcribed or not) with its role — session, interview
    or other — and its transcript's overview row; transcripts whose recording was not found are listed too.
    Media inside Condense's own output folders (compressed copies) belong to their transcript and are not listed."""
    from condense.core.transcript import TranscriptDocument
    from condense.core.util import is_system_junk
    folder = Path(folder).resolve()
    settings = project_settings(folder) if (folder / PROJECT_FILE).is_file() else {"levels": [], "skip": DEFAULT_SKIP, "structure": None}
    st = settings.get("structure")
    media: List[Path] = []
    transcripts: List[Path] = []
    output_dirs = set()
    seen = 0
    for root, dirs, files in os.walk(folder, followlinks=False, onerror=lambda e: None):
        seen += 1
        if seen > max_dirs or (stop is not None and stop.is_set()):
            break
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS and not skipped(d, settings["skip"]))
        here = Path(root)
        if any(f.endswith(TranscriptDocument.SUFFIX) and not f.startswith(".") for f in files):
            output_dirs.add(here)
        for f in sorted(files):
            if f.startswith(".") or is_system_junk(f):
                continue
            if f.endswith(TranscriptDocument.SUFFIX):
                transcripts.append(here / f)
            elif Path(f).suffix.lower() in MEDIA_EXTS:
                media.append(here / f)
    media = [m for m in media if m.parent not in output_dirs]
    rows: Dict[Path, Dict] = {}
    for t in transcripts:
        if stop is not None and stop.is_set():
            break
        try:
            rows[t] = overview_row(t)
        except Exception as exc:
            rows[t] = {"path": t, "name": t.name, "error": str(exc), "media_paths": []}
    by_path: Dict[str, List[Path]] = {}
    by_name: Dict[str, List[Path]] = {}
    for t, row in rows.items():
        for mp in row.get("media_paths") or []:
            try:
                by_path.setdefault(str(Path(mp).resolve()), []).append(t)
            except OSError:
                pass
            by_name.setdefault(Path(mp).name, []).append(t)

    def role_of(dir_: Path) -> str:
        if st is not None:
            r = st.role_of(folder, dir_)
            if r:
                return "session" if r == "sessions" else "interview"
            return "other"
        low = dir_.name.lower()
        return "interview" if "interview" in low else ("session" if "session" in low else "other")

    def group_of(path: Path) -> Tuple:
        return tuple(level_path(folder, path, settings["levels"]))

    items: List[Dict] = []
    used = set()
    for m in media:
        # 1. Condense's default: results in a folder named like the recording, next to it
        sub = m.parent / m.stem
        cands = sorted(sub.glob(f"*{TranscriptDocument.SUFFIX}")) if sub.is_dir() else []
        t = next((c for c in cands if not c.name.startswith(".") and c not in used), None)
        # 2. a transcript that names this file, preferably in the same day/group
        same = [c for c in by_path.get(str(m.resolve()), []) if c not in used]
        t = t or next((c for c in same if group_of(c) == group_of(m)), None) or next(iter(same), None)
        # 3. same file name, same level folder (the drive may be mounted elsewhere on another computer)
        t = t or next((c for c in by_name.get(m.name, []) if group_of(c) == group_of(m) and c not in used), None)
        if t is not None:
            used.add(t)
        items.append({"role": role_of(m.parent), "media": m, "transcript": t, "row": rows.get(t), "levels": group_of(m)})
    for t, row in rows.items():
        if t not in used:
            outer = t.parent.parent if t.parent in output_dirs else t.parent
            items.append({"role": role_of(outer), "media": None, "transcript": t, "row": row, "levels": group_of(t)})
    return items


def natural_key(name: str):
    """'I4' before 'I10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


class DurationCache:
    """Recording lengths, measured once with ffprobe and remembered (by path, size and modification time), so the
    Project tab can show them without reading every file again."""

    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self.data: Dict[str, List] = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    @staticmethod
    def _key(p: Path) -> Tuple[str, int, int]:
        st = p.stat()
        return str(p.resolve()), st.st_size, int(st.st_mtime)

    def get(self, p: Path) -> Optional[float]:
        try:
            key, size, mtime = self._key(p)
        except OSError:
            return None
        hit = self.data.get(key)
        return float(hit[2]) if hit and hit[0] == size and hit[1] == mtime else None

    def measure(self, p: Path) -> Optional[float]:
        import subprocess
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)],
                                 capture_output=True, text=True, timeout=60)
            dur = float(out.stdout.strip())
        except Exception:
            return None
        try:
            key, size, mtime = self._key(p)
            self.data[key] = [size, mtime, round(dur, 2)]
        except OSError:
            pass
        return dur

    def save(self):
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
            except OSError:
                pass
