"""Compression goals in the app: AV1 by default, saved with each task and shown in its summary; old tasks keep
their encoder; Copy for MAXQDA (H.264) from the Transcript menu and the Project tab."""
import os
import queue as _q
import shutil
import subprocess
import sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.media import compress as complib
from condense.gui import maxqda_copy
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]

print("[1] the goal: AV1 by default, saved with each task, in its summary")
assert app.var_hb_goal.get() == complib.GOALS[0] and app.var_hb_target.get() == complib.TARGETS[0]
snap = app._settings_snapshot()
assert snap["hb_goal"] == complib.GOALS[0] and snap["hb_target"] == complib.TARGETS[0]
summary = app._settings_summary({"settings": snap, "steps": {"compress": True}})
assert "Video: Small: AV1, up to 1080p — Looks the same" in summary, summary
old = dict(snap); old.pop("hb_goal"); old.pop("hb_target")   # a task saved before goals existed
assert "RF 22" in app._settings_summary({"settings": old, "steps": {"compress": True}}) and complib.goal_kind(old.get("hb_goal", "")) == "custom"
app.var_hb_goal.set(complib.GOALS[2])
assert "RF" in app._settings_summary({"settings": app._settings_snapshot(), "steps": {"compress": True}})
app.var_hb_goal.set(complib.GOALS[0])
ok("Small: AV1 by default; with each task; Custom and older tasks: the encoder settings as before")

print("[2] Copy for MAXQDA: H.264 720p next to the transcript, from the Transcript menu and the Project tab")
folder = Path(tempfile.mkdtemp(prefix="out_"))
src = folder / "S1.mp4"
shutil.copy(media / "P02.mp4", src)
res = {"segments": [{"start": 0.5, "end": 1.5, "text": " hello", "speaker": "A",
                     "words": [{"word": " hello", "start": 0.5, "end": 1.0, "probability": 0.9}]}]}
d = TD.create(folder, "S1", res, "m", {}, {}, {"playback": str(src)}, {"inputs": []}); d.save()
app._render_doc(TD.load(d.path))
win, st = app._maxqda_copy_dialog()
assert st["height"].get() == 720 and [j["name"] for j in st["jobs"]] == ["S1"]
st["go"](wait=True)
msgs = []
while not app.msg_queue.empty():
    m = app.msg_queue.get_nowait()
    if m.get("type") == "maxqda_copy":
        msgs.append(m); app._on_maxqda_copy_msg(m)
out = folder / "S1_MAXQDA.mp4"
info = complib.probe(out)
assert out.exists() and info["codec"] == "h264" and info["height"] <= 720 and not list(folder.glob("*.part.mp4")), info
assert msgs[-1]["event"] == "all_done" and msgs[-1]["done"] == [str(out)] and "S1_MAXQDA.mp4" in app.lbl_status.cget("text")
assert app.settings["maxqda_copy_height"] == 720
pipe.messagebox.askyesno = lambda *a, **k: False   # it exists: asked, and not replaced
win, st = app._maxqda_copy_dialog([d.path])
assert st["go"](wait=True) is None
ok("S1_MAXQDA.mp4: H.264, ≤ 720p, timed like the transcript; asks before replacing")

print("[3] nothing to copy: a clear message")
d2 = TD.create(folder, "S2", res, "m", {}, {}, {"playback": str(folder / "gone.mp4")}, {"inputs": []}); d2.save()
shown = []
pipe.messagebox.showinfo = lambda *a, **k: shown.append(a)
assert app._maxqda_copy_dialog([d2.path]) is None and "Link a video" in shown[-1][1]
ok("no video on the timeline → points to Link a video…")
