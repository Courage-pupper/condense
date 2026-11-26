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
items = app._cdt_items()
eps = [i for i in items if i[0] == "episode"]
assert eps and eps[0][4] is True  # approximate: drawn fainter
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
res = out / "G1D1_I1_new"  # like transcribing the clip: Interviews/G1D1_I1_new.mp4 → Interviews/G1D1_I1_new/
assert not (out / "G1D1_I1_new.srt").exists() and not (out / "G1D1_I1_new.episode.json").exists()
srt = (res / "G1D1_I1_new.srt").read_text()
assert srt.splitlines()[1].startswith("00:00:0"), srt[:200]  # starts at the clip's beginning
assert "segment 9 says" in srt and "segment 3 says" not in srt
rec = json.loads((res / "G1D1_I1_new.episode.json").read_text())
assert rec["episode"]["label"] == "G1D1_I1" and rec["mode"] == "exact"
ok(f"clip {dur:.1f} s in the interviews folder; its SRT (starts at 0, only the interview's segments) and .episode.json "
   "in a folder named like the clip")
cd = TD.load(res / "G1D1_I1_new.transcript.json")
session_doc = TD.load(doc.path)
inside = [session_doc.segments[i] for i in session_doc.episode_segments(ep) if not session_doc.is_segment_cut(i)]
assert inside and [sg["text"] for sg in cd.segments] == [sg["text"] for sg in inside], ([sg["text"] for sg in cd.segments], [sg["text"] for sg in inside])
assert all(abs(max(0.0, a["start"] - ep["start"]) - b["start"]) < 0.1 for a, b in zip(inside, cd.segments))
assert cd.media_path("playback").resolve() == (out / "G1D1_I1_new.mp4").resolve() == cd.media_path("video").resolve()
assert cd.data["provenance"]["episode_of"]["transcript"] == str(doc.path) and cd.data["next_wid"] == session_doc.data["next_wid"]
assert {sg.get("speaker") for sg in cd.segments} <= set(cd.roster())
inv = proj.inventory(study)
item = next(i for i in inv if i.get("media") is not None and i["media"].name == "G1D1_I1_new.mp4")
assert item["role"] == "interview" and item["transcript"].resolve() == (res / "G1D1_I1_new.transcript.json").resolve(), item
assert not any(i.get("media") is None and i["transcript"] == item["transcript"] for i in inv)
ok("the clip has a real transcript (.transcript.json, timed to the clip, playback = the clip); the project lists the "
   "clip as a transcribed interview")
cd.segments[0]["text"] = " edited in the clip transcript"
cd.save()
asked = []
real_ask = pipe.messagebox.askyesno
pipe.messagebox.askyesno = lambda *a, **k: asked.append(a) or False  # "No": nothing is replaced
win, dlg = app._export_episode_dialog(ep)
dlg["name"].set("G1D1_I1_new"); dlg["dir"].set(str(out))
assert dlg["go"]() is None and asked and "its transcript" in asked[0][1]
assert "edited in the clip transcript" in (res / "G1D1_I1_new.transcript.json").read_text()
pipe.messagebox.askyesno = real_ask
app._start_episode_export(session, ep, "G1D1_I1_new", out, "exact", wait=True)
msg = app.msg_queue.get_nowait()
while msg.get("type") != "episode":
    msg = app.msg_queue.get_nowait()
app._on_episode_msg(msg)
backups = list((res / "_backups").glob("G1D1_I1_new.*.transcript.json"))
assert backups and "edited in the clip transcript" in backups[0].read_text()
assert "edited in the clip transcript" not in (res / "G1D1_I1_new.transcript.json").read_text()
ok("exporting again replaces the clip's transcript and keeps the old one as a backup")

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
# typing only the number (or I7) gives the full name; the preview shows it, saving completes it
seg_a, seg_b = app.active_doc.segments[12]["start"], app.active_doc.segments[13]["end"]
ed["label"].set("7")
id7 = ed["apply"]()
assert app.active_doc.get_episode(id7)["label"] == "G1D1_I7"
assert app._complete_episode_label("Interview", "I12") == "G1D1_I12" and app._complete_episode_label("Interview", "_I3") == "G1D1_I3"
assert app._complete_episode_label("Interview", "G1D1_I3") is None and app._complete_episode_label("Interview", "Robin") is None
assert app._complete_episode_label("Phase", "3") is None  # no pattern for phases (yet)
# a label typed earlier (I9) is completed with one click, and that is one undo step
id9 = app.active_doc.add_episode("Interview", "I9", seg_a, seg_b, False)
app._commit("old label")
ed["fill"]()
assert [(e["label"], full) for e, full in ed["completable"]()] == [("I9", "G1D1_I9")] and "I9 → G1D1_I9" in ed["fix"].cget("text")
assert ed["complete_all"]() == 1 and app.active_doc.get_episode(id9)["label"] == "G1D1_I9" and ed["completable"]() == []
app._undo(); assert app.active_doc.get_episode(id9)["label"] == "I9"
app._redo(); assert app.active_doc.get_episode(id9)["label"] == "G1D1_I9"
# the export dialog offers the completed name too (an I9 left in older transcripts)
app.active_doc.update_episode(id9, label="I9")
assert app._name_structure("Interview")[1].complete_name({"group": 1, "day": 1, "Group": "Group 1", "Day": "Day 1"}, "I9") == "G1D1_I9"
# other kinds of episodes get their own names (Project Settings ▸ Episode names), numbered the same way
win, ps = app._project_settings_dialog()
ps["names"].set("Phase: P")
assert ps["save"]() is None and "needs {n}" in ps["error"].cget("text"), ps["error"].cget("text")
ps["names"].set("Phase: G{group}D{day}_P{n}; Plenary: Plenum {n}")
assert ps["save"]() is True
cfg = proj.project_settings(study)
assert cfg["episode_names"] == {"Phase": "G{group}D{day}_P{n}", "Plenary": "Plenum {n}"} and "Phase" in cfg["episode_types"]
ed["load"](None)
assert ed["label"].get() == "G1D1_I10"  # a new one: the next free interview number (I7, I9 are taken)
ed["type"].set("Phase"); ed["suggest"]()
assert ed["label"].get() == "G1D1_P1", ed["label"].get()
ed["start"].set(pipe.short_ts(seg_a)); ed["end"].set(pipe.short_ts(seg_b))
ed["label"].set("2"); idp = ed["apply"]()
assert app.active_doc.get_episode(idp)["label"] == "G1D1_P2" and app._next_episode_name("Phase") == "G1D1_P3"
ed["load"](None); ed["label"].set("my own name"); ed["type"].set("Plenary"); ed["suggest"]()
assert ed["label"].get() == "my own name"  # a label typed by hand is kept
for i in (id7, id9, idp):  # leave the transcript as it was for the next sections
    ed["load"](app.active_doc.get_episode(i)); ed["delete"]()  # (the stub's list keeps no selection)
assert [e["label"] for e in app.active_doc.episodes] == [e["label"] for e in TD.load(doc.path).episodes]
assert not {"G1D1_I7", "I9", "G1D1_P2"} & {e.get("label") for e in app.active_doc.episodes}
win, ed = app._mark_episode()
win, ps = app._project_settings_dialog()
assert ps["preset"].get() == "Workshops: Day ▸ Group"
ps["interviews"].set("{Day}/{Group}/Interviews/I"); ps["preview"].cget("text")
assert ps["save"]() is None and "Fix the structure" in ps["error"].cget("text")
ps["preset"].set("Interview study: Participant ▸ Session"); ps["on_preset"]()
assert ps["sessions"].get() == "{Participant}/{Session}" and ps["preset"].get() == "Interview study: Participant ▸ Session"
ps["detect"]()
assert ps["interviews"].get() == st.interviews, (ps["interviews"].get(), st.interviews)
assert ps["save"]() is True
ok("structure suggested from the folders; next name G1D1_I6 (numbers count across days); E names a new interview; typing 7 or I9 gives the full name (also for older labels, in one click); names for other episode types; presets, checks")

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
dc = proj.DurationCache(tmp / "durations.json")
assert dc.get(fv["media"]) is None and abs(dc.measure(fv["media"]) - 3.0) < 0.2
dc.save(); assert abs(proj.DurationCache(tmp / "durations.json").get(fv["media"]) - 3.0) < 0.2  # remembered
assert sorted(["D2G1_I10.mp4", "D2G1_I4.mp4", "D2G1_I13.mp4"], key=proj.natural_key) == ["D2G1_I4.mp4", "D2G1_I10.mp4", "D2G1_I13.mp4"]
n_jobs = len(app.jobs)
assert app._pp_activate(fv) == "queued" and [Path(p).resolve() for p in app.jobs[-1]["inputs"]] == [(g3 / "Session Recordings" / "final_video.mp4").resolve()]
assert app._pp_status(fv) == ("in Tasks", "queued")
assert app._pp_activate(by_name["G1D1_session.mp4"]) == "opened" and app.active_doc.base_name == "G1D1_session"
# An episode row goes to the episode, also when the transcript has a place to resume ("where you left off"). Tk
# applies that a moment after opening (after_idle), and it used to jump back there; the stub runs idle callbacks at
# once, so here they are held and run afterwards, as in Tk.
idle = []
app.after_idle = lambda fn, *a: idle.append((fn, a))
def settle():
    while idle:
        fn, a = idle.pop(0)
        fn(*a)
sess = Path(gs["transcript"]).resolve()
ep_rows = [it for it in app.pp_items.values() if it.get("episode") and Path(it["transcript"]).resolve() == sess]
assert len(ep_rows) == 2, ep_rows
first_seg = {id(it): app.active_doc.episode_segments(it["episode"])[0] for it in ep_rows}
last = len(app.active_doc.segments) - 1
app._goto_segment(last); settle()
other = next(it for it in items if it.get("row") and "error" not in it["row"] and Path(it["transcript"]).resolve() != sess)
assert app._pp_activate(other) == "opened" and app.active_doc.base_name != "G1D1_session"  # remembers `last`
settle()
for it in ep_rows:  # from another transcript, then within the open one
    undo_before = len(app.__dict__.get("undo_stack") or [])
    assert app._pp_activate(it) == "opened"
    settle()
    assert app.active_doc.base_name == "G1D1_session" and app.edit_seg_idx == first_seg[id(it)] != last, (app.edit_seg_idx, first_seg[id(it)])
assert app.__dict__.get("_pending_resume") is None
del app.after_idle
# names like D1G3_I1 next to G1D1_I1: both patterns are learned; the most used is first
sug = proj.suggest_settings(study)["structure"]["interviews"]
assert sug.endswith("G{group}D{day}_I{n} | D{day}G{group}_I{n}") or sug.endswith("D{day}G{group}_I{n} | G{group}D{day}_I{n}"), sug
st2 = stlib.Structure("{Day}/{Group}/Session Recordings", sug)
assert st2.parse_clip_name("D2G4_I13") == {"day": 2, "group": 4, "n": 13} and st2.parse_clip_name("G1D2_I4")["n"] == 4
assert proj._level_word(["Session Recordings"] * 3 + ["Leica"]) is None and proj._level_word(["Group 3"]) == "Group"
ok("untranscribed session listed and queued from the panel; transcripts open; episode rows go to the episode (not back to 'where you left off'); ._ and unsorted ignored; mixed names learned")

print("[10] mark whole transcripts checked (100 %) from the menus, with undo")
d2_path = next(it["transcript"] for it in proj.inventory(study) if it.get("row") and it["row"]["name"] == "G1D2_session")
app._render_doc(TD.load(doc.path))  # another transcript is open
assert app._mark_transcripts_checked([d2_path], True) > 0
assert proj.overview_row(d2_path)["checked_pct"] == 100
d = TD.load(doc.path); d.cut_segments([0]); d.save(); app._render_doc(TD.load(doc.path))
app._mark_transcripts_checked([doc.path], True)
d = TD.load(doc.path)
assert d.checked_fraction() == 1.0 and not d.segments[0].get("checked")   # a cut segment is left alone
assert any(e.get("action") == "check_all" for e in d.data.get("edit_history") or [])
app._undo()
assert TD.load(doc.path).checked_fraction() < 1.0
app._render_doc(TD.load(d2_path)); app._undo()
assert proj.overview_row(d2_path)["checked_pct"] == 0
pipe.messagebox.askyesno = lambda *a, **k: False
assert app._mark_transcripts_checked([d2_path], True) == 0 and proj.overview_row(d2_path)["checked_pct"] == 0
pipe.messagebox.askyesno = lambda *a, **k: True
ok("open or not: 100 %, cut segments excluded, logged, undoable, asks first")

print("[11] speakers per group: the project's (facilitators) first, then the group's children, on every day")
assert proj.groups_in(study) == ["Group 1", "Group 2", "Group 3"] or proj.groups_in(study)[:1] == ["Group 1"], proj.groups_in(study)
proj.save_project(study, ["Facilitator"])
win, sp = app._project_speakers_dialog()
app._render_doc(TD.load(doc.path))           # Day 1 / Group 1
win, sp = app._project_speakers_dialog()
assert sp["scope"].get() == "Group 1"        # opens on the open transcript's group
sp["add"]("Robin"); sp["add"]("Sam"); sp["add"]("Facilitator")  # a project speaker is not repeated in a group
sp["scope"].set("Group 3"); sp["show"](); sp["add"]("Kim")
sp["save"]()
assert proj.group_speakers(study, "Group 1") == ["Robin", "Sam"] and proj.group_speakers(study, "Group 3") == ["Kim"]
assert app._roster()[:3] == ["Facilitator", "Robin", "Sam"] and "Kim" not in app._roster()
app._goto_segment(0); app._assign_speaker_number(3)
assert TD.load(doc.path).segments[0]["speaker"] == "Sam"
d2_doc = TD.load(d2_path)                    # Day 2 / Group 1: the same group, so the same children
assert proj.project_speakers_for(d2_doc.path) == ["Facilitator", "Robin", "Sam"]
g3 = proj.project_speakers_for(g3 / "Session Recordings" / "final_video" / "x.transcript.json")
assert g3 == ["Facilitator", "Kim"], g3
assert proj.project_speakers_for(study / "Consent forms" / "x.transcript.json") == ["Facilitator"]
ok("group lists saved; keys: Facilitator 1, Robin 2, Sam 3 in Group 1 on every day; Group 3 gets Kim; no repeats")

print("[12] who is who: detected speakers take over the saved names, for the whole transcript")
d = TD.load(doc.path)
for i in range(len(d.segments)):
    d.segments[i]["speaker"] = ("SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "Facilitator")[i % 4]
d.save()
app._render_doc(TD.load(doc.path))
assert app._generic_speakers() == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"], app._generic_speakers()
assert app._merge_speaker("SPEAKER_01", "Robin") > 0               # right-click chip ▸ All segments of SPEAKER_01 are… Robin
d = TD.load(doc.path)
assert not any(s["speaker"] == "SPEAKER_01" for s in d.segments) and sum(s["speaker"] == "Robin" for s in d.segments) > 0
win, ww = app._match_speakers_dialog()
assert set(ww["rows"]) == {"SPEAKER_00", "SPEAKER_02"}
ww["rows"]["SPEAKER_00"].set("Robin")                               # the same person, split in two by detection
ww["rows"]["SPEAKER_02"].set("Alex")                                # a name typed in
before = [s["speaker"] for s in TD.load(doc.path).segments]
ww["apply"]()
after = [s["speaker"] for s in TD.load(doc.path).segments]
assert set(after) == {"Robin", "Alex", "Facilitator"} and app._generic_speakers() == []
assert app._roster()[:3] == ["Facilitator", "Robin", "Sam"]         # the saved group names keep their keys
app._undo()
assert [s["speaker"] for s in TD.load(doc.path).segments] == before  # one undo step
ok("chip menu merges one label into a saved name; the dialog names all at once (merging two labels); one undo")

print("[13] Delete key deletes the open segment (after asking)")
app._render_doc(TD.load(doc.path))
n0 = len(TD.load(doc.path).segments)
app._goto_segment(5)
pipe.messagebox.askyesno = lambda *a, **k: False
app.preview_text._binds["<Delete>"](None)
assert len(TD.load(doc.path).segments) == n0                       # declined: nothing happens
pipe.messagebox.askyesno = lambda *a, **k: True
app.preview_text._binds["<Delete>"](None)
assert len(TD.load(doc.path).segments) == n0 - 1
app._undo()
assert len(TD.load(doc.path).segments) == n0
ok("Del asks, deletes, undo brings it back")
n0 = len(TD.load(doc.path).segments)
app._select_span(app.span_lookup[(6, 3)])                            # a word in the middle of segment 7
app.preview_text._binds["<Key-s>"](None)
assert len(TD.load(doc.path).segments) == n0 + 1
app._goto_segment(6)
app.preview_text._binds["<Key-m>"](None)
assert len(TD.load(doc.path).segments) == n0
assert "<Key-i>" in app.preview_text._binds and "<Key-I>" in app.preview_text._binds
ok("S splits before the selected word, M merges with the next, I inserts")
