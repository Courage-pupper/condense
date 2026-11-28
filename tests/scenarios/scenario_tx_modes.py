"""One workspace: the Transcript tab in Code and Edit mode — the switch (⌘E, ⌘3 / ⌘4), staying at the same place,
speakers with ⌃1–9 / Alt+1–9 in both modes (on what is selected in the mode shown), Find going to Edit mode, one video
screen, and no Coding tab any more."""
import os
import sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import coding as cod, project as proj
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])


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
doc = TD.create(g / "s1", "s1", {"segments": segs(20)}, "small", {}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
doc.save()
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
app._render_doc(TD.load(doc.path))
app.notebook.select(app.tab_preview)

print("[1] one tab, two modes")
assert "tab_coding" not in app.__dict__                     # the Coding tab is gone
assert app.tx_mode == "edit" and not app._cd_visible()
assert app.btn_mode_edit.cget("style") == "SegOn.TButton" and app.btn_mode_code.cget("style") == "Seg.TButton"
assert set(app._video_screens) <= {"tx", "pop"}               # one video screen in the tab (and the pop-out)
ok("Edit mode first; no Coding tab, one video screen")

print("[2] Edit → Code: the same place")
app._goto_segment(7)
assert app.edit_seg_idx == 7
assert app._set_tx_mode("code") == "code" and app._cd_visible() and app.settings["tx_mode"] == "code"
assert app.btn_mode_code.cget("style") == "SegOn.TButton"
cb = app._cd_codebook()
making = cb.add("Making"); cb.save(); app._cd_fill_codebook()
app._cd_refresh()
w = app._cd["words"][app._cd["word"]]
assert (w[3], w[4]) == (7, 0), w                            # Code mode shows where Edit mode was
ok("switching keeps the place: the segment being edited is the word shown in Code mode")

print("[3] speakers in Code mode act on what is selected there")
roster = app._roster()
words = app._cd["words"]
first = next(k for k, x in enumerate(words) if x[3] == 3)
last = max(k for k, x in enumerate(words) if x[3] == 4)
a, b = words[first], words[last]
app.cd_text.tags["sel"] = (f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
target = roster.index("Facilitator") + 1
before7 = TD.load(doc.path).segments[7]["speaker"]
app._assign_speaker_number(target)
d = TD.load(doc.path)
assert d.segments[3]["speaker"] == d.segments[4]["speaker"] == "Facilitator", [s["speaker"] for s in d.segments[:6]]
assert d.segments[7]["speaker"] == before7                  # not the segment the (hidden) editor had open
assert app._cd.get("doc") is app.active_doc                 # Code mode shows the new speakers
app.cd_text.tags.pop("sel", None)
app._cd["word"] = next(k for k, x in enumerate(app._cd["words"]) if x[3] == 10)
app._assign_speaker_number(roster.index("Robin") + 1)
assert TD.load(doc.path).segments[10]["speaker"] == "Robin"
app._undo()
assert TD.load(doc.path).segments[10]["speaker"] != "Robin"  # one undo step
ok("⌃1–9 / Alt+1–9 in Code mode: the selected words' segments, else the clicked word's; the hidden editor is untouched")

print("[4] coding still works, then Code → Edit at the same place")
app._cd_refresh()
words = app._cd["words"]
k0 = next(k for k, x in enumerate(words) if x[3] == 12)
a, b = words[k0], words[k0 + 2]
app.cd_text.tags["sel"] = (f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
kid = app._cd_apply(making)
assert kid and cod.get_coding(TD.load(doc.path), kid)["code"] == making
app._cd["word"] = k0 + 1
assert app._set_tx_mode() == "edit" and not app._cd_visible()   # ⌘E: back to Edit
sp = app.word_spans[app.current_span]
assert (sp["seg"], sp["w"]) == (12, 1), sp
assert app.edit_seg_idx == 12 and app.settings["tx_mode"] == "edit"
ok("coding in Code mode; ⌘E back to Edit mode opens the segment where you were")

print("[5] Find, ⌘3 / ⌘4, the timeline in both modes")
app._set_tx_mode("code")
app._toggle_find()
assert app.tx_mode == "edit" and app._find_visible           # Find searches in Edit mode
app._hide_find()
app._set_tx_mode("code"); app._tl_show_for_tab()
assert app.tl_shown
app._set_tx_mode("edit"); app._tl_show_for_tab()
assert app.tl_shown
app.notebook.select(app.tab_settings); app._tl_show_for_tab()
assert not app.tl_shown and not app._cd_visible()
app._set_tx_mode("code")                                     # ⌘4: the Transcript tab, Code mode
assert app.notebook.select() == str(app.tab_preview) and app._cd_visible()
ok("Find goes to Edit mode; ⌘3 / ⌘4 open the Transcript tab in a mode; one timeline for both modes")
print("\nALL TESTS PASSED")
