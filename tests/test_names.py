"""Renaming people and pseudonyms (core/names.py): words keep their ids (so codings stay), swaps, names of several
words, other people with a name in common, copies with pseudonyms."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.core import coding as cod  # noqa: E402
from condense.core import names  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402


def doc_with(tmp_path, lines, base="S"):
    segs = []
    for k, (spk, text) in enumerate(lines):
        ws = [{"word": " " + w, "start": k * 10 + i * 0.5, "end": k * 10 + i * 0.5 + 0.4, "probability": 0.9} for i, w in enumerate(text.split())]
        segs.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": "".join(w["word"] for w in ws), "speaker": spk, "words": ws})
    return TD.create(tmp_path, base, {"segments": segs}, "small", {}, {}, {"playback": None}, {"inputs": []})


def texts(doc):
    return [s["text"] for s in doc.segments]


def test_whole_words_only_and_ids_kept(tmp_path):
    d = doc_with(tmp_path, [("Robin", "Robin, Robin's and Robinson")])
    ids = [w["wid"] for w in d.segments[0]["words"]]
    assert names.replace_names(d, {"Robin": "Sam"}) == 2
    assert texts(d) == [" Sam, Sam's and Robinson"]
    assert [w["wid"] for w in d.segments[0]["words"]] == ids
    assert d.segments[0]["speaker"] == "Robin"  # replace_names is the text only


def test_swap(tmp_path):
    d = doc_with(tmp_path, [("Kim", "Kim asks Robin")])
    names.replace_names(d, {"Kim": "Robin", "Robin": "Kim"})
    assert texts(d) == [" Robin asks Kim"]


def test_names_of_several_words_first_and_codings_follow(tmp_path):
    d = doc_with(tmp_path, [("Kim", "Thanks Robin Meyer. Robin")])
    w = d.segments[0]["words"]
    kid = cod.add_coding(d, "c", w[1]["start"], w[2]["end"], start_wid=w[1]["wid"], end_wid=w[2]["wid"])
    names.replace_names(d, {"Robin Meyer": "the teacher", "Robin": "P1"})
    assert texts(d) == [" Thanks the teacher. P1"]
    r = next(r for r in cod.resolve(d) if r["id"] == kid)
    assert cod.coded_text(d, r) == "the teacher." and not r.get("lost")


def test_other_people_with_a_name_in_common_are_left_alone(tmp_path):
    d = doc_with(tmp_path, [("Robin", "Robin Meyer met Robin"), ("Kim", "bye")])
    d.segments[1].pop("words")
    d.segments[1]["text"] = " Robin Meyer and Robin wave"
    protect = names.protect_for("Robin", [{"name": "Robin Meyer"}, {"name": "Kim"}])
    assert protect == ["Robin Meyer"]
    assert names.count_mentions(d, ["Robin"], protect) == {"Robin": 2}
    res = names.rename_in_doc(d, "Robin", "Robin Sommer", protect=protect)
    assert res == {"speaker": 1, "text": 2}
    assert texts(d) == [" Robin Meyer met Robin Sommer", " Robin Meyer and Robin Sommer wave"]
    # a renamed name is one word now: protected and replaced as a whole too
    assert names.replace_names(d, {"Robin Sommer": "P2"}, protect=["Robin Meyer"]) == 2
    assert texts(d) == [" Robin Meyer met P2", " Robin Meyer and P2 wave"]


def test_pseudonymised_copy_leaves_the_original(tmp_path):
    d = doc_with(tmp_path, [("Robin", "Hi Kim"), ("Kim", "Hi Robin")])
    d.data["media"] = {"playback": "/study/Robin.mp4"}
    twin = names.pseudonymised(d, {"Robin": "P1", "Kim": "P2"})
    assert texts(twin) == [" Hi P2", " Hi P1"] and twin.roster() == ["P1", "P2"] and twin.data["media"] == {}
    assert texts(d) == [" Hi Kim", " Hi Robin"] and d.roster() == ["Robin", "Kim"]


def test_suggest_and_merge():
    entries = names.merge_people([{"name": "Robin", "pseudonym": "P02"}], ["Robin", "Kim", "SPEAKER_00", " Alex "])
    assert [e["name"] for e in entries] == ["Robin", "Kim", "Alex"]
    assert [e["pseudonym"] for e in names.suggest(entries)] == ["P02", "P01", "P03"]
