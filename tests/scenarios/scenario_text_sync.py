import os
import importlib.util, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
TD = pipe.TranscriptDocument; media = Path(os.environ["CONDENSE_TEST_MEDIA"]); ok = lambda m: print("  ✓", m)
def W(ws, t0, p=0.9): return [{"word": " " + w, "start": round(t0 + i * 0.5, 2), "end": round(t0 + i * 0.5 + 0.4, 2), "probability": p} for i, w in enumerate(ws)]
# as in the screenshots: text and timed words of segments 35/36 are offset
segs = [
    {"start": 95.0, "end": 96.5, "text": " Pick the colour or...", "speaker": "Robin", "words": W(["Pick", "the", "colour", "or..."], 95.0)},
    {"start": 96.6, "end": 100.1, "text": " ...you have to paint it...", "speaker": "Robin", "words": W(["to", "paint", "it...", "Mmhm."], 96.6)},
    {"start": 100.1, "end": 102.0, "text": " Mmhm.", "speaker": "Sam", "words": W(["Right,", "for", "example", "you", "can", "draw?"], 100.1)},
]
d = TD.create(Path(tempfile.mkdtemp()), "demo_interview", {"segments": segs}, "m", {"srt": True, "para_txt": True}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
d.save(); d.export_all()
assert "Mmhm." in (d.out_dir / "demo_interview.srt").read_text().split("-->")[3]   # the old exports had the shifted text

holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app.var_view_mode.set("Paragraphs")
app._render_doc(TD.load(d.path))
dd = TD.load(d.path)
assert [s["text"].strip() for s in dd.segments] == ["Pick the colour or...", "to paint it... Mmhm.", "Right, for example you can draw?"]
assert dd.segments[1]["text_whisper"].strip() == "...you have to paint it..." and dd.data["edit_history"][-1]["action"] == "sync_text_with_words"
ok("on open: segment text = its timed words; Whisper's differing text kept (text_whisper); logged")

app._select_span(app.span_lookup[(1, 3)])     # click "Mmhm."
assert app.edit_seg_idx == 1 and app.edit_text.content == "to paint it... Mmhm."
a, b = app.preview_text.tags["EDITSEG"]
assert app.preview_text.get(a, b).strip() == "to paint it... Mmhm."
print("    hint:", app.lbl_edit_hint.cget("text")[:95], "…")
assert "...you have to paint it..." in app.lbl_edit_hint.cget("text")
ok("click 'Mmhm.' → editor shows exactly the highlighted words; hint shows Whisper's original text")
app._select_span(app.span_lookup[(2, 0)])     # click "Right,"
assert app.edit_seg_idx == 2 and app.edit_text.content == "Right, for example you can draw?" and app.var_seg_speaker.get() == "Sam"
ok("click 'Right,' → editor shows that segment's words and speaker")

srt = (d.out_dir / "demo_interview.srt").read_text()
para = (d.out_dir / "demo_interview_paragraphs.txt").read_text()
print("    paragraphs:", para.strip().replace("\n\n", " | "))
assert "Mmhm.\n" not in srt.split("-->")[3] and "Right, for example" in srt and para.startswith("Robin: Pick the colour or... to paint it... Mmhm.")
ok("exports regenerated from the words")

app._select_span(app.span_lookup[(1, 0)])
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "you have to paint it now. Mmhm.")
app._save_segment_edit()
w = TD.load(d.path).segments[1]["words"]
assert [x["word"].strip() for x in w] == ["you", "have", "to", "paint", "it", "now.", "Mmhm."] and w[-1]["start"] == 98.1
assert app.lbl_edit_hint.cget("text") == app.EDIT_HINT
ok("correcting the segment keeps the timing of unchanged words ('Mmhm.' still at 1:38.1); hint clears once edited")
print("\nALL TESTS PASSED")
