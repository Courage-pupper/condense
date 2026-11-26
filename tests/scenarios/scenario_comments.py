"""Comments: on selected words (Code mode), a range marked on the timeline, the open segment (Edit mode), a whole block
of the timeline; editing, deleting, undo; the Comments lane and the codings list; the commentary export (one transcript
and the whole project) as a spreadsheet with a video frame per block."""
import os
import sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import coding as cod, commentary as cm, project as proj
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])


def segs(n, offset=0):
    out = []
    lines = ["we built a raft with sticks", "mine floats look", "the sail is too heavy maybe", "put more weight at the bottom"]
    for k in range(n):
        t = 2.0 + k * 6
        ws = lines[(k + offset) % len(lines)].split()
        out.append({"start": t, "end": t + len(ws) * 0.5, "text": " " + " ".join(ws), "speaker": ("Robin", "Sam", "Facilitator")[k % 3],
                    "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": 0.95} for i, w in enumerate(ws)]})
    return out


study = Path(tempfile.mkdtemp(prefix="study_")) / "Study"
import shutil, subprocess
long_video = Path(tempfile.mkdtemp()) / "long.mp4"  # as long as the transcripts (the test media are 3 s)
if shutil.which("ffmpeg"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=2:duration=250",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(long_video)], check=True)
docs = []
for day, group in (("Day 1", "Group 1"), ("Day 1", "Group 2")):
    g = study / day / group / "Session Recordings"
    (g / "s").mkdir(parents=True)
    d = TD.create(g / "s", f"{group[-1]}D1", {"segments": segs(40, len(docs))}, "small", {}, {},
                  {"playback": str(media / "P02.mp4"), "video": str(long_video)}, {"inputs": []})
    d.save()
    docs.append(d)
proj.create_project(study)
proj.update_project(study, levels=["Day", "Group"])
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True
opened = []
app._open_externally = lambda p: opened.append(p) or True
app._render_doc(TD.load(docs[0].path))
app.notebook.select(app.tab_preview)
comments = lambda: [c for c in cod.codings(TD.load(docs[0].path)) if c["code"] == cod.COMMENT]

print("[1] Code mode: a comment on selected words")
app._set_tx_mode("code")
cb = app._cd_codebook()
making = cb.add("Making"); cb.save(); app._cd_fill_codebook()
words = app._cd["words"]
k0 = next(k for k, w in enumerate(words) if w[3] == 4)
k1 = max(k for k, w in enumerate(words) if w[3] == 6)
a, b = words[k0], words[k1]
app.cd_text.tags["sel"] = (f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
win, st = app._comment_dialog()
st["text"].insert("1.0", "They test the raft together.\nSam leads.")
kid = st["save"]()
c = cod.get_coding(TD.load(docs[0].path), kid)
assert c["code"] == cod.COMMENT and c["memo"] == "They test the raft together.\nSam leads." and c.get("start_wid")
assert app._cd["sel"] == kid
lanes = app._cdt_lane_codes(app._cd["resolved"])
assert lanes[0] == cod.COMMENT                                        # a Comments lane, first
assert cb.get(cod.COMMENT)["name"] == "✎ Comment"
ok("selected words → a comment (paragraphs kept), on its words; the timeline gets a Comments lane")

print("[2] a range marked on the timeline, then Edit mode: the open segment")
app.cd_text.tags.pop("sel", None)
app._cdt_set_mark(100.0, 130.0)
win, st = app._comment_dialog()
st["text"].insert("1.0", "Everyone at the table, nobody talks")
k_time = st["save"]()
assert cod.get_coding(TD.load(docs[0].path), k_time)["kind"] == "time"
app._cdt_clear_mark()
app._set_tx_mode("edit")
app._goto_segment(12)
win, st = app._comment_dialog()
st["text"].insert("1.0", "Robin explains the sail")
k_seg = st["save"]()
cs = cod.get_coding(TD.load(docs[0].path), k_seg)
seg12 = TD.load(docs[0].path).segments[12]
assert abs(cs["start"] - seg12["words"][0]["start"]) < 1e-6 and abs(cs["end"] - seg12["words"][-1]["end"]) < 1e-6
assert [i for i in app._cdt_items() if i[0] == "lane"][0][3] == cod.COMMENT   # Edit mode's timeline shows them too
ok("a marked range → a comment by time; in Edit mode the open segment; both on the timeline")

print("[3] a whole block, editing, deleting, undo")
app.settings["commentary_minutes"] = 1
win, st = app._comment_on_block(130.0)
st["text"].insert("1.0", "Block description: building phase")
k_blk = st["save"]()
cb_ = cod.get_coding(TD.load(docs[0].path), k_blk)
assert (cb_["start"], cb_["end"]) == (120.0, 180.0)
app._set_tx_mode("code")
win, st = app._cd_memo(k_blk)                                       # Memo… on a comment edits the comment
assert st["text"].get("1.0", "end").strip() == "Block description: building phase"
st["text"].delete("1.0", "end"); st["text"].insert("1.0", "Building phase: raft 2")
st["save"]()
assert cod.get_coding(TD.load(docs[0].path), k_blk)["memo"] == "Building phase: raft 2"
win, st = app._comment_dialog(k_blk)
st["text"].delete("1.0", "end")
st["save"]()                                                         # empty: the comment goes
assert cod.get_coding(TD.load(docs[0].path), k_blk) is None
app._undo()
assert cod.get_coding(TD.load(docs[0].path), k_blk) is not None     # undo brings it back
assert len(comments()) == 4
ok("a comment on a 1-minute block (2:00–3:00); edited (also via Memo…); emptied = deleted; undo")

print("[4] the export: this transcript")
w2 = cod.get_coding(app.active_doc, kid)
app.active_doc.segments[20]["note"] = "check the word 'raft'"
cod.update_coding(app.active_doc, cod.add_coding(app.active_doc, making, 30.0, 40.0), memo="making by time")
app._cd_commit("test")
win, st = app._commentary_dialog()
st["minutes"].set("1")
out = Path(tempfile.mkdtemp()) / "c.xlsx"
res = st["go"](target=out, wait=True, open_after=False)
assert out.exists() and res["sessions"] == 1
from openpyxl import load_workbook
ws = load_workbook(out)["Commentary"]
head = [x.value for x in ws[1]]
assert head[:3] == ["Day", "Group", "Type"] and "Session" in head and head[-len(cm.COLUMNS):] == cm.COLUMNS
vals = [[x.value for x in r] for r in ws.iter_rows(min_row=2)]
kinds = [v[head.index("Kind")] for v in vals]
assert kinds.count("Comment") == 4 and kinds.count("Coding memo") == 1 and kinds.count("Note") == 1
dur = cm.duration_of(app.active_doc)
assert len({v[head.index("Block")] for v in vals}) == len(cm.blocks(dur, 1))  # every block, also empty ones
print(f"    {res['rows']} rows, {res['frames']} frames")
if shutil.which("ffmpeg"):
    assert res["frames"] == len(cm.blocks(dur, 1)) and len(ws._images) == res["frames"]
assert "rows from 1 transcript" in app.lbl_status.cget("text")
ok("one transcript: every 1-minute block a row, comments + coding memo + note, a video frame per block")

print("[5] the export: the whole project")
win, st = app._commentary_dialog()
st["scope"].set("project"); st["minutes"].set("5"); st["per_sheet"].set(True); st["fmt"].set("csv")
out2 = out.with_suffix(".csv")
res2 = st["go"](target=out2, wait=True, open_after=False)
assert res2["sessions"] == 2 and res2["frames"] == 0
import csv
rows = list(csv.reader(open(out2, encoding="utf-8-sig")))
groups = {r[1] for r in rows[1:]}
assert rows[0][:2] == ["Day", "Group"] and groups == {"Group 1", "Group 2"}
assert app.settings["commentary_minutes"] == 5
ok("the whole project in one sheet (CSV here), with Day and Group of each transcript")
print("\nALL TESTS PASSED")
