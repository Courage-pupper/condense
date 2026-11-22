"""Coding: a project's codebook and the codings in its transcripts.

The codebook (condense-codebook.json in the study folder) is a tree of codes. It is meant to grow and change while you
code: codes are added on the fly, renamed, moved, merged; a code that turned out too big is split by moving some of its
codings to a new code. Each code has a colour, a definition, when to use it and when not, an example and a memo.

A coding lives in the transcript it belongs to (so backups, undo and the edit log cover it):
    {"id", "code", "start_wid", "end_wid", "start", "end", "memo", "created"}
It points at its first and last word by their permanent ids (and keeps their times as a fallback). A coding of a moment
without speech — someone working with the materials, a gesture — has no words: only start and end ("kind": "time"),
usually with a memo describing what happens.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

CODEBOOK_FILE = "condense-codebook.json"
COLORS = ["#f87171", "#fb923c", "#fbbf24", "#a3e635", "#34d399", "#22d3ee", "#60a5fa", "#a78bfa", "#f472b6", "#e879f9",
          "#94a3b8", "#fde047"]
FIELDS = ("name", "parent", "color", "definition", "use_when", "not_when", "example", "memo")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Codebook:
    def __init__(self, path: Path, data: Optional[Dict] = None):
        self.path = Path(path)
        self.data = data if data is not None else {"codes": [], "next": 1, "log": []}
        self.data.setdefault("codes", [])
        self.data.setdefault("next", 1 + len(self.data["codes"]))
        self.data.setdefault("log", [])

    @classmethod
    def for_project(cls, project: Path) -> "Codebook":
        path = Path(project) / CODEBOOK_FILE
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return cls(path, data)

    def save(self) -> None:
        self.data["updated"] = now()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _log(self, action: str, **details):
        self.data["log"].append({"at": now(), "action": action, **details})

    # ----- reading -----
    @property
    def codes(self) -> List[Dict]:
        return self.data["codes"]

    def get(self, code_id: str) -> Optional[Dict]:
        return next((c for c in self.codes if c["id"] == code_id), None)

    def children(self, parent: Optional[str]) -> List[Dict]:
        return [c for c in self.codes if c.get("parent") == parent]

    def descendants(self, code_id: str) -> List[str]:
        out, todo = [], [code_id]
        while todo:
            cur = todo.pop()
            for c in self.children(cur):
                out.append(c["id"])
                todo.append(c["id"])
        return out

    def path_name(self, code_id: str) -> str:
        names, cur = [], self.get(code_id)
        while cur is not None:
            names.append(cur["name"])
            cur = self.get(cur["parent"]) if cur.get("parent") else None
        return " ▸ ".join(reversed(names))

    def ordered(self) -> List[Tuple[int, Dict]]:
        """(depth, code) in tree order."""
        out: List[Tuple[int, Dict]] = []

        def walk(parent, depth):
            for c in self.children(parent):
                out.append((depth, c))
                walk(c["id"], depth + 1)
        walk(None, 0)
        return out

    def find(self, name: str, parent: Optional[str] = "*") -> Optional[Dict]:
        name = " ".join(name.split()).lower()
        return next((c for c in self.codes if c["name"].lower() == name and (parent == "*" or c.get("parent") == parent)), None)

    # ----- changing -----
    def add(self, name: str, parent: Optional[str] = None, color: Optional[str] = None, **fields) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("A code needs a name.")
        if parent is not None and self.get(parent) is None:
            raise ValueError("That parent code does not exist.")
        if self.find(name, parent) is not None:
            raise ValueError(f"There is already a code “{name}” here.")
        code_id = f"c{self.data['next']}"
        self.data["next"] += 1
        if color is None:
            color = self.get(parent)["color"] if parent else COLORS[len(self.children(None)) % len(COLORS)]
        code = {"id": code_id, "name": name, "parent": parent, "color": color, "created": now()}
        code.update({k: v for k, v in fields.items() if k in FIELDS and v})
        self.codes.append(code)
        self._log("add", code=code_id, name=name, parent=parent)
        return code_id

    def update(self, code_id: str, **fields) -> bool:
        code = self.get(code_id)
        if code is None:
            return False
        if "name" in fields:
            name = " ".join((fields["name"] or "").split())
            if not name:
                raise ValueError("A code needs a name.")
            other = self.find(name, code.get("parent"))
            if other is not None and other["id"] != code_id:
                raise ValueError(f"There is already a code “{name}” here.")
            fields["name"] = name
        if "parent" in fields:
            self.move(code_id, fields.pop("parent"))
        changed = {k: v for k, v in fields.items() if k in FIELDS and code.get(k) != v}
        code.update(changed)
        if changed:
            self._log("update", code=code_id, fields=sorted(changed))
        return bool(changed)

    def move(self, code_id: str, new_parent: Optional[str], before: Optional[str] = None) -> bool:
        """Under another code (or to the top level), optionally before a sibling. Not into its own branch."""
        code = self.get(code_id)
        if code is None:
            return False
        if new_parent == code_id or (new_parent is not None and new_parent in self.descendants(code_id)):
            raise ValueError("A code can't be moved into itself.")
        code["parent"] = new_parent
        self.codes.remove(code)
        idx = next((i for i, c in enumerate(self.codes) if c["id"] == before), len(self.codes))
        self.codes.insert(idx, code)
        self._log("move", code=code_id, parent=new_parent)
        return True

    def remove(self, code_id: str) -> List[str]:
        """Remove a code and its subcodes from the codebook. Returns the removed ids (their codings must be dealt
        with first: see Project.delete_code)."""
        ids = [code_id] + self.descendants(code_id)
        self.data["codes"] = [c for c in self.codes if c["id"] not in ids]
        self._log("remove", codes=ids)
        return ids


# ----- codings in a transcript -----
def codings(doc) -> List[Dict]:
    return doc.data.setdefault("codings", [])


def add_coding(doc, code: str, start: float, end: float, start_wid: Optional[str] = None, end_wid: Optional[str] = None,
               memo: str = "") -> str:
    if end < start:
        start, end = end, start
    c = {"id": "k" + uuid.uuid4().hex[:10], "code": code, "start": round(float(start), 3), "end": round(float(end), 3),
         "created": now()}
    if start_wid and end_wid:
        c["start_wid"], c["end_wid"] = start_wid, end_wid
    else:
        c["kind"] = "time"  # a moment without speech
    memo = " ".join((memo or "").split())
    if memo:
        c["memo"] = memo
    # the same code on exactly the same stretch twice is one coding
    for other in codings(doc):
        if other["code"] == code and other.get("start_wid") == c.get("start_wid") and other.get("end_wid") == c.get("end_wid") \
                and abs(other["start"] - c["start"]) < 1e-3 and abs(other["end"] - c["end"]) < 1e-3:
            return other["id"]
    codings(doc).append(c)
    doc.log_edit("code", coding=c["id"], code=code, start=c["start"], end=c["end"], kind=c.get("kind", "words"))
    return c["id"]


def get_coding(doc, coding_id: str) -> Optional[Dict]:
    return next((c for c in codings(doc) if c["id"] == coding_id), None)


def update_coding(doc, coding_id: str, **fields) -> bool:
    c = get_coding(doc, coding_id)
    if c is None:
        return False
    changed = {}
    for k in ("code", "memo"):
        if k in fields:
            v = " ".join((fields[k] or "").split()) if k == "memo" else fields[k]
            if c.get(k, "") != v:
                changed[k] = v
    for k, v in changed.items():
        if k == "memo" and not v:
            c.pop("memo", None)
        else:
            c[k] = v
    if changed:
        doc.log_edit("update_coding", coding=coding_id, **changed)
    return bool(changed)


def remove_coding(doc, coding_id: str) -> bool:
    before = len(codings(doc))
    doc.data["codings"] = [c for c in codings(doc) if c["id"] != coding_id]
    if len(codings(doc)) < before:
        doc.log_edit("uncode", coding=coding_id)
        return True
    return False


def resolve(doc) -> List[Dict]:
    """Every coding with where it is now: "words" ((seg, word), (seg, word)) or None for time codings and codings
    whose words are all gone ("orphan": True). Sorted by time."""
    pos = doc.word_positions()
    out = []
    for c in codings(doc):
        r = dict(c)
        if c.get("kind") == "time":
            r["words"] = None
        else:
            span = doc.resolve_span(c.get("start_wid"), c.get("end_wid"), c["start"], c["end"], pos)
            r["words"] = span
            r["orphan"] = span is None
            if span is not None:  # the times follow the words as they are now
                (si, wi), (sj, wj) = span
                r["start"] = float(doc.segments[si]["words"][wi]["start"])
                r["end"] = float(doc.segments[sj]["words"][wj]["end"])
        out.append(r)
    return sorted(out, key=lambda r: (r["start"], r["end"]))


def coded_text(doc, r: Dict) -> str:
    if not r.get("words"):
        return ""
    (si, wi), (sj, wj) = r["words"]
    words = []
    for i in range(si, sj + 1):
        ws = doc.segments[i].get("words") or []
        lo = wi if i == si else 0
        hi = wj if i == sj else len(ws) - 1
        words += [str(w.get("word", "")).strip() for w in ws[lo:hi + 1]]
    return " ".join(w for w in words if w)


def speakers_of(doc, r: Dict) -> List[str]:
    if r.get("words"):
        (si, _), (sj, _) = r["words"]
        idx = range(si, sj + 1)
    else:
        idx = [i for i, s in enumerate(doc.segments) if float(s.get("end", 0)) > r["start"] and float(s.get("start", 0)) < r["end"]]
    out = []
    for i in idx:
        spk = doc.segments[i].get("speaker")
        if spk and spk not in out:
            out.append(spk)
    return out


# ----- across the project: counts, merging, recoding -----
def project_transcripts(project: Path) -> List[Path]:
    from condense.core import project as proj
    return proj.transcripts_under(project, skip=proj.project_settings(project)["skip"])


def counts(project: Path, paths: Optional[Iterable[Path]] = None) -> Dict[str, int]:
    from condense.core.transcript import TranscriptDocument
    out: Dict[str, int] = {}
    for p in paths if paths is not None else project_transcripts(project):
        try:
            for c in json.loads(Path(p).read_text(encoding="utf-8")).get("codings") or []:
                out[c["code"]] = out.get(c["code"], 0) + 1
        except Exception:
            continue
    return out


def recode_everywhere(project: Path, mapping: Dict[str, str], save_doc=None) -> int:
    """Codings of code a -> code b in every transcript of the project (merging codes). `save_doc(doc, message)` saves
    a transcript (the GUI passes one that keeps a backup and an undo step). Returns the number of codings changed."""
    from condense.core.transcript import TranscriptDocument
    total = 0
    for p in project_transcripts(project):
        try:
            raw = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not any(c.get("code") in mapping for c in raw.get("codings") or []):
            continue
        doc = TranscriptDocument.load(p)
        n = 0
        for c in codings(doc):
            if c["code"] in mapping:
                c["code"] = mapping[c["code"]]
                n += 1
        # identical codings of the merged codes become one
        seen, keep = set(), []
        for c in codings(doc):
            key = (c["code"], c.get("start_wid"), c.get("end_wid"), round(c["start"], 2), round(c["end"], 2))
            if key not in seen:
                seen.add(key)
                keep.append(c)
        doc.data["codings"] = keep
        doc.log_edit("recode", mapping=mapping, codings=n)
        (save_doc or (lambda d, m: d.save(backup=True)))(doc, f"Codes merged: {n} coding(s)")
        total += n
    return total
