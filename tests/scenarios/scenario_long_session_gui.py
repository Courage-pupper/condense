"""A 5-hour session in the editor: edits redraw only what changed (and the result is the same as a full redraw), the
timeline stays small, exports of long sessions are written in the background, dragging along the timeline jumps once,
Go to time, and video frame grabs never pile up."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
import test_long_recording as tl
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

tl.LENGTH = 5 * 3600.0
out = Path(tempfile.mkdtemp())
formats = {k: True for k in ("srt", "vtt", "txt", "rtf", "para_rtf", "para_txt")}
video = media / "Interview O'Brien.mp4"
doc = TD.create(out, "five_hours", tl.long_result(), "small", formats, {}, {"playback": str(video), "video": str(video)}, {"inputs": []})
doc.save()
print(f"[1] open a 5-hour transcript ({len(doc.segments)} segments)")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
calls = []
real_insert = stub_tk2.Text.insert
stub_tk2.Text.insert = lambda self, *a: (calls.append(len(a)), real_insert(self, *a))[1]
t0 = time.perf_counter()
app._render_doc(TD.load(doc.path))
pt = app.preview_text
print(f"    {len(calls)} Tk insert calls for {len(app.word_spans)} words, {time.perf_counter() - t0:.2f} s")
assert len(calls) < 200, len(calls)
assert pt.content == "".join(chars for line in app._shown_lines for chars, _ in line)
ok("the whole session goes into the transcript view with a few large inserts")


def check_same_as_full_redraw(what):
    incremental = pt.content
    lines = pt.content.split("\n")
    for sp in app.word_spans[:: max(1, len(app.word_spans) // 300)]:  # spans point at their words
        assert lines[sp["line"] - 1][sp["c0"]:sp["c1"]] == sp["word"], (what, sp)
    app._shown_lines = None
    app._rerender()
    assert pt.content == incremental, what


def edit(what, fn, max_calls=3):
    calls.clear()
    fn()
    n = len(calls)
    check_same_as_full_redraw(what)
    assert n <= max_calls, (what, n)
    return n


mid = len(app.active_doc.segments) // 2
def fix_text():
    app._select_span(app.span_lookup[(mid, 0)])
    app.edit_text.content = "corrected words here"
    app._save_segment_edit()
n = edit("text correction", fix_text)
ok(f"a correction in the middle: {n} Tk insert call(s), same text as a full redraw, every word findable")
edit("speaker", lambda: (app._select_span(app.span_lookup[(mid + 3, 0)]), app._assign_speaker_number(2)))
edit("checked", app._toggle_checked)
edit("split", lambda: (app._select_span(app.span_lookup[(mid + 5, 2)]), app._split_at_current_word()))
edit("merge", lambda: (app._select_span(app.span_lookup[(mid + 8, 0)]), app._merge_with_next()))
edit("undo", app._undo)
edit("redo", app._redo)
app.var_view_mode.set("Segments")
edit("view: segments", app._rerender, max_calls=200)
app.var_view_mode.set("Paragraphs")
edit("view: paragraphs", app._rerender, max_calls=200)
ok("speaker, checked, split, merge, undo, redo and switching the view: always identical to a full redraw")

print("[2] coding tab")
cd = app.cd_text
calls.clear()
app._cd_render(app.active_doc)
first = cd.content
assert len(calls) < 100 and first.strip(), len(calls)
app.active_doc.segments[mid + 20]["words"][0]["word"] = " Zwischenruf"
calls.clear()
app._cd_render(app.active_doc)
assert len(calls) <= 2 and "Zwischenruf" in cd.content, len(calls)
again = cd.content
app._cd["shown_lines"] = None
app._cd_render(app.active_doc)
assert cd.content == again
w = app._cd["words"][len(app._cd["words"]) // 2]
assert cd.content.split("\n")[w[0] - 1][w[1]:w[2]] == app.active_doc.segments[w[3]]["words"][w[4]]["word"].strip()
ok("the Coding tab: first draw in a few inserts, a change redraws one line")

print("[3] timeline and exports")
items = app._timeline_items(1000)
print(f"    {len(items)} timeline shapes for {len(app.active_doc.segments)} segments and {len(app.low_conf)} uncertain words")
assert len(items) < len(app.active_doc.segments) + 1000 and sum(1 for i in items if i[0] == "tick") <= 1001
ok("timeline: shapes on the same pixels are merged")
app._select_span(app.span_lookup[(mid + 30, 0)])
app.edit_text.content = "exported later"
assert app._commit("x") == {}  # long session: written in the background
app._save_segment_edit()
app._wait_for_exports()
assert "exported later" in (out / "five_hours.srt").read_text(encoding="utf-8")
created = app._commit("re-export", undoable=False, export_now=True)
assert created and all(p.exists() for p in created.values())
ok("exports of a long session are written in the background (re-export: at once); quitting waits for them")

print("[4] navigation")
jumps = []
real_jump = app._timeline_jump
app._timeline_jump = lambda pos: (jumps.append(pos), real_jump(pos))
class Ev:
    def __init__(self, x): self.x = x
for x in range(100, 600, 5):
    app._timeline_scrub(Ev(x))
assert jumps == [] and app._scrub_target is not None and app.lbl_time.cget("text").startswith(pipe.short_ts(app._scrub_target)[:-2])
app._timeline_settle()
assert len(jumps) == 1 and abs(jumps[0] - 595 / 1000 * app._duration()) < 1e-6
ok("dragging along the timeline moves the playhead only; one jump when the mouse rests or is released")
app._goto_time_dialog("2:15:30")
assert abs(app.play_pos - 8130) < 1e-6 and abs(app.word_spans[app.current_span]["start"] - 8130) < 30
app._goto_time_dialog("nonsense")
assert "not a time" in app.lbl_status.cget("text")
stub_tk2.sd._next = "1:00:00"
app._goto_time_dialog()
assert abs(app.play_pos - 3600) < 1e-6
ok("Go to time (⌘J / Ctrl+J): 2:15:30 → 8130 s; nonsense is refused")

grabs = []
app._still_grab = lambda req, pos, cmd: (grabs.append(pos), setattr(app, "_still_busy", True))
app._still_busy = False
for k in range(30):  # holding ↓: 30 frames asked for while the first is still being decoded
    app._video_still(100.0 + k)
assert len(grabs) == 1 and app._still_pending[1] == 129.0
app._still_q.put((app._still_req - 29, 100.0, None))  # the first grab finishes
app._video_pump()
assert grabs == [100.0, 129.0]
ok("video frames: one grab at a time, only the newest request waits (no pile of ffmpeg processes)")
print("\nALL TESTS PASSED")
