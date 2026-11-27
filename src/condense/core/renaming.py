"""Name recordings by a pattern, e.g. "G{group}_Day{day}_{date}_{type}{part}" → G4_Day3_2026.08.10_Session.mp4.

Only on request, never by itself: plan() lists what would change (each row can be edited or left out), apply()
renames, records it in the project's move log, and undo() takes it back.

A recording takes what belongs to it along: its results folder (named like it), the transcript and its backups,
compressed / trimmed / MAXQDA copies and exports named after it, and — for a video merged from parts — the parts
(…_part1, …_part2). Every path the project's transcripts hold to a renamed file is updated (media, the video link, where
an episode came from), so nothing loses its video or its transcript.

Tokens: the levels as numbers ({group}, {day}: the first number in the folder name) and as folder names ({Group},
{Day}); {date} as yyyy.mm.dd (from a date in the folder names, else the date the video was recorded); {type} (Session,
Interview); {n} (the interview number, from the clip's name); {part} (_part1, _part2 … when one session has several
recordings, else nothing); {name} (the current name).
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from condense.core import attributes as attrlib
from condense.core import project as proj
from condense.core.transcript import TranscriptDocument

DEFAULT_PATTERNS = {"session": "G{group}_Day{day}_{date}_{type}{part}", "interview": ""}  # "": the project's clip pattern
TYPE_WORD = {"session": "Session", "interview": "Interview", "other": "Recording"}
_TOKEN = re.compile(r"\{([A-Za-z][A-Za-z0-9_ ]*)\}")
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SKIP_KEYS = ("whisper_result", "edit_history")
DERIVED = ("_MAXQDA", "_compressed", "_trimmed", "_subtitled", "_playback", "_audio", "_16k_mono")


def clean_name(name: str) -> str:
    name = _BAD.sub("-", name).strip()
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"\s{2,}", " ", name)
    return name.strip(" _-.")


def fill(pattern: str, tokens: Dict[str, str]) -> Tuple[str, List[str]]:
    """The name, and the tokens that had no value (the name is then not usable as it is)."""
    missing: List[str] = []

    def one(m):
        key = m.group(1)
        v = tokens.get(key)
        if v is None or v == "":
            if key not in ("part",):
                missing.append(key)
            return ""
        return str(v)
    return clean_name(_TOKEN.sub(one, pattern)), missing


def dotted(date_iso: str) -> str:
    return date_iso.replace("-", ".") if date_iso else ""


def recorded_date(media: Optional[Path]) -> str:
    """The date the video says it was recorded (its creation_time), as yyyy.mm.dd; '' if it doesn't say."""
    if media is None or not Path(media).exists() or not shutil.which("ffprobe"):
        return ""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time", "-of",
                              "default=nw=1:nk=1", str(media)], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", out)
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m and not out.startswith("1970") else ""


# ================================================================== the plan
def _results_dir(item: Dict) -> Optional[Path]:
    t, m = item.get("transcript"), item.get("media")
    if t is not None and m is not None and Path(t).parent == Path(m).parent / Path(m).stem:
        return Path(t).parent
    return None


def _belongs(name: str, base: str) -> Optional[str]:
    """The rest of a file name after base, if the file is named after base ("X.srt", "X_compressed.mp4")."""
    if name == base:
        return ""
    if name.startswith(base) and name[len(base):len(base) + 1] in (".", "_"):
        return name[len(base):]
    return None


def plan(project: Path, patterns: Optional[Dict[str, str]] = None, roles: Iterable[str] = ("session", "interview"),
         probe_dates: bool = True, stop=None) -> List[Dict]:
    """One row per recording (or transcript without one): role, levels, old, new, note, ok, and what it takes along."""
    project = Path(project).resolve()
    settings = proj.project_settings(project)
    st = settings.get("structure")
    patterns = dict(DEFAULT_PATTERNS, **(patterns or {}))
    if not patterns.get("interview") and st is not None and st.name_alts:
        patterns["interview"] = st.name_alts[0]
    roles = set(roles)
    items = [it for it in proj.inventory(project, stop=stop) if it["role"] in roles]
    # copies Condense made of a recording (X_MAXQDA.mp4 next to X.mp4 …) go along with it, not as recordings of their own
    stems = {(str(Path(it["media"]).parent), Path(it["media"]).stem) for it in items if it.get("media") is not None}

    def derived(m: Path) -> bool:
        return any(m.stem.endswith(suf) and (str(m.parent), m.stem[: -len(suf)]) in stems for suf in DERIVED)
    items = [it for it in items if it.get("media") is None or it.get("transcript") is not None or not derived(Path(it["media"]))]
    rows: List[Dict] = []
    merged_parts: Dict[str, Tuple[Path, int]] = {}  # a part's path → (the merged video's transcript, its place)
    docs: Dict[Path, TranscriptDocument] = {}
    for it in items:
        t = it.get("transcript")
        if t is None:
            continue
        try:
            doc = TranscriptDocument.load(Path(t))
        except Exception:
            continue
        docs[Path(t)] = doc
        merge = ((doc.data.get("provenance") or {}).get("processing") or {}).get("merge") or {}
        for k, src in enumerate(merge.get("inputs_in_order") or merge.get("inputs") or []):
            try:
                merged_parts[str(Path(src).resolve())] = (Path(t), k)
            except OSError:
                pass
    for it in items:
        if stop is not None and stop.is_set():
            break
        m, t = it.get("media"), it.get("transcript")
        doc = docs.get(Path(t)) if t is not None else None
        anchor = Path(m) if m is not None else Path(t)
        old = Path(m).stem if m is not None else (doc.base_name if doc is not None else Path(t).name[: -len(TranscriptDocument.SUFFIX)])
        vals = st.values_for(project, anchor) if st is not None else {}
        tokens: Dict[str, str] = {}
        for lv, folder in it.get("levels") or []:
            tokens[lv] = folder
            n = proj_number(folder)
            tokens[lv.lower()] = "" if n is None else str(n)
        for k, v in vals.items():
            tokens.setdefault(k, "" if v is None else str(v))
        try:
            parts = anchor.relative_to(project).parts
        except ValueError:
            parts = anchor.parts
        date = dotted(attrlib.date_in(parts[:-1]))
        date_from = "folders" if date else ""
        if not date and probe_dates and "{date}" in patterns.get(it["role"], ""):
            media_for_date = m or (doc.find_video() if doc is not None else None)
            date = recorded_date(media_for_date)
            date_from = "video" if date else ""
        tokens.update(date=date, type=TYPE_WORD.get(it["role"], "Recording"), name=old)
        if it["role"] == "interview" and st is not None:
            parsed = st.parse_clip_name(old) or {}
            if parsed.get("n") is not None:
                tokens["n"] = str(parsed["n"])
        part_of = merged_parts.get(str(Path(m).resolve())) if m is not None else None
        rows.append({"role": it["role"], "levels": it.get("levels") or [], "media": m, "transcript": t, "old": old,
                     "tokens": tokens, "date_from": date_from, "part_of": part_of,
                     "merged": bool(doc is not None and ((doc.data.get("provenance") or {}).get("processing") or {}).get("merge"))})
    # names, then parts: recordings that would get the same name in one folder are numbered (a merged video keeps the
    # name, the parts it was made from get _part1, _part2 … in the order they were merged)
    for r in rows:
        r["base_new"], r["missing"] = fill(patterns.get(r["role"], "{name}").replace("{part}", ""), r["tokens"])
    by_key: Dict[Tuple, List[Dict]] = {}
    for r in rows:
        if not r["missing"] and "{part}" in patterns.get(r["role"], ""):
            by_key.setdefault((r["role"], str(Path(r["media"] or r["transcript"]).parent), r["base_new"]), []).append(r)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        full = [r for r in group if r["merged"]]
        numbered = [r for r in group if r not in full[:1]]
        numbered.sort(key=lambda r: (r["part_of"][1] if r.get("part_of") else 10 ** 6, proj.natural_key(r["old"])))
        for k, r in enumerate(numbered, start=1):
            r["tokens"]["part"] = f"_part{k}"
    for r in rows:
        r["new"], r["missing"] = fill(patterns.get(r["role"], "{name}"), r["tokens"])
        r.pop("base_new", None)
        _check(r)
    _check_clashes(rows)
    return rows


def proj_number(folder: str) -> Optional[int]:
    from condense.core.structure import folder_number
    return folder_number(folder)


def _check(r: Dict) -> None:
    if r["missing"]:
        r["ok"], r["note"] = False, "no " + ", ".join("{" + k + "}" for k in r["missing"]) + " found: write the name yourself"
    elif r["new"] == r["old"]:
        r["ok"], r["note"] = False, "already named so"
    else:
        r["ok"] = True
        r["note"] = f"date from the {r['date_from']}" if r.get("date_from") else ""


def _check_clashes(rows: List[Dict]) -> None:
    seen: Dict[Tuple[str, str], Dict] = {}
    for r in rows:
        if not r.get("ok"):
            continue
        folder = str(Path(r["media"] or r["transcript"]).parent)
        key = (folder.lower(), r["new"].lower())
        if key in seen:
            r["ok"], r["note"] = False, f"the same name as {seen[key]['old']}"
            continue
        seen[key] = r
        for src, dst in files_of(r):
            if dst.exists() and not _same_file(src, dst):
                r["ok"], r["note"] = False, f"{dst.name} already exists"
                break


def set_name(r: Dict, new: str) -> Dict:
    """A name written by hand for one row."""
    r["new"] = clean_name(new)
    r["missing"] = []
    _check(r)
    return r


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def files_of(r: Dict) -> List[Tuple[Path, Path]]:
    """(from, to) for everything the row renames, in an order that works: inside the results folder first, then the
    folder, then the recording and the files named after it next to it."""
    old, new = r["old"], r["new"]
    out: List[Tuple[Path, Path]] = []
    m, t = r.get("media"), r.get("transcript")
    rdir = _results_dir(r)
    tdir = Path(t).parent if t is not None else None
    if t is not None:
        base = TranscriptDocument.load(Path(t)).base_name if not r.get("_base") else r["_base"]
        r["_base"] = base
        for f in sorted(tdir.iterdir()) if tdir.is_dir() else []:
            rest = _belongs(f.name, base)
            if rest is not None and f.is_file():
                out.append((f, tdir / f"{new}{rest}"))
        bdir = tdir / "_backups"
        if bdir.is_dir():
            for f in sorted(bdir.iterdir()):
                rest = _belongs(f.name, base)
                if rest is not None:
                    out.append((f, bdir / f"{new}{rest}"))
    if rdir is not None:
        out.append((rdir, rdir.parent / new))
    if m is not None:
        here = Path(m).parent
        for f in sorted(here.iterdir()):
            if f.is_file() and f != Path(m) and _belongs(f.name, old) is not None and f.parent != tdir:
                rest = _belongs(f.name, old)
                if rest.startswith("_part"):
                    continue  # a part of this recording has its own row
                out.append((f, here / f"{new}{rest}"))
        out.append((Path(m), here / f"{new}{Path(m).suffix}"))
    seen, uniq = set(), []
    for a, b in out:
        if a not in seen and a != b:
            seen.add(a)
            uniq.append((a, b))
    return uniq


# ================================================================== doing it
def real(p) -> str:
    """One spelling for a path: links and short names resolved (/var → /private/var on macOS, RUNNER~1 on Windows)."""
    return os.path.realpath(os.path.normpath(str(p)))


def _map(path: str, moves: List[Tuple[str, str]]) -> str:
    """Where path (a real path) is after the moves, in the order they were made (a file inside a folder moved later
    follows it). Compared as the system compares names (case-insensitive on Windows)."""
    for a, b in moves:
        na, nc = os.path.normcase(a), os.path.normcase(path)
        if nc == na:
            path = b
        elif nc.startswith(na + os.sep):
            path = b + path[len(a):]
    return path


def _relink_doc(path: Path, old_dir: Path, moves: List[Tuple[str, str]], moved_itself: bool) -> bool:
    """Rewrite the paths a transcript holds to renamed files (and its base_name, if it was renamed itself).
    old_dir: where the transcript lay before (its relative paths start there)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return False
    new_dir = Path(real(Path(path).parent))
    changed = False

    def fix(s: str) -> Optional[str]:
        if not s or len(s) > 4096 or ("/" not in s and "\\" not in s and "." not in s):
            return None
        p = Path(s)
        rel = not p.is_absolute()
        try:
            abs_path = real(old_dir / p if rel else s)
        except Exception:
            return None
        target = _map(abs_path, moves)
        if target == abs_path and not (rel and moved_itself):
            return None
        if rel:
            return os.path.relpath(target, new_dir)
        # in the spelling it was written in (e.g. /var… rather than /private/var…): only what changed changes
        return os.path.normpath(os.path.join(os.path.dirname(s), os.path.relpath(target, os.path.dirname(abs_path))))

    def walk(node):
        nonlocal changed
        items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for k, v in list(items):
            if isinstance(node, dict) and k in SKIP_KEYS:
                continue
            if isinstance(v, str):
                nv = fix(v)
                if nv is not None and nv != v:
                    node[k] = nv
                    changed = True
            elif isinstance(v, (dict, list)):
                walk(v)

    walk(data)
    vl = data.get("video_link")
    if isinstance(vl, dict) and isinstance(data.get("media"), dict) and data["media"].get("video"):
        name = Path(str(data["media"]["video"])).name
        if vl.get("file") != name:
            vl["file"] = name
            changed = True
    if moved_itself and str(path).endswith(TranscriptDocument.SUFFIX):
        base = Path(path).name[: -len(TranscriptDocument.SUFFIX)]
        if data.get("base_name") != base:
            data["base_name"] = base
            changed = True
    if changed:
        from condense.core.transcript import dumps_document
        Path(path).write_text(dumps_document(data), encoding="utf-8")
    return changed


def _move(a: Path, b: Path) -> None:
    if _same_file(a, b) and str(a) != str(b):  # only the case changes (macOS, Windows): through a temporary name
        tmp = a.with_name(a.name + ".condense-renaming")
        os.rename(a, tmp)
        os.rename(tmp, b)
    else:
        os.rename(a, b)


def apply(project: Path, rows: List[Dict], progress=None) -> Dict:
    """Rename the chosen rows (ok, and not left out with use=False). Returns {"renamed", "files", "relinked"}."""
    project = Path(project).resolve()
    chosen = [r for r in rows if r.get("ok") and r.get("use", True)]
    done: List[Tuple[str, str]] = []
    for n, r in enumerate(chosen, start=1):
        if progress:
            progress(f"Renaming {n}/{len(chosen)}: {r['old']} → {r['new']}")
        for a, b in files_of(r):
            if not a.exists() or (b.exists() and not _same_file(a, b)):
                continue
            ra, rb = real(a), real(b)
            _move(a, b)
            done.append((ra, rb))
    if progress:
        progress("Updating the transcripts' links…")
    relinked = relink(project, done)
    if done:
        record = project / proj_moves_file()
        history = json.loads(record.read_text(encoding="utf-8")) if record.exists() else []
        history.append({"time": datetime.now().isoformat(timespec="seconds"), "kind": "rename",
                        "moves": [{"from": a, "to": b} for a, b in done]})
        record.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"renamed": len(chosen), "files": len(done), "relinked": relinked, "moves": done}


def proj_moves_file() -> str:
    from condense.core.structure import MOVES_FILE
    return MOVES_FILE


def relink(project: Path, moves: List[Tuple[str, str]]) -> int:
    """Update every transcript of the project that was moved or names a moved file (cheap check first)."""
    if not moves:
        return 0
    back = [(b, a) for a, b in reversed(moves)]
    names = {Path(a).name for a, _b in moves}
    n = 0
    for p in proj.transcripts_under(project, skip=proj.project_settings(project)["skip"]):
        here = real(p)
        before = _map(here, back)
        moved_itself = os.path.normcase(before) != os.path.normcase(here)
        if not moved_itself:
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if not any(nm in text for nm in names):
                continue
        if _relink_doc(p, Path(before).parent, moves, moved_itself):
            n += 1
    return n


def last_was_rename(project: Path) -> bool:
    record = Path(project) / proj_moves_file()
    try:
        history = json.loads(record.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(history) and history[-1].get("kind") == "rename"


def undo(project: Path) -> List[Tuple[str, str]]:
    """Take the last renaming back: the files, then the paths in the transcripts. Returns the moves made (from, to)."""
    from condense.core.structure import undo_last_moves
    project = Path(project).resolve()
    if not last_was_rename(project):
        return []
    undone = undo_last_moves(project)  # in the order they were taken back
    moves = [(real(mv["to"]), real(mv["from"])) for mv in undone]
    relink(project, moves)
    return moves


def use_for_new_clips(project: Path, pattern: str) -> bool:
    """New interview clips are named by pattern too; the names used so far stay recognised (alternatives)."""
    data = proj.load_project(Path(project) / proj.PROJECT_FILE)
    st = dict(data.get("structure") or {})
    segs = [x for x in str(st.get("interviews", "")).strip().strip("/").split("/") if x]
    if not segs or not pattern.strip() or "{n}" not in pattern:
        return False
    alts = [a.strip() for a in segs[-1].split("|") if a.strip()]
    alts = [pattern.strip()] + [a for a in alts if a != pattern.strip()]
    st["interviews"] = "/".join(segs[:-1] + [" | ".join(alts)])
    proj.update_project(project, structure=st)
    return True
