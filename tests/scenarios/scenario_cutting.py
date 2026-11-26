import os
import json, queue, re, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import cuts as cutlib
from condense.media import trim as trimlib
from condense.media.losslesscut import LosslessCutHelper
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument

work = Path(tempfile.mkdtemp(prefix="cut_"))
video = work / "Workshop demo.mp4"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=20",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:v", "libx264", "-g", "50", "-keyint_min", "50",
                "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)

def seg(t0, t1, text, spk):
    ws = text.split(); step = (t1 - t0) / len(ws)
    return {"start": t0, "end": t1, "text": " " + text, "speaker": spk,
            "words": [{"word": " " + w, "start": round(t0 + i * step, 2), "end": round(t0 + (i + 1) * step, 2), "probability": 0.95} for i, w in enumerate(ws)]}

res = {"segments": [seg(0.5, 3.0, "Hello and welcome everyone.", "Robin"), seg(3.5, 5.0, "Shall we start?", "Robin"),
                    seg(5.5, 7.0, "Wait, my phone rings.", "Sam"), seg(7.2, 9.0, "Sorry about that, back now.", "Sam"),
                    seg(10.0, 13.0, "So what did you draw?", "Robin"), seg(13.5, 17.0, "A blue house with a garden.", "Sam")]}
out = work / "out"; out.mkdir()
d = TD.create(out, "Workshop demo", res, "small", {}, {}, {"playback": str(video), "video": str(video)}, {"inputs": [{"path": str(video)}]})
d.save()

print("[1] the cut model")
assert cutlib.normalize([(10, 20), (19.98, 25), (5, 5)]) == [(10.0, 25.0)]
assert cutlib.keep_intervals([(5.3, 8.0), (12.5, 13.0)], 20.0) == [(0.0, 5.3), (8.0, 12.5), (13.0, 20.0)]
m = cutlib.TimeMap([(0.0, 5.3), (8.0, 12.5), (13.0, 20.0)])
assert tuple(round(m(t), 6) for t in (4.0, 6.0, 10.0, 14.0, 20.0)) == (4.0, 5.3, 7.3, 10.8, 16.8)
assert cutlib.subtract([(10.0, 25.0)], 12, 14) == [(10.0, 12.0), (14.0, 25.0)]
(work / "t.llc").write_text(cutlib.llc_text("a'b.mp4", [(0.0, 5.3), (8.0, 20.0)]))
assert LosslessCutHelper.parse_llc_segments(work / "t.llc") == [(0.0, 5.3), (8.0, 20.0)]
ok("merge, keep parts, time mapping, restore part of a cut, LosslessCut project round-trip")

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._render_doc(TD.load(d.path))
pt = app.preview_text
infos = []
pipe.messagebox.showinfo = lambda *a, **k: infos.append(a)
pipe.messagebox.askyesno = lambda *a, **k: True

print("[2] cut with X, view, exports, % checked, undo")
app._goto_segment(2); app._extend_selection(2, 3)
pt._binds["<Key-x>"](None)
doc = TD.load(d.path)
assert doc.cuts == [(5.5, 9.0)] and [doc.is_segment_cut(i) for i in range(6)] == [False, False, True, True, False, False]
txt = (out / "Workshop demo.txt").read_text()
assert "phone rings" not in txt and "back now" not in txt and "Shall we start?" in txt and "blue house" in txt
srt = (out / "Workshop demo.srt").read_text()
assert "00:00:10,000" in srt                              # main exports keep the original timeline
paras = (out / "Workshop demo_paragraphs.rtf").read_text()
assert "phone" not in paras and "draw" in paras
assert "CUT" in pt.used_tags and "CUT_HDR" in pt.used_tags, pt.used_tags
assert "✂ 1 cut(s)" in app.lbl_ci_summary.cget("text"), app.lbl_ci_summary.cget("text")
assert any(it[0] == "cut" for it in app._cdt_items())
doc.set_checked(0, True); doc.set_checked(2, True); doc.save()
assert abs(doc.checked_fraction() - 2.5 / (2.5 + 1.5 + 3.0 + 3.5)) < 1e-6   # cut segment 2 does not count
app._render_doc(TD.load(d.path)); app._goto_segment(2); app._extend_selection(2, 3)
pt._binds["<Key-X>"](None)                                   # all selected are cut -> restore
assert TD.load(d.path).cuts == []
app._undo()
assert TD.load(d.path).cuts == [(5.5, 9.0)]
ok("X cuts segments 3–4 (with the pause between), struck through, left out of TXT/SRT/paragraphs, restored by X, undone by ⌘Z")

print("[3] playback skips cuts it runs into, but plays a cut you click on")
app.var_skip_cuts.set(True)
assert app._cut_skip_target(5.3, 5.6) == 9.0 and app._cut_skip_target(6.0, 6.2) is None
app.var_skip_cuts.set(False)
assert app._cut_skip_target(5.3, 5.6) is None
app.var_skip_cuts.set(True)
ok("entering a cut while playing jumps to its end; starting inside one plays it; switchable")

def run_export(mode, llc=False):
    app._start_trim_export(video, mode, llc)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            msg = app.msg_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if msg.get("type") == "trim":
            app._on_trim_msg(msg)
            if msg["event"] in ("done", "failed"):
                return msg
    raise AssertionError("trim export did not finish")

print("[4] exact trimmed export: video + re-timed transcript + record + LosslessCut project")
msg = run_export("exact", llc=True)
assert msg["event"] == "done", msg
tv = out / "Workshop demo_trimmed.mp4"
assert abs(trimlib.probe(tv)["duration"] - 16.5) < 0.15, trimlib.probe(tv)
tsrt = (out / "Workshop demo_trimmed.srt").read_text()
starts = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) -->", tsrt)
assert starts == ["00:00:00,500", "00:00:03,500", "00:00:06,500", "00:00:10,000"], starts   # 10.0 - 3.5 = 6.5
assert "phone" not in tsrt and "blue house" in tsrt
rec = json.loads((out / "Workshop demo_trimmed.cuts.json").read_text())
assert rec["mode"] == "exact" and rec["cuts"] == [[5.5, 9.0]] and rec["cuts_not_applied"] == 0
llc = video.parent / "Workshop demo-proj.llc"
assert LosslessCutHelper.find_llc_for_video(video) == llc
assert LosslessCutHelper.parse_llc_segments(llc) == [(0.0, 5.5), (9.0, 20.0)]
hist = [h["action"] for h in TD.load(d.path).data["edit_history"]]
assert "export_trimmed" in hist and infos and "Trimmed video" in infos[-1][1]
ok("16.5 s video, SRT shifted by the 3.5 s cut, .cuts.json record, Workshop demo-proj.llc for LosslessCut")

print("[5] lossless export stays in sync with what was really written")
llc.unlink()
msg = run_export("lossless")
lv = out / "Workshop demo_trimmed.mp4"
kept = msg["kept"]
assert kept[0][0] == 0.0 and kept[1][0] == 8.0, kept      # second part starts on the keyframe at 8 s (cut 5.5–9)
real = trimlib.probe(lv)["duration"]
assert abs(real - sum(b - a for a, b in kept)) < 0.2, (real, kept)
lsrt = (out / "Workshop demo_trimmed.srt").read_text()
first_after = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) -->", lsrt)[2]
expected = 10.0 - 8.0 + (kept[0][1] - kept[0][0])
assert abs(float(first_after[6:8]) + float(first_after[9:]) / 1000 - expected) < 0.01, (first_after, expected)
ok(f"lossless parts {kept}: transcript re-timed to the file actually written ({real:.2f} s)")

print("[6] export without cuts / without the recording")
d2 = TD.create(out, "No cuts", res, "small", {}, {}, {"playback": str(video)}, {"inputs": []}); d2.save()
app._render_doc(TD.load(d2.path))
n = len(infos)
assert app._export_trimmed_dialog() is None and "Nothing is cut yet" in infos[-1][1]
ok("explains instead of exporting when nothing is cut")

print("[7] trim before transcribing: mark cuts on the strip, saved as a LosslessCut project")
from condense.gui.trim_dialog import describe_trim, llc_cuts
saved = []
win, st = app._trim_dialog(video, on_saved=lambda: saved.append(True))
deadline = time.time() + 60
got_images = False
while time.time() < deadline and not got_images:
    try:
        msg = app.msg_queue.get(timeout=0.2)
    except queue.Empty:
        continue
    if msg.get("type") == "trim_ui":
        app._on_trim_ui_msg(msg)
        got_images = msg["kind"] == "images"
assert got_images and "strip" in st["imgs"] and "wave" in st["imgs"], st["imgs"].keys()
st["set_pos"](2.0); st["mark_start"](); st["set_pos"](4.0); st["mark_end"]()
st["set_pos"](18.0); st["mark_start"](); st["set_pos"](15.0); st["mark_end"]()     # backwards works too
assert st["cuts"] == [(2.0, 4.0), (15.0, 18.0)]
st["save"]()
llc = video.parent / "Workshop demo-proj.llc"
assert saved and llc.read_text().startswith(cutlib.LLC_MARKER)
assert LosslessCutHelper.parse_llc_segments(llc) == [(0.0, 2.0), (4.0, 15.0), (18.0, 20.0)]
assert describe_trim(video).startswith("3 part(s) kept, 0:00:15"), describe_trim(video)
win, st = app._trim_dialog(video)
assert st["cuts"] == [(2.0, 4.0), (15.0, 18.0)]                                     # reopened with the saved cuts
st["cuts"] = []; st["save"]()
assert not llc.exists() and describe_trim(video) == "none"                         # our own file: removed
llc.write_text("{ version: 1, mediaFileName: 'x.mp4', cutSegments: [ { start: 1, end: 10 } ] }")  # made in LosslessCut
win, st = app._trim_dialog(video)
assert st["cuts"] == [(0.0, 1.0), (10.0, 20.0)]
st["cuts"] = []; st["save"]()
assert llc.exists() and "made in LosslessCut" in infos[-1][1]                       # never deleted silently
ok("filmstrip + waveform drawn; I/O marks cuts; saved as -proj.llc; reopens; removes only its own file")
