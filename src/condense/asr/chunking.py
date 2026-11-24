"""Where to split a long recording for Whisper.

Whisper transcribes 30-second windows one after another and feeds each window the text of the one before. Over hours
one bad window (noise, a long silence, a loop) can make it skip or repeat whole stretches. Transcribing the recording
in pieces of a few minutes, each started fresh (with the vocabulary prompt), keeps such an error inside one piece.

The pieces are cut in pauses: the quietest half second near each target length, found from the loudness alone. Only
long stretches that are as quiet as the recording ever gets are left out (Whisper tends to invent text there); anything
with sound in it is transcribed. numpy only: this file also runs inside the Whisper Python (see runner.py).
"""
import wave

import numpy as np

FRAME_S = 0.01  # one loudness value per 10 ms


def wav_loudness(path, block_s=30.0):
    """(loudness in dB per 10 ms, duration in seconds) of a 16-bit PCM WAV, read in blocks (hours fit in memory)."""
    with wave.open(str(path), "rb") as wf:
        sr, ch, n = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        if wf.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        hop = max(1, int(round(sr * FRAME_S)))
        out, rest = [], np.zeros(0, np.float32)
        while True:
            raw = wf.readframes(int(sr * block_s))
            if not raw:
                break
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            x = np.concatenate([rest, x])
            k = len(x) // hop
            if k:
                fr = x[: k * hop].reshape(k, hop)
                out.append(10.0 * np.log10(np.mean(fr * fr, axis=1) + 1e-10))
            rest = x[k * hop:]
    db = np.concatenate(out) if out else np.zeros(0, np.float32)
    return db.astype(np.float32), n / float(sr)


def read_wav_slice(path, start_s, end_s):
    """Samples [start, end) of a 16-bit WAV as float32 mono at its own rate (Whisper wants 16 kHz)."""
    with wave.open(str(path), "rb") as wf:
        sr, ch, n = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        a = max(0, min(n, int(start_s * sr)))
        b = max(a, min(n, int(end_s * sr)))
        wf.setpos(a)
        x = np.frombuffer(wf.readframes(b - a), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def _smooth(db, frames):
    if frames <= 1 or len(db) < frames:
        return db
    c = np.concatenate([[0.0], np.cumsum(db, dtype=np.float64)])
    m = (c[frames:] - c[:-frames]) / frames
    pad = frames // 2
    return np.concatenate([np.full(pad, m[0]), m, np.full(len(db) - len(m) - pad, m[-1])]).astype(np.float32)


def quiet_stretches(db, min_s=30.0, margin_db=3.0):
    """[(start_s, end_s)] that stay within margin_db of the quietest the recording gets (its 5th percentile) and at
    least 15 dB below its median, for at least min_s. Background talk, activity noise or music are louder and never
    count as quiet."""
    if len(db) == 0:
        return []
    floor = float(np.percentile(db, 5))
    # Quiet means near the floor AND clearly below the recording's usual level: a recording that never falls silent
    # (constant talk, noise) has a "floor" too, but it is speech, and nothing of it may be left out.
    limit = min(floor + margin_db, float(np.median(db)) - 15.0)
    quiet = _smooth(db, 50) < limit  # 0.5 s averages: a click does not end a silence
    out, i, n = [], 0, len(quiet)
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if (j - i) * FRAME_S >= min_s:
            out.append((i * FRAME_S, j * FRAME_S))
        i = j
    return out


def plan_chunks(db, duration=None, target_s=240.0, max_s=360.0, skip_quiet_s=30.0, pad_s=1.0):
    """[(start_s, end_s)] pieces covering the recording, cut at the quietest half second between 0.75 × target_s and
    max_s from each piece's start. Long silences (skip_quiet_s) are left out, keeping pad_s of them on each side."""
    if duration is None:
        duration = len(db) * FRAME_S
    if duration <= 0:
        return []
    keep, cur = [], 0.0
    for a, b in quiet_stretches(db, skip_quiet_s):
        if a + pad_s > cur:
            keep.append((cur, a + pad_s))
        cur = max(cur, b - pad_s)
    if cur < duration:
        keep.append((cur, duration))
    sm = _smooth(db, 50)
    chunks = []
    for a, b in keep:
        start = a
        while b - start > max_s:
            lo = start + 0.75 * target_s            # not too short …
            hi = min(start + max_s, b - 0.25 * target_s)  # … and no tiny last piece
            if hi <= lo:
                break
            i0, i1 = int(lo / FRAME_S), min(len(sm), int(hi / FRAME_S))
            cut = (i0 + int(np.argmin(sm[i0:i1]))) * FRAME_S if i1 > i0 else start + max_s
            chunks.append((round(start, 2), round(cut, 2)))
            start = cut
        if b - start > 0.5:
            chunks.append((round(start, 2), round(b, 2)))
    return chunks


def shift_result(result, offset):
    """Whisper's segments (and words) of a piece, moved onto the recording's timeline."""
    out = []
    for seg in result.get("segments") or []:
        seg = dict(seg)
        seg["start"] = round(float(seg.get("start", 0.0)) + offset, 3)
        seg["end"] = round(float(seg.get("end", 0.0)) + offset, 3)
        words = []
        for w in seg.get("words") or []:
            w = dict(w)
            for k in ("start", "end"):
                if k in w:
                    w[k] = round(float(w[k]) + offset, 3)
            words.append(w)
        if "words" in seg:
            seg["words"] = words
        seg.pop("seek", None)
        out.append(seg)
    return out
