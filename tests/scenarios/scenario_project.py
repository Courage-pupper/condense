import os
import csv, queue, shutil, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_basic")
from _compat import pipe
from condense.core import project as proj
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

def seg(t, text, spk, prob=0.95):
    ws = text.split()
    return {"start": t, "end": t + len(ws) * 0.5, "text": " " + text, "speaker": spk,
            "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": prob} for i, w in enumerate(ws)]}

study = Path(tempfile.mkdtemp(prefix="study_"))
def make(sub, name, segs):
    d = study / sub; d.mkdir(parents=True)
    doc = TD.create(d, name, {"segments": segs}, "small", {}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
    doc.save(); return doc

d1 = make("day1/group A", "A1", [seg(0, "Welcome to the workshop.", "Facilitator"), seg(3, "Hi.", "SPEAKER_01", prob=0.3),
                                  seg(5, "We draw today.", "Facilitator")])
d2 = make("day2", "B1", [seg(0, "Hello again.", "SPEAKER_00"), seg(2, "I made a boat.", "SPEAKER_01")])

print("[1] project file, speaker order, overview")
assert proj.find_project_file(d1.path.parent) is None
proj.save_project(study, ["Facilitator", "Co-facilitator", "Robin", "Facilitator", "  "])
f = proj.find_project_file(d1.path.parent)
assert f == study / proj.PROJECT_FILE and proj.load_project(f)["speakers"] == ["Facilitator", "Co-facilitator", "Robin"]
assert proj.effective_roster(["SPEAKER_01", "Facilitator"], ["Facilitator", "Robin"]) == ["Facilitator", "Robin", "SPEAKER_01"]
d = TD.load(d1.path); d.set_checked(0, True); d.set_segment_note(2, "drawing starts"); d.cut_segments([1]); d.save()
rows = {r["name"]: r for r in proj.overview(study)}
assert set(rows) == {"A1", "B1"}
a1 = rows["A1"]
assert a1["checked_pct"] == round(2.0 * 100 / 3.5) and a1["notes"] == 1 and a1["cut_s"] == 0.5 and a1["uncertain_pct"] == 0
assert rows["B1"]["generic_speakers"] == 2 and rows["B1"]["checked_pct"] == 0
out_csv = proj.write_overview_csv(list(rows.values()), study / "overview.csv")
assert [r["name"] for r in csv.DictReader(open(out_csv))] == ["A1", "B1"]
ok("nearest condense-project.json, de-duplicated speakers, project-first order, per-transcript stats, CSV")

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True

print("[2] the project's speakers in the editor: same number keys in every session")
app._render_doc(TD.load(d2.path))
assert app._roster()[:3] == ["Facilitator", "Co-facilitator", "Robin"] and app._roster()[3:] == ["SPEAKER_00", "SPEAKER_01"]
chips = [w.cget("text") for w in app.spk_chips._items]
assert chips[1:4] == ["1  Facilitator", "2  Co-facilitator", "3  Robin"], chips
app._goto_segment(0); app.preview_text._binds["<Key-1>"](None)
assert TD.load(d2.path).segments[0]["speaker"] == "Facilitator"
app._goto_segment(1); app.preview_text._binds["<Key-3>"](None)
assert TD.load(d2.path).segments[1]["speaker"] == "Robin"
app._render_doc(TD.load(d1.path))
assert app._roster()[0] == "Facilitator"
ok("project speakers first in chips and number keys, also when not yet used in that transcript")

print("[3] project overview window and speaker list editor")
win, st = app._project_overview()
st["v_dir"].set(str(study)); rows_ui = st["refresh"]()
assert len(rows_ui) == 2 and "2 transcript(s)" in st["lbl"].cget("text") and "Facilitator, Co-facilitator, Robin" in st["lbl"].cget("text")
st["export"]()
assert (study / "condense-overview.csv").exists()
win2, sp = app._project_speakers_dialog(study)
sp["add"]("Sam"); sp["lst"].selection_set(3); sp["move"](-1)
assert sp["names"] == ["Facilitator", "Co-facilitator", "Sam", "Robin"]
sp["from_transcripts"]()
assert "SPEAKER_00" not in sp["names"]
sp["save"]()
assert proj.load_project(study / proj.PROJECT_FILE)["speakers"][:4] == ["Facilitator", "Co-facilitator", "Sam", "Robin"]
assert app._roster()[2] == "Sam"
ok("overview totals + CSV; speaker list: add, reorder, import names (not SPEAKER_xx), save → editor follows")

print("[4] audio cleanup for Whisper (real ffmpeg filters)")
for cleanup in ("Level quiet voices", "Reduce noise + level voices"):
    out = media / f"clean_{cleanup.split()[0]}"
    shutil.rmtree(out, ignore_errors=True)
    task = {"id": "c1", "name": "P02", "inputs": [str(media / "P02.mp4")], "steps": {"whisper": True}, "output_dir": str(out),
            "settings": {"audio_format": "WAV (16kHz Mono - Optimal for Whisper)", "whisper_backend": "MLX (Apple Silicon GPU)",
                         "whisper_model": "small", "whisper_lang": "en (English)", "diarize_mode": "Disabled (No Speaker Labels)",
                         "fmt_srt": True, "audio_cleanup": cleanup}}
    q = queue.Queue()
    pipe.PipelineWorker({"tasks": [task]}, q, threading.Event()).run()
    logs = "\n".join(m.get("text", "") for m in list(q.queue) if m.get("type") == "log")
    doc = TD.load(out / "P02.transcript.json")
    rec = doc.data["provenance"]["processing"]["whisper_audio_cleanup"]
    assert rec["setting"] == cleanup and "dynaudnorm" in rec["ffmpeg_filter"], rec
    assert f"Audio cleanup for Whisper: {cleanup}." in logs and "ERROR" not in logs, logs[-1500:]
summary = app._settings_summary({"steps": {"whisper": True}, "inputs": ["x"], "settings": dict(task["settings"], audio_cleanup="Level quiet voices")})
assert "audio cleanup: level quiet voices" in summary
ok("both cleanup levels run through ffmpeg before Whisper and are recorded in the transcript's provenance")
