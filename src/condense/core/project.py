"""Projects: a folder of transcripts from one study.

A `condense-project.json` in that folder (or a parent) holds the project's speaker list, so the same people get the same
number keys and colours in every session. The overview summarises every transcript in the folder.
"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_FILE = "condense-project.json"
GENERIC_SPEAKER = ("SPEAKER_", "Speaker ")


def find_project_file(start: Path, levels: int = 4) -> Optional[Path]:
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


def project_speakers_for(transcript_path: Path) -> List[str]:
    f = find_project_file(Path(transcript_path).parent)
    return list(load_project(f)["speakers"]) if f else []


def effective_roster(doc_roster: List[str], project: List[str]) -> List[str]:
    """Project speakers first (stable order across sessions), then this transcript's other speakers."""
    return list(project) + [s for s in doc_roster if s not in project]


def transcripts_under(folder: Path) -> List[Path]:
    from condense.core.transcript import TranscriptDocument
    return [p for p in sorted(Path(folder).rglob(f"*{TranscriptDocument.SUFFIX}")) if "_backups" not in p.parts]


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
    }


def overview(folder: Path) -> List[Dict]:
    rows = []
    for p in transcripts_under(folder):
        try:
            rows.append(overview_row(p))
        except Exception as exc:
            rows.append({"path": p, "name": p.name, "error": str(exc)})
    return rows


def write_overview_csv(rows: List[Dict], target: Path) -> Path:
    cols = ["name", "folder", "length_s", "checked_pct", "uncertain_pct", "speakers", "generic_speakers", "cut_s", "notes",
            "edits", "modified"]
    with open(target, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            if "error" not in r:
                w.writerow([r.get(c, "") for c in cols])
    return target
