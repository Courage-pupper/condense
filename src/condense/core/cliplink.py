"""Interview clips cut out of a session before Condense, transcribed and checked on their own: their transcripts taken
over into the session at the place found by sound (core/align.py), and kept in step from then on.

Taking a clip over: the session's words in the clip's stretch [a, a + length) are replaced by the clip's segments —
speakers, checked marks, notes — shifted to the session's timeline; a session segment that runs over an edge is cut
there, so speech just before and after the interview stays. The clip's codings and comments come along. The clip's
words get the session's word ids, and the clip is written again with them, so both share ids from then on.

Once linked, the session leads: after every change of the session (and undo), push() writes its stretch into the clip
(text, speakers, checked marks, notes, codings and comments of that stretch). If the clip was changed on its own since,
it is not overwritten: the episode says so, and the clip can be taken over again.

The link is kept in the episode's source ({"clip", "transcript", "offset", "length", "linked"}) and in the clip
({"linked_session": {"transcript", "episode", "offset", "session_hash", "written_hash"}}).
"""
import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from condense.core import coding as cod
from condense.core.transcript import TranscriptDocument, now_iso

SEG_KEEP = ("speaker", "speaker_manual", "checked", "note", "language")  # what a segment carries besides text and time


def clip_transcript_for(clip: Path) -> Optional[Path]:
    """The Condense transcript of a clip: in the results folder named like it, next to it (Condense's default), else
    next to it with its name."""
    clip = Path(clip)
    sub = clip.parent / clip.stem
    for folder in (sub, clip.parent):
        if folder.is_dir():
            exact = folder / f"{clip.stem}{TranscriptDocument.SUFFIX}"
            if exact.exists():
                return exact
            if folder == sub:
                found = sorted(p for p in folder.glob(f"*{TranscriptDocument.SUFFIX}") if not p.name.startswith("."))
                if found:
                    return found[0]
    return None


def _text(words: List[Dict]) -> str:
    return "".join(str(w.get("word", "")) for w in words)


def _cut_segment(seg: Dict, words: List[Dict]) -> Dict:
    out = {k: copy.deepcopy(v) for k, v in seg.items() if k not in ("words", "text", "start", "end")}
    out["words"] = words
    out["text"] = _text(words)
    out["start"], out["end"] = float(words[0]["start"]), float(words[-1]["end"])
    return out


def stretch(doc, a: float, b: float) -> List[Dict]:
    """The segments of doc in [a, b), cut to the words that start there (copies; times as on doc's timeline)."""
    out = []
    for seg in doc.segments:
        s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if s1 <= a or s0 >= b:
            continue
        words = seg.get("words") or []
        if words:
            inside = [copy.deepcopy(w) for w in words if a <= float(w["start"]) < b]
            if inside:
                out.append(_cut_segment(seg, inside))
        elif a <= (s0 + s1) / 2 < b:
            out.append(copy.deepcopy(seg))
    return out


def _shift(segs: List[Dict], dt: float) -> List[Dict]:
    for seg in segs:
        seg["start"] = round(float(seg["start"]) + dt, 3)
        seg["end"] = round(float(seg["end"]) + dt, 3)
        for w in seg.get("words") or []:
            w["start"] = round(float(w["start"]) + dt, 3)
            w["end"] = round(float(w["end"]) + dt, 3)
    return segs


def codings_within(doc, a: float, b: float, resolved: Optional[List[Dict]] = None) -> List[Dict]:
    """Codings and comments that lie inside [a, b] (as resolved now: where their words are)."""
    pos = {r["id"]: r for r in (resolved if resolved is not None else cod.resolve(doc))}
    out = []
    for c in cod.codings(doc):
        r = pos.get(c["id"], c)
        if float(r["start"]) >= a - 0.05 and float(r["end"]) <= b + 0.05:
            out.append(copy.deepcopy(c))
    return out


def content_hash(segs: List[Dict], codings: List[Dict]) -> str:
    """What a stretch holds, on its own timeline (starting at 0): equal in the session and the clip when in step."""
    def seg_key(s):
        return {"t": round(float(s["start"]), 2), "e": round(float(s["end"]), 2), "x": s.get("text", ""),
                **{k: s.get(k) for k in SEG_KEEP if s.get(k) not in (None, False, "")},
                "w": [(w.get("wid"), w.get("word"), round(float(w["start"]), 2)) for w in s.get("words") or []]}

    def cod_key(c):
        return {k: (round(float(v), 2) if k in ("start", "end") else v) for k, v in sorted(c.items())
                if k in ("id", "code", "start", "end", "start_wid", "end_wid", "memo", "kind")}
    blob = json.dumps({"s": [seg_key(s) for s in segs], "c": sorted((cod_key(c) for c in codings), key=lambda c: c["id"])},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _relative(doc, a: float, b: float, resolved=None) -> Tuple[List[Dict], List[Dict]]:
    """doc's stretch [a, b) and its codings, moved to start at 0."""
    segs = _shift(stretch(doc, a, b), -a)
    cods = codings_within(doc, a, b, resolved)
    for c in cods:
        c["start"] = round(float(c["start"]) - a, 3)
        c["end"] = round(float(c["end"]) - a, 3)
    return segs, cods


def _new_wid(doc) -> str:
    n = int(doc.data.get("next_wid") or 0)
    doc.data["next_wid"] = n + 1
    return f"w{n:x}"


def adopt(session, clip_doc, a: float, length: float, episode_id: str) -> Dict:
    """Replace the session's stretch [a, a + length) by the clip's transcript (see the module's docstring).
    Returns {"removed": words, "added": segments, "codings": n}."""
    b = a + float(length)
    session.ensure_word_ids()
    clip_doc.ensure_word_ids()
    removed = 0
    kept: List[Dict] = []
    at = None
    for seg in session.segments:
        s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if s1 <= a or s0 >= b:
            if at is None and s0 >= b:
                at = len(kept)
            kept.append(seg)
            continue
        words = seg.get("words") or []
        if not words:
            if a <= (s0 + s1) / 2 < b:
                removed += 1
                continue
            kept.append(seg)
            continue
        before = [w for w in words if float(w["start"]) < a]
        after = [w for w in words if float(w["start"]) >= b]
        removed += len(words) - len(before) - len(after)
        if before:
            kept.append(_cut_segment(seg, before))
        if at is None:
            at = len(kept)
        if after:
            kept.append(_cut_segment(seg, after))
    if at is None:
        at = len(kept)
    # the clip's words with the session's ids (a map, for its codings)
    wid_map: Dict[str, str] = {}
    new_segs = _shift(copy.deepcopy(clip_doc.segments), a)
    for seg in new_segs:
        for w in seg.get("words") or []:
            old = w.get("wid")
            w["wid"] = _new_wid(session)
            if old:
                wid_map[old] = w["wid"]
        seg["from_clip"] = episode_id
    kept[at:at] = new_segs
    session.segments[:] = kept
    n_cod = 0
    for c in cod.codings(clip_doc):
        nc = copy.deepcopy(c)
        nc["id"] = "k" + uuid.uuid4().hex[:10]
        nc["start"] = round(float(nc["start"]) + a, 3)
        nc["end"] = round(float(nc["end"]) + a, 3)
        for k in ("start_wid", "end_wid"):
            if nc.get(k):
                nc[k] = wid_map.get(nc[k])
        cod.codings(session).append(nc)
        n_cod += 1
    for name in sorted({s.get("speaker") for s in new_segs if s.get("speaker")}):
        if name not in session.roster():
            session.add_speaker(name)
    session.log_edit("take_over_clip", episode=episode_id, clip=clip_doc.path.name, start=round(a, 3), end=round(b, 3),
                     words_replaced=removed, segments=len(new_segs))
    return {"removed": removed, "added": len(new_segs), "codings": n_cod}


def linked_episodes(doc) -> List[Dict]:
    return [e for e in doc.episodes if (e.get("source") or {}).get("linked") and (e.get("source") or {}).get("transcript")]


def push(session, ep: Dict, force: bool = False, resolved=None) -> str:
    """Write the session's stretch of a linked episode into its clip. 'same' (nothing to do), 'written', 'missing'
    (the clip's transcript is gone) or 'clip changed' (edited on its own since: not overwritten unless force)."""
    src = ep.get("source") or {}
    path = Path(src["transcript"])
    if not path.exists():
        return "missing"
    a, length = _place(ep)
    segs, cods = _relative(session, a, a + length, resolved)
    h_session = content_hash(segs, cods)
    clip = TranscriptDocument.load(path)
    link = clip.data.get("linked_session") or {}
    if link.get("session_hash") == h_session and not force:
        return "same"
    if link.get("written_hash") and not force:
        now = content_hash(clip.segments, cod.codings(clip))
        if now != link["written_hash"]:
            return "clip changed"
    clip.segments[:] = segs
    clip.data["codings"] = cods
    clip.data["next_wid"] = max(int(clip.data.get("next_wid") or 0), int(session.data.get("next_wid") or 0))
    clip.data["linked_session"] = {"transcript": str(session.path), "episode": ep["id"], "offset": round(a, 3),
                                   "session_hash": h_session, "written_hash": content_hash(segs, cods), "at": now_iso()}
    clip.log_edit("from_session", transcript=session.path.name, episode=ep["id"])
    clip.save(backup=True)
    return "written"


def session_hash(session, ep: Dict, resolved=None) -> str:
    a, length = _place(ep)
    return content_hash(*_relative(session, a, a + length, resolved))


def _place(ep: Dict) -> Tuple[float, float]:
    """Where the clip lies in the session: as found by its sound when it was linked (moving the episode's edges later
    doesn't move the clip's sound)."""
    src = ep.get("source") or {}
    a = float(src["offset"]) if src.get("offset") is not None else float(ep["start"])
    length = float(src.get("length") or (float(ep["end"]) - a))
    return a, length


def clip_state(ep: Dict) -> str:
    """How a linked clip stands, without writing: 'in step', 'clip changed' (edited on its own), 'missing'."""
    src = ep.get("source") or {}
    path = Path(src.get("transcript") or "")
    if not src.get("transcript") or not path.exists():
        return "missing"
    try:
        clip = TranscriptDocument.load(path)
    except Exception:
        return "missing"
    link = clip.data.get("linked_session") or {}
    if link.get("written_hash") and content_hash(clip.segments, cod.codings(clip)) != link["written_hash"]:
        return "clip changed"
    return "in step"
