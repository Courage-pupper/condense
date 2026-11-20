import os
import importlib.util, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
TD = pipe.TranscriptDocument; media = Path(os.environ["CONDENSE_TEST_MEDIA"]); ok = lambda m: print("  ✓", m)
W = lambda ws, t0: [{"word": " " + w, "start": t0 + i, "end": t0 + i + 0.8, "probability": 0.9} for i, w in enumerate(ws)]
segs = [{"start": 10.0, "end": 12.8, "text": " So what happened", "speaker": "Kim", "words": W(["So", "what", "happened"], 10.0)},
        {"start": 20.0, "end": 21.8, "text": " Thank you.", "speaker": "Kim", "words": W(["Thank", "you."], 20.0)}]
d = TD.create(Path(tempfile.mkdtemp()), "demo_interview", {"segments": segs}, "m", {"para_txt": True, "srt": True}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
d.save()

print("[1] model")
P = pipe.launch_gui  # ensure module ok
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pt = app._parse_time
assert pt("83.4") == 83.4 and pt("1:23.4") == 83.4 and abs(pt("0:01:23.4") - 83.4) < 1e-9 and pt("1,5") == 1.5 and pt("abc") is None and pt("") is None
ok("time input: 83.4 · 1:23.4 · 0:01:23.4 · 1,5")
app.var_view_mode.set("Paragraphs")
app._render_doc(TD.load(d.path))
app.play_pos = 14.2
start, end, spk = app._insert_defaults()
assert (start, end, spk) == (14.2, 20.0, "Kim"), (start, end, spk)
ok("defaults: start = paused position, end = next segment's start, speaker = who spoke before")

print("[2] insert from the dialog path")
err = app._do_insert_segment("0:00:14.2", "0:00:18.0", "Sam", "and then we went outside to the playground")
assert err is None
dd = TD.load(d.path)
assert [s["text"].strip() for s in dd.segments] == ["So what happened", "and then we went outside to the playground", "Thank you."]
new = dd.segments[1]
assert new["speaker"] == "Sam" and new["inserted"] and new["words"][0]["start"] == 14.2 and new["words"][-1]["end"] == 18.0
assert all(w["probability"] == 1.0 and w["edited"] for w in new["words"])
assert "Sam" in dd.roster() and dd.data["edit_history"][-1]["action"] == "insert_segment"
ok("inserted in time order with the new speaker added; words spread over 14.2–18.0 s, marked typed-by-person; logged")
assert app.edit_seg_idx == 1 and app.word_spans[app.current_span]["word"] == "and"
ok("new segment opens in the editor and its first word is selected")
para = (d.out_dir / "demo_interview_paragraphs.txt").read_text()
print("    ---\n    " + para.strip().replace("\n", "\n    ") + "\n    ---")
assert para.count("Kim:") == 2 and "Sam: and then we went outside" in para
assert (d.out_dir / "demo_interview.srt").read_text().count("-->") == 3
ok("paragraphs and SRT regenerated")

print("[3] same speaker joins the paragraph; validation")
assert app._do_insert_segment("0:00:13.0", "", "Kim", "uh, at the break") is None
dd = TD.load(d.path)
assert [p["speaker"] for p in dd.paragraphs()] == ["Kim", "Sam", "Kim"] and dd.paragraphs()[0]["text"] == "So what happened uh, at the break"
ok("empty End → sensible length; same-speaker insert merges into the paragraph")
assert app._do_insert_segment("x", "1:00", "Kim", "hi") and app._do_insert_segment("1:00", "1:02", "Kim", "   ")
ok("bad time or empty text → message in the dialog, nothing inserted")
print("\nALL TESTS PASSED")
