"""Episodes: named stretches of a recording — an interview with the facilitator inside a group's session, an activity
phase (Dioramas, Risks intro …). Unlike cuts they remove nothing; they label time. Stored in the transcript as
{"id", "type", "label", "start", "end", "approx", "source", "note"} on the original recording's timeline."""
import uuid
from typing import Dict, List, Optional, Sequence

DEFAULT_TYPES = ["Interview", "Phase"]
PALETTE = ["#38bdf8", "#a3e635", "#f472b6", "#fbbf24", "#c084fc", "#2dd4bf", "#fb7185", "#94a3b8"]


def type_colors(types: Sequence[str]) -> Dict[str, str]:
    return {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(types)}


def new_id() -> str:
    return "ep_" + uuid.uuid4().hex[:10]


def normalize(ep: Dict) -> Dict:
    a, b = float(ep.get("start", 0.0)), float(ep.get("end", 0.0))
    if b < a:
        a, b = b, a
    out = dict(ep, start=round(a, 3), end=round(b, 3))
    out.setdefault("id", new_id())
    out.setdefault("type", DEFAULT_TYPES[0])
    out.setdefault("label", "")
    out.setdefault("approx", False)
    return out


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def segment_in(seg: Dict, ep: Dict) -> bool:
    """A segment belongs to an episode if at least half of it lies inside (the same rule as for cuts)."""
    s, e = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    dur = max(1e-6, e - s)
    return overlap(s, e, ep["start"], ep["end"]) >= 0.5 * dur


def boundaries(episodes: Sequence[Dict]) -> List[float]:
    return sorted({t for ep in episodes for t in (ep["start"], ep["end"])})


def describe(ep: Dict, short_ts) -> str:
    approx = "≈ " if ep.get("approx") else ""
    return f"{ep['type']} {ep.get('label') or ''}".strip() + f"  {approx}{short_ts(ep['start'])[:-2]} – {short_ts(ep['end'])[:-2]}"


def parse_time(text: str) -> Optional[float]:
    """'3:47:37', '47:37', '227.5', '3:47:37.250' -> seconds."""
    text = str(text).strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs
