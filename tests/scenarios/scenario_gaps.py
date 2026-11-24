"""Untranscribed stretches (what Whisper skipped) are found by their sound and re-transcribed one after another; the
transcript's right-click re-transcribes what is highlighted; Settings ▸ Long recordings reaches the runner."""
import os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_rt")
from _compat import pipe
from condense.core import gaps as gaplib
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
work = Path(tempfile.mkdtemp(prefix="gaps_"))
audio = work / "session.m4a"
# talk-loud sound where there is talk (0–26.2 s, 39.6–44.2 s), a quiet room elsewhere
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "anoisesrc=d=50:c=pink:seed=5:a=0.5,volume=eval=frame:volume='if(lt(t,26.2)+between(t,39.6,44.2),0.25+0.2*abs(sin(1.7*t)),0.0004)'",
                "-c:a", "aac", str(audio)], check=True)


def words(t0, text):
    return [{"word": " " + w, "start": round(t0 + i * 0.5, 2), "end": round(t0 + i * 0.5 + 0.4, 2), "probability": 0.9}
            for i, w in enumerate(text.split())]


segs = []
for t0, text, spk in ((0.5, "Okay everyone let's start with the boats now please", "Kim"),
                      (22.0, "This one floats really well I think so", "Robin"),
                      (40.0, "And that is the end of the session", "Kim")):
    w = words(t0, text)
    segs.append({"start": w[0]["start"], "end": w[-1]["end"], "text": "".join(x["word"] for x in w), "speaker": spk, "words": w})
out = work / "session"
out.mkdir()
d = TD.create(out, "session", {"segments": segs, "language": "en"}, "small", {}, {}, {"playback": str(audio)},
              {"inputs": [], "whisper": {"language_setting": "auto", "backend_setting": "MLX (Apple Silicon GPU)", "model": "small",
                                         "vocabulary_prompt": "Robin, Kim"}})
d.save()

print("[1] finding what Whisper skipped")
from condense.core import align
env = align.stream_envelope(audio)
found = gaplib.find_untranscribed(env, segs)
print("   ", found)
assert len(found) == 1 and abs(found[0]["start"] - 4.9) < 0.05 and abs(found[0]["end"] - 22.0) < 0.01 and found[0]["share"] > 0.8
ok("the stretch with talk but no transcript (4.9–22 s) is found; the quiet room (26–40 s) and the quiet end are not")
assert gaplib.parts(0, 25 * 60) == [(0.0, 500.0), (500.0, 1000.0), (1000.0, 1500.0)]
ok("long stretches are re-transcribed in parts of at most 10 minutes")

print("[2] the dialog re-transcribes them one after another")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._render_doc(TD.load(d.path))
win, g = app._gaps_dialog(wait=True)
assert len(g["state"]["gaps"]) == 1
logs = []
orig = app._append_log
app._append_log = lambda m, l="INFO": (logs.append(m), orig(m, l))
g["retranscribe"]()
t_end = time.time() + 120
while time.time() < t_end and (app.__dict__.get("_gap_queue") is not None or not app.msg_queue.empty()):
    app._poll_queue()
    time.sleep(0.1)
assert app.__dict__.get("_gap_queue") is None, "the queue did not finish"
opts = next(m for m in logs if "FAKE_OPTIONS" in m)
assert '"initial_prompt": "Robin, Kim"' in opts and '"language": "en"' in opts and "whisper-small" in opts, opts
dd = TD.load(d.path)
texts = [s["text"].strip() for s in dd.segments]
print("    segments now:", texts)
assert len(dd.segments) == 4 and texts[1] == "We went to the playground"
assert 4.85 <= dd.segments[1]["start"] and dd.segments[1]["end"] <= 22.3, dd.segments[1]["start"]
assert g["state"]["status"][0] == "✓ 1 segment(s)" and "done — 1 new segment(s)" in app.lbl_status.cget("text")
ok("re-transcribed with the transcript's model, language and vocabulary; the new segment fills the stretch; one undo step each")
app._undo()
assert len(TD.load(d.path).segments) == 3
app._redo()

print("[3] right-click: re-transcribe what is highlighted")
app._render_doc(TD.load(d.path))
pt = app.preview_text
seg = 2  # "This one floats …"
first = app.span_lookup[(seg, 2)]
last = app.span_lookup[(seg, 6)]
a, b = app.word_spans[first], app.word_spans[last]
pt.tags["sel"] = (f"{a['line']}.{a['c0'] + 1}", f"{b['line']}.{b['c0'] + 2}")  # from inside "floats" to inside "think"
rng = app._selection_time_range()
assert rng == (a["start"], b["end"]), rng
asked = []
app._retranscribe_dialog = lambda r=None: asked.append(r)
m = app._transcript_menu(type("E", (), {"x_root": 5, "y_root": 5, "x": 1, "y": 1})())
label = next(lb for lb in m.labels() if lb.startswith("Re-transcribe this passage"))
assert pipe.short_ts(a["start"])[:-2] in label, label
m.invoke(label)
assert asked == [rng]
pt.tags.pop("sel")
app.sel_segs = []
app._span_at = lambda e: app.span_lookup[(0, 1)]
m = app._transcript_menu(type("E", (), {"x_root": 5, "y_root": 5, "x": 1, "y": 1})())
m.invoke(next(lb for lb in m.labels() if lb.startswith("Re-transcribe")))
assert asked[-1] == (TD.load(d.path).segments[0]["start"], TD.load(d.path).segments[0]["end"])
ok("the highlighted words' times (first to last word) are suggested; without a highlight, the segment under the mouse")

print("[4] Settings ▸ Long recordings")
w = pipe.PipelineWorker({"tasks": []}, app.msg_queue, __import__("threading").Event())
for setting, mode in (("Split at pauses if over 1 hour", "auto"), ("Always split at pauses", "on"), ("In one go (Whisper's default)", "off"), (None, "auto")):
    w.config = {"whisper_chunking": setting}
    assert w.chunking_mode() == mode, (setting, w.chunking_mode())
assert app._settings_snapshot()["whisper_chunking"] == "Split at pauses if over 1 hour"
ok("the setting is saved per task and reaches Whisper (auto: recordings over an hour)")
print("\nALL TESTS PASSED")
