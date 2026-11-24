"""A study's folder structure, written as templates — like a file-naming pattern in a reference manager.

    sessions:    {Day}/{Group}/Session Recordings
    interviews:  {Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}

{Day}, {Group} … are the levels: any folder at that place (e.g. "Day 1 2026-08-10"). {day}, {group} … are their numbers
(the first number in the folder name: 1). {n} numbers the interviews of the last level (a group) across all the others
(all days). The last part of the interviews template is the clip's file name without extension.

Condense only reads the structure, and uses it for what it writes itself (exported clips). Tidying files from unsorted
folders is a plan you look at first; nothing is moved unless you confirm it, and every move is recorded and can be undone.
"""
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PRESETS: Dict[str, Dict[str, str]] = {
    "Workshops: Day ▸ Group": {"sessions": "{Day}/{Group}/Session Recordings",
                               "interviews": "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}"},
    "Interview study: Participant ▸ Session": {"sessions": "{Participant}/{Session}",
                                               "interviews": "{Participant}/{Session}/Interviews/P{participant}S{session}_I{n}"},
    "One folder": {"sessions": "", "interviews": "Interviews/I{n}"},
}
CUSTOM = "Custom"
MOVES_FILE = "condense-moves.json"
_PH = re.compile(r"\{([A-Za-z][A-Za-z0-9_ ]*)\}")


def folder_number(name: str) -> Optional[int]:
    m = re.search(r"\d+", name)
    return int(m.group()) if m else None


def _segments(template: str) -> List[str]:
    return [s for s in template.strip().strip("/").split("/") if s]


class Structure:
    def __init__(self, sessions: str = "", interviews: str = ""):
        self.sessions = sessions.strip().strip("/")
        self.interviews = interviews.strip().strip("/")
        segs = _segments(self.interviews)
        # the clip name may have alternatives, "G{group}D{day}_I{n} | D{day}G{group}_I{n}": all are recognised, the first
        # is used for new clips
        self.name_alts: List[str] = [a.strip() for a in segs[-1].split("|") if a.strip()] if segs else []
        self.levels: List[str] = []
        for seg in _segments(self.sessions) + _segments(self.interviews)[:-1]:
            m = re.fullmatch(r"\{([A-Z][A-Za-z0-9_ ]*)\}", seg)
            if m and m.group(1) not in self.levels:
                self.levels.append(m.group(1))

    # ----- describing -----
    def problems(self) -> List[str]:
        out = []
        for name, t in (("Session folders", self.sessions), ("Interview clips", self.interviews)):
            for seg in _segments(t)[:-1] if name == "Interview clips" else _segments(t):
                if _PH.search(seg) and not re.fullmatch(r"\{[A-Z][A-Za-z0-9_ ]*\}", seg):
                    out.append(f"{name}: a level like {{Day}} must be a whole folder name ({seg}).")
        for alt in self.name_alts:
            if "{n}" not in alt:
                out.append(f"Interview clips: the name {alt} needs {{n}}, the interview number.")
        for ph in _PH.findall(" ".join(self.name_alts)):
            if ph != "n" and ph.capitalize() not in self.levels and ph not in self.levels:
                out.append(f"Interview clips: {{{ph}}} is not a level of the folders.")
        return out

    def example(self, role: str = "interviews") -> str:
        vals = {lv: f"{lv} {i + 1}" for i, lv in enumerate(self.levels)}
        vals.update({lv.lower(): str(i + 1) for i, lv in enumerate(self.levels)})
        if role == "sessions":
            return "/".join(_fill(seg, vals) for seg in _segments(self.sessions)) + "/recording.mp4"
        segs = _segments(self.interviews)
        return "/".join(_fill(seg, vals) for seg in segs[:-1]) + "/" + _fill(self.name_alts[0], dict(vals, n="4")) + ".mp4"

    # ----- reading paths -----
    def values_for(self, project: Path, path: Path) -> Dict:
        """{"Day": "Day 1 2026-08-10", "day": 1, "Group": "Group 1", "group": 1} for a file below those folders."""
        try:
            parts = Path(path).resolve().relative_to(Path(project).resolve()).parts
        except ValueError:
            return {}
        vals: Dict = {}
        for lv, name in zip(self.levels, parts[:-1]):
            vals[lv] = name
            vals[lv.lower()] = folder_number(name)
        return vals if len([k for k in vals if k in self.levels]) == len(self.levels) else {}

    def level_folder(self, project: Path, path: Path) -> Optional[Path]:
        vals = self.values_for(project, path)
        return Path(project).resolve().joinpath(*[vals[lv] for lv in self.levels]) if vals else None

    def role_of(self, project: Path, folder: Path) -> Optional[str]:
        """'sessions' or 'interviews' if folder is that folder of some level folders, else None."""
        try:
            parts = Path(folder).resolve().relative_to(Path(project).resolve()).parts
        except ValueError:
            return None
        for role, segs in (("sessions", _segments(self.sessions)), ("interviews", _segments(self.interviews)[:-1])):
            if len(parts) != len(segs):
                continue
            if all(re.fullmatch(r"\{[A-Z][A-Za-z0-9_ ]*\}", sg) or sg.lower() == pt.lower() for sg, pt in zip(segs, parts)):
                return role
        return None

    def role_folder(self, project: Path, role: str, vals: Dict) -> Optional[Path]:
        segs = _segments(self.sessions) if role == "sessions" else _segments(self.interviews)[:-1]
        if not all(lv in vals for lv in self.levels):
            return None
        return Path(project).resolve().joinpath(*[_fill(seg, vals) for seg in segs])

    def name_regexes(self) -> List[re.Pattern]:
        out = []
        for last in self.name_alts:
            pat, pos = "", 0
            for m in _PH.finditer(last):
                pat += re.escape(last[pos:m.start()])
                key = m.group(1)
                pat += f"(?P<{_group_name(key)}>\\d+)" if key == "n" or key.islower() else f"(?P<{_group_name(key)}>.+?)"
                pos = m.end()
            pat += re.escape(last[pos:])
            out.append(re.compile(f"^{pat}$", re.IGNORECASE))
        return out

    def parse_clip_name(self, stem: str) -> Optional[Dict]:
        for rx in self.name_regexes():
            m = rx.match(stem)
            if m:
                return {_ungroup(k): (int(v) if v.isdigit() else v) for k, v in m.groupdict().items()}
        return None

    def clip_name(self, vals: Dict, n: int) -> str:
        return _fill(self.name_alts[0], dict(vals, n=str(n)))

    def for_names(self, pattern: str) -> "Structure":
        """The same folders with another name pattern (e.g. for Phase episodes: G{group}D{day}_P{n})."""
        return Structure(self.sessions, "/".join(_segments(self.interviews)[:-1] + [pattern.strip()]))

    def name_with_number(self, vals: Dict, n: int) -> Tuple[str, int, int]:
        """The name for number n, and where the number sits in it (to select it, so typing replaces just the number)."""
        alt = self.name_alts[0]
        i = alt.index("{n}") if "{n}" in alt else len(alt)
        start = len(_fill(alt[:i], vals))
        return self.clip_name(vals, n), start, start + len(str(n))

    def complete_name(self, vals: Dict, label: str) -> Optional[str]:
        """A short label made whole: "14", "I14" or "_I14" → "G4D3_I14" (in Group 4, Day 3). None if the label is
        already a full name, or doesn't look like the end of one."""
        label = (label or "").strip()
        if not label or not self.name_alts or (self.levels and not vals) or self.parse_clip_name(label):
            return None
        m = re.search(r"(\d+)$", label)
        if not m:
            return None
        full = self.clip_name(vals, int(m.group(1)))
        if label.isdigit() or full.lower().endswith(label.lower()):
            return full if self.parse_clip_name(full) else None
        return None

    # ----- numbering -----
    def used_numbers(self, project: Path, group_value: Dict, extra_labels: List[str] = ()) -> List[int]:
        """Interview numbers already used by this group (the last level) on any day: clip names anywhere in the project
        (also unsorted folders), and Interview episode labels."""
        if not self.levels:
            key = None
        else:
            key = self.levels[-1].lower()
        want = group_value.get(key) if key else None
        nums: List[int] = []
        for root, dirs, files in os.walk(project, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.lower().startswith("consent")]
            for f in files:
                if f.startswith("."):
                    continue
                parsed = self.parse_clip_name(Path(f).stem)
                if parsed and "n" in parsed and (key is None or parsed.get(key) == want):
                    nums.append(int(parsed["n"]))
        for label in extra_labels:
            parsed = self.parse_clip_name(label)
            if parsed and "n" in parsed and (key is None or parsed.get(key) == want):
                nums.append(int(parsed["n"]))
        return sorted(set(nums))

    def next_clip_name(self, project: Path, path: Path, extra_labels: List[str] = ()) -> Optional[str]:
        vals = self.values_for(project, path)
        if not self.interviews or (self.levels and not vals):
            return None
        used = self.used_numbers(project, vals, extra_labels)
        return self.clip_name(vals, (max(used) + 1) if used else 1)

    # ----- tidy: a plan only -----
    def tidy_plan(self, project: Path, unsorted_patterns=("*unsorted*",)) -> List[Dict]:
        """Files in unsorted folders whose names say where they belong. Each item: src, dst, reason, ok (movable)."""
        import fnmatch
        plan: List[Dict] = []
        project = Path(project).resolve()
        for root, dirs, files in os.walk(project, onerror=lambda e: None):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.lower().startswith("consent"))
            here = Path(root)
            if not any(fnmatch.fnmatch(here.name.lower(), p.lower()) for p in unsorted_patterns):
                continue
            dirs[:] = []  # look only at what lies directly in the unsorted folder
            for f in sorted(files):
                if f.startswith("."):
                    continue
                src = here / f
                parsed = self.parse_clip_name(src.stem)
                if not parsed:
                    plan.append({"src": src, "dst": None, "ok": False, "reason": "the name does not follow the interview pattern"})
                    continue
                dst_dir = self._folder_from_numbers(project, parsed)
                if dst_dir is None:
                    plan.append({"src": src, "dst": None, "ok": False,
                                 "reason": "no folder for " + ", ".join(f"{lv} {parsed.get(lv.lower())}" for lv in self.levels)})
                    continue
                dst = dst_dir / f
                if dst.exists():
                    same = dst.stat().st_size == src.stat().st_size
                    plan.append({"src": src, "dst": dst, "ok": False,
                                 "reason": "already there (same size — a duplicate?)" if same else "a different file with this name is already there"})
                    continue
                item = {"src": src, "dst": dst, "ok": True,
                        "reason": "name says " + ", ".join(f"{lv} {parsed.get(lv.lower())}" for lv in self.levels if parsed.get(lv.lower()) is not None)
                        + ("" if dst_dir.is_dir() else f" (creates the folder {dst_dir.name})")}
                companion = here / src.stem  # e.g. the transcript folder made next to the clip
                if companion.is_dir() and not (dst_dir / src.stem).exists():
                    item["companion"] = companion
                plan.append(item)
        return plan

    def _folder_from_numbers(self, project: Path, parsed: Dict) -> Optional[Path]:
        """The interviews folder of the level folders whose numbers are in the clip's name (Day 2, Group 1 …)."""
        vals: Dict = {}
        base = project
        for lv in self.levels:
            num = parsed.get(lv.lower())
            if num is None:
                return None
            match = [d for d in sorted(base.iterdir()) if d.is_dir() and not d.name.lower().endswith("unsorted")
                     and d.name.lower().startswith(lv.lower()) and folder_number(d.name) == num] if base.is_dir() else []
            if len(match) != 1:
                return None
            vals[lv], vals[lv.lower()] = match[0].name, num
            base = match[0]
        return self.role_folder(project, "interviews", vals)


def _group_name(key: str) -> str:
    return "g_" + re.sub(r"\W", "_", key)


def _ungroup(name: str) -> str:
    return name[2:]


def _fill(template: str, vals: Dict) -> str:
    return _PH.sub(lambda m: str(vals.get(m.group(1), m.group(0))), template)


def suggest(project: Path, levels: List[str]) -> Dict[str, str]:
    """A structure for an existing study: its levels, and the folders inside the deepest level that look like session
    recordings and interviews. Clip names follow the existing clips if they share a pattern, else e.g. G{group}D{day}_I{n}."""
    if not levels:
        return {"sessions": "", "interviews": "Interviews/I{n}"}
    lv_path = "/".join(f"{{{lv}}}" for lv in levels)
    inner: Dict[str, int] = {}
    base = [Path(project)]
    for lv in levels:
        base = [d for b in base if b.is_dir() for d in b.iterdir() if d.is_dir() and d.name.lower().startswith(lv.lower())]
    for b in base:
        for d in b.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                inner[d.name] = inner.get(d.name, 0) + 1
    sessions_dir = next((n for n, _ in sorted(inner.items(), key=lambda x: -x[1]) if "session" in n.lower() or "recording" in n.lower()
                         and "screen" not in n.lower()), None)
    interviews_dir = next((n for n, _ in sorted(inner.items(), key=lambda x: -x[1]) if "interview" in n.lower()), None) or "Interviews"
    initials = "".join(f"{lv[0].upper()}{{{lv.lower()}}}" for lv in reversed(levels))
    folder_part = f"{lv_path}/{interviews_dir}"
    learned = learn_name_patterns(Path(project), Structure("", f"{folder_part}/{initials}_I{{n}}"))
    return {"sessions": lv_path + (f"/{sessions_dir}" if sessions_dir else ""),
            "interviews": f"{folder_part}/{' | '.join(learned) if learned else initials + '_I{n}'}"}


def name_pattern(stem: str, vals: Dict, levels: List[str]) -> Optional[str]:
    """'D1G4_I3' in Day 1 / Group 4 -> 'D{day}G{group}_I{n}': numbers become the level they equal (the letter before a
    number decides when two levels have the same number); the last unexplained number is {n}."""
    nums = list(re.finditer(r"\d+", stem))
    if not nums:
        return None
    used, out, pos, n_done = set(), "", 0, False
    for i, m in enumerate(nums):
        value, before = int(m.group()), stem[:m.start()]
        letter = before[-1].upper() if before and before[-1].isalpha() else ""
        choice = None
        for lv in levels:
            if lv.lower() in used or vals.get(lv.lower()) != value:
                continue
            if letter and lv[0].upper() == letter:
                choice = lv.lower()
                break
            choice = choice or lv.lower()
        if choice and i < len(nums) - 1:
            used.add(choice)
            ph = choice
        elif i == len(nums) - 1 and not n_done:
            ph, n_done = "n", True
        else:
            return None
        out += stem[pos:m.start()] + "{" + ph + "}"
        pos = m.end()
    out += stem[pos:]
    return out if n_done and len(used) == len(levels) else None


def learn_name_patterns(project: Path, structure: "Structure", video_exts=(".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts")) -> List[str]:
    """The naming patterns of the clips already in the interview folders, most used first."""
    counts: Dict[str, int] = {}
    project = Path(project)
    for root, dirs, files in os.walk(project, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.lower().startswith("consent") and "unsorted" not in d.lower()]
        if structure.role_of(project, Path(root)) != "interviews":
            continue
        vals = structure.values_for(project, Path(root) / "x")
        for f in files:
            if f.startswith(".") or Path(f).suffix.lower() not in video_exts:
                continue
            pat = name_pattern(Path(f).stem, vals, structure.levels)
            if pat and pat.endswith("{n}"):  # "G1D1_I1_new" is a one-off, not a naming scheme
                counts[pat] = counts.get(pat, 0) + 1
    return [p for p, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def from_project(data: Dict) -> Optional[Structure]:
    s = data.get("structure")
    if not s:
        return None
    return Structure(s.get("sessions", ""), s.get("interviews", ""))


# ----- moving, only when confirmed; recorded so it can be undone -----
def apply_moves(project: Path, items: List[Dict]) -> List[Dict]:
    done = []
    for it in items:
        if not it.get("ok"):
            continue
        pairs = [(Path(it["src"]), Path(it["dst"]))]
        if it.get("companion"):
            pairs.append((Path(it["companion"]), Path(it["dst"]).parent / Path(it["companion"]).name))
        for src, dst in pairs:
            if dst.exists() or not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append({"from": str(src), "to": str(dst)})
    if done:
        record = Path(project) / MOVES_FILE
        history = json.loads(record.read_text(encoding="utf-8")) if record.exists() else []
        history.append({"time": datetime.now().isoformat(timespec="seconds"), "moves": done})
        record.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return done


def undo_last_moves(project: Path) -> List[Dict]:
    record = Path(project) / MOVES_FILE
    if not record.exists():
        return []
    history = json.loads(record.read_text(encoding="utf-8"))
    if not history:
        return []
    last = history.pop()
    undone = []
    for mv in reversed(last["moves"]):
        src, dst = Path(mv["to"]), Path(mv["from"])
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            undone.append(mv)
    record.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return undone
