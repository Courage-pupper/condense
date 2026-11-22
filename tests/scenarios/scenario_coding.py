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
    lines = ["we built a raft with sticks", "mine floats look", "the sail is too heavy maybe", "put more weight at the bottom",
             "I am scared of strangers online", "my brother plays games all night"]
    for k in range(n):
        t = 2.0 + k * 6
        ws = lines[k % len(lines)].split()
        out.append({"start": t, "end": t + len(ws) * 0.5, "text": " " + " ".join(ws), "speaker": ("Robin", "Sam", "Facilitator")[k % 3],
                    "words": [{"word": " " + w, "start": t + i * 0.5, "end": t + i * 0.5 + 0.4, "probability": 0.95} for i, w in enumerate(ws)]})
    return out


study = Path(tempfile.mkdtemp(prefix="study_")) / "Study"
g = study / "Day 1" / "Group 1" / "Session Recordings"
g.mkdir(parents=True)
proj.create_project(study)
(g / "s1").mkdir()
doc = TD.create(g / "s1", "s1", {"segments": segs(12)}, "small", {"srt": True}, {}, {"playback": str(media / "P02.mp4")}, {"inputs": []})
doc.save()

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
pipe.messagebox.askyesno = lambda *a, **k: True

print("[1] no project: the Coding tab says where codes live")
app._set_project(None)
assert not app._cd_show_state()
print("[2] open a transcript of the study: its codebook, its text")
app._render_doc(TD.load(doc.path))
app._cd_refresh()
assert app._cd_project() == study.resolve() or app._cd_project() == study
text = app.cd_text.content
assert "Robin" in text and "raft with sticks" in text and len(app._cd["words"]) == sum(len(s["words"]) for s in doc.segments)
ok("transcript shown with speakers; every word mapped")

def select(seg, w0, w1):
    words = app._cd["words"]
    a = next(n for n, w in enumerate(words) if w[3] == seg and w[4] == w0)
    b = next(n for n, w in enumerate(words) if w[3] == seg and w[4] == w1)
    app.cd_text.tags["sel"] = (f"{words[a][0]}.{words[a][1]}", f"{words[b][0]}.{words[b][2]}")

print("[3] code inductively: new codes from selections, recent codes, subcodes")
stub_tk2.sd._next = "Building"
select(0, 1, 4)                                  # "built a raft with"
k1 = None
app._cd_new_code(apply=True)
cb = app._cd_codebook()
build = cb.find("Building")["id"]
d = TD.load(doc.path)
assert len(cod.codings(d)) == 1 and cod.coded_text(d, cod.resolve(d)[0]) == "built a raft with"
stub_tk2.sd._next = "Online risks"
select(4, 4, 5)                                  # "strangers online"
app._cd_new_code(apply=True)
risk = cb.find("Online risks")["id"]
assert app._cd["recent"][:2] == [risk, build]
select(6, 0, 2)                                  # "we built a"
app._cd_key(type("E", (), {"keysym": "2", "state": 0})())   # 2 = the second recent code: Building
assert [r["code"] for r in cod.resolve(TD.load(doc.path))].count(build) == 2
stub_tk2.sd._next = "Strangers"
app.cd_tree.selection_set(risk) if hasattr(app.cd_tree, "selection_set") else None
sub = app._cd_new_code(parent=risk)
assert cb.get(sub)["parent"] == risk and cb.path_name(sub) == "Online risks ▸ Strangers"
assert cod.counts(study) == {build: 2, risk: 1}
ok("N makes a code from the selection and codes it; 1–9 reuse recent codes; subcodes")

print("[4] a moment without speech, described")
win, td = app._cd_time_dialog(40.0, 47.5)
td["code"].set("Building"); td["memo"].insert("1.0", "tapes the sail to the stick")
assert td["save"]()
moments = [r for r in cod.resolve(TD.load(doc.path)) if r.get("kind") == "time"]
assert len(moments) == 1 and moments[0]["memo"] == "tapes the sail to the stick" and moments[0]["start"] == 40.0
assert "◆ Building  0:00:40 – 0:00:47   tapes the sail to the stick" in app.cd_text.content
ok("time coding saved with its description and shown in the text at its time")

print("[5] change, memo, split a big code by moving codings, merge, delete, undo")
d = TD.load(doc.path)
k_words = [r for r in cod.resolve(d) if r["code"] == build and r.get("words")]
app._cd_select_coding(k_words[1]["id"])
app._cd_memo(k_words[1]["id"], text="same activity, day 1")
assert cod.get_coding(TD.load(doc.path), k_words[1]["id"])["memo"] == "same activity, day 1"
stub_tk2.sd._next = "Rafts"
rafts = app._cd_new_code(parent=build)
app._cd_change_code(k_words[0]["id"], rafts)      # splitting "Building": this coding belongs to the new subcode
assert cod.get_coding(TD.load(doc.path), k_words[0]["id"])["code"] == rafts
app._undo()
assert cod.get_coding(TD.load(doc.path), k_words[0]["id"])["code"] == build
app._redo()
app._cd_merge_dialog(rafts, target=build)         # changed our mind: merge back
assert cb.get(rafts) is None and all(r["code"] == build for r in cod.resolve(TD.load(doc.path)) if r["code"] != risk)
assert any(e["action"] == "merge" for e in cb.data["log"])
app._cd_delete_code(risk)
assert cb.get(risk) is None and cb.get(sub) is None and not any(r["code"] == risk for r in cod.resolve(TD.load(doc.path)))
before = len(cod.codings(TD.load(doc.path)))
app._cd_remove(k_words[1]["id"])
assert len(cod.codings(TD.load(doc.path))) == before - 1
app._undo()
assert len(cod.codings(TD.load(doc.path))) == before
ok("memo; split by moving a coding to a new subcode (undo/redo); merge back; delete with subcodes; remove + undo")

print("[6] codings stay on their words when the text is edited in the Transcript tab")
d = TD.load(doc.path)
r = next(r for r in cod.resolve(d) if r.get("words"))
expected = cod.coded_text(d, r)
(si, wi), _ = r["words"]
app._render_doc(TD.load(doc.path))
app._goto_segment(si)
app.edit_text.delete("1.0", "end"); app.edit_text.insert("1.0", "Okay " + d.segments[si]["text"].strip())
app._save_segment_edit()
d2 = TD.load(doc.path)
r2 = next(x for x in cod.resolve(d2) if x["id"] == r["id"])
assert cod.coded_text(d2, r2) == expected and d2.segments[si]["text"].strip().startswith("Okay"), (cod.coded_text(d2, r2), expected)
ok("adding a word before the coded ones keeps the coding on the same words")

print("[7] the codebook file")
data = cod.Codebook.for_project(study).data
assert [c["name"] for c in data["codes"]] == ["Building"] and (study / cod.CODEBOOK_FILE).exists()
ok("condense-codebook.json in the study folder, with its change log")
