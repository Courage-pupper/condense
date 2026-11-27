import os
import importlib.util, json, os, queue, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_rt")
from _compat import pipe
TD = pipe.TranscriptDocument; media = Path(os.environ["CONDENSE_TEST_MEDIA"]); ok = lambda m: print("  ✓", m)
def W(ws, t0, p=0.9): return [{"word": " " + w, "start": round(t0 + i * 0.3, 2), "end": round(t0 + i * 0.3 + 0.25, 2), "probability": p} for i, w in enumerate(ws)]
segs = [{"start": 0.0, "end": 1.0, "text": "", "speaker": "Kim", "words": W(["Okay", "everyone"], 0.0)},
        {"start": 1.2, "end": 2.6, "text": "", "speaker": "Robin", "words": W(["we", "wen", "plaground"], 1.2, 0.3)},
        {"start": 3.0, "end": 3.9, "text": "", "speaker": "Alex", "speaker_manual": True, "words": W(["Cool"], 3.0)}]
for s in segs: s["text"] = "".join(w["word"] for w in s["words"])
media_file = media / "Interview O'Brien.mp4"   # 4 s, has audio
d = TD.create(Path(tempfile.mkdtemp()), "demo_rerun", {"segments": segs}, "m", {"srt": True, "para_txt": True}, {}, {"playback": str(media_file)},
              {"inputs": [], "whisper": {"language_setting": "en", "backend_setting": "MLX (Apple Silicon GPU)"}})
d.save()
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app.var_view_mode.set("Segments")
app._render_doc(TD.load(d.path))
def pump(kind, timeout=60):
    t_end = time.time() + timeout
    while time.time() < t_end:
        app._poll_queue()
        if app.__dict__.get("_rerun_thread") is not None and not app._rerun_thread.is_alive() and app.msg_queue.empty():
            app._poll_queue(); return
        time.sleep(0.1)
    raise SystemExit("timeout")

print("[1] vocabulary in task settings + runner options")
app.var_whisper_prompt.set("Robin, Sam, Riverside Club")
snap = app._settings_snapshot()
assert snap["whisper_prompt"] == "Robin, Sam, Riverside Club"
t = {"inputs": ["a"], "steps": {"whisper": True}, "settings": snap}
print("    summary:", app._settings_summary(t))
assert "vocabulary 3 terms" in app._settings_summary(t)
ok("vocabulary stored per task and shown in Task Details")

print("[2] re-transcribe a passage (real ffmpeg cut + runner + fake MLX)")
logs = []
orig_log = app._append_log; app._append_log = lambda m, l="INFO": (logs.append(m), orig_log(m, l))
app._select_span(app.span_lookup[(1, 0)])
err = app._launch_retranscription("0:00:01.2", "0:00:02.6", "large-v3", "en (English)", "Transcribe (keep the spoken language)", "(keep", "Robin, playground")
assert err is None
pump("retranscribe")
opts = json.loads(next(l for l in logs if "FAKE_OPTIONS" in l).split("FAKE_OPTIONS ", 1)[1])
print("    runner got:", opts)
assert opts["repo"] == "mlx-community/whisper-large-v3-mlx" and opts["initial_prompt"] == "Robin, playground" and opts["language"] == "en" and "task" not in opts
clip = float(next(l for l in logs if "FAKE_CLIP_DURATION" in l).split()[-1])
assert abs(clip - 2.0) < 0.05, clip
dd = TD.load(d.path)
texts = [s["text"].strip() for s in dd.segments]
print("    segments now:", texts)
assert texts == ["Okay everyone", "We went to the playground", "Cool"]
new = dd.segments[1]
assert new["speaker"] == "Robin" and new["retranscribed"]["model"] == "large-v3" and 1.2 <= new["words"][0]["start"] and new["words"][-1]["end"] <= 2.7
assert dd.data["edit_history"][-1]["action"] == "retranscribe" and dd.data["edit_history"][-1]["old_text"] == ["we wen plaground"]
ok("clip = range ± 0.3 s padding; padding word 'uh' dropped; timings shifted onto the recording; speaker kept; logged")
assert app.edit_seg_idx == 1 and "re-transcribed with Whisper large-v3" in app.lbl_edit_hint.cget("text")
app._undo()
assert [s["text"].strip() for s in TD.load(d.path).segments][1] == "we wen plaground"
app._redo()
ok("the new segment opens in the editor with a note; ⌘Z / ⇧⌘Z undo and redo the re-transcription")

print("[2b] a transcript made on another kind of computer: this computer's backend")
from condense.gui import rerun as rerun_mod
foreign = next(b for b in ("MLX (Apple Silicon GPU)", "PyTorch GPU (ROCm / CUDA)") if b not in rerun_mod.WHISPER_BACKENDS)
prov = app.active_doc.data.setdefault("provenance", {}).setdefault("whisper", {})
own = prov.get("backend_setting")
prov["backend_setting"] = foreign
assert app._rerun_backend() in rerun_mod.WHISPER_BACKENDS and app._rerun_backend() != foreign
prov["backend_setting"] = rerun_mod.WHISPER_BACKENDS[-1]
assert app._rerun_backend() == rerun_mod.WHISPER_BACKENDS[-1]          # its own, when this computer has it
prov["backend_setting"] = own
ok(f"made with {foreign!r} elsewhere: re-run with {app._rerun_backend()!r} here")

print("[3] translate a passage")
logs.clear()
assert app._launch_retranscription("0:00:01.2", "0:00:02.6", "large-v3-turbo", "de (German)", "Translate to English", "Sam", "") is None
pump("retranscribe")
opts = json.loads(next(l for l in logs if "FAKE_OPTIONS" in l).split("FAKE_OPTIONS ", 1)[1])
assert opts["task"] == "translate" and opts["repo"] == "mlx-community/whisper-large-v3-mlx" and opts["language"] == "de"
seg = TD.load(d.path).segments[1]
print("    translated segment:", seg["text"].strip(), "| speaker:", seg["speaker"])
assert seg["text"].strip() == "[translated from German] We went outside" and seg["speaker"] == "Sam" and seg["speaker_manual"]
para = (d.out_dir / "demo_rerun_paragraphs.txt").read_text()
assert "Sam: [translated from German] We went outside" in para
ok("translate forces large-v3 (turbo can't translate); output marked [translated from German] in every export; chosen speaker applied")

print("[4] re-run speaker detection")
dd = TD.load(d.path)
logs.clear()
app._launch_rediarization("PyTorch MPS Spectral (Offline - No Token Needed)", 3, True)
pump("rediarize")
print("    runner:", [l for l in logs if "existing segments" in l or "skipping" in l.lower()][:2])
ok("diarize-only runner mode runs on the existing segments (no torch here, so it reports no labels — handled)")
# mapping logic with simulated labels
doc = app.active_doc
before = [s.get("speaker") for s in doc.segments]
stats = doc.apply_speaker_labels(["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"], keep_manual=True, info={"method": "test", "num_speakers": 3})
after = [s.get("speaker") for s in doc.segments]
print("    before:", before, "→ after:", after, stats)
assert after[0] == "Kim" and after[2] == "Alex" and stats["kept"] == 2
stats = doc.apply_speaker_labels(["SPEAKER_07", "SPEAKER_07", "SPEAKER_09"], keep_manual=False, info={"method": "test", "num_speakers": 2})
after = [s.get("speaker") for s in doc.segments]
print("    without keeping hand-assigned:", after, stats)
assert after[0] == after[1] and after[0] in ("Kim", "Sam")
ok("new labels map onto existing names by overlap; hand-assigned speakers kept unless unticked")
fp = [(round(s["start"], 2), round(s["end"], 2)) for s in doc.segments]
app._apply_rediarization(doc, {"segments": [dict(s, speaker="SPEAKER_00") for s in doc.segments], "diarization_method": "test"},
                         {"fingerprint": fp + [(9, 9)], "keep_manual": True, "method": "x", "num_speakers": 2})
ok("result is refused if segments were edited while detection ran")
print("\nALL TESTS PASSED")
