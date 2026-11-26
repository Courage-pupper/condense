"""Making recordings smaller without losing what matters: AV1 (or H.264 for a compatible file), at most 1080p, at
the smallest quality setting that still looks the same — found by encoding a few short samples and comparing them
with the original (VMAF when ffmpeg has it, SSIM otherwise) — and never larger than the original.

Encoders, best first for the computer: av1_vaapi (AMD RX 7000 / Intel Arc on Linux), av1_nvenc / av1_amf / av1_qsv
(Windows, recent GPUs), libsvtav1 (CPU; Macs have no AV1 encoder, not even M5). For H.264: libx264 (CPU, veryfast).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

GOALS = ("Small: AV1, up to 1080p (recommended)", "Compatible: H.264 MP4, up to 1080p (MAXQDA, every player)",
         "Custom: the encoder settings below")
TARGETS = ("Looks the same", "Smaller, small differences")
# VMAF a sample must reach (when ffmpeg has libvmaf). VMAF follows what people see; SSIM does not on camera footage
# (grain dominates it: a blurred copy scores almost as well as a good one), so without VMAF no search is made and the
# well-known settings below are used.
THRESHOLDS = {TARGETS[0]: 94.0, TARGETS[1]: 90.0}
KEEP_IF_ABOVE = 0.80     # an estimated copy at more than 80 % of the original is not worth it: keep the original
AUDIO_KBPS = 128

# quality value per encoder: (best, the most a search may go to, "looks the same", "smaller"); higher = smaller file.
# The fixed values are the usual recommendations for 1080p (SVT-AV1 CRF ~30–35 is visually close to the source).
RANGES = {
    "libsvtav1": (20, 40, 32, 38), "av1_vaapi": (60, 170, 120, 150), "av1_nvenc": (20, 44, 34, 40),
    "av1_amf": (60, 170, 120, 150), "av1_qsv": (60, 170, 120, 150), "libx264": (16, 28, 23, 26),
}


def goal_kind(goal: str) -> str:
    """'av1', 'h264' or 'custom'."""
    g = (goal or "").lower()
    return "av1" if g.startswith("small") else "h264" if g.startswith("compatible") else "custom"


def has_filter(name: str) -> bool:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    return re.search(rf"\s{re.escape(name)}\s", out) is not None


def _works(cmd: List[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=40).returncode == 0
    except Exception:
        return False


def vaapi_av1_works(device: Optional[str]) -> bool:
    if not device:
        return False
    return _works(["ffmpeg", "-hide_banner", "-loglevel", "error", "-vaapi_device", device, "-f", "lavfi", "-i",
                   "testsrc2=s=640x360:d=0.3", "-vf", "format=nv12,hwupload", "-c:v", "av1_vaapi", "-f", "null", "-"])


def encoder_works(codec: str) -> bool:
    return _works(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=0.3",
                   "-frames:v", "5", "-c:v", codec, "-f", "null", "-"])


def pick_encoder(kind: str, vaapi_device: Optional[str] = None, check: Callable[[str], bool] = encoder_works,
                 check_vaapi: Callable[[Optional[str]], bool] = vaapi_av1_works) -> Optional[str]:
    """The best working encoder for 'av1' or 'h264' on this computer, or None."""
    if not shutil.which("ffmpeg"):
        return None
    if kind == "h264":
        return "libx264" if check("libx264") else None
    if check_vaapi(vaapi_device):
        return "av1_vaapi"
    for codec in ("av1_nvenc", "av1_amf", "av1_qsv"):
        if check(codec):
            return codec
    return "libsvtav1" if check("libsvtav1") else None


def _box(max_h: int) -> Tuple[str, str]:
    """"1080p" limits the short side: 1920×1080 for landscape, 1080×1920 for portrait (phones)."""
    long_side = max_h * 16 // 9
    return (f"if(gte(iw,ih),min(iw,{long_side}),min(iw,{max_h}))", f"if(gte(iw,ih),min(ih,{max_h}),min(ih,{long_side}))")


def scale_box(max_h: int = 1080) -> str:
    """At most max_h on the short side, never enlarged, even sizes."""
    w, h = _box(max_h)
    return f"scale=w='{w}':h='{h}':force_original_aspect_ratio=decrease:force_divisible_by=2"


def encode_command(codec: str, src: Path, dst: Path, q: int, max_h: int = 1080, audio_kbps: int = AUDIO_KBPS,
                   vaapi_device: Optional[str] = None, start: Optional[float] = None, duration: Optional[float] = None,
                   fps: Optional[str] = None, progress: bool = True) -> List[str]:
    """The ffmpeg command for one encode (a sample: start + duration, no sound)."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if codec == "av1_vaapi":
        cmd += ["-vaapi_device", vaapi_device or "/dev/dri/renderD128"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-map", "0:v:0"]
    if codec == "av1_vaapi":
        w, h = _box(max_h)
        cmd += ["-vf", f"format=nv12,hwupload,scale_vaapi=w='{w}':h='{h}':force_original_aspect_ratio=decrease"]
    else:
        cmd += ["-vf", scale_box(max_h)]
    q = int(q)
    if codec == "libsvtav1":   # preset 7: a good balance for hours of recording; a keyframe every ~5 s for seeking
        cmd += ["-c:v", "libsvtav1", "-crf", str(q), "-preset", "7", "-g", "150", "-pix_fmt", "yuv420p"]
    elif codec == "av1_vaapi":
        cmd += ["-c:v", "av1_vaapi", "-rc_mode", "CQP", "-qp", str(q), "-g", "150"]
    elif codec == "av1_nvenc":
        cmd += ["-c:v", "av1_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0", "-g", "150"]
    elif codec == "av1_amf":
        cmd += ["-c:v", "av1_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q), "-g", "150"]
    elif codec == "av1_qsv":
        cmd += ["-c:v", "av1_qsv", "-global_quality", str(q), "-g", "150"]
    else:                      # libx264: plays everywhere (MAXQDA, QuickTime, Windows)
        cmd += ["-c:v", "libx264", "-crf", str(q), "-preset", "veryfast", "-pix_fmt", "yuv420p", "-g", "150"]
    if fps:
        cmd += ["-r", fps]
    if duration is None:       # the real thing: with sound
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{audio_kbps}k"]
    else:
        cmd += ["-an"]
    if Path(dst).suffix.lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    if progress:
        cmd += ["-progress", "pipe:1", "-nostats"]
    return cmd + [str(dst)]


def probe(path: Path) -> Dict:
    """{"duration", "size", "width", "height", "codec", "bitrate"} of a file (missing values: 0 / "")."""
    out = {"duration": 0.0, "size": 0, "width": 0, "height": 0, "codec": "", "bitrate": 0}
    try:
        out["size"] = Path(path).stat().st_size
        res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,bit_rate:stream=codec_type,codec_name,width,height",
                              "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(res.stdout or "{}")
        fmt = data.get("format") or {}
        out["duration"] = float(fmt.get("duration") or 0.0)
        out["bitrate"] = int(float(fmt.get("bit_rate") or 0))
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if v:
            out.update(width=int(v.get("width") or 0), height=int(v.get("height") or 0), codec=v.get("codec_name") or "")
    except Exception:
        pass
    return out


def sample_starts(duration: float, n: int = 4, length: float = 6.0) -> List[float]:
    """Where to take the samples: spread over the recording, away from its very start and end."""
    if duration <= length * 1.5:
        return [0.0]
    usable = duration * 0.9 - length
    first = duration * 0.05
    n = max(1, min(n, int(duration // 60) or 1))
    return [round(first + usable * (k + 0.5) / n, 3) for k in range(n)]


def compare(src: Path, sample: Path, start: float, length: float, use_vmaf: bool) -> Optional[float]:
    """How close an encoded sample is to the original at the same size: VMAF (0–100) or SSIM (0–1)."""
    info = probe(sample)
    w, h = info["width"], info["height"]
    if not w or not h:
        return None
    metric = "libvmaf" if use_vmaf else "ssim"
    graph = (f"[0:v]scale={w}:{h}:flags=bicubic,format=yuv420p,setpts=PTS-STARTPTS[ref];"
             f"[1:v]format=yuv420p,setpts=PTS-STARTPTS[dist];[dist][ref]{metric}")
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src),
                              "-i", str(sample), "-lavfi", graph, "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    except Exception:
        return None
    if use_vmaf:
        m = re.search(r"VMAF score[:=]\s*([0-9.]+)", res.stderr)
    else:
        m = re.search(r"SSIM .*All:([0-9.]+)", res.stderr)
    return float(m.group(1)) if m else None


def search_quality(src: Path, codec: str, work_dir: Path, target: str = TARGETS[0], max_h: int = 1080,
                   vaapi_device: Optional[str] = None, use_vmaf: Optional[bool] = None, length: float = 6.0,
                   log: Callable[[str], None] = lambda s: None, stop: Callable[[], bool] = lambda: False) -> Dict:
    """Choose the quality value and estimate the size from a few short samples.
    With VMAF: the highest value (= smallest file) whose every sample still reaches the target, within a safe range.
    Without VMAF: the well-known setting for the target (no search — see THRESHOLDS).
    Returns {"q", "score", "metric", "estimate" (bytes, sound included), "source_size", "keep_original", "reason",
    "tries": [(q, score, kbps)], "size": (w, h), …}."""
    info = probe(src)
    duration = info["duration"]
    lo, cap, same, smaller = RANGES[codec]
    use_vmaf = has_filter("libvmaf") if use_vmaf is None else use_vmaf
    need = THRESHOLDS.get(target, THRESHOLDS[TARGETS[0]])
    starts = sample_starts(duration, length=length)
    if starts == [0.0]:
        length = max(1.0, duration)
    work_dir.mkdir(parents=True, exist_ok=True)
    tried: Dict[int, Tuple[Optional[float], float]] = {}   # q -> (VMAF or None, video kbps)
    size_seen: Tuple[int, int] = (0, 0)

    def trial(q: int) -> Tuple[Optional[float], float]:
        nonlocal size_seen
        if q in tried:
            return tried[q]
        scores, total_bytes = [], 0
        for k, st in enumerate(starts):
            if stop():
                raise InterruptedError("Stopped.")
            out = work_dir / f"sample_{q}_{k}.mkv"
            cmd = encode_command(codec, src, out, q, max_h=max_h, vaapi_device=vaapi_device, start=st, duration=length, progress=False)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if res.returncode != 0 or not out.exists():
                raise RuntimeError(f"The {codec} encoder failed on a sample: " + " ".join(res.stderr.strip().splitlines()[-2:]))
            p = probe(out)
            size_seen = (p["width"], p["height"])
            total_bytes += out.stat().st_size
            if use_vmaf:
                score = compare(src, out, st, length, True)
                if score is None:
                    raise RuntimeError("Could not compare the sample with the original (VMAF).")
                scores.append(score)
            out.unlink(missing_ok=True)
        score = min(scores) if scores else None  # every sample must look right, not just on average
        kbps = total_bytes * 8 / 1000 / (length * len(starts))
        tried[q] = (score, kbps)
        log(f"Sample check: {codec} {q} → " + (f"VMAF {score:.1f}, " if score is not None else "") + f"~{kbps:.0f} kbit/s")
        return tried[q]

    if use_vmaf:
        best: Optional[int] = None
        a, b = lo, cap
        q = same
        for _ in range(6):                  # binary search for the largest q that still passes
            score, _kbps = trial(q)
            if score is not None and score >= need:
                best = q if best is None else max(best, q)
                a = q + 1
            else:
                b = q - 1
            if a > b:
                break
            q = (a + b) // 2
        if best is None:
            best = lo                       # nothing passed: the best quality there is
            trial(best)
    else:
        best = same if target == TARGETS[0] else smaller
        trial(best)
    score, kbps = tried[best]
    estimate = int((kbps * 1000 + AUDIO_KBPS * 1000) / 8 * duration) if duration else 0
    keep = bool(info["size"]) and estimate >= KEEP_IF_ABOVE * info["size"]
    reason = ""
    if keep:
        reason = (f"a copy would be about {estimate / 1e9:.1f} GB of the original {info['size'] / 1e9:.1f} GB — "
                  "not worth it, so the original is used")
    return {"q": best, "score": score, "metric": "VMAF" if use_vmaf else "fixed", "need": need if use_vmaf else None,
            "estimate": estimate, "source_size": info["size"], "keep_original": keep, "reason": reason,
            "tries": sorted((qq, s_, k) for qq, (s_, k) in tried.items()), "size": size_seen, "duration": duration,
            "source_width": info["width"], "source_height": info["height"]}


def padded(size: Tuple[int, int], source: Tuple[int, int], max_h: int = 1080) -> bool:
    """True if the encoder added lines (AMD's AV1 encoder pads 1080 to 1082 on some drivers)."""
    w, h = source
    if not w or not h or not size[1]:
        return False
    long_side = max_h * 16 // 9
    box_w, box_h = (long_side, max_h) if w >= h else (max_h, long_side)
    f = min(1.0, box_w / w, box_h / h)
    want_h = int(h * f) // 2 * 2
    return size[1] != want_h


def maxqda_command(src: Path, dst: Path, max_h: int = 720) -> List[str]:
    """An H.264 MP4 MAXQDA (and every player) opens: AAC sound, at most 720p (MAXQDA's own advice) or 1080p."""
    return encode_command("libx264", src, dst, 23, max_h=max_h, audio_kbps=AUDIO_KBPS)
