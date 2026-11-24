"""Find where a cut-out clip lies inside the full recording it was cut from, by its sound.

Both are decoded to 8 kHz mono and reduced to a loudness curve (100 values per second). The clip's curve is slid along
the recording's (normalised cross-correlation via FFT), then refined on the raw samples around the best match to a few
milliseconds. Works whatever tool cut the clip, also when it was re-encoded. Needs only ffmpeg and numpy.
"""
import hashlib
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

RATE = 8000
HOP = 80  # samples per loudness value: 10 ms


def _decode(path: Path, start: Optional[float] = None, duration: Optional[float] = None, stop=None,
            on_progress: Optional[Callable[[float], None]] = None, total: Optional[float] = None) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks, got = [], 0
    try:
        while True:
            if stop is not None and stop.is_set():
                proc.kill()
                raise InterruptedError("Cancelled.")
            buf = proc.stdout.read(RATE * 2 * 30)  # 30 s at a time
            if not buf:
                break
            chunks.append(np.frombuffer(buf, dtype="<i2"))
            got += len(buf) // 2
            if on_progress and total:
                on_progress(min(1.0, got / RATE / total))
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {Path(path).name}: {err.strip()[-300:]}")
    return np.concatenate(chunks).astype(np.float32) / 32768.0 if chunks else np.zeros(0, np.float32)


def envelope(samples: np.ndarray) -> np.ndarray:
    n = len(samples) // HOP
    if n == 0:
        return np.zeros(0, np.float32)
    frames = samples[: n * HOP].reshape(n, HOP)
    return np.log10(np.mean(frames * frames, axis=1) + 1e-8).astype(np.float32)


def _ncc(long: np.ndarray, short: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of short at every offset in long (values -1..1)."""
    n, m = len(long), len(short)
    if m == 0 or n < m:
        return np.zeros(0)
    s = (short - short.mean()) / (short.std() + 1e-9)
    size = 1 << int(np.ceil(np.log2(n + m)))
    corr = np.fft.irfft(np.fft.rfft(long, size) * np.conj(np.fft.rfft(s, size)), size)[: n - m + 1]
    c1 = np.concatenate([[0.0], np.cumsum(long, dtype=np.float64)])
    c2 = np.concatenate([[0.0], np.cumsum(long.astype(np.float64) ** 2)])
    wsum = c1[m:] - c1[:-m]
    wsq = c2[m:] - c2[:-m]
    std = np.sqrt(np.maximum(wsq / m - (wsum / m) ** 2, 1e-12))
    return corr / (m * std)


def stream_envelope(path: Path, stop=None, on_progress: Optional[Callable[[float], None]] = None,
                    total: Optional[float] = None) -> np.ndarray:
    """envelope(_decode(path)) without holding the decoded sound in memory (5 hours at 8 kHz would be ~600 MB)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE),
           "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, rest, got = [], np.zeros(0, np.float32), 0
    try:
        while True:
            if stop is not None and stop.is_set():
                proc.kill()
                raise InterruptedError("Cancelled.")
            buf = proc.stdout.read(RATE * 2 * 30)
            if not buf:
                break
            x = np.concatenate([rest, np.frombuffer(buf[: len(buf) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0])
            got += len(buf) // 2
            k = len(x) // HOP
            if k:
                fr = x[: k * HOP].reshape(k, HOP)
                out.append(np.log10(np.mean(fr * fr, axis=1) + 1e-8).astype(np.float32))
            rest = x[k * HOP:]
            if on_progress and total:
                on_progress(min(1.0, got / RATE / total))
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read {Path(path).name}: {err.strip()[-300:]}")
    return np.concatenate(out) if out else np.zeros(0, np.float32)


class SessionAudio:
    """The full recording, decoded once (and cached on disk) so several clips can be matched against it."""

    def __init__(self, path: Path, cache_dir: Optional[Path] = None, stop=None, on_progress=None, duration: Optional[float] = None):
        self.path = Path(path)
        st = self.path.stat()
        key = hashlib.sha1(f"{self.path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
        cached = Path(cache_dir) / f"{key}.env.npy" if cache_dir else None
        if cached is not None and cached.exists():
            self.env = np.load(cached)
        else:
            self.env = stream_envelope(self.path, stop=stop, on_progress=on_progress, total=duration)
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                np.save(cached, self.env)
        self.duration = len(self.env) * HOP / RATE


def find_clip(session: SessionAudio, clip_path: Path, stop=None, max_probe_s: float = 120.0) -> Dict:
    """Where clip_path starts in the session. Returns {"start", "end", "score" (0..1), "margin", "confident"}."""
    clip = _decode(Path(clip_path), stop=stop)
    clip_len = len(clip) / RATE
    probe = envelope(clip[: int(max_probe_s * RATE)])
    if len(probe) < 300:  # under 3 s of sound
        raise ValueError(f"{Path(clip_path).name} is too short to match (under 3 seconds).")
    ncc = _ncc(session.env, probe)
    if len(ncc) == 0:
        raise ValueError(f"{Path(clip_path).name} is longer than the recording it should be part of.")
    best = int(np.argmax(ncc))
    score = float(ncc[best])
    guard = max(300, len(probe) // 4)  # the next best match at least 3 s away: how distinct the match is
    masked = ncc.copy()
    masked[max(0, best - guard): best + guard] = -1
    second = float(masked.max()) if masked.size and masked.max() > -1 else 0.0
    coarse = best * HOP / RATE
    start = _refine(session.path, clip, coarse, stop)
    return {"start": round(start, 3), "end": round(start + clip_len, 3), "score": round(score, 3),
            "margin": round(score - second, 3), "confident": score >= 0.8 and score - second >= 0.15,
            "clip_length": round(clip_len, 3)}


def _refine(session_path: Path, clip: np.ndarray, coarse: float, stop=None, window: float = 0.1, probe_s: float = 8.0) -> float:
    """Sample-accurate start: correlate the clip's first seconds with the session around the coarse match."""
    head = clip[: int(probe_s * RATE)]
    if len(head) < RATE:
        return coarse
    seg = _decode(session_path, start=coarse - window, duration=probe_s + 2 * window, stop=stop)
    if len(seg) < len(head):
        return coarse
    ncc = _ncc(seg, head)
    if len(ncc) == 0 or float(ncc.max()) < 0.5:
        return coarse
    return max(0.0, coarse - window) + int(np.argmax(ncc)) / RATE
