import os
import shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

def seg(t, text, spk):
    ws = text.split()
    return {"start": t, "end": t + len(ws) * 0.5, "text": " " + text, "speaker": spk,
            "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": 0.95} for i, w in enumerate(ws)]}

res = {"segments": [seg(0, "Hello everyone.", "Robin"), seg(2, "Hi there.", "Sam"), seg(4, "We draw now.", "Sam"),
                    seg(6, "Which colour?", "Sam"), seg(8, "Any colour you like.", "Robin"), seg(11, "Blue then.", "Sam")]}
tmp = Path(tempfile.mkdtemp())
d = TD.create(tmp, "Group demo", res, "small", {}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
d.add_speaker("Kim"); d.save()

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
app._render_doc(TD.load(d.path))
pt = app.preview_text
key = lambda k: pt._binds[k](None)
spk = lambda: [s["speaker"] for s in TD.load(d.path).segments]
chips = lambda: [w.cget("text") for w in app.spk_chips._items]

print("[1] number keys in the transcript, ↑/↓ between segments")
assert app.edit_seg_idx is None
key("<Down>"); assert app.edit_seg_idx == 0
key("<Down>"); assert app.edit_seg_idx == 1
key("<Key-3>")
assert spk() == ["Robin", "Kim", "Sam", "Sam", "Robin", "Sam"], spk()
key("<Up>"); assert app.edit_seg_idx == 0
key("<Up>"); assert app.edit_seg_idx == 0  # stays at the first one
assert chips()[1:] == ["1  Robin", "2  Sam", "3  Kim", "0  + Speaker"], chips()
from types import SimpleNamespace
before = spk()
for state in (0x4, 0x8):  # Ctrl / Command(Mac) or Alt: copy, tab switching, ⌃digits must pass through
    assert pt._binds["<Key-c>"](SimpleNamespace(state=state)) is None and pt._binds["<Key-2>"](SimpleNamespace(state=state)) is None
assert spk() == before and not TD.load(d.path).segments[0].get("checked")
assert pt._binds["<Key-c>"](SimpleNamespace(state=0x1 | 0x2 | 0x10 * (pipe.IS_MAC is False))) == "break"  # Shift / Caps / Num Lock still count
pt._binds["<Key-c>"](None)  # undo that check mark
assert not TD.load(d.path).segments[0].get("checked")
ok("1–9 assign the open segment; ↑/↓ move between segments; chips show their key")

print("[2] ⇧↓ selects several segments; one key assigns them all, as one undo step")
key("<Down>"); key("<Shift-Down>"); key("<Shift-Down>"); key("<Shift-Down>")
assert app.sel_segs == [1, 2, 3, 4] and app.sel_anchor == 1 and app.edit_seg_idx == 4
assert chips()[0] == "Assign to 4 selected segments (2–5):", chips()[0]
key("<Shift-Up>")
assert app.sel_segs == [1, 2, 3]
n_undo = len(app._undo_stacks[str(d.path)][0])
key("<Key-1>")
assert spk() == ["Robin", "Robin", "Robin", "Robin", "Robin", "Sam"], spk()
assert len(app._undo_stacks[str(d.path)][0]) == n_undo + 1
assert app.sel_segs == [1, 2, 3] and "Robin" in app.lbl_status.cget("text")
assert chips()[1] == "1  Robin" and app.spk_chips._items[1].cget("bg") == pipe.SPEAKER_COLORS[0]  # shared speaker highlighted
app._undo()
assert spk() == ["Robin", "Kim", "Sam", "Sam", "Robin", "Sam"], spk()
assert app.sel_segs == [1, 2, 3]  # undo keeps the selection, so you can pick another speaker
ok("⇧↓/⇧↑ grow and shrink the selection; 1 assigned all three; one ⌘Z undid all three")

print("[3] mouse: drag across segments, ⇧-click, plain click clears")
pt.tags["sel"] = (app.seg_ranges[3][0], app.seg_ranges[5][0].split(".")[0] + "." + str(int(app.seg_ranges[5][0].split(".")[1]) + 3))
app._span_at = lambda e: app.span_lookup[(5, 0)]
app._on_text_click(None)
assert app.sel_segs == [3, 4, 5] and app.sel_anchor == 3 and app.edit_seg_idx == 5, (app.sel_segs, app.sel_anchor)
key("<Key-c>")
dd = TD.load(d.path)
assert [bool(s.get("checked")) for s in dd.segments] == [False, False, False, True, True, True]
key("<Key-C>")
assert not any(s.get("checked") for s in TD.load(d.path).segments)
pt.tags.pop("sel", None)  # a redraw clears the selection, as in Tk
app._span_at = lambda e: app.span_lookup[(0, 0)]
app._on_text_click(None)
assert app.sel_segs == [] and app.edit_seg_idx == 0 and chips()[0] == "Assign:"
app._span_at = lambda e: app.span_lookup[(2, 1)]
app._on_shift_click(None)
assert app.sel_segs == [0, 1, 2] and app.edit_seg_idx == 2
app._span_at = lambda e: app.span_lookup[(4, 0)]
app._on_shift_click(None)
assert app.sel_segs == [0, 1, 2, 3, 4] and app.sel_anchor == 0
ok("drag selected 4–6, C checked/unchecked them all; click cleared; ⇧-click extends from the anchor")

print("[4] an unsaved text edit is kept; editor speaker list and ⌃digits also apply to the selection")
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "Any colour you like, really.")
app._assign_speaker_number(2)  # what ⌃2 does while typing
dd = TD.load(d.path)
assert [s["speaker"] for s in dd.segments] == ["Sam"] * 6, [s["speaker"] for s in dd.segments]
assert dd.segments[4]["text"].strip() == "Any colour you like, really."
app.var_seg_speaker.set("Kim"); app._on_speaker_combo()
assert spk()[:5] == ["Kim"] * 5 and spk()[5] == "Sam"
ok("typed correction saved first, then all five assigned; the speaker list applies to all selected too")

print("[5] selection is dropped when segments are renumbered")
assert app.sel_segs
app._span_at = lambda e: app.span_lookup[(4, 2)]
app.current_span = app.span_lookup[(4, 2)]
app._split_at_current_word()
assert len(TD.load(d.path).segments) == 7 and app.sel_segs == [] and "MULTISEL" not in pt.tags
ok("split → old selection cleared instead of pointing at the wrong segments")

print("[6] ⌘A + 0 (new speaker for everything), R replays, Return/Tab move focus, shortcut list")
key(f"<{pipe.MOD}-Key-a>" if hasattr(pipe, "MOD") else "<Command-Key-a>")
assert app.sel_segs == list(range(7))
stub_tk2.sd._next = "Facilitator"
key("<Key-0>")
assert spk() == ["Facilitator"] * 7 and "Facilitator" in TD.load(d.path).roster()
played = []
app._player_start = lambda pos, duration=None: played.append((round(pos, 2), round(duration, 2)))
app._goto_segment(1); key("<Key-r>")
assert played == [(1.7, 1.6)], played
key("<Return>")
assert app.edit_text.marks["insert"] == "end-1c"
assert "<Tab>" in app.edit_text._binds and "<Shift-ButtonRelease-1>" in pt._binds
win = app._show_shortcuts()
assert win is not None
ok("all 7 assigned to a new speaker in one go; R plays 1.7 s for 1.6 s; focus keys and shortcut window work")
