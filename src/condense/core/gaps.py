"""Stretches of a recording with voices in them but no transcript: what Whisper skipped.

The loudness of the recording (core/align.py's envelope, 10 ms steps) is compared with the loudness of the parts that
were transcribed. A gap between segments counts when it holds at least a few seconds as loud as the transcribed talk
(within 12 dB of its median) — quiet rooms, breaks and silence do not. The stretches can then be re-transcribed one by
one (Transcript ▾ ▸ Find untranscribed stretches…).
"""
from typing import Dict, List, Sequence, Tuple

import numpy as np

FRAME_S = 0.01
MAX_PART_S = 10 * 60.0  # longer stretches are re-transcribed in parts


def find_untranscribed(env: np.ndarray, segments: Sequence[Dict], min_gap_s: float = 6.0, min_sound_s: float = 2.0,
                       min_share: float = 0.2, margin_s: float = 0.3) -> List[Dict]:
    """[{"start", "end", "sound_s", "share"}] for the gaps between segments (and before the first / after the last)
    that are at least min_gap_s long and hold at least min_sound_s of talk-loud sound making up min_share of them.
    env: log10 loudness per 10 ms (align.stream_envelope)."""
    db = 10.0 * np.asarray(env, dtype=np.float32)
    n = len(db)
    if n == 0:
        return []
    duration = n * FRAME_S
    spans = sorted((float(s.get("start", 0.0)), float(s.get("end", 0.0))) for s in segments)
    mask = np.zeros(n, bool)
    for a, b in spans:
        mask[max(0, int(a / FRAME_S)): min(n, int(b / FRAME_S))] = True
    ref = float(np.median(db[mask])) if mask.sum() > 200 else float(np.percentile(db, 75))
    loud = db > ref - 12.0
    gaps: List[Tuple[float, float]] = []
    prev = 0.0
    for a, b in spans:
        if a - prev >= min_gap_s:
            gaps.append((prev, a))
        prev = max(prev, b)
    if duration - prev >= min_gap_s:
        gaps.append((prev, duration))
    out = []
    for a, b in gaps:
        i0, i1 = int((a + margin_s) / FRAME_S), int((b - margin_s) / FRAME_S)
        if i1 <= i0:
            continue
        sound = float(loud[i0:i1].sum()) * FRAME_S
        share = sound / ((i1 - i0) * FRAME_S)
        if sound >= min_sound_s and share >= min_share:
            out.append({"start": round(a, 2), "end": round(b, 2), "sound_s": round(sound, 1), "share": round(share, 2)})
    return out


def parts(start: float, end: float, max_s: float = MAX_PART_S) -> List[Tuple[float, float]]:
    """A long stretch in parts of at most max_s (a re-transcription takes at most 30 minutes)."""
    n = max(1, int(np.ceil((end - start) / max_s)))
    step = (end - start) / n
    return [(round(start + k * step, 2), round(start + (k + 1) * step if k < n - 1 else end, 2)) for k in range(n)]
