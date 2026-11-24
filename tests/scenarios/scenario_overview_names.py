"""Phase 1: the project overview as one table (day, group, type, date from the folders; the project's own attributes;
sort, filter, CSV), attributes for many transcripts at once, the names list (rename throughout, pseudonyms) and
copies exported with pseudonyms."""
import csv, json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(tempfile.mkdtemp(prefix="home_")); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
from condense.core import attributes as attrlib, coding as cod, names as nameslib, project as proj
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument

study = Path(tempfile.mkdtemp(prefix="study_")) / "Demo study"
formats = {"srt": True, "txt": True, "para_txt": True, "para_rtf": True, "vtt": False, "rtf": False}


def words(t0, text):
    return [{"word": " " + w, "start": round(t0 + i * 0.5, 2), "end": round(t0 + i * 0.5 + 0.4, 2), "probability": 0.95}
            for i, w in enumerate(text.split())]


def make(folder, base, lines):
    folder.mkdir(parents=True, exist_ok=True)
    segs = []
    for k, (spk, text) in enumerate(lines):
        w = words(1 + k * 6, text)
        segs.append({"start": w[0]["start"], "end": w[-1]["end"], "text": "".join(x["word"] for x in w), "speaker": spk, "words": w})
    d = TD.create(folder, base, {"segments": segs}, "small", formats, {}, {"playback": None}, {"inputs": []})
    d.save()
    return d


g1d1 = study / "Day 1 2026-08-10" / "Group 1"
g3d1 = study / "Day 1 2026-08-10" / "Group 3"
g3d2 = study / "Day 2 2026-08-11" / "Group 3"
s1 = make(g1d1 / "Session Recordings" / "G1D1_session", "G1D1_session",
          [("Facilitator", "Welcome everyone, Robin and Kim sit here"), ("Robin", "Can Kim help me with the clay"), ("Kim", "Yes Robin")])
s2 = make(g3d1 / "Session Recordings" / "G3D1_session", "G3D1_session", [("Facilitator", "Today we build paper boats"), ("Alex", "Mine floats")])
i1 = make(g3d1 / "Semi-structured Interviews" / "G3D1_I1", "G3D1_I1", [("Facilitator", "What did you like Alex"), ("Alex", "The boats")])
s3 = make(g3d2 / "Session Recordings" / "G3D2_session", "G3D2_session", [("Facilitator", "Robin Meyer visits us today"), ("Alex", "Hello")])
(study / "Consent forms").mkdir()
w = s1.segments[1]["words"]
cod.add_coding(s1, "c1", w[0]["start"], w[3]["end"], start_wid=w[0]["wid"], end_wid=w[3]["wid"])  # "Can Kim help me"
s1.save()

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
proj.create_project(study, name="Demo study", speakers=["Facilitator"])
proj.update_project(study, levels=["Day", "Group"])
app._set_project(study)

def filt(text):  # typing in the filter (the stub's StringVar has no traces)
    st["filter"].set(text)
    st["show"](st["rows"])


print("[1] the overview table")
win, st = app._project_overview()
rows = st["refresh"](background=False)
assert sorted(r["name"] for r in rows) == ["G1D1_session", "G3D1_I1", "G3D1_session", "G3D2_session"]
by = {r["name"]: r for r in rows}
assert by["G3D1_I1"]["auto"] == {"Day": "Day 1 2026-08-10", "Group": "Group 3", "Type": "Interview", "Date": "2026-08-10"}, by["G3D1_I1"]["auto"]
assert by["G3D2_session"]["auto"]["Type"] == "Session" and by["G3D2_session"]["auto"]["Date"] == "2026-08-11"
assert by["G1D1_session"]["codings"] == 1 and by["G1D1_session"]["speaker_names"] == ["Facilitator", "Robin", "Kim"]
tree = st["tree"]
cols = [c for c, _, _ in st["cols"]]
assert cols[:9] == ["name", "a:Day", "a:Group", "a:Type", "a:Date", "length", "checked", "codings", "speakers"], cols
assert "4 transcript(s)" in st["lbl"].cget("text") and "1 codings" in st["lbl"].cget("text")
ok("one row per transcript: Day, Group, Type (session / interview) and Date from the folders, codings, speakers")
filt("group 3 interview")
assert [r["name"] for r in st["shown"]] == ["G3D1_I1"] and "1 of 4 transcript(s)" in st["lbl"].cget("text")
filt("group 3")
assert len(st["shown"]) == 3
filt("")
st["sort_by"]("a:Date"); st["sort_by"]("a:Date")  # descending
assert st["shown"][0]["name"] == "G3D2_session"
st["sort_by"]("codings"); st["sort_by"]("codings")
assert st["shown"][0]["name"] == "G1D1_session"
ok("filter by typing (all words must match: group 3 interview → 1 of 4); sort by any column, again for descending")

print("[2] attributes")
win2, sa = app._set_attribute_dialog(study, [by["G3D1_session"]["path"], by["G3D1_I1"]["path"]])
sa["name"].set("Activity"); sa["value"].set("Paper boats")
assert sa["apply"]() == 2
assert [d["name"] for d in attrlib.definitions(proj.load_project(study / proj.PROJECT_FILE))] == ["Activity"]
assert TD.load(by["G3D1_I1"]["path"]).data["attributes"] == {"Activity": "Paper boats"}
app._render_doc(TD.load(s1.path))
win3, sa = app._set_attribute_dialog(study, [s1.path])
sa["name"].set("Activity"); sa["value"].set("Clay"); sa["apply"]()
assert app.active_doc.data["attributes"]["Activity"] == "Clay" and TD.load(s1.path).data["attributes"]["Activity"] == "Clay"
app._undo()
assert "Activity" not in (TD.load(s1.path).data.get("attributes") or {})
app._redo()
rows = st["refresh"](background=False)
assert "a:Activity" in [c for c, _, _ in st["cols"]]
filt("paper boats")
assert sorted(r["name"] for r in st["shown"]) == ["G3D1_I1", "G3D1_session"]
filt("")
ok("set an attribute for several transcripts at once (a new one joins the project), the open one with undo; it becomes a column and filters")
winA, ad = app._attributes_dialog(study)
ad["list"].curselection = lambda: (0,)  # "Activity" selected in the list
ad["name"].set("Material"); ad["choices"].set("Clay, Paper boats, Dioramas")
ad["save"]()
assert attrlib.definitions(proj.load_project(study / proj.PROJECT_FILE)) == [{"name": "Material", "choices": ["Clay", "Paper boats", "Dioramas"]}]
assert TD.load(by["G3D1_I1"]["path"]).data["attributes"] == {"Material": "Paper boats"}
assert app.active_doc.data["attributes"] == {"Material": "Clay"}
ok("renaming an attribute (with values to choose from) follows in every transcript")
proj.write_overview_csv(st["refresh"](background=False), study / "o.csv", extra=["Material"])
first = {r["name"]: r for r in csv.DictReader(open(study / "o.csv"))}
assert first["G3D1_I1"]["Type"] == "Interview" and first["G3D1_I1"]["Material"] == "Paper boats" and first["G1D1_session"]["codings"] == "1"
ok("Save as CSV has Type, Date, codings and the project's attributes")

print("[3] names: rename throughout")
winN, nd = app._names_dialog(study)


def choose(name):  # select a name in the list (the stub's Treeview keeps no selection)
    k = str([e["name"] for e in nd["state"]["entries"]].index(name))
    nd["tree"].selection = lambda: (k,)


names = [e["name"] for e in nd["state"]["entries"]]
assert names[:1] == ["Facilitator"] and {"Robin", "Kim", "Alex"} <= set(names) and not any(n.startswith("SPEAKER_") for n in names)
asked = []
pipe.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
assert nd["add"]("Robin Meyer")  # someone else, only talked about
choose("Robin")
assert nd["rename"]("Robin Sommer") == 1
assert "1 transcript(s)" in asked[0][1] and "1 speaker label(s) and 2 time(s) said" in asked[0][1], asked[0][1]
assert "Left alone where it is part of: Robin Meyer" in asked[0][1]
d1 = TD.load(s1.path)
assert [sg["speaker"] for sg in d1.segments] == ["Facilitator", "Robin Sommer", "Kim"]
assert d1.segments[0]["text"] == " Welcome everyone, Robin Sommer and Kim sit here" and d1.segments[2]["text"] == " Yes Robin Sommer"
r = cod.resolve(d1)[0]
assert cod.coded_text(d1, r) == "Can Kim help me" and not r.get("lost")
assert TD.load(s3.path).segments[0]["text"] == " Robin Meyer visits us today"  # another person: untouched
assert "Robin Sommer" in [e["name"] for e in nd["state"]["entries"]] and "Robin" not in [e["name"] for e in nd["state"]["entries"]]
assert "Robin Sommer" in (s1.path.parent / "G1D1_session.srt").read_text()
app._undo()
assert TD.load(s1.path).segments[1]["speaker"] == "Robin"
app._redo()
ok("renamed in speakers and text (whole words: 'Robin Meyer' is someone else), codings stay, exports and the list follow, undo")

print("[4] pseudonyms and exports with pseudonyms")
choose("Kim")
nd["pseudonym"].set("Child 2"); assert nd["set"]()
choose("Alex")
nd["pseudonym"].set("Child 2"); assert nd["set"]() is None  # must tell people apart
nd["prefix"].set("P"); nd["suggest"]()
ent = {e["name"]: e["pseudonym"] for e in nd["state"]["entries"]}
assert ent["Kim"] == "Child 2" and ent["Facilitator"] == "P01" and all(ent.values())
saved = {e["name"]: e["pseudonym"] for e in nameslib.name_list(proj.load_project(study / proj.PROJECT_FILE))}
assert saved == ent
target = study.parent / "export"
assert nd["export"](target) == 4
assert ent["Robin Meyer"] and "Robin" not in (target / "G3D2_session" / "G3D2_session.txt").read_text()
pd = TD.load(target / "G1D1_session" / "G1D1_session.transcript.json")
assert [sg["speaker"] for sg in pd.segments] == [ent["Facilitator"], ent["Robin Sommer"], "Child 2"]
assert pd.segments[1]["text"] == " Can Child 2 help me with the clay" and pd.data["media"] == {} and "edit_history" not in pd.data
srt = (target / "G1D1_session" / "G1D1_session.srt").read_text()
assert "Kim" not in srt and "Robin" not in srt and "Child 2" in srt
assert "Kim" in TD.load(s1.path).segments[2]["speaker"]  # the original is untouched
ok("pseudonyms (unique), the rest numbered P01…; copies with pseudonyms (transcript + exports, no paths, no edit log), originals untouched")

print("[5] the overview is fast the second time")
cache = app._overview_cache(study)
t0 = time.perf_counter(); app._overview_rows(study); t1 = time.perf_counter()
assert not cache.dirty and t1 - t0 < 1.0
s2b = TD.load(s2.path); s2b.segments[0]["speaker"] = "Kim"; s2b.save()
rows = app._overview_rows(study)
assert "Kim" in next(r for r in rows if r["name"] == "G3D1_session")["speaker_names"]
ok("rows are kept between openings; a changed transcript is read again")
print("\nALL TESTS PASSED")
