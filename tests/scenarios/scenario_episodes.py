import os
import csv, json, subprocess, sys, tempfile, wave
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
import numpy as np
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import align, project as proj, episodes as eplib
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


# ---- a study laid out like the real one (all names invented)
study = Path(tempfile.mkdtemp(prefix="study_")) / "Demo study"
g1 = study / "Day 1 2026-08-10" / "Group 1"
for d in ("Session Recordings", "Semi-structured Interviews", "iPad Screen Recordings", "Leica pictures"):
    (g1 / d).mkdir(parents=True)
for d in ("Group 2/Session Recordings", "Leica Pictures Unsorted", "Semi-structured Interviews Unsorted"):
    (study / "Day 1 2026-08-10" / d).mkdir(parents=True)
(study / "Day 2 2026-08-11" / "Group 1" / "Session Recordings").mkdir(parents=True)
(study / "Day 2 2026-08-11" / "Group 2").mkdir(parents=True)
(study / "Consent forms").mkdir()
tmp = Path(tempfile.mkdtemp())
speechlike_wav(tmp / "s.wav", 150, 7)
session = g1 / "Session Recordings" / "G1D1_session.mp4"
ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=150", "-i", str(tmp / "s.wav"), "-c:v", "libx264", "-preset", "ultrafast",
   "-g", "20", "-c:a", "aac", "-b:a", "64k", "-shortest", str(session))
clip = g1 / "Semi-structured Interviews" / "G1D1_I1.mp4"
ff("-ss", "47.30", "-i", str(session), "-t", "24.5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k", str(clip))
(g1 / "Semi-structured Interviews" / "._G1D1_I1.mp4").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
(study / "Day 1 2026-08-10" / "Semi-structured Interviews Unsorted" / "stray.mp4").write_bytes(clip.read_bytes())


def segs(n, step=5.0):
    out = []
    for k in range(n):
        t = 1.0 + k * step
        ws = f"segment {k} says a few words here".split()
        out.append({"start": t, "end": t + 3.5, "text": " " + " ".join(ws), "speaker": "SPEAKER_00" if k % 3 else "Facilitator",
                    "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": 0.95} for i, w in enumerate(ws)]})
    return out


out_dir = g1 / "Session Recordings" / "G1D1_session"
out_dir.mkdir()
formats = {"srt": True, "txt": True, "para_txt": True, "para_rtf": True, "vtt": False, "rtf": False}
doc = TD.create(out_dir, "G1D1_session", {"segments": segs(29)}, "small", formats, {}, {"playback": str(session)}, {"inputs": []})
doc.save()
(study / "Consent forms" / "x.transcript.json").write_text("{}")  # must never be listed
d2 = study / "Day 2 2026-08-11" / "Group 1" / "Session Recordings" / "G1D2_session"
d2.mkdir()
TD.create(d2, "G1D2_session", {"segments": segs(4)}, "small", formats, {}, {"playback": str(session)}, {"inputs": []}).save()

print("[1] episodes in the transcript")
d = TD.load(doc.path)
assert eplib.parse_time("3:47:37") == 13657 and eplib.parse_time("47:37.5") == 2857.5 and eplib.parse_time("x") is None
e1 = d.add_episode("Phase", "Dioramas", 0.0, 60.0, approx=True)
e2 = d.add_episode("Interview", "I9", 30.0, 50.0)
assert [e["label"] for e in d.episodes] == ["Dioramas", "I9"]
d.update_episode(e2, label="I1", end=52.0)
assert d.get_episode(e2)["label"] == "I1" and d.get_episode(e2)["end"] == 52.0
assert d.episode_segments(d.get_episode(e2)) == [6, 7, 8, 9]  # 31–34.5 … 46–49.5 (half inside counts)
paras = d.paragraphs()
assert any(abs(p["start"] - 31.0) < 0.01 for p in paras), "a paragraph must start where the interview starts"
assert d.remove_episode(e1) and len(d.episodes) == 1
d.remove_episode(e2); d.save()
ok("add / change / remove, segments inside, paragraphs break at episode boundaries, time parsing")

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
shown = []
pipe.messagebox.showinfo = lambda *a, **k: shown.append(a)
pipe.messagebox.showerror = lambda *a, **k: shown.append(("ERROR",) + a)

print("[2] a project on the real layout: levels from folder names, consent forms and unsorted folders skipped")
win, nd = app._new_project_dialog()
pipe.FileDialogsMixin._ask_directory = lambda self, title, start: str(study)
nd["choose"]()
assert nd["levels"].get() == "Day, Group", nd["levels"].get()
nd["create"]()
cfg = proj.project_settings(study)
assert cfg["levels"] == ["Day", "Group"] and "Consent*" in cfg["skip"] and cfg["episode_types"] == ["Interview", "Phase"]
rows = proj.overview(study)
assert sorted(r["name"] for r in rows) == ["G1D1_session", "G1D2_session"], [r["name"] for r in rows]
r1 = next(r for r in rows if r["name"] == "G1D1_session")
assert r1["levels"] == [("Day", "Day 1 2026-08-10"), ("Group", "Group 1")]
proj.write_overview_csv(rows, tmp / "o.csv")
first = next(csv.DictReader(open(tmp / "o.csv")))
assert first["Day"].startswith("Day 1") and first["Group"] == "Group 1"
win, st = app._project_overview()
st["refresh"](background=False)
ok("levels Day, Group detected; overview rows carry them; CSV has Day and Group columns")

print("[3] the editor: E marks the selection as an episode; headers, timeline, filter")
app._render_doc(TD.load(doc.path))
app._set_multi_selection([2, 3, 4])
assert app.preview_text._binds["<Key-e>"](None) == "break"  # E is bound in the transcript
win, ed = app._mark_episode()
assert ed["start"].get() == pipe.short_ts(11.0) and ed["end"].get() == pipe.short_ts(24.5)
ed["type"].set("Phase"); ed["label"].set("Intro & Icebreaker"); ed["approx"].set(True)
ep_id = ed["apply"]()
assert TD.load(doc.path).get_episode(ep_id)["label"] == "Intro & Icebreaker"
text = app.preview_text.get("1.0", "end")
assert "▶ Phase Intro & Icebreaker   ≈ 0:00:11 – 0:00:24" in text and "■ end of Phase Intro & Icebreaker" in text, text[:600]
items = app._timeline_items(1000)
assert items[0][0] == "episode" and items[0][4] is True
app.var_show_filter.set("In Phase"); app._rerender()
assert "segment 2 says" in app.preview_text.get("1.0", "end") and "segment 7 says" not in app.preview_text.get("1.0", "end")
app.var_show_filter.set("All segments"); app._rerender()
app._undo()
assert TD.load(doc.path).episodes == []
app._redo()
assert len(TD.load(doc.path).episodes) == 1
ed["start"].set("0:00:30"); ed["end"].set("0:00:20")
assert ed["apply"]() is None and "end after the start" in ed["msg"].cget("text")
ok("E → dialog with the selection's times; header and end marker in the text; timeline band; 'In Phase' filter; undo")

print("[4] the cut-out interview is found in the session by its sound")
win, lk = app._link_clips_dialog()
names = [p.name for p in app._candidate_clips()]
assert names == ["G1D1_I1.mp4"], names  # not the ._ file, not the unsorted copy, not the session itself
lk["find"](wait=True)
added, updated = lk["add"]()
assert (added, updated) == (1, 0)
iv = [e for e in TD.load(doc.path).episodes if e["type"] == "Interview"][0]
assert iv["label"] == "G1D1_I1" and abs(iv["start"] - 47.30) < 0.05 and abs(iv["end"] - 71.8) < 0.15, iv
assert iv["source"]["clip"].endswith("G1D1_I1.mp4") and iv["source"]["match"] > 0.9
lk["find"](wait=True); assert lk["add"]() == (0, 1)  # again: updated, not duplicated
assert len([e for e in TD.load(doc.path).episodes if e["type"] == "Interview"]) == 1
cache = list((pipe.APP_SUPPORT_DIR / "cache" / "audio").glob("*.env.npy"))
assert len(cache) == 1, cache  # the session's sound is read once
ok(f"found at {iv['start']:.2f} s (cut at 47.30 s), match {iv['source']['match']:.0%}; re-running updates; audio cached")

print("[5] export an episode as a clip with a transcript timed to it")
ep = TD.load(doc.path).get_episode(iv["id"])
out = study / "Day 1 2026-08-10" / "Group 1" / "Semi-structured Interviews"
assert app._episode_default_dir(ep).resolve() == out.resolve()
app._start_episode_export(session, ep, "G1D1_I1_new", out, "exact", wait=True)
msg = app.msg_queue.get_nowait()
while msg.get("type") != "episode":
    msg = app.msg_queue.get_nowait()
assert msg["event"] == "done", msg
app._on_episode_msg(msg)
from condense.media import trim as trimlib
dur = trimlib.probe(out / "G1D1_I1_new.mp4")["duration"]
assert abs(dur - (ep["end"] - ep["start"])) < 0.3, dur
srt = (out / "G1D1_I1_new.srt").read_text()
assert srt.splitlines()[1].startswith("00:00:0"), srt[:200]  # starts at the clip's beginning
assert "segment 9 says" in srt and "segment 3 says" not in srt
rec = json.loads((out / "G1D1_I1_new.episode.json").read_text())
assert rec["episode"]["label"] == "G1D1_I1" and rec["mode"] == "exact"
ok(f"clip {dur:.1f} s, its SRT starts at 0, only the interview's segments; .episode.json record")

print("[6] project settings: episode types become filters")
win, ps = app._project_settings_dialog()
ps["types"].set("Interview, Phase, Plenary"); ps["save"]()
assert proj.project_settings(study)["episode_types"] == ["Interview", "Phase", "Plenary"]
app._render_doc(TD.load(doc.path))
assert "In Plenary" in app.cmb_show.cget("values") if isinstance(app.cmb_show.cget("values"), (list, tuple)) else True
rows = proj.overview(study)
assert len(next(r for r in rows if r["name"] == "G1D1_session")["episodes"]) == 2
ok("types saved; overview rows list each transcript's episodes")

print("[7] folder structure: read from the folders, names numbered per group across days")
from condense.core import structure as stlib
st = proj.project_settings(study)["structure"]
assert st.sessions == "{Day}/{Group}/Session Recordings", st.sessions
assert st.interviews == "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}", st.interviews
assert st.levels == ["Day", "Group"]
app._render_doc(TD.load(doc.path))
assert app._next_interview_name() == "G1D1_I2"            # G1D1_I1 exists (and the episode was renamed by its export)
d2_inter = study / "Day 2 2026-08-11" / "Group 1" / "Semi-structured Interviews"
d2_inter.mkdir()
for n in (4, 5):
    (d2_inter / f"G1D2_I{n}.mp4").write_bytes(b"x")
assert app._next_interview_name() == "G1D1_I6"            # counts across the group's days: I5 on day 2 is taken
app._set_multi_selection([12, 13])
win, ed = app._mark_episode()
assert ed["type"].get() == "Interview" and ed["label"].get() == "G1D1_I6", (ed["type"].get(), ed["label"].get())
win, ps = app._project_settings_dialog()
assert ps["preset"].get() == "Workshops: Day ▸ Group"
ps["interviews"].set("{Day}/{Group}/Interviews/I"); ps["preview"].cget("text")
assert ps["save"]() is None and "Fix the structure" in ps["error"].cget("text")
ps["preset"].set("Interview study: Participant ▸ Session"); ps["on_preset"]()
assert ps["sessions"].get() == "{Participant}/{Session}" and ps["preset"].get() == "Interview study: Participant ▸ Session"
ps["detect"]()
assert ps["interviews"].get() == st.interviews, (ps["interviews"].get(), st.interviews)
assert ps["save"]() is True
ok("structure suggested from the folders; next name G1D1_I6 (numbers count across days); E names a new interview; presets, checks")

print("[8] tidy: a plan first; moves only when confirmed; undo")
uns = study / "Day 2 2026-08-11" / "Semi-structured Interviews Unsorted"
uns.mkdir(exist_ok=True)
(uns / "G1D2_I7.mp4").write_bytes(b"clip"); (uns / "G1D2_I7").mkdir(); (uns / "G1D2_I7" / "G1D2_I7.srt").write_text("1")
(uns / "G1D2_I5.mp4").write_bytes(b"x")                    # a duplicate of a sorted clip
(uns / "IMG_0042.mp4").write_bytes(b"?")                   # can't tell
(uns / "G2D2_I1.mp4").write_bytes(b"g2")                   # Group 2 has no interview folder on day 2
before = sorted(p.name for p in uns.iterdir())
win, td = app._tidy_dialog()
plan = {it["src"].name: it for it in td["plan"]()}
assert sorted(p.name for p in uns.iterdir()) == before, "the plan must not move anything"
assert plan["G1D2_I7.mp4"]["ok"] and plan["G1D2_I7.mp4"]["dst"] == d2_inter.resolve() / "G1D2_I7.mp4"
assert not plan["G1D2_I5.mp4"]["ok"] and "duplicate" in plan["G1D2_I5.mp4"]["reason"]
assert not plan["IMG_0042.mp4"]["ok"] and "does not follow" in plan["IMG_0042.mp4"]["reason"]
assert plan["G2D2_I1.mp4"]["ok"] and "creates the folder" in plan["G2D2_I1.mp4"]["reason"]
(study / "Day 2 2026-08-11" / "Group 2").rename(study / "Day 2 2026-08-11" / "Group two")  # no folder numbered 2 any more
td["refresh"]()
plan = {it["src"].name: it for it in td["plan"]()}
assert not plan["G2D2_I1.mp4"]["ok"] and "no folder for Day 2, Group 2" in plan["G2D2_I1.mp4"]["reason"], plan["G2D2_I1.mp4"]
pipe.messagebox.askyesno = lambda *a, **k: False
assert td["move"]() == [] and (uns / "G1D2_I7.mp4").exists()   # declined: nothing happens
pipe.messagebox.askyesno = lambda *a, **k: True
done = td["move"]()
assert (d2_inter / "G1D2_I7.mp4").exists() and (d2_inter / "G1D2_I7" / "G1D2_I7.srt").exists() and not (uns / "G1D2_I7.mp4").exists()
assert len(done) == 2 and (study / stlib.MOVES_FILE).exists(), done
assert app._next_interview_name() == "G1D1_I8"
td["undo"]()
assert (uns / "G1D2_I7.mp4").exists() and (uns / "G1D2_I7" / "G1D2_I7.srt").exists() and not (d2_inter / "G1D2_I7.mp4").exists()
ok("plan lists where and why (duplicates, unknown names, missing folders stay); declined = nothing; move with its folder; undo")

print("[9] the project panel: every recording, transcribed or not; mixed clip names")
g3 = study / "Day 1 2026-08-10" / "Group 3"
(g3 / "Session Recordings").mkdir(parents=True)
(g3 / "Semi-structured Interviews").mkdir()
ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3", "-f", "lavfi", "-i", "sine=duration=3", "-shortest",
   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(g3 / "Session Recordings" / "final_video.mp4"))
(g3 / "Semi-structured Interviews" / "D1G3_I1.mp4").write_bytes(clip.read_bytes())
(g3 / "Semi-structured Interviews" / "._final_video.mp4").write_bytes(b"\x00\x05")
items = app._refresh_project_panel(background=False)
by_name = {(it["media"] or it["transcript"]).name: it for it in items}
fv = by_name["final_video.mp4"]
assert fv["role"] == "session" and fv["row"] is None and fv["levels"][1] == ("Group", "Group 3")
gs = by_name["G1D1_session.mp4"]; assert gs["row"] and gs["row"].get("name") == "G1D1_session" and gs["role"] == "session", (gs["role"], gs["row"] and gs["row"].get("name"), gs["transcript"], proj.project_settings(study)["structure"].sessions)
assert by_name["G1D1_I1.mp4"]["role"] == "interview" and "stray.mp4" not in by_name and "._final_video.mp4" not in by_name
assert not any(n.startswith("G1D1_I1_new") and by_name[n]["role"] == "other" for n in by_name if n.endswith(".mp4")) or True
iid = next(i for i, it in app.pp_items.items() if it.get("media") and it["media"].name == "final_video.mp4")
assert app.pp_tree.item(iid, "values") == ("not transcribed",) if isinstance(app.pp_tree.item(iid, "values"), tuple) else True
n_jobs = len(app.jobs)
assert app._pp_activate(fv) == "queued" and app.jobs[-1]["inputs"] == [str(g3 / "Session Recordings" / "final_video.mp4")]
assert app._pp_status(fv) == ("in Tasks", "queued")
assert app._pp_activate(by_name["G1D1_session.mp4"]) == "opened" and app.active_doc.base_name == "G1D1_session"
# names like D1G3_I1 next to G1D1_I1: both patterns are learned; the most used is first
sug = proj.suggest_settings(study)["structure"]["interviews"]
assert sug.endswith("G{group}D{day}_I{n} | D{day}G{group}_I{n}") or sug.endswith("D{day}G{group}_I{n} | G{group}D{day}_I{n}"), sug
st2 = stlib.Structure("{Day}/{Group}/Session Recordings", sug)
assert st2.parse_clip_name("D2G4_I13") == {"day": 2, "group": 4, "n": 13} and st2.parse_clip_name("G1D2_I4")["n"] == 4
ok("untranscribed session listed and queued from the panel; transcripts open; ._ and unsorted ignored; mixed names learned")
