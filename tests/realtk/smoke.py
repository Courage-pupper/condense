"""Drives the real Tk app (not the stand-in): builds every tab and dialog, renders a demo transcript and a 3-hour one,
and uses the editor through real key events. Fails on any exception, including ones Tk reports from callbacks.

Run by tests/test_real_tk.py (needs a display; CI uses Xvfb on Linux). With CONDENSE_SCREENSHOTS=<dir> on Linux it also
saves the screenshots used in docs/user-guide.md — all content is invented demo data.
"""
import os
import random
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import demo_data  # noqa: E402

from condense import updates  # noqa: E402
from condense.core import project as proj  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402
from condense.gui.app import CondenseApp  # noqa: E402

SHOTS = Path(os.environ["CONDENSE_SCREENSHOTS"]) if os.environ.get("CONDENSE_SCREENSHOTS") else None
WORK = Path(os.environ["CONDENSE_WORK"])
errors = []
dialogs = []

updates.due = lambda settings: False  # no network at start-up
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda title, msg=None, _n=name, **k: dialogs.append((_n, title, msg)))
messagebox.askyesno = lambda *a, **k: True
messagebox.askokcancel = lambda *a, **k: True
messagebox.askyesnocancel = lambda *a, **k: True
simpledialog.askstring = lambda *a, **k: "Co-facilitator"


def ok(msg):
    print("  ✓", msg, flush=True)


def pump(app, seconds=0.3):
    end = time.time() + seconds
    while True:
        app.update()
        if time.time() >= end:
            break
        time.sleep(0.02)
    check_errors()


def check_errors():
    if errors:
        print("".join(errors[0]), file=sys.stderr)
        raise SystemExit(f"Tk reported {len(errors)} exception(s) in callbacks")


def toplevels(app):
    return [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]


def new_window(app, before):
    new = [w for w in toplevels(app) if w not in before]
    assert new, "no window opened"
    return new[-1]


def shot(app, name, win=None):
    if SHOTS is None or not sys.platform.startswith("linux"):
        return
    win = win or app
    pump(app, 0.6)
    win.lift()
    pump(app, 0.2)
    SHOTS.mkdir(parents=True, exist_ok=True)
    wid = win.wm_frame() if win is not app else app.wm_frame()
    subprocess.run(["import", "-window", wid, str(SHOTS / f"{name}.png")], check=True, timeout=30)
    print(f"    (screenshot {name}.png)")


def to_foreground(app):
    """Windows only lets a process take the foreground right after an input event, and CI starts the app in the
    background. Minimising and restoring the window usually activates it."""
    if sys.platform != "win32":
        return
    import ctypes
    user32 = ctypes.windll.user32
    user32.keybd_event(0x87, 0, 0, 0)  # F24 down/up: an input event nobody uses
    user32.keybd_event(0x87, 0, 2, 0)
    user32.SetForegroundWindow(int(app.wm_frame(), 16))
    app.iconify()
    pump(app, 0.2)
    app.deiconify()
    app.lift()
    pump(app, 0.3)


def _direct(app, sequence):
    """What a key does, for a Windows CI machine that never gives the app the keyboard focus. The key bindings
    themselves are platform-independent Tk and are checked with real key events on macOS and Linux."""
    from condense.gui.widgets import EDIT_MOD, MOD
    m = re.fullmatch(r"<(?:(\w+)-)?Key-(\d)>", sequence)
    if m and m.group(1) in (None, EDIT_MOD):
        app._assign_speaker_number(int(m.group(2)))
    elif m and m.group(1) == MOD:
        app.notebook.select((app.tab_settings, app.tab_console, app.tab_preview)[int(m.group(2)) - 1])
    elif sequence == "<Control-Shift-Key-L>":
        app._insert_marker("[laughs]")
    elif sequence in ("<Down>", "<Up>"):
        app._move_segment(+1 if sequence == "<Down>" else -1)
    elif sequence == "<Escape>":
        app._focus_transcript()
    elif sequence == "<Return>":
        app._focus_editor()
    pump(app, 0.1)


def key(app, widget, sequence):
    """A real key press. Key events go to the focused widget, so wait until it really has the focus."""
    for attempt in range(40):
        widget.focus_force()
        pump(app, 0.05)
        if app.focus_get() is widget:
            break
        if attempt % 10 == 9:
            to_foreground(app)
    if app.focus_get() is not widget and sys.platform == "win32":
        if not KEYS["warned"]:
            print("    (this Windows machine gives the app no keyboard focus: key actions checked by direct calls)")
            KEYS["warned"] = True
        _direct(app, sequence)
        return
    assert app.focus_get() is widget, f"{widget} did not get the keyboard focus"
    widget.event_generate(sequence)
    pump(app, 0.15)


KEYS = {"warned": False}


def main():
    study, video, docs = demo_data.build(WORK)
    if SHOTS is not None:  # the guide shows a working GPU set-up, not the CI runner's missing PyTorch
        from condense.platform import SystemProbe
        SystemProbe.detect_gpu = staticmethod(lambda: {"gpu_name": "AMD Radeon RX 7900 XTX", "torch_gpu": True, "gpu_api": "ROCm",
                                                       "vaapi_ok": True, "gpu_test": {"vram_gb": 24}})
    app = CondenseApp()
    app.report_callback_exception = lambda exc, val, tb: errors.append(traceback.format_exception(exc, val, tb))
    app.geometry("1560x960+0+0" if SHOTS is not None else "1440x860+0+0")  # a laptop screen
    pump(app, 1.0)
    to_foreground(app)

    print("[1] main window, queue, settings")
    tasks = []
    for i, (doc, status) in enumerate(zip(docs, ("Completed", "Completed", "Pending"))):
        done = status == "Completed"
        t = app._new_task([str(video)], name=doc.base_name)
        t.update(status=status, stage="Done ✓" if done else "Queued", progress=100.0 if done else 0.0,
                 doc_path=str(doc.path) if done else None, output_dir=str(doc.out_dir))
        tasks.append(t)
    app._add_tasks(tasks)
    app.queue_tree.selection_set(tasks[0]["id"])
    app._on_job_selected()
    for d in docs[:2]:
        app._update_task_progress(TD.load(d.path))  # "Done ✓ · 27% checked", as after reviewing
    pump(app, 1.0)
    app.notebook.select(app.tab_settings)
    shot(app, "main-window")
    app.notebook.select(app.tab_console)
    pump(app)
    ok("queue with tasks, task details, settings and console tabs")

    print("[1b] ↑/↓ keep the layout still and scroll only when needed")
    app._render_doc(TD.load(docs[0].path))
    app.notebook.select(app.tab_preview)
    pump(app, 0.8)
    pt = app.preview_text
    lines = pt.winfo_height() / app.f["transcript"].metrics("linespace")
    if app.winfo_height() >= 820:  # some CI machines have a 1024x768 screen; a laptop has more
        assert lines >= 4, f"the transcript shows only {lines:.1f} lines in a {app.winfo_width()}x{app.winfo_height()} window"
    print(f"    (transcript: {lines:.1f} lines in a {app.winfo_width()}x{app.winfo_height()} window)")
    app.var_autoplay.set(False)
    app._goto_segment(0)
    pump(app, 0.5)
    layout = []
    moves = []
    for i in range(14):
        nxt = app.seg_ranges.get(app.edit_seg_idx + 1) if app.edit_seg_idx is not None else None
        visible = bool(nxt and pt.bbox(nxt[0]) and pt.bbox(f"{nxt[0]} lineend") is not None)
        y0 = pt.yview()[0]
        key(app, pt, "<Down>")
        pump(app, 0.2)
        layout.append((pt.winfo_height(), pt.winfo_rooty(), app.edit_text.winfo_rooty()))
        moves.append((visible, y0, pt.yview()[0]))
    assert len(set(layout)) == 1, f"the layout moved while pressing ↓: {layout}"
    # a segment that is only partly visible is scrolled fully into view: a few pixels, not a jump
    small = 1.5 * app.f["transcript"].metrics("linespace") / max(1, pt.winfo_height()) * (pt.yview()[1] - pt.yview()[0])
    jumped = [(i, y0, y1) for i, (vis, y0, y1) in enumerate(moves) if vis and abs(y1 - y0) > small]
    assert not jumped, f"the transcript scrolled although the next segment was visible: {jumped}"
    assert any(abs(y1 - y0) > 1e-6 for _, y0, y1 in moves) or all(v for v, _, _ in moves)
    # assigning another speaker regroups the paragraphs; merging joins two segments: the open segment stays put
    def screen_y():
        sp = app.word_spans[app.current_span]
        bb = pt.bbox(f"{sp['line']}.{sp['c0']}")
        return bb[1] if bb else None

    d0 = TD.load(docs[0].path)
    k = next(i for i in range(10, len(d0.segments) - 2) if d0.segments[i]["speaker"] != d0.segments[i - 1]["speaker"])
    if lines < 4:  # too small a screen on this CI machine to see a jump; Linux CI runs this at laptop size
        print("    (screen too small here to measure jumps; checked on Linux)")
        y_before = y_after = y_merged = "-"
    else:
        y_before, y_after, y_merged = check_no_jump(app, pt, k, d0, screen_y)
    app.var_autoplay.set(True)
    ok(f"14 × ↓: layout unchanged; scrolled only when needed; speaker change and merge keep the segment in place "
       f"(y {y_before} → {y_after} → {y_merged})")

    print("[1c] Esc while editing")

    # Esc while typing in the segment box: back to the transcript, no play/pause; then ↓ moves on, Return edits again
    app._goto_segment(3)
    pump(app, 0.2)
    app._focus_editor()
    pump(app, 0.2)
    was_playing = app.playing
    if sys.platform != "win32":  # Windows CI gives the app no reliable keyboard focus (see key())
        key(app, app.edit_text, "<Escape>")
        assert app.focus_get() is pt, f"Esc left the focus on {app.focus_get()}"
        assert app.playing == was_playing, "Esc in the segment box must not start or stop playback"
        key(app, pt, "<Down>")
        assert app.edit_seg_idx == 4, app.edit_seg_idx
        key(app, pt, "<Return>")
        assert app.focus_get() is app.edit_text
        key(app, app.edit_text, "<Escape>")
        ok("Esc in the segment box → back to the transcript; ↓ moves on; Return edits again")

    print("[2] transcript editor with real key events")
    app._render_doc(TD.load(docs[0].path))
    app.notebook.select(app.tab_preview)
    pump(app, 0.5)
    pt = app.preview_text
    content = pt.get("1.0", tk.END)
    assert "paper boat" in content, content[:300]
    assert any(pt.tag_ranges(t) for t in pt.tag_names() if t.startswith("SPK")), "no speaker colours"
    app._goto_segment(2)
    pump(app, 0.8)
    shot(app, "editor")
    roster = app._roster()
    key(app, pt, "<Key-2>")
    assert TD.load(docs[0].path).segments[2]["speaker"] == roster[1], TD.load(docs[0].path).segments[2]["speaker"]
    before_multi = [TD.load(docs[0].path).segments[i]["speaker"] for i in (4, 5, 6)]
    app._set_multi_selection([4, 5, 6])
    pump(app)
    shot(app, "multi-select")
    key(app, pt, "<Key-3>")
    d = TD.load(docs[0].path)
    assert {d.segments[i]["speaker"] for i in (4, 5, 6)} == {roster[2]}
    app._clear_multi_selection()
    app._undo()
    pump(app)
    assert [TD.load(docs[0].path).segments[i]["speaker"] for i in (4, 5, 6)] == before_multi
    app._redo()
    pump(app)
    assert {TD.load(docs[0].path).segments[i]["speaker"] for i in (4, 5, 6)} == {roster[2]}
    ok("number keys assign speakers (one and several segments), undo/redo")

    # Modifier shortcuts, as real key events. Mac: ⌃digit assigns, ⌘digit switches tabs. Linux/Windows: Alt+digit
    # assigns, Ctrl+digit switches tabs, and Ctrl+C/Ctrl+I are left alone (copy, insert segment); markers are Ctrl+Shift.
    from condense.gui.widgets import EDIT_MOD, IS_MAC, MOD
    app._goto_segment(3)
    pump(app, 0.2)
    app._focus_editor()
    key(app, app.edit_text, f"<{EDIT_MOD}-Key-4>")
    assert TD.load(docs[0].path).segments[3]["speaker"] == roster[3], "speaker number while typing"
    key(app, app.edit_text, f"<{MOD}-Key-1>")
    assert app.notebook.select() == str(app.tab_settings), "tab shortcut"
    app.notebook.select(app.tab_preview)
    pump(app)
    if not IS_MAC:
        assert not app.bind_all("<Control-Key-c>"), "Ctrl+C is bound app-wide"
        before_text = app.edit_text.get("1.0", "end-1c")
        key(app, app.edit_text, "<Control-Key-c>")
        assert app.edit_text.get("1.0", "end-1c") == before_text, "Ctrl+C changed the text"
    app._focus_editor()
    app.edit_text.mark_set("insert", "end-1c")
    key(app, app.edit_text, "<Control-Shift-Key-L>")
    assert "[laughs]" in app.edit_text.get("1.0", "end-1c"), "marker shortcut"
    app._revert_segment_edit()
    pump(app)
    ok("modifier shortcuts: speaker while typing, tab switching, markers; Ctrl+C left to copy")

    app._goto_segment(7)
    app.var_seg_note.set("Robin shows the drawing to the group")
    app._save_segment_edit()
    assert TD.load(docs[0].path).segments[7].get("note")
    app._goto_segment(8)
    app._toggle_cut()
    pump(app)
    assert TD.load(docs[0].path).cuts, "cut not saved"
    assert pt.tag_ranges("CUT"), "cut not shown"
    app._goto_segment(7)
    pump(app, 0.5)
    shot(app, "notes-and-cuts")
    for flt in ("Uncertain", "Not checked", "With notes", "Cut", "All segments"):
        app.var_show_filter.set(flt)
        app._rerender()
        pump(app, 0.1)
    ok("notes, cut (struck through), show filter")

    app._toggle_find()
    app.var_find.set("boat")
    pump(app, 0.3)
    assert pt.tag_ranges("FOUND"), "find did not highlight (variable trace)"
    app._find_step(+1)
    pump(app)
    shot(app, "find")
    app._hide_find()
    before = toplevels(app)
    win, st = app._search_all_dialog()
    pump(app)
    win.destroy()
    ok("find bar (live highlight through the real variable trace), search all transcripts")

    print("[3] dialogs")
    before = toplevels(app)
    app._show_shortcuts()
    w = new_window(app, before)
    shot(app, "shortcuts", w)
    w.destroy()
    app._render_doc(TD.load(docs[2].path))  # a session whose speakers have no names yet
    pump(app, 0.3)
    assert app._generic_speakers(), app._roster()
    w_who, ww = app._match_speakers_dialog()
    pump(app, 0.3)
    shot(app, "who-is-who", w_who)
    w_who.destroy()
    app._render_doc(TD.load(docs[0].path))
    pump(app, 0.3)
    before = toplevels(app)
    app._show_speakers_dialog()
    w = new_window(app, before)
    pump(app)
    shot(app, "speakers", w)
    w.destroy()
    # projects: opening a transcript of the study opened its project; the overview scans in the background
    assert app._project_dir() == study.resolve(), app._project_dir()
    assert app.btn_project.cget("text") == "Project: Demo study"
    app._set_project(None)
    win0, _ = app._project_start_dialog()
    pump(app)
    shot(app, "project-start", win0)
    win0.destroy()
    win0, nd = app._new_project_dialog()
    nd["folder"].set(str(study))
    nd["name"].set("Demo study")
    pump(app)
    shot(app, "project-new", win0)
    nd["create"]()  # the folder is already a project: keeps its speakers, opens the overview
    for w in toplevels(app):
        if w.title().startswith("Project —"):
            w.destroy()
    assert app._project_dir() == study.resolve()
    win, st = app._project_overview()
    for _ in range(100):
        pump(app, 0.1)
        if st["rows"]:
            break
    assert len(st["rows"]) == 3, st["lbl"].cget("text")
    assert [st["tree"].item(i, "text") for i in st["tree"].get_children()] == ["Day 1 2026-03-12", "Day 2 2026-03-13"]
    shot(app, "project-overview", win)
    # a window opened from another window comes to the front with the focus (it used to appear behind its opener)
    if sys.platform != "win32":
        st["tree"].focus_force()
        pump(app, 0.2)
        w_spk, _sp = app._project_speakers_dialog(group="Group 1")
        for _ in range(20):
            pump(app, 0.1)
            if app.focus_get() is not None and app.focus_get().winfo_toplevel() is w_spk:
                break
        assert app.focus_get() is not None and app.focus_get().winfo_toplevel() is w_spk, app.focus_get()
        assert str(w_spk.wm_transient()) == str(win), (w_spk.wm_transient(), win)
        shot(app, "speakers-groups", w_spk)
        w_spk.destroy()
    w_set, ps = app._project_settings_dialog()
    pump(app)
    shot(app, "project-settings", w_set)
    w_set.destroy()
    app.left_tabs.select(app.tab_project)
    for _ in range(100):
        pump(app, 0.1)
        if app.pp_items:
            break
    names = {it["media"].name: it for it in app.pp_items.values() if it.get("media") and not it.get("episode")}
    assert names["final_video.mp4"]["row"] is None and names["demo_G1D1.mp4"]["row"] is not None, sorted(names)
    fv_iid = next(i for i, it in app.pp_items.items() if it.get("media") and it["media"].name == "final_video.mp4" and not it.get("episode"))
    for _ in range(100):  # lengths are measured in the background
        pump(app, 0.1)
        if app.pp_tree.set(fv_iid, "length") not in ("…", ""):
            break
    assert app.pp_tree.set(fv_iid, "length") == "0:02:00", app.pp_tree.set(fv_iid, "length")
    shot(app, "project-panel")
    app.left_tabs.select(app.tab_tasks)
    w_tidy, td = app._tidy_dialog()
    pump(app, 0.3)
    plan = {it["src"].name: it for it in td["plan"]()}
    assert plan["G1D2_I2.mp4"]["ok"] and not plan["IMG_0042.mp4"]["ok"], plan
    shot(app, "tidy", w_tidy)
    w_tidy.destroy()
    win2, sp = app._project_speakers_dialog(study)
    pump(app)
    shot(app, "project-speakers", win2)
    win2.destroy()
    win.destroy()
    # episodes: the session's phases, then the cut-out interview found by its sound
    app._render_doc(TD.load(docs[0].path))
    pump(app, 0.3)
    assert "▶ Phase Dioramas" in pt.get("1.0", tk.END)
    w_link, lk = app._link_clips_dialog()
    assert [p.name for p in lk["clips"]] == ["G1D1_I1.mp4"], lk["clips"]
    lk["find"]()
    for _ in range(300):
        pump(app, 0.1)
        if not lk["busy"] and lk["results"]:
            break
    r = next(iter(lk["results"].values()))
    assert r.get("confident") and abs(r["start"] - 70.0) < 0.1, r
    shot(app, "find-clips", w_link)
    assert lk["add"]() == (1, 0)
    w_link.destroy()
    w_ep, es = app._episodes_dialog()
    pump(app, 0.3)
    iv = [e for e in TD.load(docs[0].path).episodes if e["type"] == "Interview"][0]
    es["select"](iv["id"])
    pump(app, 0.3)
    shot(app, "episodes", w_ep)
    w_ep.destroy()
    app._goto_segment(TD.load(docs[0].path).episode_segments(iv)[0])
    pump(app, 0.6)
    shot(app, "editor-episodes")
    ok("episodes: phases shown, interview clip found at 70.0 s by its sound and added")

    # coding: the Coding tab with the demo codebook; code a selection for real; stripes; a moment without speech
    app._render_doc(TD.load(docs[0].path))
    app.notebook.select(app.tab_coding)
    pump(app, 1.0)
    assert app._cd["words"] and app._cd["resolved"], "the Coding tab shows nothing"
    ct = app.cd_text
    words = app._cd["words"]
    first = next(n for n, w in enumerate(words) if w[3] == 9)       # "Green, because green is the colour of the river."
    last = max(n for n, w in enumerate(words) if w[3] == 9)
    ct.tag_add("sel", f"{words[first][0]}.{words[first][1]}", f"{words[last][0]}.{words[last][2]}")
    cb = app._cd_codebook()
    feelings = cb.find("Feelings")["id"]
    app.cd_tree.selection_set(feelings)
    pump(app, 0.2)
    kid = app._cd_apply(feelings)
    assert kid and any(r["id"] == kid for r in app._cd["resolved"])
    ct.tag_remove("sel", "1.0", "end")
    ct.see(f"{words[first][0]}.0")
    pump(app, 0.5)
    assert app._cd.get("stripe_hits"), "no coding stripes drawn"
    shot(app, "coding")
    w_m, tdlg = app._cd_time_dialog(30.0, 36.0)
    pump(app, 0.3)
    shot(app, "coding-moment", w_m)
    w_m.destroy()
    w_e, ce = app._cd_edit_code(cb.find("Making")["id"])
    pump(app, 0.3)
    shot(app, "coding-code", w_e)
    w_e.destroy()
    app.notebook.select(app.tab_preview)
    pump(app, 0.3)
    ok("Coding tab: demo codebook, a real selection coded, stripes drawn, moment and code dialogs")

    res = app._trim_dialog(video)
    assert res is not None
    win, st = res
    pump(app, 1.5)
    st["set_pos"](20.0)
    st["mark_start"]()
    st["set_pos"](32.0)
    st["mark_end"]()
    pump(app, 1.5)
    shot(app, "trim", win)
    st["close"]()
    pump(app)
    for z in (1.2, 0.9, 1.0):
        app._set_zoom(z, save=False)
        pump(app, 0.1)
    size0 = int(app.f["transcript"].cget("size"))
    app._transcript_size_step(+1)
    app._transcript_size_step(+1)
    pump(app, 0.1)
    assert int(app.f["transcript"].cget("size")) > size0, "transcript text size did not change"
    assert abs(app.var_text_size.get() - app.transcript_scale * 100) < 3, "the slider did not follow the size"
    # every action that moved into a menu is still there
    dropdowns = {}
    def collect(w):
        for c in w.winfo_children():
            if hasattr(c, "menu"):
                dropdowns[c.cget("text")] = c.menu
            collect(c)
    collect(app.tab_preview)
    labels = lambda m: [m.entrycget(i, "label") for i in range(m.index("end") + 1) if m.type(i) != "separator"]
    assert {"Transcript ▾", "Playback ▾", "More ▾"} <= set(dropdowns), sorted(dropdowns)
    assert "Speakers…" in labels(dropdowns["Transcript ▾"]) and "Skip cut parts" in labels(dropdowns["Playback ▾"])
    assert "Re-transcribe this passage…" in labels(dropdowns["More ▾"])
    app._set_transcript_size(1.0, save=False)
    ok("shortcuts, speakers, project overview + speakers, trim (with frames from ffmpeg), zoom")

    print("[4] a 3-hour transcript in the real text widget")
    long_doc = demo_data.long_session(WORK)
    t0 = time.perf_counter()
    app._render_doc(TD.load(long_doc.path))
    pump(app, 0.2)
    secs = time.perf_counter() - t0
    assert secs < 60, f"rendering 3 hours took {secs:.1f}s"
    n = len(app.active_doc.segments)
    app._goto_segment(n - 3)
    pump(app, 0.3)
    key(app, pt, "<Key-1>")
    assert TD.load(long_doc.path).segments[n - 3]["speaker"] == app._roster()[0]
    t0 = time.perf_counter()
    app._toggle_cut()
    pump(app, 0.1)
    edit_secs = time.perf_counter() - t0
    ok(f"{n} segments rendered in {secs:.1f}s; an edit near the end takes {edit_secs:.2f}s")

    print("[5] close")
    app._on_close()
    check_errors()
    unexpected = [d for d in dialogs if d[0] == "showerror"]
    assert not unexpected, unexpected
    ok("closed cleanly; no error dialogs")
    print("REAL TK PASSED")


def check_no_jump(app, pt, k, d0, screen_y):
    app._goto_segment(k)
    pump(app, 0.4)
    sp0 = app.word_spans[app.current_span]
    pt.yview(f"{sp0['line']}.{sp0['c0']}")  # the open segment a line below the top: a jump would show
    pump(app, 0.1)
    pt.yview_scroll(-1, "units")
    pump(app, 0.2)
    y_before = screen_y()
    assert y_before is not None and y_before > 5, (y_before, pt.winfo_height())
    prev_speaker = d0.segments[k - 1]["speaker"]
    key(app, pt, f"<Key-{app._roster().index(prev_speaker) + 1}>")
    pump(app, 0.4)
    assert TD.load(app.active_doc.path).segments[k]["speaker"] == prev_speaker
    y_after = screen_y()
    line_h = app.f["transcript"].metrics("linespace")
    assert y_after is not None and abs(y_after - y_before) <= line_h * 1.5, f"the transcript jumped: {y_before} → {y_after}"
    app._merge_with_next()
    pump(app, 0.4)
    y_merged = screen_y()
    assert y_merged is not None and abs(y_merged - y_before) <= line_h * 2.5, f"merging made the transcript jump: {y_before} → {y_merged}"
    app._undo()
    app._undo()
    pump(app, 0.3)
    return y_before, y_after, y_merged


if __name__ == "__main__":
    main()
