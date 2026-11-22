"""Permanent word ids — the anchor for coding. Thousands of random edits of every kind (retyping, inserting and deleting
words, splitting, merging, deleting and inserting segments, re-transcribing passages, saving and loading) must never
move an id to another word, and a stretch of words must always be found again at the same place in time."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.core.transcript import TranscriptDocument as TD  # noqa: E402

VOCAB = "we made a boat out of paper and it floats on the water tray today look mine is faster".split()


def make(tmp_path, n_segs=40, seed=1):
    rng = random.Random(seed)
    segs, t = [], 1.0
    for i in range(n_segs):
        words = []
        for _ in range(rng.randint(2, 9)):
            d = rng.uniform(0.2, 0.5)
            words.append({"word": " " + rng.choice(VOCAB), "start": round(t, 3), "end": round(t + d, 3), "probability": 0.9})
            t += d + 0.05
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": "".join(w["word"] for w in words),
                     "speaker": f"S{i % 3}", "words": words})
        t += 0.8
    doc = TD.create(tmp_path, "ids", {"segments": segs}, "small", {"srt": True}, {}, {"playback": None}, {"inputs": []})
    doc.save()
    return doc


def all_words(doc):
    return [w for seg in doc.segments for w in seg.get("words") or []]


def check_invariants(doc, known):
    ws = all_words(doc)
    wids = [w["wid"] for w in ws]
    assert len(wids) == len(set(wids)), "duplicate word id"
    for w in ws:
        if w["wid"] in known:  # an id never moves to another word, and its timing stays
            assert known[w["wid"]] == (w["word"].strip(), w["start"], w["end"]), (w, known[w["wid"]])
        known[w["wid"]] = (w["word"].strip(), w["start"], w["end"])


def random_edit(doc, rng, step):
    segs = doc.segments
    if not segs:
        return
    i = rng.randrange(len(segs))
    op = rng.choice(["retype", "retype", "retype", "split", "merge", "delete", "insert", "retranscribe"])
    words = segs[i].get("words") or []
    if op == "retype" and words:
        toks = [w["word"].strip() for w in words]
        kind = rng.choice(["sub", "ins", "del"])
        k = rng.randrange(len(toks))
        if kind == "sub":
            toks[k] = rng.choice(VOCAB) + "x"
        elif kind == "ins":
            toks.insert(k, rng.choice(VOCAB))
        elif len(toks) > 1:
            del toks[k]
        doc.edit_segment_text(i, " ".join(toks))
    elif op == "split" and len(words) > 1:
        doc.split_segment(i, rng.randrange(1, len(words)))
    elif op == "merge" and i < len(segs) - 1:
        doc.merge_with_next(i)
    elif op == "delete" and len(segs) > 5:
        doc.delete_segment(i)
    elif op == "insert":
        t = float(segs[i]["end"]) + 0.01
        doc.insert_segment(t, t + 0.3, "inserted words here")
    elif op == "retranscribe":
        a, b = float(segs[i]["start"]), float(segs[i]["end"])
        new = [{"start": a, "end": b, "text": " new text", "words": [
            {"word": " new", "start": a, "end": (a + b) / 2, "probability": 0.8},
            {"word": " text", "start": (a + b) / 2, "end": b, "probability": 0.8}]}]
        doc.replace_range(a, b, new, {"model": "small", "language": "en", "task": "transcribe", "backend": "test"})
    doc.ensure_word_ids()


def test_ids_survive_random_edits(tmp_path):
    doc = make(tmp_path)
    rng = random.Random(7)
    known = {}
    check_invariants(doc, known)
    # stretches of words as a coding would store them: first and last word id, and their times
    spans = []
    for _ in range(60):
        ws = all_words(doc)
        a = rng.randrange(len(ws) - 3)
        b = a + rng.randrange(1, 4)
        spans.append((ws[a]["wid"], ws[b]["wid"], ws[a]["start"], ws[b]["end"]))
    for step in range(800):
        random_edit(doc, rng, step)
        if step % 250 == 0:  # through the file, as in real use
            doc.save()
            doc = TD.load(doc.path)
        check_invariants(doc, known)
    pos = doc.word_positions()
    ws_by_pos = {(i, k): w for i, seg in enumerate(doc.segments) for k, w in enumerate(seg.get("words") or [])}
    found = 0
    mid = lambda w: (w["start"] + w["end"]) / 2
    for sw, ew, t0, t1 in spans:
        r = doc.resolve_span(sw, ew, t0, t1, pos)
        left = any(t0 - 1e-3 <= mid(w) <= t1 + 1e-3 for w in all_words(doc))
        assert (r is not None) == left, (sw, ew, t0, t1, r)  # found exactly when some word is still there
        if r is None:
            continue
        found += 1
        first, last = ws_by_pos[r[0]], ws_by_pos[r[1]]
        assert t0 - 1e-3 <= mid(first) and mid(last) <= t1 + 1e-3, (sw, ew, t0, t1, first, last)
        assert r[0] <= r[1]
        if sw in pos and ew in pos and pos[sw] <= pos[ew]:
            assert r == (pos[sw], pos[ew])  # both ends survive: exactly them
    assert found > 0


def test_unchanged_words_keep_ids_when_retyping(tmp_path):
    doc = make(tmp_path, n_segs=3, seed=3)
    seg = doc.segments[0]
    before = [(w["wid"], w["word"].strip()) for w in seg["words"]]
    toks = [w for _, w in before]
    toks[1] = "changed"
    doc.edit_segment_text(0, " ".join(toks + ["added"]))
    doc.ensure_word_ids()
    after = [(w["wid"], w["word"].strip()) for w in doc.segments[0]["words"]]
    assert after[0] == before[0] and after[2:len(before)] == before[2:]
    assert after[1][0] not in {wid for wid, _ in before} and after[-1][0] not in {wid for wid, _ in before}


def test_split_merge_and_files(tmp_path):
    doc = make(tmp_path, n_segs=4, seed=4)
    ids = [w["wid"] for w in all_words(doc)]
    doc.split_segment(1, 1)
    doc.merge_with_next(2)
    assert [w["wid"] for w in all_words(doc)] == ids  # moving words between segments keeps them
    doc.save()
    again = TD.load(doc.path)
    assert [w["wid"] for w in all_words(again)] == ids and again.data["next_wid"] == doc.data["next_wid"]


def test_old_files_get_ids_the_same_way_every_time(tmp_path):
    doc = make(tmp_path, n_segs=5, seed=5)
    for w in all_words(doc):
        del w["wid"]
    doc.data.pop("next_wid")
    import json
    doc.path.write_text(json.dumps(doc.data), encoding="utf-8")  # a transcript from before word ids
    a, b = TD.load(doc.path), TD.load(doc.path)
    assert [w["wid"] for w in all_words(a)] == [w["wid"] for w in all_words(b)]
    assert len({w["wid"] for w in all_words(a)}) == len(all_words(a))
