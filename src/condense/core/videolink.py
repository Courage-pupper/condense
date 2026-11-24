"""Link a video to a transcript afterwards, without re-encoding anything.

For when the editor has no video on the transcript's timeline: the recording was trimmed in LosslessCut and
transcribed without the Compress step (older versions did not keep the trimmed file), the video was moved or
renamed, or the picture comes from a separate camera that started earlier or later than the audio.

A link is a list of pieces [transcript_start, video_start, length]: inside a piece, transcript time t shows the video
at video_start + (t - transcript_start); transcript time outside every piece has no picture. The pieces are found by
matching the transcript's sound against the video's (see core/align.py), so the video file itself is never touched.
"""
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from condense.core import align

RATE = align.RATE
PROBE_S = 8.0        # seconds of sound compared at each anchor of a LosslessCut piece
SEARCH_S = 8.0       # how far (±) around the expected place an anchor is looked for (keyframe snapping, drift)
WHOLE_PROBE_S = 120.0
GOOD_ANCHOR = 0.6    # sample correlation of an anchor that counts as found


# ----- using a link -----------------------------------------------------------------------------------------------
def to_video_time(pieces: Optional[Sequence[Sequence[float]]], t: float) -> Tuple[Optional[float], Optional[float]]:
    """(video position for transcript time t, or None where the video has no picture for it;
    transcript time at which that answer changes: the end of t's piece, or the start of the next piece when t is in a
    gap; None = never). No pieces = the video is on the transcript's timeline."""
    if not pieces:
        return max(0.0, t), None
    for ts, vs, ln in sorted(pieces, key=lambda p: p[0]):
        if t < ts:
            return None, ts
        if t < ts + ln:
            return vs + (t - ts), ts + ln
    return None, None


def is_same_timeline(pieces: Optional[Sequence[Sequence[float]]], tolerance: float = 0.05) -> bool:
    """True if transcript time and video time are the same (no link, or one piece starting at 0 in both)."""
    if not pieces:
        return True
    return len(pieces) == 1 and abs(pieces[0][0]) <= tolerance and abs(pieces[0][1]) <= tolerance


# ----- finding a link ---------------------------------------------------------------------------------------------
def _locate(long_path: Path, short: np.ndarray, guess: float, window: float, stop=None) -> Tuple[float, float]:
    """Where the samples `short` start in long_path, searched within guess ± window. Returns (seconds, score -1..1)."""
    start = max(0.0, guess - window)
    seg = align._decode(long_path, start=start, duration=len(short) / RATE + (guess - start) + window, stop=stop)
    ncc = align._ncc(seg, short)
    if len(ncc) == 0:
        return guess, 0.0
    i = int(np.argmax(ncc))
    return start + i / RATE, float(ncc[i])


def match_losslesscut(transcript_audio: Path, video: Path, segments: Sequence[Sequence[float]], transcript_dur: float,
                      stop=None) -> Dict:
    """The transcript was made from these LosslessCut segments of `video`, joined. One piece per segment; where each
    one really starts is checked by its sound (stream-copy cuts start at the keyframe before the cut, so the trimmed
    file the transcript was made from was a little longer than the segments say)."""
    pieces: List[List[float]] = []
    scores: List[float] = []
    bound = 0.0          # where this segment starts on the transcript's timeline
    offset = 0.0         # video time - transcript time of the previous segment
    for s, e in segments:
        s, e = float(s), float(e)
        if e - s <= 0.05:
            continue
        nominal = s - bound  # video time - transcript time, if the trimmed file had started exactly at s
        offset = nominal
        probe_len = min(PROBE_S, e - s - 1.0)
        if probe_len >= 2.0:
            lead = min(0.5, (e - s - probe_len) / 2)
            sample = align._decode(video, start=s + lead, duration=probe_len, stop=stop)
            if len(sample) >= RATE:
                pos, score = _locate(transcript_audio, sample, (s + lead) - nominal, SEARCH_S, stop)
                scores.append(score)
                if score >= GOOD_ANCHOR:
                    offset = (s + lead) - pos
        pieces.append([round(bound, 3), round(bound + offset, 3), 0.0])
        bound = max(bound, e - offset)
    for i, p in enumerate(pieces):
        end = pieces[i + 1][0] if i + 1 < len(pieces) else max(transcript_dur, p[0])
        p[2] = round(max(0.0, end - p[0]), 3)
    found = [sc for sc in scores if sc >= GOOD_ANCHOR]
    return {"pieces": [p for p in pieces if p[2] > 0], "method": "losslesscut segments, checked by sound",
            "score": round(float(np.mean(scores)) if scores else 0.0, 3),
            "confident": bool(scores) and len(found) * 2 >= len(scores) and len(found) >= 1}


def _whole(session_path: Path, clip_path: Path, clip_len: float, stop=None, cache_dir=None, session_dur=None) -> Dict:
    """Where clip_path starts inside session_path (envelope search on the first two minutes, then sample-exact)."""
    session = align.SessionAudio(session_path, cache_dir=cache_dir, stop=stop, duration=session_dur)
    head = align._decode(clip_path, duration=WHOLE_PROBE_S, stop=stop)
    probe = align.envelope(head)
    if len(probe) < 300:
        raise ValueError(f"{Path(clip_path).name} has under 3 seconds of sound to compare.")
    ncc = align._ncc(session.env, probe)
    if len(ncc) == 0:
        raise ValueError("The two files' sound could not be compared.")
    best = int(np.argmax(ncc))
    score = float(ncc[best])
    guard = max(300, len(probe) // 4)
    masked = ncc.copy()
    masked[max(0, best - guard): best + guard] = -1
    second = float(masked.max()) if masked.size and masked.max() > -1 else 0.0
    start = align._refine(session_path, head, best * align.HOP / RATE, stop)
    return {"start": round(start, 3), "score": round(score, 3), "margin": round(score - second, 3),
            "confident": score >= 0.8 and score - second >= 0.15, "clip_length": clip_len}


def match_whole(transcript_audio: Path, video: Path, transcript_dur: float, video_dur: float, stop=None, cache_dir=None) -> Dict:
    """One offset: the transcript's sound is part of the video (camera started earlier), the video is part of the
    transcript (camera started later), or both are the same recording."""
    if video_dur >= transcript_dur - 1.0:
        r = _whole(video, transcript_audio, transcript_dur, stop, cache_dir, video_dur)
        pieces = [[0.0, r["start"], round(transcript_dur, 3)]]
    else:
        r = _whole(transcript_audio, video, video_dur, stop, cache_dir, transcript_dur)
        pieces = [[r["start"], 0.0, round(video_dur, 3)]]
    return {"pieces": pieces, "method": "matched by sound", "score": r["score"], "margin": r["margin"],
            "confident": r["confident"]}


def losslesscut_segments(provenance: Optional[Dict]) -> Optional[List[List[float]]]:
    """The LosslessCut segments a single-recording transcript was cut from, if any (not for merged recordings)."""
    proc = (provenance or {}).get("processing") or {}
    rec = proc.get("losslesscut")
    if not isinstance(rec, dict) or proc.get("merge"):
        return None
    segs = rec.get("segments_s") or []
    return [[float(s), float(e)] for s, e in segs] or None


def find_link(transcript_audio: Path, video: Path, transcript_dur: float, video_dur: float, provenance: Optional[Dict] = None,
              stop=None, cache_dir=None, on_status: Optional[Callable[[str], None]] = None) -> Dict:
    """How `video` lines up with the transcript. Returns {"pieces", "method", "score", "confident"}; pieces is None when
    the video is on the transcript's timeline already."""
    status = on_status or (lambda s: None)
    segs = losslesscut_segments(provenance)
    if segs and video_dur > transcript_dur + 0.5:
        status(f"Lining up the {len(segs)} LosslessCut segment(s) with {Path(video).name}…")
        r = match_losslesscut(transcript_audio, video, segs, transcript_dur, stop)
        if r["confident"]:
            return r
    status(f"Comparing the sound of {Path(video).name} with the transcript's…")
    r = match_whole(transcript_audio, video, transcript_dur, video_dur, stop, cache_dir)
    if r["confident"] and is_same_timeline(r["pieces"], tolerance=0.1):
        r = dict(r, pieces=None, method="same timeline (checked by sound)")
    return r
