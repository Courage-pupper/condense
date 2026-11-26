"""The video preview fills its screen (enlarged too, never distorted), follows the tab in view (Transcript or Coding),
moves into a window of its own and back, and can decode the original recording for a sharper picture."""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
work = Path(tempfile.mkdtemp(prefix="screens_"))
orig = work / "G1D1.mp4"           # the camera's recording (16:9)
comp = work / "G1D1" / "G1D1_compressed.mp4"
comp.parent.mkdir()
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(orig)], check=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(orig), "-vf", "scale=320:180", "-c:v", "libx264",
                "-crf", "30", "-c:a", "copy", str(comp)], check=True)
segs = [{"start": 0.5, "end": 3.0, "text": " Hello there.", "speaker": "Kim", "words": [{"word": " Hello", "start": 0.5, "end": 1.0}, {"word": " there.", "start": 1.1, "end": 3.0}]}]
d = TD.create(comp.parent, "G1D1", {"segments": segs}, "small", {}, {}, {"playback": str(comp), "video": str(comp)}, {"inputs": [{"path": str(orig)}]})
d.save()

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._toggle_video_panel()
app._render_doc(TD.load(d.path))
screens = app._video_screens
assert set(screens) == {"tx"} and app._video_panel_shown  # one screen in the tab (Code and Edit mode)


def size(scr, w, h):
    scr.label.winfo_width = lambda: w
    scr.label.winfo_height = lambda: h


def frame_now(pos=1.0, timeout=20):
    app._video_img = None
    app._still_busy = False
    app._video_still(pos)
    t_end = time.time() + timeout
    while time.time() < t_end and app._video_img is None:
        app._video_pump()
        time.sleep(0.05)
    assert app._video_img is not None, "no frame"
    return app._video_img


print("[1] the picture fills its screen")
app._cd_visible = lambda: False
size(screens["tx"], 800, 600)
img = frame_now()
assert (img.w, img.h) == (800, 450), (img.w, img.h)  # 16:9 into 800×600: full width, not stretched
size(screens["tx"], 400, 600)
assert (frame_now().w, app._video_img.h) == (400, 226) or (app._video_img.w, app._video_img.h) == (400, 224), (app._video_img.w, app._video_img.h)
size(screens["tx"], 1200, 300)
img = frame_now()
assert img.h == 300 and abs(img.w - 534) <= 2, (img.w, img.h)  # height-bound
ok("a 320×180 compressed video fills an 800×600 screen at 800×450 (enlarged, 16:9 kept); fitted by height when wide")
assert screens["tx"].label.cget("image") is img and "G1D1_compressed.mp4" in screens["tx"].caption.cget("text")

print("[2] Code mode: the same screen")
del app._cd_visible
app.notebook.select(app.tab_preview)
app._set_tx_mode("code")
assert app._cd_visible() and app._video_screen() is screens["tx"]
size(screens["tx"], 640, 480)
app._video_retarget(force=True)
img = frame_now()
assert screens["tx"].label.cget("image") is img and (img.w, img.h) == (640, 360)
app._set_tx_mode("edit")
assert app._video_screen() is screens["tx"]
ok("in Code mode the frame goes to the tab's one video column (640×360); switching modes keeps it")

print("[3] a window of its own")
pop = app._video_pop_out()
assert pop.win.condense_free  # not made transient: it can go to another screen (macOS)
size(pop, 1600, 900)
app._video_retarget()
img = frame_now()
assert pop.label.cget("image") is img and (img.w, img.h) == (1600, 900)
assert app._video_pop_out() is pop  # a second ⤢ brings the same window forward
app._video_pop_in()
assert "pop" not in app._video_screens and app._video_popout is None
img = frame_now()
assert screens["tx"].label.cget("image") is img
ok("⤢ opens the video in its own window (1600×900 there), closing it puts the video back into the tab")

print("[4] sharper: from the original recording")
calls = []
real_run = pipe.subprocess.run
pipe.subprocess.run = lambda cmd, *a, **k: (calls.append(cmd), real_run(cmd, *a, **k))[1]
frame_now()
assert str(comp) in calls[-1]
app.var_preview_original.set(True)
frame_now()
assert str(orig) in calls[-1] and "G1D1.mp4" in screens["tx"].caption.cget("text")
pipe.subprocess.run = real_run
dd = TD.load(d.path)
dd.data["provenance"]["processing"] = {"losslesscut": {"segments_s": [[0, 3]]}}
assert dd.original_recording() is None  # trimmed: the original is on another timeline
ok("Playback ▸ Sharper preview decodes the camera's original (same timeline); never a trimmed or merged one")

print("[5] the video hidden")
app.var_show_video.set(False); app._toggle_video_panel()
assert not app._video_panel_shown and app._video_screen() is None
app.var_show_video.set(True); app._toggle_video_panel()
assert app._video_screen() is screens["tx"]
ok("Show the video hides and shows the column")
print("\nALL TESTS PASSED")
