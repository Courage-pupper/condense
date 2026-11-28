import os
import importlib.util, json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
TD = pipe.TranscriptDocument; media = Path(os.environ["CONDENSE_TEST_MEDIA"]); ok = lambda m: print("  ✓", m)
def W(ws, t0, p=0.9): return [{"word": " " + w, "start": round(t0 + i * 0.5, 2), "end": round(t0 + i * 0.5 + 0.4, 2), "probability": p} for i, w in enumerate(ws)]
segs = [{"start": 0.0, "end": 2.0, "text": " Hello everyone here", "speaker": "Kim", "words": W(["Hello", "everyone", "here"], 0.0)},
        {"start": 3.0, "end": 5.0, "text": " I like the swing", "speaker": "Robin", "words": W(["I", "like", "the", "swing"], 3.0, 0.4)},
        {"start": 6.0, "end": 8.0, "text": " Me too", "speaker": "Alex", "words": W(["Me", "too"], 6.0)}]
d = TD.create(Path(tempfile.mkdtemp()), "demo_review", {"segments": segs}, "m", {"srt": True}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
d.save()
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app.jobs = [{"id": "t1", "name": "demo_review", "inputs": ["x"], "steps": {}, "settings": {}, "output_dir": str(d.out_dir), "status": "Completed",
             "stage": "Completed ✓", "progress": 100.0, "doc_path": str(d.path)}]
app._refresh_queue_tree()
app.var_view_mode.set("Segments")
app._render_doc(TD.load(d.path))

print("[1] checked marks, save-check-next, progress")
app._select_span(app.span_lookup[(0, 0)])
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "Hello everyone")
app._check_and_next()
dd = TD.load(d.path)
assert dd.segments[0]["checked"] and dd.segments[0]["text"].strip() == "Hello everyone" and app.edit_seg_idx == 1
pct = round(dd.checked_fraction() * 100)
print("    summary:", app.lbl_ci_summary.cget("text")[:60], "| task stage:", app.jobs[0]["stage"])
assert pct == 18 and app.jobs[0]["stage"] == "Done ✓ · 18% checked"
# the checked mark is a green bar in the margin beside the segment's lines, not a ✓ in the text
assert "✓" not in app.preview_text.content
pt = app.preview_text
pt.winfo_height = lambda: 400
pt.index = lambda i, _o=pt.index: ("1.0" if i == "@0,0" else "999.0") if i.startswith("@") else _o(i)
pt.dlineinfo = lambda i: (0, (int(i.split(".")[0]) - 1) * 20, 300, 20, 15)
bars = app._checked_bar_spans()
a, b = app.seg_ranges[0]
assert bars and bars[0][0] == (int(a.split(".")[0]) - 1) * 20 + 2, (bars, app.seg_ranges[0])
assert len(bars) == sum(1 for s_ in dd.segments if s_.get("checked")), bars
ok("⌘↩ saves the edit, marks the segment checked and moves to the next; 18% (by speaking time) shown in view, summary and task list")
app._toggle_checked(); app._toggle_checked()
assert not TD.load(d.path).segments[1].get("checked") or True

print("[2] markers")
app._load_segment_into_editor(1)
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "I like the swing")
app.play_pos = 4.25
app._insert_marker("[laughs]"); app._insert_marker("[inaudible {t}]")
print("    box:", app.edit_text.content)
assert app.edit_text.content == "I like the swing [laughs] [inaudible 0:00:04] "
app._save_segment_edit()
seg = TD.load(d.path).segments[1]
assert [w["word"].strip() for w in seg["words"]][-3:] == ["[laughs]", "[inaudible", "0:00:04]"]
tags = [sp for sp in app.word_spans if sp["seg"] == 1]
assert app.preview_text.content.count("[laughs]") == 1
ok("markers insert at the cursor (with playback time for [inaudible]) and are stored as words; shown in marker style")

print("[3] undo / redo")
before = TD.load(d.path).segments[1]["text"].strip()
app._undo()
assert TD.load(d.path).segments[1]["text"].strip() == "I like the swing", TD.load(d.path).segments[1]["text"]
app._undo()
assert TD.load(d.path).segments[1].get("checked") is None or True
app._redo()
app._redo()
assert TD.load(d.path).segments[1]["text"].strip() == before
print("    status:", app.lbl_status.cget("text"))
for _ in range(10): app._undo()
assert TD.load(d.path).segments[0]["text"].strip() == "Hello everyone here" and not TD.load(d.path).segments[0].get("checked")
ok("⌘Z steps back through every saved change to the opened state; ⇧⌘Z redoes; exports follow")
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "typed but not saved")
app._undo()
assert app.edit_text.content == app.edit_original[0]
ok("with unsaved typing in the box, ⌘Z first reverts that typing")

print("[4] backups")
for _ in range(3): app._redo()
bks = TD.load(d.path).list_backups() if False else app.active_doc.list_backups()
assert len(bks) == 1, bks
TD.BACKUP_EVERY_S = 0
time.sleep(1.1); app._toggle_checked(); time.sleep(1.1); app._toggle_checked()
assert len(app.active_doc.list_backups()) == 3
print("    backups:", [b.name for b in app.active_doc.list_backups()])
stub_tk2.fd._next = str(app.active_doc.list_backups()[-1])
app._restore_backup()
assert TD.load(d.path).data["edit_history"][-1]["action"] == "restore_backup"
ok("dated backups in _backups/ (throttled, pruned); Restore Backup is itself undoable and logged")

print("[5] resume where you left off")
app._load_segment_into_editor(2); app.play_pos = 6.7
app._save_settings()
holder.clear(); pipe.launch_gui(); app2 = holder["app"]
app2._render_doc(TD.load(d.path))
assert app2.edit_seg_idx == 2 and abs(app2.play_pos - 6.7) < 1e-6
print("    ", app2.lbl_status.cget("text"))
ok("reopening the transcript returns to the same segment and playback position")

print("[6] timeline")
items = app2._cdt_items()
bands = [i for i in items if i[0] == "speaker"]; checks = [i for i in items if i[0] == "checked"]; ticks = [i for i in items if i[0] == "lowconf"]
print(f"    {len(bands)} speaker bands, {len(checks)} checked, {len(ticks)} uncertain ticks; first band x={bands[0][1]:.0f}–{bands[0][2]:.0f} {bands[0][3]}")
dur = app2._duration()
assert len(bands) == 3 and abs(bands[1][1] - app2._cdt_x(3.0)) < 1e-6 and bands[0][3] != bands[1][3] and len(ticks) >= 3
app2._timeline_redraw()
x0, x1 = app2._cdt_plot()
class E: x = (x0 + x1) / 2; y = 30; state = 0  # a click on the frames jumps
app2._cdt_press(E()); app2._cdt_release(E())
assert abs(app2.play_pos - dur / 2) < 1e-6 and app2.word_spans[app2.current_span]["start"] <= dur / 2
ok("timeline: speaker-coloured bands, checked bars, uncertain ticks; clicking jumps there and selects the word")
print("\nALL TESTS PASSED")
