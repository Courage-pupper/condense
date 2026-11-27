"""Taking a clip's checked transcript over into the session, and keeping the clip in step afterwards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.core import cliplink as cl  # noqa: E402
from condense.core import coding as cod  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402


def seg(t, words, speaker, **kw):
    ws = [{"word": " " + w, "start": round(t + i * 0.5, 3), "end": round(t + i * 0.5 + 0.4, 3), "probability": 0.6}
          for i, w in enumerate(words)]
    return dict({"start": ws[0]["start"], "end": ws[-1]["end"], "text": "".join(w["word"] for w in ws), "speaker": speaker,
                 "words": ws}, **kw)


def make(tmp_path):
    # the session: 40 segments of 4 words, every 5 s; Whisper's guesses, unchecked
    s = [seg(2.0 + k * 5, ["session", "words", "number", str(k)], ("Robin", "Sam")[k % 2]) for k in range(40)]
    (tmp_path / "s").mkdir()
    session = TD.create(tmp_path / "s", "session", {"segments": s}, "small", {}, {}, {}, {"inputs": []})
    session.ensure_word_ids()
    k_before = cod.add_coding(session, "c_old", 60.0, 66.0)  # a coding by time in the interview's stretch
    session.save()
    # the clip: cut from 60.0 s to 90.0 s, transcribed and checked on its own (its own word ids w0 …)
    c = [seg(0.3, ["Santiago", "asks", "a", "question"], "Santiago", checked=True),
         seg(5.2, ["the", "answer", "is", "long"], "P03", checked=True, note="unclear word"),
         seg(25.0, ["thanks", "a", "lot", "bye"], "Santiago", checked=True)]
    (tmp_path / "c").mkdir()
    clip = TD.create(tmp_path / "c", "G4D1_I1", {"segments": c}, "small", {}, {}, {}, {"inputs": []})
    clip.ensure_word_ids()
    w = clip.segments[1]["words"]
    kc = cod.add_coding(clip, "c_answer", w[0]["start"], w[-1]["end"], w[0]["wid"], w[-1]["wid"], memo="long answer")
    cod.add_coding(clip, cod.COMMENT, 20.0, 29.0, memo="they laugh")
    clip.save()
    return session, clip, k_before, kc


def test_adopt(tmp_path):
    session, clip, k_before, kc = make(tmp_path)
    before_words = [(w["word"], w["start"]) for s in session.segments for w in s["words"] if w["start"] < 60.0]
    after_words = [(w["word"], w["start"]) for s in session.segments for w in s["words"] if w["start"] >= 90.0]
    ep = session.add_episode("Interview", "G4D1_I1", 60.0, 90.0)
    res = cl.adopt(session, clip, 60.0, 30.0, ep)
    assert res["added"] == 3 and res["codings"] == 2 and res["removed"] > 0
    words = [(w["word"], w["start"]) for s in session.segments for w in s["words"]]
    assert [x for x in words if x[1] < 60.0] == before_words and [x for x in words if x[1] >= 90.0] == after_words
    inside = [s for s in session.segments if 60.0 <= s["start"] < 90.0]
    assert [s["text"] for s in inside] == [s["text"] for s in clip.segments]
    assert inside[0]["start"] == 60.3 and inside[0]["checked"] and inside[1]["speaker"] == "P03" and inside[1]["note"] == "unclear word"
    assert "P03" in session.roster() and all(s.get("from_clip") == ep for s in inside)
    wids = [w["wid"] for s in session.segments for w in s["words"]]
    assert len(wids) == len(set(wids))                                     # word ids stay unique
    res_c = {r["code"]: r for r in cod.resolve(session)}
    assert res_c["c_answer"]["memo"] == "long answer" and res_c["c_answer"]["start"] == inside[1]["words"][0]["start"]
    assert not res_c["c_answer"].get("orphan")                             # on the session's new words
    assert res_c[cod.COMMENT]["start"] == 80.0 and res_c[cod.COMMENT]["memo"] == "they laugh"
    assert res_c["c_old"]["start"] == 60.0                                 # the session's own coding stays (by time)
    # a segment over an edge is cut there: what was said before 60 s stays
    s59 = [s for s in session.segments if s["start"] < 60.0 <= s["end"] + 2]
    assert all(w["start"] < 60.0 for s in s59 for w in s["words"])


def test_push_and_clip_changed(tmp_path):
    session, clip, _k, _kc = make(tmp_path)
    ep_id = session.add_episode("Interview", "G4D1_I1", 60.0, 90.0, source={"clip": "x.mp4", "transcript": str(clip.path),
                                                                              "length": 30.0, "linked": "now"})
    cl.adopt(session, clip, 60.0, 30.0, ep_id)
    ep = next(e for e in session.episodes if e["id"] == ep_id)
    assert cl.push(session, ep) == "written"                              # the clip gets the session's word ids
    c2 = TD.load(clip.path)
    assert [s["text"] for s in c2.segments] == [s["text"] for s in clip.segments]
    sw = [w["wid"] for s in session.segments if 60 <= s["start"] < 90 for w in s["words"]]
    assert [w["wid"] for s in c2.segments for w in s["words"]] == sw
    assert {c["code"] for c in cod.codings(c2)} == {"c_answer", cod.COMMENT, "c_old"}
    assert cl.push(session, ep) == "same"
    moved = dict(ep, start=55.0, end=95.0, source=dict(ep["source"], offset=60.0))  # the episode's edges moved later:
    assert cl.push(session, moved) == "same"                                     # the clip's place doesn't
    # a correction in the session goes into the clip
    i = next(k for k, s in enumerate(session.segments) if s.get("from_clip") == ep_id and "answer" in s["text"])
    session.edit_segment_text(i, " the answer is very long")
    session.set_segment_speaker(i, "Robin")
    assert cl.push(session, ep) == "written"
    c3 = TD.load(clip.path)
    assert c3.segments[1]["text"].strip() == "the answer is very long" and c3.segments[1]["speaker"] == "Robin"
    assert abs(c3.segments[1]["start"] - (session.segments[i]["start"] - 60.0)) < 1e-6
    # the clip changed on its own: not overwritten, unless asked
    c3.segments[0]["text"] = " edited in the clip"
    c3.save()
    session.edit_segment_text(i, " the answer is short")
    assert cl.push(session, ep) == "clip changed"
    assert TD.load(clip.path).segments[0]["text"] == " edited in the clip"
    assert cl.push(session, ep, force=True) == "written"
    assert TD.load(clip.path).segments[1]["text"].strip() == "the answer is short"


def test_clip_transcript_for(tmp_path):
    clip = tmp_path / "G4D1_I2.mp4"
    clip.write_bytes(b"x")
    assert cl.clip_transcript_for(clip) is None
    (tmp_path / "G4D1_I2").mkdir()
    s = [seg(0.0, ["hi"], "A")]
    d = TD.create(tmp_path / "G4D1_I2", "G4D1_I2", {"segments": s}, "small", {}, {}, {}, {"inputs": []})
    d.save()
    assert cl.clip_transcript_for(clip) == d.path
