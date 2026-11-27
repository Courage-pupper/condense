"""Project ▸ Name Recordings by Pattern…: the preview (patterns, left out, written by hand), Rename (the open transcript
opens again where it is now), the new interview clip pattern, and Undo."""
import os
import sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import project as proj, renaming as rn
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
import shutil

root = Path(tempfile.mkdtemp(prefix="study_")) / "Study"
proj.create_project(root)
proj.update_project(root, levels=["Day", "Group"], structure={
    "sessions": "{Day}/{Group}/Session Recordings",
    "interviews": "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}"})
seg = {"start": 0.0, "end": 1.0, "text": " hi", "speaker": "A", "words": [{"word": " hi", "start": 0.0, "end": 0.5, "probability": 0.9}]}
docs = {}
for g in (4, 5):
    s = root / "Day 3 2026-08-12" / f"Group {g}" / "Session Recordings"
    s.mkdir(parents=True)
    v = s / "final video.mp4"
    shutil.copy(media / "P02.mp4", v)
    (s / "final video").mkdir()
    d = TD.create(s / "final video", "final video", {"segments": [seg]}, "small", {}, {}, {"video": str(v)}, {"inputs": [{"path": str(v)}]})
    d.save()
    docs[g] = d
i = root / "Day 3 2026-08-12" / "Group 4" / "Semi-structured Interviews"
i.mkdir(parents=True)
shutil.copy(media / "P02.mp4", i / "G4D3_I1.mp4")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
app._render_doc(TD.load(docs[4].path))

print("[1] the preview")
win, st = app._rename_dialog()
st["use_interview"].set(True)
st["interview"].set("G{group}_Day{day}_{date}_I{n}")
n = st["preview"](wait=True)
rows = {(r["role"], r["old"], " ".join(v for _l, v in r["levels"])): r for r in st["rows"]}
s4 = rows[("session", "final video", "Day 3 2026-08-12 Group 4")]
s5 = rows[("session", "final video", "Day 3 2026-08-12 Group 5")]
iv = rows[("interview", "G4D3_I1", "Day 3 2026-08-12 Group 4")]
assert s4["new"] == "G4_Day3_2026.08.12_Session" and s5["new"] == "G5_Day3_2026.08.12_Session"
assert iv["new"] == "G4_Day3_2026.08.12_I1" and n == 3
k5 = str(st["rows"].index(s5))
st["toggle"](k5)                                   # left out
assert st["rows"][int(k5)]["use"] is False
k4 = str(st["rows"].index(s4))
st["edit"](k4, "G4_Day3_2026.08.12_Session_cameraA")  # written by hand
assert st["rows"][int(k4)]["new"] == "G4_Day3_2026.08.12_Session_cameraA" and st["rows"][int(k4)]["ok"]
ok("the preview: names from the pattern; a row left out; a name written by hand")

print("[2] Rename: the open transcript opens again where it is now; new clips follow the new pattern")
st["new_clips"].set(True)
res = st["rename"](wait=True)
assert res["renamed"] == 2
new4 = root / "Day 3 2026-08-12" / "Group 4" / "Session Recordings" / "G4_Day3_2026.08.12_Session_cameraA"
assert Path(app.active_doc.path) == new4 / "G4_Day3_2026.08.12_Session_cameraA.transcript.json"
assert app.active_doc.base_name == "G4_Day3_2026.08.12_Session_cameraA" and app.active_doc.find_video().exists()
assert TD.load(docs[5].path).base_name == "final video"                 # left out: unchanged
assert (i / "G4_Day3_2026.08.12_I1.mp4").exists()
assert proj.project_settings(root)["structure"].name_alts[0] == "G{group}_Day{day}_{date}_I{n}"
assert proj.load_project(root / proj.PROJECT_FILE)["rename_patterns"]["session"] == rn.DEFAULT_PATTERNS["session"]
ok("renamed (2 recordings); the open transcript followed; interview clips are named the new way from now on")

print("[3] Undo")
n = st["undo"]()
assert n >= 4 and Path(app.active_doc.path) == docs[4].path and app.active_doc.base_name == "final video"
assert (i / "G4D3_I1.mp4").exists() and not rn.last_was_rename(root)
ok("undo: the old names again, and the open transcript with them")
print("\nALL TESTS PASSED")
