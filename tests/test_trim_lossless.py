"""Lossless (stream-copy) clips and cuts stay in sync with their transcript, also for camera files whose clock doesn't
start at 0 (MP4 with a start offset, .MTS) — these used to come out seconds off."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.media import trim  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")

EVENT = 23.0  # a white flash and a beep, on the recording's timeline


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def recordings(tmp_path_factory):
    d = tmp_path_factory.mktemp("lossless")
    base = d / "base.mp4"
    # 29.97 fps, B-frames and a keyframe every 5 s, like a camera
    ff("-f", "lavfi", "-i", "color=c=black:s=160x120:r=30000/1001:d=40", "-f", "lavfi", "-i", "sine=f=1000:d=40:sample_rate=48000",
       "-filter_complex", f"[0:v]drawbox=c=white:t=fill:enable='between(t,{EVENT},{EVENT + 0.2})'[v];"
                          f"[1:a]volume=enable='not(between(t,{EVENT},{EVENT + 0.2}))':volume=0[a]",
       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-bf", "2", "-g", "150", "-keyint_min", "150",
       "-sc_threshold", "0", "-c:a", "aac", str(base))
    offset = d / "offset.mp4"  # the file's clock starts at 3.7 s
    ff("-i", str(base), "-c", "copy", "-output_ts_offset", "3.7", str(offset))
    mts = d / "camera.mts"  # MPEG-TS starts at 1.4 s, and a copy keeps some sound from before the keyframe
    ff("-i", str(base), "-c", "copy", "-f", "mpegts", str(mts))
    return {"plain": base, "offset": offset, "mts": mts}


def event_in(path: Path):
    """Where the flash and the beep are in a file (seconds from its start)."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                          "-af", "silencedetect=n=-30dB:d=0.05", "-f", "null", "-"], capture_output=True, text=True).stderr
    flash = next(float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)[^\n]*\n[^\n]*YAVG=([\d.]+)", err) if float(m.group(2)) > 128)
    beep = float(re.search(r"silence_end: ([\d.]+)", err).group(1))
    return flash, beep


def in_clip(kept, t):
    """Where time t of the recording is in the written file, by the parts write_trimmed says it wrote."""
    off = 0.0
    for a, b in kept:
        if a <= t < b:
            return off + t - a
        off += b - a
    raise AssertionError(f"{t} not in {kept}")


def test_keyframes_count_from_the_start_of_the_file(recordings):
    for name in ("plain", "offset", "mts"):
        keys = trim.keyframes(recordings[name])
        assert keys[0] < 0.1 and abs(keys[1] - 5.005) < 0.1, (name, keys[:3])


@pytest.mark.parametrize("name", ["plain", "offset", "mts"])
@pytest.mark.parametrize("parts", [[(17.3, 30.0)], [(0.0, 8.0), (21.1, 30.0)]], ids=["episode", "cut"])
def test_lossless_copy_matches_the_transcript_timing(recordings, tmp_path, name, parts):
    src = recordings[name]
    res = trim.write_trimmed(src, tmp_path / f"out{src.suffix}", parts, "lossless")
    want = in_clip(res["kept"], EVENT)
    flash, beep = event_in(tmp_path / f"out{src.suffix}")
    # within about two frames (what "exact", re-encoded clips reach in the same measurement); was 0.1–3.8 s
    assert abs(flash - want) < 0.1 and abs(beep - want) < 0.1, (res["kept"], want, flash, beep)
