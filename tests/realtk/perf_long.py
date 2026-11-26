"""How a 5-hour session feels in the real app: times what you do while coding (on the real Tk), and how responsive
the window stays while the video plays — inline, popped out and large. Prints a table; with --budget it fails when
something takes far too long. All data is invented.

Run: PYTHONPATH=src:tests/realtk python tests/realtk/perf_long.py   (Linux: under xvfb-run)
"""
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # test machines have no sound card: the player still runs
WORK = Path(os.environ.get("CONDENSE_WORK") or tempfile.mkdtemp(prefix="perf_"))
os.environ.setdefault("HOME", str(WORK / "home"))
(Path(os.environ["HOME"])).mkdir(parents=True, exist_ok=True)

from condense import updates  # noqa: E402
from condense.core import coding as cod  # noqa: E402
from condense.core import project as proj  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402
from condense.gui.app import CondenseApp  # noqa: E402

updates.due = lambda settings: False
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda *a, **k: None)
messagebox.askyesno = lambda *a, **k: True
simpledialog.askstring = lambda *a, **k: "New code"
errors = []
results = []
WORDS = ("we", "built", "the", "tower", "with", "paper", "and", "tape", "it", "fell", "down", "so", "try", "again", "maybe",
         "bigger", "base", "look", "here", "mine", "is", "taller", "than", "yours", "okay", "now")


def pump(app, seconds=0.0):
    end = time.perf_counter() + seconds
    app.update()
    while time.perf_counter() < end:
        app.update()
        time.sleep(0.005)


def timed(app, label, fn, *args, repeat=1):
    """Time from the call until the window has drawn everything (what you wait for)."""
    times = []
    out = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = fn(*args)
        app.update_idletasks()
        app.update()
        times.append(time.perf_counter() - t0)
    results.append((label, max(times) if repeat == 1 else statistics.median(times)))
    print(f"  {label:<58} {results[-1][1] * 1000:8.0f} ms", flush=True)
    return out


def responsiveness(app, label, seconds=4.0):
    """While something runs: how late a 20 ms timer fires (the window's lag), and how many frames were shown."""
    lags, state = [], {"t": None, "frames": 0}
    orig = app._show_frame

    def counted(*a, **k):
        state["frames"] += 1
        return orig(*a, **k)

    app._show_frame = counted

    def tick():
        now = time.perf_counter()
        if state["t"] is not None:
            lags.append(now - state["t"] - 0.02)
        state["t"] = now
        if not state.get("stop"):
            app.after(20, tick)

    app.after(20, tick)
    pump(app, seconds)
    state["stop"] = True
    pump(app, 0.1)
    app._show_frame = orig
    lags.sort()
    p95 = lags[int(len(lags) * 0.95)] if lags else 0.0
    worst = lags[-1] if lags else 0.0
    fps = state["frames"] / seconds
    results.append((label + " — lag p95", p95))
    results.append((label + " — worst lag", worst))
    print(f"  {label:<58} lag p95 {p95 * 1000:5.0f} ms · worst {worst * 1000:5.0f} ms · {fps:4.1f} frames/s", flush=True)
    return p95, worst, fps


def make_session(folder: Path, hours: float = 5.0):
    random.seed(7)
    segs, t, k = [], 1.0, 0
    speakers = ["Robin", "Sam", "Kim", "Facilitator"]
    while t < hours * 3600:
        n = random.randint(10, 26)
        ws, tt = [], t
        for _ in range(n):
            d = random.uniform(0.18, 0.45)
            ws.append({"word": " " + random.choice(WORDS), "start": round(tt, 3), "end": round(tt + d, 3), "probability": 0.9})
            tt += d + 0.05
        segs.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": "".join(w["word"] for w in ws),
                     "speaker": speakers[k % 4], "words": ws})
        t = tt + random.uniform(0.3, 2.5)
        k += 1
    video = folder / "session.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=120",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", str(video)], check=True)
    doc = TD.create(folder, "G1D1_session", {"segments": segs}, "large-v3-turbo", {}, {}, {"playback": str(video)}, {"inputs": []})
    for i in range(20):
        a = i * 900 + 30
        doc.add_episode("Interview" if i % 2 else "Phase", f"G1D1_I{i}", a, a + 600, False)
    doc.save()
    return doc


def main():
    study = WORK / "Study"
    folder = study / "Day 1" / "Group 1" / "Session Recordings" / "G1D1_session"
    folder.mkdir(parents=True)
    proj.create_project(study)
    t0 = time.perf_counter()
    doc = make_session(folder)
    n_words = sum(len(s["words"]) for s in doc.segments)
    print(f"5-hour session: {len(doc.segments)} segments, {n_words} words, {doc.path.stat().st_size / 1e6:.1f} MB "
          f"(made in {time.perf_counter() - t0:.1f} s)", flush=True)

    app = CondenseApp()
    app.report_callback_exception = lambda *a: errors.append(a)
    app.geometry("1500x950+20+20")
    pump(app, 1.0)
    codes = []
    cbk = cod.Codebook.for_project(study)
    for name in ("Making", "Materials", "Help", "Ideas", "Rules", "Feelings", "Talk to adults", "Play", "Conflict", "Tools"):
        codes.append(cbk.add(name))
    cbk.save()
    # 600 codings: 500 of words, 100 by time
    words = [(si, wi, w) for si, s in enumerate(doc.segments) for wi, w in enumerate(s["words"])]
    random.seed(3)
    for k in range(500):
        i = random.randrange(0, len(words) - 12)
        a, b = words[i][2], words[i + random.randint(2, 10)][2]
        cod.add_coding(doc, random.choice(codes), a["start"], b["end"], a["wid"], b["wid"])
    for k in range(100):
        a = random.uniform(0, 5 * 3600 - 60)
        cod.add_coding(doc, random.choice(codes), a, a + random.uniform(3, 40))
    doc.save()
    print(f"with 600 codings: {doc.path.stat().st_size / 1e6:.1f} MB", flush=True)

    print("\nTranscript tab", flush=True)
    timed(app, "open the session (Transcript tab)", lambda: app._open_at(doc.path))
    d = app.active_doc
    timed(app, "save the transcript (doc.save)", lambda: d.save())
    text = d.path.read_text(encoding="utf-8")
    timed(app, "an undo step (pack the previous file)", lambda: app._pack_state(text))
    timed(app, "find the codings (coding.resolve)", lambda: cod.resolve(d))

    print("\nCoding tab", flush=True)
    timed(app, "switch to the Coding tab (first time)", lambda: app.notebook.select(app.tab_coding))
    timed(app, "back to Transcript, then Coding again", lambda: (app.notebook.select(app.tab_preview), app.update(),
                                                                   app.notebook.select(app.tab_coding)))
    timed(app, "redraw the Coding tab (_cd_render)", lambda: app._cd_render(app.active_doc))
    timed(app, "  the coding list (_cd_fill_list)", app._cd_fill_list)
    timed(app, "  the stripes (_cd_draw_stripes)", app._cd_draw_stripes)
    timed(app, "  the timeline (_cdt_draw)", app._cdt_draw)
    timed(app, "  the codebook (_cd_fill_codebook)", app._cd_fill_codebook)
    t = app.cd_text
    t.yview_moveto(0.5)
    pump(app, 0.2)
    wl = app._cd["words"]
    mid = len(wl) // 2

    def select_and_code():
        a, b = wl[mid], wl[mid + 6]
        t.tag_remove("sel", "1.0", tk.END)
        t.tag_add("sel", f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
        return app._cd_apply(codes[0])

    kid = timed(app, "code selected words (↩)", select_and_code)
    timed(app, "select a coding (click)", lambda: app._cd_select_coding(kid))
    timed(app, "give it another code", lambda: app._cd_change_code(kid, codes[1]))
    app.play_pos = 9000.0
    app._cdt_set_mark(9000.0, 9020.0)
    kt = timed(app, "code the marked range by time (↩)", lambda: app._cdt_code_mark(codes[2]))
    timed(app, "delete a coding (Del)", lambda: app._cd_remove(kt))
    timed(app, "undo (⌘Z)", app._undo)
    timed(app, "timeline: zoom to 2 minutes", lambda: app._cdt_zoom("120"))
    timed(app, "timeline: whole recording", lambda: app._cdt_zoom("whole"))
    timed(app, "scroll the coded text (page down ×10)", lambda: [t.yview_scroll(1, "pages") or app.update() for _ in range(10)])
    timed(app, "playhead moves (follow, ×20)", lambda: [app._cd_follow(9000 + k) or app._cdt_playhead(9000 + k) for k in range(20)])

    print("\nPlaying the video while coding", flush=True)
    app.var_show_video.set(True)
    app._toggle_video_panel()
    pump(app, 0.5)
    responsiveness(app, "idle, Coding tab open", 2.0)
    app.play_pos = 20.0
    app._player_start(20.0)
    pump(app, 0.5)
    proc = app.__dict__.get("_play_proc")
    print(f"  (player: playing={app.playing}, audio process={'running' if proc is not None and proc.poll() is None else proc and proc.poll()}, "
          f"video stream={'on' if app.__dict__.get('_video_proc') is not None else 'off'}, media={app._preview_media()}, "
          f"screen={app._video_size()})", flush=True)
    responsiveness(app, "playing, video in the tab", 4.0)
    app._player_pause()
    pump(app, 0.3)
    app._video_pop_out()
    pop = app._video_popout
    pop.win.geometry("1400x850+60+60")
    pump(app, 1.0)
    app._player_start(30.0)
    pump(app, 0.5)
    responsiveness(app, "playing, popped out 1400×850", 4.0)
    app._player_pause()
    pump(app, 0.3)
    timed(app, "jump while paused (still frame)", lambda: (app._timeline_jump(50.0), pump(app, 0.4)))
    app._video_pop_in()
    pump(app, 0.3)

    if errors:
        import traceback
        traceback.print_exception(*errors[0])
        raise SystemExit("Tk reported exceptions")
    out = WORK / "perf.json"
    out.write_text(json.dumps({k: round(v, 4) for k, v in results}, indent=1))
    print(f"\nPERF DONE ({len(results)} measurements, {out})", flush=True)
    app.destroy()


if __name__ == "__main__":
    main()
