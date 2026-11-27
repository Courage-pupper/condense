"""Interview clips cut out before Condense, with checked transcripts of their own: found in the session by their sound,
their transcripts taken over into the session (one undo step), and corrections in the session written into the clip."""
import os
import subprocess, sys, tempfile, wave
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
import numpy as np
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import cliplink as cl, coding as cod, project as proj
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument


def speechlike_wav(path, secs, seed):
    rng = np.random.default_rng(seed)
    rate = 16000
    out = np.zeros(rate * secs, np.float32)
    t = 0.0
    while t < secs - 1:
        d = rng.uniform(0.15, 0.6); n = int(d * rate); i = int(t * rate)
        tt = np.arange(n) / rate
        out[i:i + n] += (np.sin(2 * np.pi * rng.uniform(120, 300) * tt) * np.hanning(n) * rng.uniform(0.1, 0.6))[: len(out) - i]
        t += d + rng.exponential(0.25)
    out += rng.normal(0, 0.003, len(out))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.clip(out, -1, 1) * 32000).astype("<i2").tobytes())


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def words_seg(t, text, speaker, **kw):
    ws = text.split()
    w = [{"word": " " + x, "start": round(t + i * 0.5, 3), "end": round(t + i * 0.5 + 0.4, 3), "probability": 0.7} for i, x in enumerate(ws)]
    return dict({"start": w[0]["start"], "end": w[-1]["end"], "text": "".join(x["word"] for x in w), "speaker": speaker, "words": w}, **kw)


study = Path(tempfile.mkdtemp(prefix="study_")) / "Study"
g1 = study / "Day 1 2026-08-10" / "Group 1"
(g1 / "Session Recordings").mkdir(parents=True)
(g1 / "Semi-structured Interviews").mkdir(parents=True)
proj.create_project(study)
proj.update_project(study, levels=["Day", "Group"], structure={
    "sessions": "{Day}/{Group}/Session Recordings",
    "interviews": "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}"})
tmp = Path(tempfile.mkdtemp())
speechlike_wav(tmp / "s.wav", 150, 7)
session = g1 / "Session Recordings" / "G1D1_session.mp4"
ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=150", "-i", str(tmp / "s.wav"), "-c:v", "libx264", "-preset",
   "ultrafast", "-g", "20", "-c:a", "aac", "-b:a", "64k", "-shortest", str(session))
clip = g1 / "Semi-structured Interviews" / "G1D1_I1.mp4"
ff("-ss", "47.30", "-i", str(session), "-t", "24.5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k", str(clip))
# the session's transcript: Whisper's guesses, a checked one inside the interview's stretch
out_dir = g1 / "Session Recordings" / "G1D1_session"
out_dir.mkdir()
s_segs = [words_seg(1.0 + k * 5, f"session words {k} here", ("Santiago", "SPEAKER_01")[k % 2]) for k in range(29)]
s_segs[11]["checked"] = True  # 56 s: inside the interview (47.3–71.8)
doc = TD.create(out_dir, "G1D1_session", {"segments": s_segs}, "small", {}, {}, {"playback": str(session), "video": str(session)}, {"inputs": []})
doc.save()
# the clip's own transcript, checked (made before Condense knew about the session)
(g1 / "Semi-structured Interviews" / "G1D1_I1").mkdir()
c_segs = [words_seg(0.5, "so what did you build", "Santiago", checked=True),
          words_seg(4.0, "a raft with a big sail", "P03", checked=True),
          words_seg(15.0, "and did it float", "Santiago", checked=True)]
cdoc = TD.create(g1 / "Semi-structured Interviews" / "G1D1_I1", "G1D1_I1", {"segments": c_segs}, "small", {}, {}, {"video": str(clip)}, {"inputs": []})
cdoc.save()
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
app._render_doc(TD.load(doc.path))

print("[1] found by its sound; its transcript taken over")
win, lk = app._link_clips_dialog()
assert [p.name for p in lk["clips"]] == ["G1D1_I1.mp4"]
lk["find"](wait=True)
r = lk["clips"] and next(iter(lk["clips"]))
res = lk["take_over"](wait=True)
assert res == {"clips": 1, "segments": 3, "codings": 0}, res
d = TD.load(doc.path)
ep = cl.linked_episodes(d)[0]
a = ep["start"]
assert abs(a - 47.3) < 0.05 and ep["label"] == "G1D1_I1" and ep["source"]["linked"]
inside = [s for s in d.segments if a <= s["start"] < a + 24.5]
assert [s["text"].strip() for s in inside] == ["so what did you build", "a raft with a big sail", "and did it float"]
assert inside[1]["speaker"] == "P03" and all(s["checked"] for s in inside) and abs(inside[0]["start"] - (a + 0.5)) < 1e-3
assert not any(s["text"].strip() == "session words 11 here" for s in d.segments)   # the checked session segment: replaced
assert [s["text"].strip() for s in d.segments if s["start"] < a][-1].startswith("session words")  # before: kept
c2 = TD.load(cdoc.path)
assert (c2.data.get("linked_session") or {}).get("episode") == ep["id"]          # the clip now shares the session's ids
assert [w["wid"] for s in c2.segments for w in s["words"]] == [w["wid"] for s in inside for w in s["words"]]
ok(f"found at {a:.2f} s; 3 checked segments with their speakers replaced the session's stretch; the clip is linked")

print("[2] a correction in the session goes into the clip; undo too")
i = next(k for k, s in enumerate(app.active_doc.segments) if s["text"].strip() == "a raft with a big sail")
app._goto_segment(i)
app.active_doc.edit_segment_text(i, " a raft with a huge sail")
app._commit("corrected a word")
assert TD.load(cdoc.path).segments[1]["text"].strip() == "a raft with a huge sail"
k = cod.add_coding(app.active_doc, cod.COMMENT, a + 3.0, a + 9.0, memo="the sail idea")
app._cd_commit("comment")
assert any(c.get("memo") == "the sail idea" for c in cod.codings(TD.load(cdoc.path)))   # comments go along
app._undo()
app._undo()
c3 = TD.load(cdoc.path)
assert c3.segments[1]["text"].strip() == "a raft with a big sail" and not cod.codings(c3)
ok("corrections and comments in the session are written into the clip; undo puts the clip back as well")

print("[3] the clip changed on its own: not overwritten; written on request")
c3.segments[0]["text"] = " edited in the clip"
c3.save()
app.active_doc.edit_segment_text(i, " a raft with a red sail")
app._commit("another correction")
assert TD.load(cdoc.path).segments[0]["text"] == " edited in the clip"
assert "changed on its own" in app.lbl_status.cget("text")
assert cl.clip_state(cl.linked_episodes(app.active_doc)[0]) == "clip changed"
assert lk["write_session"]() == 1
assert TD.load(cdoc.path).segments[1]["text"].strip() == "a raft with a red sail"
ok("a clip edited on its own is left alone (the status line says so); Write the session into the clips does it")
print("\nALL TESTS PASSED")
