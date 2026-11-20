import os
import importlib.util, json, os, queue, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_basic")
os.environ["PATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "bin") + ":" + os.environ["PATH"]
from _compat import pipe
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
REAL_POPEN = subprocess.Popen

def make_doc(tmp):
    res = {"segments": [
        {"start": 0.0, "end": 1.4, "text": " Hallo Welt.", "speaker": "SPEAKER_01", "words": [
            {"word": " Hallo", "start": 0.0, "end": 0.6, "probability": 0.97}, {"word": " Welt.", "start": 0.6, "end": 1.4, "probability": 0.31}]},
        {"start": 1.5, "end": 2.9, "text": " Wie geht's?", "speaker": "SPEAKER_01", "words": [
            {"word": " Wie", "start": 1.5, "end": 1.9, "probability": 0.91}, {"word": " geht's?", "start": 1.9, "end": 2.9, "probability": 0.33}]},
        {"start": 3.2, "end": 5.0, "text": " Gut, danke dir.", "speaker": "SPEAKER_02", "words": [
            {"word": " Gut,", "start": 3.2, "end": 3.6, "probability": 0.95}, {"word": " danke", "start": 3.7, "end": 4.2, "probability": 0.92},
            {"word": " dir.", "start": 4.2, "end": 5.0, "probability": 0.5}]}]}
    d = TD.create(tmp, "P01", res, "large-v3-turbo", {"vtt": True, "para_txt": True}, {}, {"playback": str(Path(os.environ["CONDENSE_TEST_MEDIA"]) / "P02.mp4")}, {"inputs": []})
    d.save(); d.export_all(); return d

print("[1] segment text editing keeps timings")
tmp = Path(tempfile.mkdtemp()); d = make_doc(tmp)
assert d.edit_segment_text(0, "Hallo   Welt.") is False
assert d.edit_segment_text(0, "Hallo Erde.")
w = d.segments[0]["words"]
assert (w[0]["start"], w[0]["end"], w[0]["probability"]) == (0.0, 0.6, 0.97) and "edited" not in w[0]
assert (w[1]["word"], w[1]["start"], w[1]["end"], w[1]["probability"], w[1]["edited"]) == (" Erde.", 0.6, 1.4, 1.0, True)
ok("replaced word: timing of the word it replaces, p=1.0, marked edited; unchanged word untouched")
assert d.edit_segment_text(2, "Gut, vielen Dank dir.")
w = d.segments[2]["words"]
print("    ", [(x["word"], x["start"], x["end"]) for x in w])
assert [x["word"] for x in w] == [" Gut,", " vielen", " Dank", " dir."] and w[1]["start"] == 3.7 and w[2]["end"] == 4.2 and w[3]["start"] == 4.2
ok("one word → two words: split evenly inside the old word's span")
assert d.edit_segment_text(2, "Gut, dir.")
assert [x["word"] for x in d.segments[2]["words"]] == [" Gut,", " dir."]
assert d.edit_segment_text(2, "Also gut, dir.")
w = d.segments[2]["words"]
assert w[0]["word"] == " Also" and w[0]["start"] == 3.2 and d.segments[2]["start"] == 3.2
ok("deletion and insertion at start work; segment text/start/end rebuilt")
hist = [h["action"] for h in d.data["edit_history"]]
assert hist.count("edit_text") == 4

print("[2] split / merge / speaker / paragraphs")
assert d.split_segment(2, 2)
assert [s["text"] for s in d.segments[2:]] == [" Also gut,", " dir."] and d.segments[3]["start"] == 4.2 and d.segments[3]["speaker"] == "SPEAKER_02"
assert d.merge_with_next(2) and len(d.segments) == 3 and d.segments[2]["text"] == " Also gut, dir." and d.segments[2]["end"] == 5.0
assert d.set_segment_speaker(1, "SPEAKER_02")
paras = d.paragraphs()
assert [(p["speaker"], p["segments"]) for p in paras] == [("SPEAKER_01", [0]), ("SPEAKER_02", [1, 2])]
d.set_segment_speaker(1, "SPEAKER_01")
paras = d.paragraphs()
assert [p["segments"] for p in paras] == [[0, 1], [2]] and paras[0]["text"] == "Hallo Erde. Wie geht's?"
ok("split/merge round-trip, speaker change regroups paragraphs")
d.rename_speakers({"SPEAKER_01": "Interviewer", "SPEAKER_02": "P01"})
d.save(); created = d.export_all()
print("    exports:", sorted(p.name for p in created.values()))
ptxt = (tmp / "P01_paragraphs.txt").read_text()
print("    ---\n    " + ptxt.strip().replace("\n", "\n    ") + "\n    ---")
assert ptxt.startswith("Interviewer: Hallo Erde. Wie geht's? [00:00:02]")
prtf = (tmp / "P01_paragraphs.rtf").read_text()
assert "\\b Interviewer:\\b0" in prtf and "[00:00:05]" in prtf
srt = (tmp / "P01.srt").read_text()
assert srt.count("-->") == 3
ok("paragraph TXT/RTF = one block per speaker turn; SRT keeps 3 timed segments")
rtf = (tmp / "P01_confidence.rtf").read_text()
import re as _re
assert not _re.findall(r"\\fs16 \[\d+%\]", rtf) and "\\ul\\b  geht's?\\b0\\ulnone" in rtf
ok("confidence RTF: no [NN%] after red words")

print("[3] Keychain (fake `security`)")
KS = pipe.KeychainSecret
KS.available = classmethod(lambda cls: True)
assert KS.load() == ""
assert KS.save("hf_AbC123xyz") and KS.load() == "hf_AbC123xyz"
assert not KS.save('bad" -w "inject')
KS.delete(); assert KS.load() == ""
ok("save/load/delete; token passed on stdin, odd characters rejected")

print("[4] GUI editor + player")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
KS.save("hf_Remembered1")
pipe.launch_gui(); app = holder["app"]
assert app.var_hf_token.get() == "hf_Remembered1"
ok("token restored from Keychain at launch")
d2 = make_doc(Path(tempfile.mkdtemp()))
app.var_view_mode.set("Paragraphs")
app._render_doc(TD.load(d2.path))
txt = app.preview_text
for sp in app.word_spans:
    assert txt.get(f"{sp['line']}.{sp['c0']}", f"{sp['line']}.{sp['c1']}") == sp["word"], sp
print("    ---\n    " + txt.content.strip().replace("\n", "\n    ") + "\n    ---")
assert txt.content.count("SPEAKER_01") == 1
ok("paragraph view: one block per speaker turn, word spans exact")
app.var_view_mode.set("Segments"); app._rerender()
assert txt.content.count("SPEAKER_01") == 2
app.var_view_mode.set("Paragraphs"); app._rerender()
ok("view toggles between paragraphs and segments")

launched = []
class FakeProc:
    def __init__(self, cmd, **k): self.cmd = cmd; self.alive = True; launched.append(cmd)
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
pipe.subprocess.Popen = FakeProc
clock = [100.0]
pipe.time.monotonic = lambda: clock[0]

i_welt = app.span_lookup[(0, 1)]
app._select_span(i_welt)
assert app.edit_seg_idx == 0 and app.edit_text.content == "Hallo Welt."
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "Hallo Erde.")
i_dir = app.span_lookup[(2, 2)]
app._select_span(i_dir)
d3 = TD.load(d2.path)
assert d3.segments[0]["text"].strip() == "Hallo Erde." and app.edit_seg_idx == 2
assert app.word_spans[app.current_span]["word"] == "dir."
assert "Hallo Erde." in (d2.path.parent / "P01_paragraphs.txt").read_text()
ok("edit autosaves when clicking another segment; clicked word still selected correctly; exports updated")
app.var_seg_speaker.set("SPEAKER_01"); app._save_segment_edit()
assert TD.load(d2.path).paragraphs()[0]["segments"] == [0, 1, 2]
app.var_seg_speaker.set("SPEAKER_02"); app._save_segment_edit()
ok("speaker of a single segment changed from the editor (paragraphs regroup)")
app._select_span(app.span_lookup[(2, 1)])
app._split_at_current_word()
d4 = TD.load(d2.path)
assert len(d4.segments) == 4 and d4.segments[3]["text"].strip() == "danke dir." and app.edit_seg_idx == 3
app._load_segment_into_editor(2); app._merge_with_next()
assert len(TD.load(d2.path).segments) == 3
ok("split before word / merge with next from the editor")

app._select_span(app.span_lookup[(1, 0)])
assert abs(app.play_pos - 1.0) < 1e-6
app._toggle_play()
assert app.playing and "-ss" in launched[-1] and launched[-1][launched[-1].index("-ss") + 1] == "1.00"
clock[0] += 0.25 + 2.0
assert abs(app._current_pos() - 3.0) < 1e-6
app._follow(app._current_pos())
assert txt.get(*txt.tags["PLAYING"]) == "geht's?"; app._follow(3.3); assert txt.get(*txt.tags["PLAYING"]) == "Gut,"
app._toggle_play()
assert not app.playing and abs(app.play_pos - 3.0) < 1e-6
app._toggle_play()
assert launched[-1][launched[-1].index("-ss") + 1] == "3.00"
ok("play → pause remembers position → play resumes there; follow highlight on the spoken word")
app.var_speed.set("0.75×"); app._on_speed_change()
assert "atempo=0.75" in " ".join(launched[-1])
clock[0] += 0.25 + 4.0
assert abs(app._current_pos() - 6.0) < 1e-6
app._seek_rel(-5)
assert launched[-1][launched[-1].index("-ss") + 1] == "1.00"
app._player_pause()
ok("slower playback (atempo), position math honours speed, ⟲5 s seeks")
app._jump_low_conf(+1)
c = launched[-1]
assert "-t" in c and app.playing
ok("jump to uncertain word plays a short snippet")
app._player_pause()

pipe.subprocess.Popen = REAL_POPEN
app.var_hf_token.set("hf_NewToken2"); app._sync_token()
assert KS.load() == "hf_NewToken2"
app.var_remember_token.set(False); app._sync_token()
assert KS.load() == ""
ok("token saved when changed; unticking 'Remember' removes it from the Keychain")
print("\nALL TESTS PASSED")
