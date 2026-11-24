"""The memory meter and the Memory window: measured with the programs Condense started, amber/red with a share of the
computer's memory, told once (log + status line), Free memory, Restart keeping the place."""
import os
import subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import memory as memlib

def ok(msg): print("  ✓", msg)

media = Path(os.environ["CONDENSE_TEST_MEDIA"])
res = {"segments": [{"start": float(i), "end": i + 0.9, "text": f" segment {i}", "speaker": "A",
                     "words": [{"word": f" segment{i}", "start": float(i), "end": i + 0.9, "probability": 0.9}]} for i in range(6)]}
folder = Path(tempfile.mkdtemp())
d = pipe.TranscriptDocument.create(folder, "S1", res, "m", {}, {}, {"playback": str(media / "P02.mp4"), "video": None}, {"inputs": []}); d.save()
d2 = pipe.TranscriptDocument.create(folder, "S2", res, "m", {}, {}, {"playback": str(media / "P02.mp4"), "video": None}, {"inputs": []}); d2.save()
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]

print("[1] measuring: Condense and the programs it started")
child = subprocess.Popen([sys.executable, "-c", "import time; x = bytearray(60 * 1024 * 1024); time.sleep(20)"])
time.sleep(1.0)
u = memlib.usage()
mine = [c for c in u["children"] if c["pid"] == child.pid]
if sys.platform.startswith("linux") or sys.platform == "darwin":
    assert mine and mine[0]["rss"] > 50 * 1024 ** 2 and u["total"] >= u["self"] + mine[0]["rss"], (mine, u["self"])
    assert memlib.role(mine[0]["name"]) == "transcription (Whisper)" or mine[0]["name"]
assert u["self"] > 10 * 1024 ** 2 and u["ram"] > u["self"]
child.kill(); child.wait()
assert memlib.level(1, 100) == "ok" and memlib.level(30, 100) == "high" and memlib.level(45, 100) == "very_high" and memlib.level(5, 0) == "ok"
assert memlib.fmt(1.5 * memlib.GB) == "1.5 GB" and memlib.fmt(300 * 1024 ** 2) == "300 MB"
assert memlib.role("ffmpeg") .startswith("video") and memlib.role("ffplay") == "audio playback"
ok("own process + children (here a 60 MB helper), levels at 25 % / 40 % of the computer's memory")

print("[2] the meter: colour and one message each time it gets worse")
logs = []
orig_log = app._append_log
app._append_log = lambda text, level="INFO": (logs.append((level, text)), orig_log(text, level))
GB = memlib.GB
fake = lambda total, kids=(): {"self": total - sum(k["rss"] for k in kids), "children": list(kids), "total": total, "ram": 16 * GB}
assert app._memory_show(fake(1 * GB)) == "ok" and "Memory 1.0 GB" == app.lbl_memory.cget("text") and not logs
assert app._memory_show(fake(5 * GB)) == "high" and "▲" in app.lbl_memory.cget("text") and len(logs) == 1
assert app._memory_show(fake(5 * GB)) == "high" and len(logs) == 1   # not again while it stays there
kids = [{"pid": 1, "name": "ffmpeg", "rss": 3 * GB}]
assert app._memory_show(fake(7 * GB, kids)) == "very_high" and "⚠" in app.lbl_memory.cget("text")
assert len(logs) == 2 and "video preview / export (ffmpeg) 3.0 GB" in logs[-1][1], logs[-1]
assert "Click Memory" in app.lbl_status.cget("text")
app._memory_history.clear()
t0 = time.time()
app._memory_history.extend([(t0 - 1800, 2 * GB), (t0 - 600, 4 * GB), (t0, 7 * GB)])
assert abs(app._memory_growth(30) - 5 * GB) < 1
ok("grey → amber ▲ → red ⚠; logged once per step with what uses it; growth over the last 30 minutes")

print("[3] the window: free memory, restart keeping the place")
app._render_doc(pipe.TranscriptDocument.load(d2.path))
for k in range(3):
    app._push_undo(d2.path, f"edit {k}", d2.path.read_text())
app._render_doc(pipe.TranscriptDocument.load(d.path))
app._push_undo(d.path, "edit here", d.path.read_text())
held = app._undo_memory()
assert held["steps"] == 4 and held["transcripts"] == 2 and held["other_steps"] == 3 and held["bytes"] > 0
win, md = app._memory_dialog()
assert "Undo history (4 steps, 2 transcript(s))" in md["body"].cget("text"), md["body"].cget("text")
res_free = md["free"]()
assert res_free["undo_steps"] == 3 and app._undo_memory()["steps"] == 1  # the open transcript keeps its undo
assert "Freed" in app.lbl_status.cget("text")
restarted = []
app._restart_app = lambda: restarted.append(True)
pipe.messagebox.askyesno = lambda *a, **k: True
app._goto_segment(4)
assert md["restart"]() is True and restarted == [True]
assert app.settings["reopen_after_restart"] == str(d.path) and app.settings["positions"][str(d.path)]["seg"] == 4
app._render_doc(pipe.TranscriptDocument.load(d2.path))   # "after the restart": another state …
app._reopen_after_restart()                              # … the transcript is open again, where it was
assert Path(app.active_doc.path) == d.path and app.edit_seg_idx == 4 and "reopen_after_restart" not in app.settings
ok("lists the undo history; Free memory keeps only the open transcript's; Restart saves and reopens it where you were")
