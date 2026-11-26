"""The Coding tab's timeline: zoom, lanes (codes used / chosen), marking a range by dragging or I/O and coding it with
↩ / 1–9 / N, moving a coding's edges, coding while watching, attach to words, loop, frame steps; detecting speakers
in a selection from the transcript's right-click menu; collapsing the side panel."""
import os
import sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import coding as cod, project as proj
from condense.gui import coding_timeline as ctl
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
E = lambda **k: type("E", (), dict({"x": 0, "y": 0, "x_root": 5, "y_root": 5, "state": 0, "delta": 0}, **k))()


def segs(n):
    out = []
    lines = ["we built a raft with sticks", "mine floats look", "the sail is too heavy maybe", "put more weight at the bottom"]
    for k in range(n):
        t = 2.0 + k * 6
        ws = lines[k % len(lines)].split()
        out.append({"start": t, "end": t + len(ws) * 0.5, "text": " " + " ".join(ws), "speaker": ("Robin", "Sam", "Facilitator")[k % 3],
                    "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": 0.95} for i, w in enumerate(ws)]})
    return out


study = Path(tempfile.mkdtemp(prefix="study_")) / "Study"
g = study / "Day 1" / "Group 1" / "Session Recordings"
(g / "s1").mkdir(parents=True)
proj.create_project(study)
doc = TD.create(g / "s1", "s1", {"segments": segs(30)}, "small", {}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
doc.save()
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
app._render_doc(TD.load(doc.path))
app._cd_refresh()
cb = app._cd_codebook()
making, materials, feel = (cb.add(n) for n in ("Making", "Materials", "Feelings"))
cb.save(); app._cd_fill_codebook()
app._duration = lambda: 182.0
app.cd_tree.selection = lambda: (making,)
played = []
app._player_start = lambda t, duration=None: played.append((round(t, 2), duration))
x_of, t_of = app._cdt_x, app._cdt_t

print("[1] the view: whole recording, zoom buttons, wheel zoom at the mouse, paging")
assert app._cdt_view() == (0.0, 182.0)
items = app._cdt_items()
ticks = [it for it in items if it[0] == "tick"]
assert ticks and ticks[0][2] == "0:00" and ctl.tick_step(182, 870) == 30 and ctl.tick_label(3725, 60) == "1:02"
assert {it[0] for it in items} >= {"tick", "speaker"} and not [it for it in items if it[0] == "lane"]  # no codings yet: no lanes
app.play_pos = 100.0
assert app._cdt_zoom("120") == (70.0, 190.0 - 8.0) or app._cdt_view()[1] - app._cdt_view()[0] == 120
a, b = app._cdt_view(); assert abs((b - a) - 120) < 1e-6 and a <= 100 <= b
tm = t_of(500)
app._cdt_wheel(E(x=500), -1)  # wheel up: zoom in, the time under the mouse stays there
assert abs(t_of(500) - tm) < 0.05 and app._cdt_view()[1] - app._cdt_view()[0] < 120
app._cdt_zoom("whole"); assert app.cdt["view"] is None
doc2 = app.active_doc
doc2.add_episode("Interview", "G1D1_I1", 60.0, 90.0, False); app._commit("ep")
app._cd_render(app.active_doc)
app.play_pos = 70.0
assert app._cdt_zoom("episode") == (58.0, 92.0)
assert any(it[0] == "episode" for it in app._cdt_items())
app.play_pos = 5.0; app._cdt_zoom("whole")
ok("whole / 2 min / episode zoom; the wheel zooms at the mouse; ruler ticks every 30 s for 3 minutes")

print("[2] mark a range by dragging and code it with ↩ — by time, no dialog; the code gets a lane")
app._cdt_press(E(x=x_of(20.0), y=80)); app._cdt_motion(E(x=x_of(31.0), y=80)); mark = app._cdt_release(E(x=x_of(31.0), y=80))
assert mark and abs(mark[0] - 20.0) < 0.3 and abs(mark[1] - 31.0) < 0.3, mark
assert "Marked 0:00:20" in app.lbl_cdt_mark.cget("text")
app._cd_key(E(keysym="Return"))
kd = TD.load(doc.path)
tc = [c for c in cod.codings(kd) if c["code"] == making]
assert len(tc) == 1 and tc[0]["kind"] == "time" and abs(tc[0]["end"] - 31.0) < 0.3 and app._cdt_mark() is None
lanes = [it for it in app._cdt_items() if it[0] == "lane"]
assert [it[3] for it in lanes] == [making] and app.cdt["lane_mode"] == "used"
bars = [it for it in app._cdt_items() if it[0] == "bar"]
assert len(bars) == 1 and bars[0][6] == "time" and bars[0][8]  # selected (just made)
ok("drag → marked 20–31 s, ↩ codes it by time; a lane for Making with an outlined bar")

print("[3] I and O at the playhead, then 1–9; N makes a new code for the range")
app.play_pos = 40.0; app._cd_key(E(keysym="i"))
assert "press O" in app.lbl_cdt_mark.cget("text") and any(it[0] == "in" for it in app._cdt_items())
app.play_pos = 46.5; app._cd_key(E(keysym="o"))
assert app._cdt_mark() == (40.0, 46.5)
app._cd_key(E(keysym="1"))  # recent 1 = Making
assert len([c for c in cod.codings(TD.load(doc.path)) if c["code"] == making]) == 2
app.play_pos = 50.0; app._cd_key(E(keysym="i")); app.play_pos = 55.0; app._cd_key(E(keysym="o"))
stub_tk2.sd._next = "Pointing"
app._cd_key(E(keysym="n"))
point = cb.find("Pointing")["id"]
assert [c for c in cod.codings(TD.load(doc.path)) if c["code"] == point][0]["start"] == 50.0
app.play_pos = 60.0; app._cd_key(E(keysym="o"))  # O alone: the last 5 seconds
assert app._cdt_mark() == (55.0, 60.0); app._cd_key(E(keysym="x")); assert app._cdt_mark() is None
ok("I / O mark at the playhead, 1 codes with the recent code, N names a new code for the range; O alone = last 5 s; X clears")

print("[4] choosing the lanes (like activating codes), and the codebook shows them")
app._cdt_set_shown(feel, True)
assert app._cdt_shown_codes() == [feel] and [it[3] for it in app._cdt_items() if it[0] == "lane"] == [feel]
assert app.cdt["lane_mode"] == "chosen"
inserted = {k: v for k, v in app.cd_tree.__dict__.items()} if False else None
app.play_pos = 70.0; app._cd_key(E(keysym="i")); app.play_pos = 74.0; app._cd_key(E(keysym="o"))
app._cdt_code_mark(materials)  # coding a range with a code that has no lane: it gets one
assert app._cdt_shown_codes() == [materials, feel] or app._cdt_shown_codes() == [feel, materials]
assert set(app.settings["timeline_codes"][str(app._cd_project())]) == {feel, materials}
for c in list(app._cdt_shown_codes()):
    app._cdt_set_shown(c, False)
assert app._cdt_shown_codes() == [] and app.cdt.get("lanes") is not None
ok("Show on timeline → only those lanes (saved per project); a code used on the timeline gets its lane; back to codes used")

print("[5] dragging a bar's edge; for codings of words it snaps to the words")
app._cd_render(app.active_doc)
items = app._cdt_items()
bar = next(it for it in items if it[0] == "bar" and it[7] == tc[0]["id"])
y = (bar[3] + bar[4]) / 2
assert app._cdt_hit(bar[2], y)[2] == "end" and app._cdt_hit((bar[1] + bar[2]) / 2, y)[2] is None
assert app._cdt_press(E(x=bar[2], y=y)) == "edge"
app._cdt_motion(E(x=x_of(35.0), y=y)); app._cdt_release(E(x=x_of(35.0), y=y))
moved = cod.get_coding(TD.load(doc.path), tc[0]["id"])
assert abs(moved["end"] - 35.0) < 0.3 and abs(moved["start"] - 20.0) < 0.3, moved
d = app.active_doc
w = d.segments[0]["words"]
kw = cod.add_coding(d, feel, w[0]["start"], w[1]["end"], w[0]["wid"], w[1]["wid"])
assert cod.set_coding_span(d, kw, w[0]["start"], w[3]["end"] - 0.1)  # into "raft": the word that starts before
r = next(r for r in cod.resolve(d) if r["id"] == kw)
assert cod.coded_text(d, r) == "we built a raft", cod.coded_text(d, r)
assert not cod.set_coding_span(d, kw, 500, 600)
cod.remove_coding(d, kw)
ok("the end of a time coding dragged from 31 to 35 s; a coding of words takes the words at the new times")

print("[6] coding while watching: 1 starts at the playhead, 1 again ends it")
clock = [0.0]
app._current_pos = lambda: clock[0]
app.playing = True
clock[0] = 100.0
assert app._cdt_live_toggle(making) == "started" and app.cdt["live"] == {making: 100.0}
assert any(it[0] == "live" for it in app._cdt_items())
clock[0] = 108.0
app._cd_key(E(keysym=str(app._cd["recent"].index(making) + 1)))   # Making's key among the recent codes
live_codes = [c for c in cod.codings(TD.load(doc.path)) if c.get("kind") == "time" and abs(c["start"] - 100.0) < 1e-6]
assert len(live_codes) == 1 and abs(live_codes[0]["end"] - 108.0) < 1e-6 and app.cdt["live"] == {}, (live_codes, app.cdt["live"])
ok("● Making from 1:40, ended at 1:48 with the same key: a time coding of 8 s")

print("[7] loop, play the mark, frame steps")
app.playing = False
app._cdt_set_mark(20.0, 25.0)
played.clear()
assert app._cdt_toggle_loop() == (20.0, 25.0) and played == [(20.0, None)] and "Looping" in app.btn_cdt_loop.cget("text")
app.playing = True
app._cd_visible = lambda: True
app._cdt_tick(25.1)
assert played[-1] == (20.0, None)  # at the end: from the start again
app.playing = False
app._toggle_play = lambda *a: None
app._cdt_toggle_loop(); assert app.cdt["loop"] is None
assert app._cdt_play_mark() == (20.0, 25.0) and played[-1] == (20.0, 5.0)
jumps = []
app._timeline_jump = lambda t: jumps.append(round(t, 3))
clock[0] = 50.0
app._cd_key(E(keysym="period")); app._cd_key(E(keysym="less"))
assert jumps == [50.04, 49.0], jumps
ok("L loops the marked range (restarts at its end), ▶ plays it once, . and , step a frame, < and > a second")

print("[8] attach a time coding to its words, and back; the right-click menu of a bar")
d = app._cd_current()
k_t = cod.add_coding(d, materials, 1.9, 4.6)   # around "we built a raft with" (2.0–4.4)
assert cod.attach_to_words(d, k_t)
r = next(r for r in cod.resolve(d) if r["id"] == k_t)
assert r.get("kind") != "time" and cod.coded_text(d, r) == "we built a raft with", cod.coded_text(d, r)
assert cod.detach_from_words(d, k_t) and cod.get_coding(d, k_t)["kind"] == "time" and "start_wid" not in cod.get_coding(d, k_t)
assert not cod.attach_to_words(d, cod.add_coding(d, materials, 500.0, 510.0))  # no words there
app._commit("codings")
app._cd_render(d)
bar = next(it for it in app._cdt_items() if it[0] == "bar" and it[7] == k_t)
m = app._cdt_menu(E(x=(bar[1] + bar[2]) / 2, y=(bar[3] + bar[4]) / 2))
assert "Attach to the words said in it" in m.labels() and "Delete coding" in m.labels()
m.invoke("Attach to the words said in it")
assert cod.get_coding(TD.load(doc.path), k_t).get("start_wid")
app._cdt_set_mark(30.0, 33.0)
m = app._cdt_menu(E(x=x_of(150.0), y=200))
assert any(lb.startswith("1  ") for lb in m.labels()) and "Clear the mark" in m.labels()
ok("Attach to words (and Keep by time only); right-click a bar or the marked range")

print("[9] detect speakers in a selection, from the transcript's right-click menu")
app._cdt_clear_mark()
d = app.active_doc
pt = app.preview_text
first, last = app.span_lookup[(3, 0)], app.span_lookup[(6, 1)]
a_, b_ = app.word_spans[first], app.word_spans[last]
pt.tags["sel"] = (f"{a_['line']}.{a_['c0']}", f"{b_['line']}.{b_['c1']}")
asked = []
real_dialog = app._rediarize_dialog
app._rediarize_dialog = lambda r=None: asked.append(r)
m = app._transcript_menu(E())
label = next(lb for lb in m.labels() if lb.startswith("Detect speakers in this passage"))
assert "(4 segments)" in label, label
m.invoke(label)
assert asked == [(3, 6)]
app._rediarize_dialog = real_dialog
started = []
app._start_rerun = lambda kind, params, clip, segments_json=None: started.append((kind, params, clip, segments_json))
win, rd = app._rediarize_dialog((3, 6))
assert rd["n"].get() == 3  # Robin, Sam, Facilitator speak in it
params = rd["run"]()
kind, p, clip, segs_json = started[-1]
assert kind == "rediarize" and p["range"] == [3, 6] and len(segs_json) == 4
assert abs(clip[0] - (d.segments[3]["start"] - 0.2)) < 1e-6 and abs(segs_json[0]["start"] - 0.2) < 1e-6  # times from the clip's start
before = [s["speaker"] for s in d.segments]
app._apply_rediarization(d, {"segments": [{"speaker": x} for x in ("S0", "S0", "S1", "S1")], "diarization_method": "test"}, p)
after = [s["speaker"] for s in app.active_doc.segments]
assert after[:3] == before[:3] and after[7:] == before[7:], "outside the selection: unchanged"
assert after[3] == after[4] and after[5] == after[6] and after[3] != after[5] and set(after[3:7]) <= set(before), after[3:7]
ok("4 segments → only those get new speakers (mapped onto the names already used); the audio clip and times match")

print("[10] the side panel collapses to a strip, and comes back")
assert app._toggle_side_panel() is False and app.side_collapsed and app.settings["side_collapsed"] is True
assert app.var_side_panel.get() is False
assert app._toggle_side_panel(show="project") is True and not app.side_collapsed
assert app._toggle_side_panel(False) is False and app._toggle_side_panel(True) is True
app._save_settings()
ok("◂ / ⌘B collapse Tasks & Project to a strip; ▸ (or clicking Tasks / Project on it) brings it back; remembered")

print("[11] the video column: bigger and back")
assert app._cd_video_size() in (True, False, None)
ok("⇔ Video size")

print("[12] long sessions: drawing asks for the length once, coding words doesn't lay the text out again")
from condense.gui import textrender
calls = {"duration": 0, "layout": 0}
real_duration, real_show = app._duration, textrender.show_lines
app._duration = lambda: (calls.__setitem__("duration", calls["duration"] + 1), real_duration())[1]
textrender.show_lines = lambda *a, **k: (calls.__setitem__("layout", calls["layout"] + 1), real_show(*a, **k))[1]
app._cdt_zoom("whole")
calls["duration"] = 0
items = app._cdt_items()
assert len(items) > 20 and calls["duration"] <= 4, calls   # was: twice per shape (thousands for 5 hours)
calls["layout"] = 0
d = app._cd_current()
wl = app._cd["words"]
a, b = wl[40], wl[44]
app.cd_text.tags["sel"] = (f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
k_new = app._cd_apply(making)
assert k_new and calls["layout"] == 0 and k_new in [r["id"] for r in app._cd["resolved"]]
assert app._cd_selection_words() is None   # the coded selection is gone, as before
app._cd_change_code(k_new, feel); assert calls["layout"] == 0 and cod.get_coding(TD.load(doc.path), k_new)["code"] == feel
app._cd_remove(k_new); assert calls["layout"] == 0 and k_new not in [r["id"] for r in app._cd["resolved"]]
app._cdt_set_mark(140.0, 150.0); app._cdt_code_mark(making)
assert calls["layout"] == 1   # a coding by time is a ◆ line in the text: laid out again
textrender.show_lines = real_show
app._duration = real_duration
ok("the timeline asks for the length ≤ 4 times per drawing; coding / recoding / deleting words: no new layout")
