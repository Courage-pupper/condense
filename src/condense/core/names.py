"""People's names in a study: renaming someone throughout, and pseudonyms for everything that leaves the lab.

The project keeps one list (condense-project.json "names"):
    [{"name": "Robin", "pseudonym": "Child 3"}, …]
*Rename throughout* changes a name for good — speaker labels, the project's and groups' speaker lists, and the name
where it is said in the text. *Pseudonyms* change nothing: exports made "with pseudonyms" get the pseudonyms instead.

In the text a name is replaced as a whole word ("Robin," and "Robin's" yes, "Robinson" no), and inside each word, so
the word keeps its timing, its id and therefore its codings.
"""
import copy
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

GENERIC = ("SPEAKER_", "Speaker ")


def name_rx(name: str) -> "re.Pattern":
    return re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])")


def clean(name) -> str:
    return " ".join(str(name or "").split())


def name_list(project_data: Dict) -> List[Dict]:
    out: List[Dict] = []
    for d in project_data.get("names") or []:
        n = clean(d.get("name"))
        if n and all(o["name"] != n for o in out):
            out.append({"name": n, "pseudonym": clean(d.get("pseudonym"))})
    return out


def mapping(entries: Iterable[Dict]) -> Dict[str, str]:
    """{name: pseudonym} for the entries that have one."""
    return {e["name"]: e["pseudonym"] for e in entries if e.get("name") and e.get("pseudonym") and e["pseudonym"] != e["name"]}


def merge_people(entries: List[Dict], people: Iterable[str]) -> List[Dict]:
    """The list with every name from people added (speakers found in the project), generic labels left out."""
    out = [dict(e) for e in entries]
    known = {e["name"] for e in out}
    for p in people:
        p = clean(p)
        if p and p not in known and not p.startswith(GENERIC):
            out.append({"name": p, "pseudonym": ""})
            known.add(p)
    return out


def suggest(entries: List[Dict], prefix: str = "P") -> List[Dict]:
    """Fill missing pseudonyms with prefix + number (P01, P02 …), not reusing a pseudonym already given."""
    out = [dict(e) for e in entries]
    used = {e["pseudonym"] for e in out if e.get("pseudonym")}
    n = 1
    for e in out:
        if e.get("pseudonym"):
            continue
        while f"{prefix}{n:02d}" in used:
            n += 1
        e["pseudonym"] = f"{prefix}{n:02d}"
        used.add(e["pseudonym"])
    return out


# ----- in one transcript -----
def _token_core(tok: str) -> str:
    return str(tok).strip()


def _replace_multi_in_words(seg: Dict, parts: List[str], holder: str, moved: Dict[str, str]) -> int:
    """A name of several words: the first word takes the new name (keeping its id and start), the others are removed
    (their ids are noted in moved, so codings that start or end on them move to the first word)."""
    words = seg.get("words") or []
    k, n, i = len(parts), 0, 0
    last_rx = re.compile(r"^" + re.escape(parts[-1]) + r"(?![\w])")
    while i + k <= len(words):
        toks = [_token_core(w.get("word", "")) for w in words[i:i + k]]
        if toks[:-1] == parts[:-1] and last_rx.match(toks[-1]):
            first, last = words[i], words[i + k - 1]
            lead = str(first.get("word", ""))[: len(str(first.get("word", ""))) - len(str(first.get("word", "")).lstrip())]
            rest = toks[-1][len(parts[-1]):]
            first["word"] = lead + holder + rest
            first["end"] = last.get("end", first.get("end"))
            for w in words[i + 1:i + k]:
                if w.get("wid") and first.get("wid"):
                    moved[w["wid"]] = first["wid"]
            del words[i + 1:i + k]
            n += 1
        i += 1
    return n


def _protected_positions(words: List[Dict], protect: Sequence[str]) -> set:
    """Indexes of words that belong to one of the protected names (other people with a name in common)."""
    out = set()
    toks = [_token_core(w.get("word", "")) for w in words]
    for name in protect:
        rx = name_rx(name)
        out.update(i for i, t in enumerate(toks) if rx.search(t))  # the whole name in one word (after a rename)
        parts = name.split(" ")
        k = len(parts)
        last_rx = re.compile(r"^" + re.escape(parts[-1]) + r"(?![\w])")
        for i in range(len(toks) - k + 1):
            if toks[i:i + k - 1] == parts[:-1] and last_rx.match(toks[i + k - 1]):
                out.update(range(i, i + k))
    return out


def replace_names(doc, repl: Dict[str, str], protect: Sequence[str] = ()) -> int:
    """Replace names said in the text (whole words), several at once (swaps like A->B, B->A work), longest first
    ("Robin Meyer" before "Robin"). Words keep their ids, timings and probabilities, so codings stay on them.
    protect: longer names of other people that contain a replaced name ("Robin Meyer" when renaming "Robin"); their
    words are left alone. Returns the number of names replaced."""
    repl = {o: n for o, n in repl.items() if o and n and o != n}
    if not repl:
        return 0
    protect = [p for p in protect if p and p not in repl]
    order = sorted(repl, key=len, reverse=True)
    holders = {o: f"\uE000{i}\uE001" for i, o in enumerate(order)}
    moved: Dict[str, str] = {}
    total = 0
    for seg in doc.segments:
        words = seg.get("words") or []
        k = 0
        if words:
            keep = _protected_positions(words, protect) if protect else set()
            for o in order:
                parts = o.split(" ")
                if len(parts) > 1:
                    rx = name_rx(o)
                    for wi, w in enumerate(words):  # the whole name inside one word (a renamed one)
                        if wi not in keep:
                            new, m = rx.subn(holders[o], str(w.get("word", "")))
                            if m:
                                w["word"] = new
                                k += m
                    k += _replace_multi_in_words(seg, parts, holders[o], moved)
                    keep = _protected_positions(words, protect) if protect else set()  # positions moved
                else:
                    rx = name_rx(o)
                    for wi, w in enumerate(words):
                        if wi in keep:
                            continue
                        new, m = rx.subn(holders[o], str(w.get("word", "")))
                        if m:
                            w["word"] = new
                            k += m
            if k:
                for w in words:
                    tok = str(w.get("word", ""))
                    for o in order:
                        tok = tok.replace(holders[o], repl[o])
                    w["word"] = tok
                doc._rebuild_text(seg)
        else:
            text = str(seg.get("text") or "")
            guards = {p: f"\uE002{i}\uE003" for i, p in enumerate(sorted(protect, key=len, reverse=True))}
            for p, g in guards.items():
                text = name_rx(p).sub(g, text)
            for o in order:
                text, m = name_rx(o).subn(holders[o], text)
                k += m
            for o in order:
                text = text.replace(holders[o], repl[o])
            for p, g in guards.items():
                text = text.replace(g, p)
            seg["text"] = text
        total += k
    if moved:  # codings that started or ended on a removed word
        for c in doc.data.get("codings") or []:
            for key in ("start_wid", "end_wid"):
                if c.get(key) in moved:
                    c[key] = moved[c[key]]
    if total:
        doc.log_edit("replace_names", names=repl, count=total)
    return total


def count_mentions(doc, names: Iterable[str], protect: Sequence[str] = ()) -> Dict[str, int]:
    """How often each name is said, not counting where it is part of a protected longer name."""
    rx = {n: name_rx(n) for n in names}
    out = {n: 0 for n in rx}
    guards = sorted(protect, key=len, reverse=True)
    for seg in doc.segments:
        text = str(seg.get("text") or "")
        for p in guards:
            text = name_rx(p).sub("\uE002", text)
        for n, r in rx.items():
            out[n] += len(r.findall(text))
    return out


def protect_for(old: str, entries: Iterable[Dict]) -> List[str]:
    """The other people on the list whose name contains old as a word ("Robin Meyer" for "Robin")."""
    rx = name_rx(old)
    return [e["name"] for e in entries if e.get("name") and e["name"] != old and rx.search(e["name"])]


def rename_in_doc(doc, old: str, new: str, in_text: bool = True, protect: Sequence[str] = ()) -> Dict[str, int]:
    """Rename a person in one transcript: speaker labels (and its speaker list) and, if in_text, where the name is
    said (not where it is part of a protected longer name). Returns {"speaker": segments, "text": mentions}."""
    spk = doc.rename_speakers({old: new})
    txt = replace_names(doc, {old: new}, protect) if in_text else 0
    return {"speaker": spk, "text": txt}


def pseudonymised(doc, repl: Dict[str, str]):
    """A copy of the transcript with speakers and names replaced, for exports that leave the lab. The original is not
    changed. Paths to recordings, the edit log and the transcription's provenance are left out of the copy."""
    from condense.core.transcript import TranscriptDocument
    data = copy.deepcopy(doc.data)
    twin = TranscriptDocument(doc.path, data)
    if repl:
        twin.rename_speakers(repl)
        replace_names(twin, repl)
    data["media"] = {}
    data.pop("edit_history", None)
    data["provenance"] = {"pseudonymised": True, "from": doc.base_name}
    for k in ("video_link", "speakers_mapping"):
        data.pop(k, None)
    return twin


# ----- across the project -----
def rename_in_project_file(project, old: str, new: str) -> bool:
    """The project's speakers, each group's speakers and the name list follow a rename."""
    from pathlib import Path
    from condense.core import project as proj
    path = Path(project) / proj.PROJECT_FILE
    data = proj.load_project(path)

    def fix(lst):
        out = []
        for n in lst or []:
            n = new if n == old else n
            if n not in out:
                out.append(n)
        return out

    speakers = fix(data.get("speakers"))
    groups = {g: fix(v) for g, v in (data.get("group_speakers") or {}).items()}
    entries = name_list(data)
    for e in entries:
        if e["name"] == old:
            e["name"] = new
    merged: List[Dict] = []
    for e in entries:  # renamed onto a name already listed: keep one entry (its pseudonym if it had one)
        same = next((m for m in merged if m["name"] == e["name"]), None)
        if same is None:
            merged.append(e)
        elif not same.get("pseudonym"):
            same["pseudonym"] = e.get("pseudonym", "")
    changed = (speakers != list(data.get("speakers") or []) or groups != (data.get("group_speakers") or {})
               or merged != name_list(data))
    if changed:
        proj.update_project(Path(project), speakers=speakers, group_speakers=groups, names=merged)
    return changed


def rename_preview(paths, old: str, protect: Sequence[str] = ()) -> Dict[str, Dict[str, int]]:
    """{path: {"speaker": segments, "text": mentions}} for the transcripts where the name occurs."""
    from condense.core.transcript import TranscriptDocument
    out = {}
    for p in paths:
        try:
            doc = TranscriptDocument.load(p)
        except Exception:
            continue
        spk = sum(1 for s in doc.segments if s.get("speaker") == old) + (1 if old in (doc.data.get("speakers") or []) and
                                                                           not any(s.get("speaker") == old for s in doc.segments) else 0)
        txt = count_mentions(doc, [old], protect)[old]
        if spk or txt:
            out[str(p)] = {"speaker": spk, "text": txt}
    return out
