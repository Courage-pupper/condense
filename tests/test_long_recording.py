"""A 3-hour session: every core path (load/save, cuts, paragraphs, exports, re-timing, project overview) on a transcript
of realistic size, and a real 3-hour media file through both trim modes. Checks correctness past the 1-hour mark and that
nothing is accidentally quadratic."""
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.core import cuts as cutlib  # noqa: E402
from condense.core import project as proj  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402

HOURS = 3
LENGTH = HOURS * 3600.0
WORDS = "we made a big boat out of paper and then it sailed across the table until it fell off".split()


def long_result(seed=1):
    rng = random.Random(seed)
    segs, t = [], 2.0
    speakers = ["Facilitator", "Robin", "Sam", "Kim", "SPEAKER_04"]
    while t < LENGTH - 10:
        n = rng.randint(3, 18)
        words, wt = [], t
        for _ in range(n):
            w = rng.choice(WORDS)
            d = rng.uniform(0.2, 0.5)
            words.append({"word": " " + w, "start": round(wt, 3), "end": round(wt + d, 3), "probability": rng.uniform(0.3, 1.0)})
            wt += d + rng.uniform(0.02, 0.15)
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": "".join(w["word"] for w in words),
                     "speaker": rng.choice(speakers), "words": words})
        t = wt + rng.uniform(0.3, 2.5)
    return {"segments": segs, "language": "en"}


def timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return out, time.perf_counter() - t0


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    d = tmp_path_factory.mktemp("long")
    formats = {k: True for k in ("srt", "vtt", "txt", "rtf", "para_rtf", "para_txt")}
    doc = TD.create(d, "demo_long", long_result(), "small", formats, {}, {"playback": None}, {"inputs": []})
    doc.save()
    return doc


def test_size_and_roundtrip(doc):
    assert len(doc.segments) > 1500
    loaded, secs = timed(TD.load, doc.path)
    assert len(loaded.segments) == len(doc.segments) and secs < 10
    loaded.set_checked(5, True)
    _, secs = timed(loaded.save)
    assert secs < 10 and TD.load(doc.path).segments[5].get("checked")


def test_cuts_paragraphs_and_reports(doc):
    rng = random.Random(2)
    d = TD.load(doc.path)
    # 120 cuts spread over the session, including some past 2 h
    for i in rng.sample(range(len(d.segments)), 120):
        s = d.segments[i]
        d.cut_range(s["start"], s["end"])
    assert len(d.cuts) >= 100
    kept, secs = timed(d.kept_segments)
    assert secs < 5 and 0 < len(d.segments) - len(kept) <= 130
    paras, secs = timed(d.paragraphs)
    assert secs < 10 and paras and all(p["start"] < p["end"] for p in paras)
    report, secs = timed(d.confidence_report, all_segments=True)
    assert secs < 10 and len(report["segments"]) == len(d.segments)
    frac, secs = timed(d.checked_fraction)
    assert secs < 5 and 0 <= frac <= 1
    d.save()


def test_exports_past_one_hour(doc, tmp_path):
    d = TD.load(doc.path)
    created, secs = timed(d.export_all)
    assert secs < 30 and set(created) >= {"srt", "vtt", "txt", "rtf", "para_rtf", "para_txt"}
    srt = created["srt"].read_text(encoding="utf-8")
    last = re.findall(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> ", srt)[-1]
    assert int(last[0]) == HOURS - 1 or (int(last[0]) == HOURS and int(last[1]) == 0), last  # hours, not wrapped minutes
    assert "02:" in created["vtt"].read_text(encoding="utf-8")
    para = created["para_txt"].read_text(encoding="utf-8")
    stamps = re.findall(r"\b(\d\d):(\d\d):(\d\d)\b", para)
    secs_list = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in stamps]
    assert secs_list == sorted(secs_list) and secs_list[-1] > 2 * 3600


def test_retime_onto_trimmed(doc, tmp_path):
    d = TD.load(doc.path)
    kept = cutlib.keep_intervals(d.cuts, LENGTH)
    tmap = cutlib.TimeMap(kept)
    retimed, secs = timed(cutlib.retime_segments, d.segments, d.cuts, tmap)
    assert secs < 10
    assert len(retimed) == len(d.kept_segments())
    starts = [s["start"] for s in retimed]
    assert starts == sorted(starts) and retimed[-1]["end"] <= tmap.duration + 0.01
    for s in retimed:  # words stay inside their segment and in order
        ws = s.get("words") or []
        assert all(a["start"] <= b["start"] for a, b in zip(ws, ws[1:]))
    created, secs = timed(d.export_trimmed, kept, "demo_long_trimmed", tmp_path)
    assert secs < 30 and created["srt"].exists()


def test_timemap_many_cuts_is_fast():
    kept = [(i * 10.0, i * 10.0 + 8.0) for i in range(2000)]  # 2000 kept parts
    tmap = cutlib.TimeMap(kept)
    ts = [random.Random(3).uniform(0, 20000) for _ in range(100000)]
    _, secs = timed(lambda: [tmap(t) for t in ts])
    assert secs < 3, secs
    assert tmap(5.0) == 5.0 and tmap(9.0) == 8.0 and tmap(15.0) == 13.0 and tmap(1e9) == tmap.duration


def test_project_overview_of_long_session(doc):
    rows, secs = timed(proj.overview, doc.path.parent)
    assert secs < 15 and rows[0]["length_s"] > (HOURS - 0.1) * 3600


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_trim_three_hour_file(tmp_path):
    from condense.media import trim
    src = tmp_path / "demo_3h.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=gray:size=32x32:rate=2:duration={LENGTH}",
                    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "40", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "16k", "-ac", "1", "-shortest", str(src)], check=True, timeout=600)
    cuts = [(600.0, 900.0), (3700.0, 3760.0), (7300.0, 7400.0), (10000.0, 10100.0)]
    kept = cutlib.keep_intervals(cuts, LENGTH)
    for mode in ("lossless", "exact"):
        dst = tmp_path / f"out_{mode}.mp4"
        res = trim.write_trimmed(src, dst, kept, mode=mode)
        expected = sum(b - a for a, b in res["kept"])
        got = trim.probe(dst)["duration"]
        assert abs(got - expected) < 1.0, (mode, got, expected)
        assert abs(expected - (LENGTH - 560)) < 60, (mode, expected)
        assert not list(tmp_path.glob(".out_*part*")), "temporary pieces left behind"
