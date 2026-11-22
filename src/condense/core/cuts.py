"""Cut lists: parts of a recording left out of the transcript's exports and of a trimmed video.

Cuts are time ranges on the original recording's timeline. Nothing is deleted: a cut segment stays in the transcript
(struck through) and can be restored. A trimmed export maps every remaining time onto the shortened timeline, so
the exported transcript's timestamps match the exported video exactly (what MAXQDA needs to link them).
"""
import copy
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Range = Tuple[float, float]
JOIN_GAP = 0.05  # cuts closer than this are merged into one


def normalize(ranges: Iterable[Sequence[float]]) -> List[Range]:
    """Sorted, non-overlapping ranges; touching or overlapping ones merged; empty ones dropped."""
    rs = sorted((float(min(a, b)), float(max(a, b))) for a, b in ranges if abs(float(b) - float(a)) > 1e-6)
    out: List[Range] = []
    for a, b in rs:
        if out and a <= out[-1][1] + JOIN_GAP:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract(ranges: Sequence[Range], a: float, b: float) -> List[Range]:
    """ranges minus [a, b] (used to restore part of a cut)."""
    out: List[Range] = []
    for s, e in ranges:
        if e <= a or s >= b:
            out.append((s, e))
            continue
        if s < a:
            out.append((s, a))
        if e > b:
            out.append((b, e))
    return normalize(out)


def keep_intervals(cuts: Sequence[Range], duration: float) -> List[Range]:
    """The parts of [0, duration] that are not cut."""
    keep: List[Range] = []
    t = 0.0
    for s, e in normalize(cuts):
        s, e = max(0.0, s), min(duration, e)
        if s > t + 1e-6:
            keep.append((t, s))
        t = max(t, e)
    if duration > t + 1e-6:
        keep.append((t, duration))
    return keep


def segment_is_cut(seg: Dict, cuts: Sequence[Range]) -> bool:
    """A segment counts as cut when at least half of it lies inside cuts."""
    s, e = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    dur = max(1e-3, e - s)
    inside = sum(max(0.0, min(e, ce) - max(s, cs)) for cs, ce in cuts)
    if e - s <= 1e-3:  # zero-length segment: cut if its time is inside a cut
        return any(cs <= s <= ce for cs, ce in cuts)
    return inside / dur >= 0.5


def cut_between(cuts: Sequence[Range], t0: float, t1: float) -> bool:
    """Is there a cut between two times (used to start a new paragraph after a cut)?"""
    return any(cs < t1 and ce > t0 for cs, ce in cuts)


class TimeMap:
    """Original time -> time on the trimmed recording, given the kept intervals (as actually written to the file)."""

    def __init__(self, kept: Sequence[Range]):
        self.kept = list(kept)
        self.offsets: List[float] = []
        acc = 0.0
        for a, b in self.kept:
            self.offsets.append(acc)
            acc += b - a
        self.duration = acc

    def __call__(self, t: float) -> float:
        for (a, b), off in zip(self.kept, self.offsets):
            if t < a:
                return off  # inside a cut before this interval: snaps to where the cut was
            if t <= b:
                return off + (t - a)
        return self.duration


def retime_segments(segments: Sequence[Dict], cuts: Sequence[Range], tmap: TimeMap) -> List[Dict]:
    """Copies of the segments that are not cut, with start/end (and word times) on the trimmed timeline."""
    out: List[Dict] = []
    for seg in segments:
        if segment_is_cut(seg, cuts):
            continue
        s = copy.deepcopy(seg)
        s["start"], s["end"] = round(tmap(float(seg.get("start", 0.0))), 3), round(tmap(float(seg.get("end", 0.0))), 3)
        for w in s.get("words") or []:
            if "start" in w:
                w["start"] = round(tmap(float(w["start"])), 3)
            if "end" in w:
                w["end"] = round(tmap(float(w["end"])), 3)
        out.append(s)
    return out


LLC_MARKER = "// Written by Condense (cut list). LosslessCut reads this file; Condense applies it before transcribing."


def llc_text(media_name: str, kept: Sequence[Range]) -> str:
    """A LosslessCut project listing the parts to keep (LosslessCut exports the listed segments)."""
    lines = [LLC_MARKER, "{", "  version: 1,", f"  mediaFileName: '{media_name.replace(chr(39), chr(92) + chr(39))}',", "  cutSegments: ["]
    for a, b in kept:
        lines.append(f"    {{ start: {a:.3f}, end: {b:.3f}, name: '' }},")
    lines += ["  ],", "}", ""]
    return "\n".join(lines)


def describe(cuts: Sequence[Range]) -> str:
    total = sum(e - s for s, e in cuts)
    return f"{len(cuts)} cut(s), {int(total // 60)} min {int(total % 60)} s"


def next_cut_end(cuts: Sequence[Range], t: float) -> Optional[float]:
    """If t lies inside a cut, where that cut ends (the player jumps there)."""
    for s, e in cuts:
        if s <= t < e - 0.05:
            return e
    return None
