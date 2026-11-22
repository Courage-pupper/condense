"""Drives the real Tk app (not the stand-in): builds every tab and dialog, renders a demo transcript and a 3-hour one,
and uses the editor through real key events. Fails on any exception, including ones Tk reports from callbacks.

Run by tests/test_real_tk.py (needs a display; CI uses Xvfb on Linux). With CONDENSE_SCREENSHOTS=<dir> on Linux it also
saves the screenshots used in docs/user-guide.md — all content is invented demo data.
"""
import os
import random
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


def key(app, widget, sequence):
    widget.focus_force()
    pump(app, 0.05)
    widget.event_generate(sequence)
    pump(app, 0.1)


def main():
    study, video, docs = demo_data.build(WORK)
    if SHOTS is not None:  # the guide shows a working GPU set-up, not the CI runner's missing PyTorch
        from condense.platform import SystemProbe
        SystemProbe.detect_gpu = staticmethod(lambda: {"gpu_name": "AMD Radeon RX 7900 XTX", "torch_gpu": True, "gpu_api": "ROCm",
                                                       "vaapi_ok": True, "gpu_test": {"vram_gb": 24}})
    app = CondenseApp()
    app.report_callback_exception = lambda exc, val, tb: errors.append(traceback.format_exception(exc, val, tb))
    app.geometry("1560x960+0+0" if SHOTS is not None else "1280x800+0+0")
    pump(app, 1.0)

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
    w_set, ps = app._project_settings_dialog()
    pump(app)
    shot(app, "project-settings", w_set)
    w_set.destroy()
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


if __name__ == "__main__":
    main()
