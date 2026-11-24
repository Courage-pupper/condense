"""Linking a video to a transcript afterwards (no re-encoding): LosslessCut-trimmed without Compress, a moved video,
a camera that started earlier or later, and a video that doesn't belong to the transcript."""
import os, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
from condense.core import videolink as vl
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
REAL_POPEN = subprocess.Popen
W = media / "link"
W.mkdir()


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


# a 60 s "recording" whose sound has a changing loudness (so it can be matched), keyframes every 2 s like a camera
src = W / "camera" / "P07.mp4"
src.parent.mkdir()
ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=60",
   "-f", "lavfi", "-i", "anoisesrc=d=60:c=pink:seed=7:a=0.6,volume=eval=frame:volume='0.05+abs(sin(0.31*t)*sin(0.17*t*t/7))'",
   "-c:v", "libx264", "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
(src.parent / "P07.llc").write_text("{ version: 1, cutSegments: [ { start: 3.3, end: 12 }, { start: 20.7, end: 35 }, { start: 41.1, end: 55 } ] }")

print("[1] an old transcript: LosslessCut-trimmed, no Compress, the trimmed video not kept")
tmpd = W / "tmp"; tmpd.mkdir()
trimmed, rec = pipe.LosslessCutHelper.apply_llc_cuts_if_present(src, tmpd, lambda *a: None,
                                                                lambda cmd, what: subprocess.run(cmd, check=True, capture_output=True))
out = W / "P07"; out.mkdir()
play = out / "P07_playback.m4a"
ff("-i", trimmed, "-vn", "-c:a", "aac", "-b:a", "96k", play)
tdur = dur(play)
res = {"segments": [{"start": float(t), "end": float(t) + 4.0, "text": f" Teil {t}.", "speaker": "P07",
                     "words": [{"word": f" Teil {t}.", "start": float(t), "end": float(t) + 4.0, "probability": 0.95}]}
                    for t in range(0, 40, 5)]}
d = TD.create(out, "P07", res, "small", {}, {}, {"playback": str(play), "video": None},
              {"inputs": [{"path": str(src)}], "processing": {"losslesscut": rec}})
d.save()
shutil.rmtree(tmpd)
print("    trimmed timeline:", round(tdur, 2), "s from segments", rec["segments_s"])

print("[2] core: the LosslessCut pieces are found by their sound")
link = vl.find_link(play, src, tdur, dur(src), d.data["provenance"])
print("    ", link)
assert link["confident"] and len(link["pieces"]) == 3, link
for (ts, vs, ln), (s, e) in zip(link["pieces"], rec["segments_s"]):
    kf = (s // 2) * 2  # stream copy starts at the keyframe before the cut
    assert abs(vs - kf) < 0.2, (vs, kf)
    assert abs((vs + ln) - e) < 0.25, (vs + ln, e)
assert abs(sum(p[2] for p in link["pieces"]) - tdur) < 0.01
assert vl.to_video_time(link["pieces"], 15.0)[0] == link["pieces"][1][1] + 15.0 - link["pieces"][1][0]
ok("one piece per kept segment, each starting at the keyframe the cut snapped to (±0.2 s)")
assert vl.to_video_time(None, 7.5) == (7.5, None) and vl.is_same_timeline(None)
assert vl.to_video_time([[10, 0, 5]], 3) == (None, 10) and vl.to_video_time([[10, 0, 5]], 12) == (2, 15)
assert vl.to_video_time([[10, 0, 5]], 16) == (None, None)
ok("transcript time → video time, with gaps and the time the answer changes")

print("[3] GUI: no video → 'Link a video…' → the original recording plays along")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._render_doc(TD.load(d.path))
assert app._video_media() is None
app._video_still(0.0)
assert app._link_btn_shown and "Link the recording's video" in app.video_label.cget("text"), app.video_label.cget("text")
ok("the empty video panel offers 'Link a video…'")
stub_tk2.fd._next = str(src)
app._link_video_dialog(wait=True)
dd = TD.load(d.path)
assert dd.media_path("video") == src.resolve() and len(dd.video_pieces()) == 3, dd.data.get("video_link")
assert app._video_media() == src.resolve() and "3 pieces" in app.lbl_status.cget("text"), app.lbl_status.cget("text")
assert app._trim_source() == play, "a trimmed export must not cut the original on the transcript's times"
ok(f"linked and saved: {app.lbl_status.cget('text')}")

grabbed = []
def popen(cmd, *a, **k):
    if cmd and cmd[0] == "ffmpeg":
        grabbed.append(cmd)
        return REAL_POPEN(cmd, *a, **k)
    return FakeProc(cmd)
class FakeProc:
    def __init__(self, cmd, **k): self.cmd = cmd; self.alive = True
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
pipe.subprocess.Popen = popen

app._video_img = None
app._video_still(15.0)
deadline = time.time() + 15
while time.time() < deadline and app._video_img is None:
    app._video_pump(); time.sleep(0.1)
assert app._video_img is not None and not app._link_btn_shown
ok("paused at 0:15 of the transcript: the frame is taken from the original at its matching time")

clock = [1000.0]
pipe.time.monotonic = lambda: clock[0]
b = link["pieces"][1][0]
app.play_pos = b - 0.5
grabbed.clear()
app._player_start(b - 0.5)
first = next(c for c in grabbed if "-t" in c)
assert abs(float(first[first.index("-ss") + 1]) - (link["pieces"][0][1] + b - 0.5)) < 0.01 and abs(float(first[first.index("-t") + 1]) - 0.5) < 0.01, first
clock[0] += 0.5 + app.PLAYER_LATENCY_S + 0.2  # 0.2 s past the cut
now = app._current_pos()
app._video_pump()
second = [c for c in grabbed if "-t" in c][-1]
assert second is not first and abs(float(second[second.index("-ss") + 1]) - (link["pieces"][1][1] + now - b)) < 0.01, (now, second)
assert abs(app._video_seg_end - link["pieces"][2][0]) < 0.01
app._player_pause()
pipe.subprocess.Popen = REAL_POPEN
ok("playing over a LosslessCut cut: the video stream jumps to the next kept part of the original")

app._unlink_video()
dd = TD.load(d.path)
assert dd.media_path("video") is None and "video_link" not in dd.data and app._video_media() is None
app._undo()
assert TD.load(d.path).video_pieces() and app.active_doc.video_pieces()
ok("unlink (the file stays), and undo brings the link back")

print("[4] other videos")
same = W / "moved" / "P07 trimmed copy.mp4"; same.parent.mkdir()
# the transcript's own trimmed video (as Condense ≥ 0.17 keeps it), moved somewhere else
parts = []
for i, (s, e) in enumerate(rec["segments_s"]):
    pp = W / f"part{i}.mp4"; parts.append(pp)
    ff("-ss", s, "-to", e, "-i", src, "-c", "copy", "-avoid_negative_ts", "make_zero", pp)
(W / "list.txt").write_text("".join(f"file '{pp}'\n" for pp in parts))
ff("-f", "concat", "-safe", "0", "-i", W / "list.txt", "-c", "copy", same)
app._link_video_dialog(str(same), wait=True)
assert app._video_media() == same.resolve() and app.active_doc.video_pieces() is None
assert app._trim_source() == same.resolve() and "on the transcript's timeline" in app.lbl_status.cget("text")
ok("a moved copy of the trimmed video: recognised as the same timeline (and usable for trimmed exports)")

late = W / "late camera.mp4"
ff("-ss", 12, "-i", play, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=20", "-t", 20,
   "-map", "1:v", "-map", "0:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", late)
app._link_video_dialog(str(late), wait=True)
pcs = app.active_doc.video_pieces()
assert pcs and len(pcs) == 1 and abs(pcs[0][0] - 12) < 0.1 and pcs[0][1] == 0.0, pcs
assert "after the transcript" in app.lbl_status.cget("text"), app.lbl_status.cget("text")
app._video_still(5.0)
assert "no picture for this moment" in app.video_label.cget("text")
ok(f"a camera that started 12 s later: {app.lbl_status.cget('text')}; before it, 'no picture for this moment'")

other = media / "Interview O'Brien.mp4"
stub_tk2.mb.askyesno = lambda *a, **k: False
app._link_video_dialog(str(other), wait=True)
assert app._video_media() == late.resolve()
asked = []
stub_tk2.mb.askyesno = lambda *a, **k: asked.append(a) or True
app._link_video_dialog(str(other), wait=True)
assert asked and "could not find" in asked[0][1] and app._video_media() == other.resolve() and app.active_doc.video_pieces() is None
ok("a video that doesn't match: asks first; 'No' keeps the current link, 'Yes' links it from the start")

errs = []
pipe.messagebox.showerror = lambda *a, **k: errs.append(a)
audio_only = W / "audio.m4a"
ff("-i", play, "-c", "copy", audio_only)
app._link_video_dialog(str(audio_only), wait=True)
assert errs and "has no picture" in errs[0][1]
ok("an audio file is refused (no picture)")
print("\nALL TESTS PASSED")
