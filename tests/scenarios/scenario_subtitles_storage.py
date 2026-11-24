"""Video with subtitles (track and burned in), the Storage dialog (thinning backups, leftovers, audio cache) and the
themed popup menus."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import storage
from condense.media import subtitles as sublib
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
work = Path(tempfile.mkdtemp(prefix="subs_"))


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def streams(p):
    out = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration",
                                              "-of", "json", str(p)]))
    return [(s["codec_type"], s["codec_name"]) for s in out["streams"]], float(out["format"]["duration"])


video = work / "Interviews" / "G3D1_I1.mp4"
video.parent.mkdir()
ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=14", "-f", "lavfi", "-i", "sine=frequency=300:duration=14",
   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video)


def words(t0, text):
    return [{"word": " " + w, "start": round(t0 + i * 0.4, 2), "end": round(t0 + i * 0.4 + 0.35, 2), "probability": 0.9}
            for i, w in enumerate(text.split())]


segs = [{"start": 0.5, "end": 6.9, "speaker": "Robin", "text": "", "words": words(0.5, "Hello there and welcome to the session about the paper boats we built today with everyone")},
        {"start": 7.5, "end": 9.0, "speaker": "Sam", "text": "", "words": words(7.5, "Thank you Robin.")},
        {"start": 9.5, "end": 11.5, "speaker": "Sam", "text": "", "words": words(9.5, "This part is cut out")}]
for sg in segs:
    sg["text"] = "".join(w["word"] for w in sg["words"])
    sg["end"] = sg["words"][-1]["end"]
res = {"segments": segs, "language": "de"}
out_dir = video.parent / "G3D1_I1"
out_dir.mkdir()
d = TD.create(out_dir, "G3D1_I1", res, "small", {}, {}, {"playback": str(video), "video": str(video)}, {"inputs": [{"path": str(video)}]})
d.cut_range(segs[2]["start"], segs[2]["end"])
d.segments[0]["checked"] = True
d.save()

print("[1] subtitle cues")
cs = sublib.cues(TD.load(d.path).kept_segments())
print("   ", cs)
assert cs[0][2].startswith("Robin: Hello") and all(len(line) <= 50 for _, _, t in cs for line in t.split("\n"))
assert any(t.startswith("Sam: Thank you") for _, _, t in cs) and not any("cut out" in t for _, _, t in cs)
assert all(b - a >= 0.2 for a, b, _ in cs) and all(cs[i][1] <= cs[i + 1][0] for i in range(len(cs) - 1))
assert not any(t.startswith("Robin:") for _, _, t in cs[1:] if "Thank" not in t)  # the name only when the speaker changes
ok(f"{len(cs)} subtitles from the words: two lines at most, names when the speaker changes, cut parts left out, no overlaps")

print("[2] Transcript ▾ ▸ Video with subtitles…")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._render_doc(TD.load(d.path))
win, dlg = app._subtitles_dialog()
assert len(dlg["jobs"]) == 1 and dlg["jobs"][0]["checked"] < 100


def run(mode):
    dlg["mode"].set(mode)
    dlg["go"](wait=True)
    while not app.msg_queue.empty():
        msg = app.msg_queue.get_nowait()
        if msg.get("type") == "subtitles":
            app._on_subtitles_msg(msg)


run("track")
dst = out_dir / "G3D1_I1_subtitled.mp4"
kinds, dur = streams(dst)
assert ("subtitle", "mov_text") in kinds and ("video", "h264") in kinds and abs(dur - 14) < 0.5, (kinds, dur)
srt = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(dst), "-map", "0:s:0", "-f", "srt", "-"]).decode()
assert "Robin: Hello there" in srt and "Sam: Thank you Robin." in srt and "cut out" not in srt
assert "G3D1_I1_subtitled.mp4" in app.lbl_status.cget("text")
ok("subtitle track: next to the transcript, nothing re-encoded (h264 copied), the subtitles inside, cut part left out")
if sublib.can_burn():
    asked = []
    pipe.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
    win, dlg = app._subtitles_dialog()
    run("burn")
    kinds, dur = streams(dst)
    assert asked and "Replace" in asked[0][1]
    assert not any(k == "subtitle" for k, _ in kinds) and abs(dur - 14) < 0.5, kinds
    ok("burned in: the text is drawn into the picture (no subtitle track), after asking to replace the earlier file")
else:
    print("    (this ffmpeg has no libass: burning in is offered as unavailable)")
(work / "audio_only").mkdir()
nov = TD.create(work / "audio_only", "A1", {"segments": segs[:1]}, "small", {}, {}, {"playback": str(video), "video": None}, {"inputs": []})
nov.save()
infos = []
pipe.messagebox.showinfo = lambda *a, **k: infos.append(a)
assert app._subtitles_dialog([nov.path]) is None and "No video" in infos[0][1]
ok("a transcript without a video on its timeline: explained, nothing made")

print("[3] storage")
proj_dir = work
bdir = out_dir / "_backups"
bdir.mkdir(exist_ok=True)
for k in range(6):
    (bdir / f"G3D1_I1.2026111{k}-120000.transcript.json").write_text("x" * 1000)
other = video.parent / "G3D1_I2" / "_backups"
other.mkdir(parents=True)
for k in range(2):
    (other / f"G3D1_I2.2026111{k}-120000.transcript.json").write_text("y" * 500)
old_part = video.parent / ".G3D1_I1_subtitled.part.mp4"
old_part.write_bytes(b"0" * 2000)
os.utime(old_part, (time.time() - 3600, time.time() - 3600))
fresh_part = video.parent / ".G3D1_I2.part000.mp4"
fresh_part.write_bytes(b"0" * 100)
cache = pipe.APP_SUPPORT_DIR / "cache" / "audio"
cache.mkdir(parents=True, exist_ok=True)
(cache / "abc.env.npy").write_bytes(b"0" * 3000)
win, sd = app._storage_dialog(proj_dir)
rep = sd["state"]["rep"]
assert len(rep["backups"]) == 8 and rep["leftovers"] == [old_part] and rep["cache_bytes"] >= 3000
assert any(p.name == "G3D1_I1_subtitled.mp4" for p in rep["made"]) and rep["transcripts"] == 2
asked = []
pipe.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
sd["keep"].set("3"); sd["thin"]()
left1 = sorted(p.name for p in bdir.iterdir())
assert left1 == [f"G3D1_I1.2026111{k}-120000.transcript.json" for k in (3, 4, 5)], left1
assert len(list(other.iterdir())) == 2 and "Delete 3 backup(s)" in asked[0][1]
ok("thinning keeps the newest 3 backups of each transcript (the one with 2 keeps both)")
sd["clear"]("leftovers")
assert not old_part.exists() and fresh_part.exists()
sd["clear"]("cache")
assert not list(cache.glob("*"))
sd["keep"].set("none"); sd["thin"]()
assert not bdir.exists() and not other.exists()
ok("leftovers of interrupted exports (not one being written now), the audio cache, and all backups can be cleared")

print("[4] popup menus (themed)")
m = app._speaker_chip_menu(type("E", (), {"x_root": 10, "y_root": 10})(), "Robin")
assert isinstance(m, pipe.PopupMenu) and m.is_open and any("Who is who" in lb for lb in m.labels())
m.unpost()
assert not m.is_open
chosen = []
m2 = pipe.PopupMenu(app)
m2.add_command(label="A", command=lambda: chosen.append("A"))
m2.add_command(label="B", command=lambda: chosen.append("B"), state="disabled")
v = stub_tk2.tk.BooleanVar(value=False)
m2.add_checkbutton(label="C", variable=v)
m2.tk_popup(5, 5)
m2.invoke("B"); assert chosen == [] and m2.is_open  # disabled
m2.invoke("A"); assert chosen == ["A"] and not m2.is_open
m2.tk_popup(5, 5); m2.invoke("C"); assert v.get() is True
ok("right-click menus are PopupMenus; disabled entries do nothing; choosing closes the menu; check entries toggle")
print("\nALL TESTS PASSED")
