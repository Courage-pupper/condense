import os
import importlib.util, json, os, queue, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_empty"); os.environ["EMPTY_SEGS"] = "9"
from _compat import pipe
TD = pipe.TranscriptDocument; media = Path(os.environ["CONDENSE_TEST_MEDIA"]); ok = lambda m: print("  ✓", m)
assert pipe.is_blank_text("") and pipe.is_blank_text(" ...") and pipe.is_blank_text("…") and pipe.is_blank_text(" ♪ ") and not pipe.is_blank_text(" Yeah.")
ok("blank = empty, '...', '…', '♪'; real words are kept")

print("[1] new transcriptions are cleaned by the worker")
shutil.rmtree(media / "P02", ignore_errors=True)
base = {"respect_llc": False, "whisper_backend": "MLX (Apple Silicon GPU)", "whisper_model": "small", "whisper_lang": "en (English)",
        "diarize_mode": "Disabled (No Speaker Labels)", "fmt_srt": True, "fmt_para_txt": True}
q = queue.Queue(); pipe.PipelineWorker({"tasks": [{"id": "t1", "name": "P02", "inputs": [str(media / "P02.mp4")], "steps": {"whisper": True},
                                                   "output_dir": str(media / "P02"), "settings": base}]}, q, threading.Event()).run()
logs = [m["text"] for m in iter(lambda: q.get() if not q.empty() else None, None) if m.get("type") == "log"]
print("   ", [l for l in logs if "empty" in l or "hallucination" in l.lower()][:2])
d = TD.load(media / "P02" / "P02.transcript.json")
assert len(d.segments) == 2 and d.result["cleanup"]["empty_segments_removed"] == 10
srt = (media / "P02" / "P02.srt").read_text()
assert srt.count("-->") == 2
ok("10 empty segments removed after transcription and recorded; SRT has only the 2 real cues")
print("    runner fell back without hallucination_silence_threshold (fake mlx lacks it):",
      "hallucination_silence_threshold" not in json.dumps(d.data["provenance"]["whisper"]["runtime"]["transcribe_options"]))

print("[2] existing transcript (like the screenshot) is cleaned when opened")
segs = [{"start": 180.0, "end": 184.9, "text": " Fine. Okay. So, let's start the next game.", "speaker": "Sam",
         "words": [{"word": w, "start": 180 + i * 0.5, "end": 180.4 + i * 0.5, "probability": 0.9} for i, w in enumerate([" Fine.", " Okay.", " So,", " let's", " start"])]},
        {"start": 185.3, "end": 185.5, "text": " Yeah. Thank you.", "speaker": "Kim",
         "words": [{"word": " Yeah.", "start": 185.3, "end": 185.4, "probability": 0.3}, {"word": " Thank", "start": 185.4, "end": 185.45, "probability": 0.7}, {"word": " you.", "start": 185.45, "end": 185.5, "probability": 0.95}]}]
segs += [{"start": 185.4, "end": 185.4, "text": "", "speaker": "Kim", "words": []} for _ in range(9)]
segs.append({"start": 190.0, "end": 192.0, "text": " Next one.", "speaker": "Sam", "words": [{"word": " Next", "start": 190.0, "end": 190.5, "probability": 0.9}, {"word": " one.", "start": 190.5, "end": 192.0, "probability": 0.9}]})
old = TD.create(Path(tempfile.mkdtemp()), "demo_interview", {"segments": segs}, "m", {"srt": True, "vtt": True}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
old.save(); old.export_all()
assert (old.out_dir / "demo_interview.srt").read_text().count("-->") == 3  # writers already skip blanks
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app.var_view_mode.set("Paragraphs")
app._render_doc(TD.load(old.path))
txt = app.preview_text.content
print("    ---\n    " + txt.strip().replace("\n", "\n    ") + "\n    ---")
assert "…" not in txt and txt.count("Kim") == 1
re_d = TD.load(old.path)
assert len(re_d.segments) == 3 and re_d.data["edit_history"][-1] == {**re_d.data["edit_history"][-1], "action": "remove_empty_segments", "count": 9}
ok("9 empty 'Kim' segments removed on open, saved + logged; view shows clean paragraphs")
app.var_view_mode.set("Segments"); app._rerender()
assert app.preview_text.content.count("Kim") == 1

print("[3] deleting a junk segment by hand")
app._select_span(app.span_lookup[(1, 0)])
app.edit_text.delete("1.0", "end")
app._select_span(app.span_lookup[(2, 1)])   # click elsewhere with an emptied segment → asks, deletes, keeps the clicked word
d2 = TD.load(old.path)
assert [s["text"].strip() for s in d2.segments] == ["Fine. Okay. So, let's start", "Next one."]
assert app.word_spans[app.current_span]["word"] == "one."
assert d2.data["edit_history"][-1]["action"] == "delete_segment"
ok("emptying a segment and moving on deletes it (with a question), and the clicked word is still the one selected")
app._load_segment_into_editor(0); app._delete_segment(ask=False)
assert len(TD.load(old.path).segments) == 1
ok("Delete button removes the current segment; logged")
print("\nALL TESTS PASSED")
