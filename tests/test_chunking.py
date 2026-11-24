"""Splitting long recordings at pauses for Whisper (asr/chunking.py)."""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.asr import chunking as ch  # noqa: E402
from condense.asr.runner import WHISPER_RUNNER  # noqa: E402

FPS = 100  # loudness values per second


def loudness(spec, seed=1):
    """dB per 10 ms from [(seconds, level_db)], with a little jitter like real sound."""
    rng = np.random.default_rng(seed)
    parts = [np.full(int(sec * FPS), lvl, np.float32) for sec, lvl in spec]
    db = np.concatenate(parts)
    return db + rng.normal(0, 1.5, len(db)).astype(np.float32)


def test_pieces_cover_everything_and_cut_in_pauses():
    # 20 minutes of talk (-25 dB) with a short pause (-60 dB) every 50 s
    spec = []
    for _ in range(24):
        spec += [(48.0, -25.0), (2.0, -60.0)]
    db = loudness(spec)
    plan = ch.plan_chunks(db, target_s=240, max_s=360)
    assert plan[0][0] == 0.0 and abs(plan[-1][1] - 1200.0) < 0.05
    assert all(abs(plan[i][1] - plan[i + 1][0]) < 1e-6 for i in range(len(plan) - 1))  # nothing in between is lost
    assert all(180 - 1e-6 <= b - a <= 360 + 1e-6 for a, b in plan[:-1])
    for a, b in plan[:-1]:
        assert (b % 50.0) >= 47.9, f"cut at {b:.2f} s is not in a pause"


def test_long_silence_is_left_out_but_noise_is_kept():
    db = loudness([(300.0, -25.0), (120.0, -70.0), (300.0, -25.0), (120.0, -40.0), (60.0, -25.0)])
    plan = ch.plan_chunks(db, target_s=240, max_s=360, skip_quiet_s=30, pad_s=1.0)
    covered = sum(b - a for a, b in plan)
    assert abs(covered - (900.0 - 118.0)) < 3, plan  # the silence (minus 1 s each side) is skipped …
    assert not any(a < 360 < b for a, b in plan), plan  # the middle of the silence
    assert any(a <= 800 <= b for a, b in plan), plan  # … the -40 dB noise (not silence) is transcribed


def test_short_recording_is_one_piece():
    assert ch.plan_chunks(loudness([(90.0, -25.0)]), target_s=240, max_s=360) == [(0.0, 90.0)]


def test_shift_result_moves_segments_and_words():
    r = {"segments": [{"start": 1.0, "end": 2.0, "seek": 0, "text": " a", "words": [{"word": " a", "start": 1.0, "end": 2.0}]}]}
    out = ch.shift_result(r, 600.0)
    assert out[0]["start"] == 601.0 and out[0]["words"][0]["end"] == 602.0 and "seek" not in out[0]
    assert r["segments"][0]["start"] == 1.0  # the original is not changed


def test_wav_loudness_and_slices(tmp_path):
    sr = 16000
    t = np.arange(sr * 3) / sr
    x = np.where(t < 1.5, 0.3 * np.sin(2 * np.pi * 300 * t), 0.0)
    pcm = (x * 32767).astype("<i2")
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    db, dur = ch.wav_loudness(path, block_s=0.7)  # blocks that do not divide into 10 ms frames evenly
    assert abs(dur - 3.0) < 1e-6 and len(db) == 300
    assert db[:150].min() > -20 and db[150:].max() < -90
    sl = ch.read_wav_slice(path, 1.0, 2.0)
    assert len(sl) == sr and abs(sl[:100]).max() > 0.1 and abs(sl[-100:]).max() == 0.0


def test_runner_contains_the_chunking_code():
    compile(WHISPER_RUNNER, "<runner>", "exec")
    assert "def plan_chunks" in WHISPER_RUNNER and "#@CHUNKING@" not in WHISPER_RUNNER


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg is not installed")
def test_runner_transcribes_in_pieces_with_fake_whisper(tmp_path):
    fakes = Path(__file__).parent / "fakes" / "mlx_basic"
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=d=40:c=pink:seed=3:a=0.5,volume=eval=frame:volume='0.3+0.3*abs(sin(t))'",
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
    out = tmp_path / "out.json"
    env = dict(__import__("os").environ, PIPELINE_CHUNKING="on", PIPELINE_CHUNK_SECONDS="8", PYTHONPATH=str(fakes))
    res = subprocess.run([sys.executable, "-c", WHISPER_RUNNER, str(wav), str(wav), "small", "auto", str(out), "Disabled", "0",
                          "MLX (Apple Silicon GPU)"], env=env, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    import json
    r = json.loads(out.read_text())
    pieces = r["chunking"]["pieces"]
    assert len(pieces) >= 3 and pieces[0][0] == 0.0 and abs(pieces[-1][1] - 40.0) < 0.05
    starts = [s["start"] for s in r["segments"]]
    assert starts == sorted(starts) and len(starts) == 2 * len(pieces)  # the fake gives 2 segments per piece
    assert [s["id"] for s in r["segments"]] == list(range(len(starts)))
    assert all(abs(starts[2 * k] - a) < 0.01 for k, (a, b) in enumerate(pieces))  # each piece's text at its own time
    assert "pieces split at pauses" in res.stdout
