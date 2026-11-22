"""Coding core: an evolving codebook (add, rename, move, merge, split by recoding, remove) and codings that stay on their
words through random edits, including codings of moments without speech."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from condense.core import coding  # noqa: E402
from condense.core import project as proj  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402
from test_word_ids import all_words, make, random_edit  # noqa: E402


def test_codebook_grows_and_changes(tmp_path):
    cb = coding.Codebook.for_project(tmp_path)
    risk = cb.add("Risks")
    online = cb.add("Online risks", parent=risk, definition="Dangers children name for apps and games")
    strangers = cb.add("Strangers", parent=online)
    agency = cb.add("Agency")
    assert [(d, c["name"]) for d, c in cb.ordered()] == [(0, "Risks"), (1, "Online risks"), (2, "Strangers"), (0, "Agency")]
    assert cb.path_name(strangers) == "Risks ▸ Online risks ▸ Strangers"
    assert cb.get(online)["color"] == cb.get(risk)["color"] and cb.get(agency)["color"] != cb.get(risk)["color"]
    with pytest.raises(ValueError):
        cb.add("strangers", parent=online)  # same name in the same place
    cb.add("Strangers", parent=agency)  # same name elsewhere is fine
    with pytest.raises(ValueError):
        cb.move(risk, strangers)  # not into its own branch
    cb.move(strangers, None, before=agency)
    assert [c["name"] for _, c in cb.ordered()][:4] == ["Risks", "Online risks", "Strangers", "Agency"]
    assert cb.get(strangers)["parent"] is None
    cb.update(online, name="Online dangers", memo="split later?")
    cb.save()
    again = coding.Codebook.for_project(tmp_path)
    assert again.get(online)["name"] == "Online dangers" and again.get(online)["memo"] == "split later?"
    assert [e["action"] for e in again.data["log"]][:4] == ["add", "add", "add", "add"]
    assert again.remove(risk) == [risk, online] and again.get(online) is None


def test_codings_follow_their_words(tmp_path):
    doc = make(tmp_path, n_segs=40, seed=11)
    rng = random.Random(3)
    ws = all_words(doc)
    made = []
    for _ in range(40):
        a = rng.randrange(len(ws) - 4)
        b = a + rng.randrange(0, 4)
        cid = coding.add_coding(doc, rng.choice(["c1", "c2"]), ws[a]["start"], ws[b]["end"], ws[a]["wid"], ws[b]["wid"])
        made.append((cid, ws[a]["start"], ws[b]["end"]))
    silent = coding.add_coding(doc, "c3", 100.0, 104.5, memo="  builds   the raft with tape ")
    assert coding.get_coding(doc, silent)["kind"] == "time" and coding.get_coding(doc, silent)["memo"] == "builds the raft with tape"
    doc.save()
    for step in range(600):
        random_edit(doc, rng, step)
        if step % 100 == 0:
            doc.save()
            doc = TD.load(doc.path)
    res = {r["id"]: r for r in coding.resolve(doc)}
    mid = lambda w: (w["start"] + w["end"]) / 2
    for cid, t0, t1 in made:
        r = res[cid]
        left = any(t0 - 1e-3 <= mid(w) <= t1 + 1e-3 for w in all_words(doc))
        assert r["orphan"] == (not left), (cid, r)
        if not r["orphan"]:
            assert t0 - 1e-3 <= (r["start"] + r["end"]) / 2 <= t1 + 1e-3 or (r["start"] >= t0 - 1e-3 and r["end"] <= t1 + 1e-3)
            assert coding.coded_text(doc, r)
    assert res[silent]["words"] is None and res[silent]["start"] == 100.0  # time codings keep their time


def test_coding_operations_and_merge_across_project(tmp_path):
    study = tmp_path / "Study"
    (study / "Day 1" / "Group 1").mkdir(parents=True)
    (study / "Day 1" / "Group 2").mkdir(parents=True)
    proj.create_project(study)
    d1 = make(study / "Day 1" / "Group 1", n_segs=6, seed=1)
    d2 = make(study / "Day 1" / "Group 2", n_segs=6, seed=2)
    cb = coding.Codebook.for_project(study)
    a, b = cb.add("Play"), cb.add("Games")
    cb.save()
    w1, w2 = all_words(d1), all_words(d2)
    k1 = coding.add_coding(d1, a, w1[0]["start"], w1[2]["end"], w1[0]["wid"], w1[2]["wid"])
    assert coding.add_coding(d1, a, w1[0]["start"], w1[2]["end"], w1[0]["wid"], w1[2]["wid"]) == k1  # no duplicate
    coding.add_coding(d1, b, w1[0]["start"], w1[2]["end"], w1[0]["wid"], w1[2]["wid"])
    coding.add_coding(d2, b, w2[3]["start"], w2[5]["end"], w2[3]["wid"], w2[5]["wid"])
    assert coding.update_coding(d1, k1, memo="first time they mention it") and not coding.update_coding(d1, k1, memo="first time they mention it")
    d1.save()
    d2.save()
    assert coding.counts(study) == {a: 1, b: 2}
    # merge "Games" into "Play": codings move; the now identical pair in d1 becomes one
    n = coding.recode_everywhere(study, {b: a})
    assert n == 2 and coding.counts(study) == {a: 2}
    assert len(coding.codings(TD.load(d1.path))) == 1 and TD.load(d1.path).data["edit_history"][-1]["action"] == "recode"
    # split by recoding a single coding
    d1 = TD.load(d1.path)
    c = coding.codings(d1)[0]
    new = cb.add("Pretend play", parent=a)
    assert coding.update_coding(d1, c["id"], code=new)
    assert coding.remove_coding(d1, c["id"]) and not coding.codings(d1)
