import os
import importlib.util, json, os, queue, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2  # noqa: E402

HOME = Path(tempfile.mkdtemp(prefix="home_"))
(HOME / "Desktop").mkdir()
os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_basic")  # fake mlx_whisper for the Whisper subprocess
from _compat import pipe
ok = lambda msg: print("  ✓", msg)

print("[1] timestamps")
F = pipe.RTFConfidenceWriter.format_timestamp
assert F(59.9996, True) == "00:01:00,000", F(59.9996, True)
assert F(3599.9996) == "01:00:00.000"
assert F(83.4567, True) == "00:01:23,457"
ok("no more 00:00:60 rollover")
assert pipe.RTFConfidenceWriter._escape_rtf("😀") == "\\u-10179?\\u-8704?"
ok("emoji escaped as surrogate pair in RTF")

print("[2] subprocess helper")
w = pipe.PipelineWorker({}, queue.Queue(), threading.Event())
try:
    w.run_checked(["ffmpeg", "-hide_banner", "-i", "/nope/missing.mp4", "/tmp/x.wav"], "Test conversion")
    raise SystemExit("should have failed")
except RuntimeError as e:
    assert "Test conversion failed" in str(e) and "No such file" in str(e), e
    ok("error carries ffmpeg's own message: " + str(e).splitlines()[-1][:70])
ev = threading.Event()
w2 = pipe.PipelineWorker({}, queue.Queue(), ev)
threading.Timer(0.6, ev.set).start()
t0 = time.time()
try:
    w2.run_checked(["sleep", "20"], "sleep")
except InterruptedError:
    assert time.time() - t0 < 3
    ok(f"Stop interrupts a running tool in {time.time() - t0:.1f}s")

print("[3] merge with apostrophe + format check")
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
tmp = Path(tempfile.mkdtemp())
out = Path(tempfile.mkdtemp())
cfg = {"hb_container": "MP4", "merged_filename": "merged_session", "respect_llc": False}
w = pipe.PipelineWorker(cfg, queue.Queue(), threading.Event())
sig1, sig2, sig3 = (w.probe_media_signature(media / n) for n in ("Interview O'Brien.mp4", "P02.mp4", "P03_48k.mp4"))
assert sig1 == sig2 and sig2 != sig3, (sig1, sig3)
ok(f"44.1 kHz vs 48 kHz audio detected as not stream-copyable: {sig2[1]} vs {sig3[1]}")
merged, rec = w.run_stage_merge([media / "Interview O'Brien.mp4", media / "P02.mp4"], out, tmp, compress_also_enabled=False)
dur = w.probe_video_duration(merged)
assert merged.exists() and 6.5 < dur < 7.5, dur
ok(f"merged '{merged.name}' via {rec['method']} ({dur:.1f}s) despite the apostrophe")

print("\nALL TESTS PASSED")
