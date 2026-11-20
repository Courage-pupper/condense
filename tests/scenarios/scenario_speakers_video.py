import os
import importlib.util, io, json, os, queue, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
os.environ["PYTHONPATH"] = os.path.join(os.environ["CONDENSE_FAKES"], "mlx_basic")
from _compat import pipe
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
REAL_POPEN = subprocess.Popen
for d in ("Interview O'Brien", "P04_trimmed", "P02"):
    shutil.rmtree(media / d, ignore_errors=True)

print("[1] speaker roster")
res = {"segments": [
    {"start": 0.0, "end": 1.4, "text": " Hallo Welt.", "speaker": "SPEAKER_01", "words": [{"word": " Hallo", "start": 0.0, "end": 0.6, "probability": 0.97}, {"word": " Welt.", "start": 0.6, "end": 1.4, "probability": 0.31}]},
    {"start": 1.5, "end": 2.9, "text": " Wie geht's?", "speaker": "SPEAKER_02", "words": [{"word": " Wie", "start": 1.5, "end": 1.9, "probability": 0.91}, {"word": " geht's?", "start": 1.9, "end": 2.9, "probability": 0.33}]},
    {"start": 3.0, "end": 3.9, "text": " Gut.", "speaker": "SPEAKER_02", "words": [{"word": " Gut.", "start": 3.0, "end": 3.9, "probability": 0.9}]}]}
tmp = Path(tempfile.mkdtemp())
d = TD.create(tmp, "Interview O'Brien", res, "large-v3-turbo", {}, {}, {"playback": str(media / "Interview O'Brien.mp4"), "video": str(media / "Interview O'Brien.mp4")}, {"inputs": []})
assert d.roster() == ["SPEAKER_01", "SPEAKER_02"]
assert d.add_speaker("P03") and not d.add_speaker("P03") and d.roster() == ["SPEAKER_01", "SPEAKER_02", "P03"]
assert d.set_segment_speaker(2, "P03") and d.speakers() == {"SPEAKER_01": 1, "SPEAKER_02": 1, "P03": 1}
assert not d.remove_speaker("P03")
d.set_segment_speaker(0, "SPEAKER_02")
assert d.roster() == ["SPEAKER_01", "SPEAKER_02", "P03"] and "SPEAKER_01" not in d.speakers()
assert d.rename_speakers({"SPEAKER_01": "Facilitator"}) and d.roster() == ["Facilitator", "SPEAKER_02", "P03"]
assert d.remove_speaker("Facilitator") and d.roster() == ["SPEAKER_02", "P03"]
d.save()
assert TD.load(d.path).roster() == ["SPEAKER_02", "P03"]
ok("add / assign / rename (also unused) / remove-only-if-unused; roster saved in the master file")

print("[2] worker records the video on the transcript's timeline")
base = {"respect_llc": True, "hb_container": "MKV", "audio_format": "WAV (16kHz Mono - Optimal for Whisper)", "whisper_backend": "MLX (Apple Silicon GPU)",
        "whisper_model": "small", "whisper_lang": "en (English)", "diarize_mode": "Disabled (No Speaker Labels)", "fmt_srt": True}
tasks = [{"id": "t1", "name": "P02", "inputs": [str(media / "P02.mp4")], "steps": {"whisper": True}, "output_dir": str(media / "P02"), "settings": base},
         {"id": "t2", "name": "P04_trimmed", "inputs": [str(media / "P04_trimmed.mp4")], "steps": {"whisper": True}, "output_dir": str(media / "P04_trimmed"), "settings": base}]
q = queue.Queue(); pipe.PipelineWorker({"tasks": tasks}, q, threading.Event()).run()
m1 = TD.load(media / "P02" / "P02.transcript.json").data["media"]
m2 = TD.load(media / "P04_trimmed" / "P04_trimmed.transcript.json").data["media"]
assert m1["video"] == str((media / "P02.mp4").resolve()) and m2["video"] is None and "video" in m2
ok(f"original video recorded; LosslessCut-trimmed task records no video (timeline differs)")

print("[3] ffmpeg frame stream (real decode)")
cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(media / "Interview O'Brien.mp4"), "-an", "-vf", "fps=10,scale=200:-2", "-f", "image2pipe", "-vcodec", "ppm", "-"]
proc = REAL_POPEN(cmd, stdout=subprocess.PIPE)
frames = []
while True:
    f = pipe.read_ppm_frame(proc.stdout)
    if f is None: break
    frames.append(f)
proc.wait()
assert 28 <= len(frames) <= 32 and frames[0][:2] == (200, 150), (len(frames), frames[0][:2])
ok(f"{len(frames)} frames of {frames[0][0]}×{frames[0][1]} from 3 s of video at 10 fps (PPM parsing works)")

print("[4] GUI: chips, ⌃digits, speakers dialog, video panel")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
assert app._video_panel_shown
app._render_doc(TD.load(d.path))
assert app._video_media().name == "Interview O'Brien.mp4"
names = [w.cget("text") for w in app.spk_chips._items]
print("    chips:", names)
assert names[1:] == ["1  SPEAKER_02", "2  P03", "0  + Speaker"]
app._select_span(app.span_lookup[(0, 0)])
assert app.edit_seg_idx == 0
app._assign_speaker_number(2)
assert TD.load(d.path).segments[0]["speaker"] == "P03"
ok("chip / ⌃2 assigns the selected segment and saves")
stub_tk2.sd._next = "Facilitator"
app._add_speaker_quick()
dd = TD.load(d.path)
assert dd.segments[0]["speaker"] == "Facilitator" and "Facilitator" in dd.roster()
ok("+ Speaker adds a new speaker and assigns it to the current segment")
app._apply_speaker_changes({"SPEAKER_02": "P01"}, [], ["P04", "P05"])
dd = TD.load(d.path)
assert dd.roster() == ["P01", "P03", "Facilitator", "P04", "P05"], dd.roster()
app._apply_speaker_changes({}, ["P05"], [])
assert "P05" not in TD.load(d.path).roster()
ok(f"Speakers dialog: rename + add several + remove unused → {TD.load(d.path).roster()}")

# still frame when paused
deadline = time.time() + 15
while time.time() < deadline and app._video_img is None:
    app._video_pump(); time.sleep(0.1)
assert app._video_img is not None
print("    still:", app._video_img.w, "x", app._video_img.h, "|", app.lbl_video_caption.cget("text"))
ok("paused: the frame at the selected word is shown")

# streaming while playing: fake ffplay (audio), real ffmpeg (video), fake clock
class FakeProc:
    def __init__(self, cmd, **k): self.cmd = cmd; self.alive = True
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
def popen(cmd, *a, **k):
    return REAL_POPEN(cmd, *a, **k) if cmd and cmd[0] == "ffmpeg" else FakeProc(cmd)
pipe.subprocess.Popen = popen
clock = [1000.0]
pipe.time.monotonic = lambda: clock[0]
app.play_pos = 0.0
app._player_start(0.0)
assert app._video_proc is not None
shown = []
orig_show = app._show_frame
app._show_frame = lambda pos, fr: (shown.append(round(pos, 2)), orig_show(pos, fr))
for step in range(12):                 # advance the playback clock 0.25 s at a time
    clock[0] += 0.25 if step else 0.25 + 0.25
    t_end = time.time() + 2
    while time.time() < t_end:
        app._video_pump()
        if shown and shown[-1] >= app._current_pos() - 0.11:
            break
        time.sleep(0.02)
print("    frames shown at:", shown[:6], "…", shown[-3:], " | clock at", round(app._current_pos(), 2))
assert shown == sorted(shown) and abs(shown[-1] - app._current_pos()) <= 0.11 and len(shown) >= 8
ok("playing: frames are paced by the player clock (never ahead, ≤ 1 frame behind)")
app._player_pause()
assert app._video_proc is None
ok("pause stops the stream and shows a still at the pause position")
pipe.subprocess.Popen = REAL_POPEN
app.var_show_video.set(False); app._toggle_video_panel()
assert not app._video_panel_shown
ok("video panel can be hidden (View ▸ Show Video Preview / 'Video' checkbox)")
print("\nALL TESTS PASSED")
