"""Naming recordings by a pattern: the plan (tokens, dates from folders or the video, parts of a merged video, clashes,
names written by hand), renaming everything that belongs to a recording, links in all transcripts, and undo."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.core import project as proj  # noqa: E402
from condense.core import renaming as rn  # noqa: E402
from condense.core.transcript import TranscriptDocument as TD  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


def video(path: Path, recorded: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    if FFMPEG:
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x36:d=1", "-c:v", "libx264",
               "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if recorded:
            cmd += ["-metadata", f"creation_time={recorded}"]
        subprocess.run(cmd + [str(path)], check=True)
    else:
        path.write_bytes(b"\0" * 64)
    return path


def transcript(results: Path, base: str, media: dict, provenance: dict = None) -> TD:
    results.mkdir(parents=True, exist_ok=True)
    seg = {"start": 0.0, "end": 1.0, "text": " hello", "speaker": "A",
           "words": [{"word": " hello", "start": 0.0, "end": 0.5, "probability": 0.9}]}
    d = TD.create(results, base, {"segments": [seg]}, "small", {}, {}, media, provenance or {"inputs": []})
    d.save()
    return d


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    proj.create_project(root)
    proj.update_project(root, levels=["Day", "Group"], structure={
        "sessions": "{Day}/{Group}/Session Recordings",
        "interviews": "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}"})
    # Group 4, Day 1: "final video" with its results folder, a compressed copy, subtitles and a backup
    s41 = root / "Day 1 2026-08-10" / "Group 4" / "Session Recordings"
    v = video(s41 / "final video.mp4")
    res = s41 / "final video"
    video(res / "final video_compressed.mp4")
    d = transcript(res, "final video", {"video": str(res / "final video_compressed.mp4"), "playback": str(v)},
                   {"inputs": [{"path": str(v)}]})
    (res / "final video.srt").write_text("1\n", encoding="utf-8")
    (res / "_backups").mkdir()
    shutil.copy(d.path, res / "_backups" / "final video.20260810-120000.transcript.json")
    (s41 / "final video_MAXQDA.mp4").write_bytes(b"x")
    # Group 5, Day 1: a video merged from two parts (merged in the order B, A)
    s51 = root / "Day 1 2026-08-10" / "Group 5" / "Session Recordings"
    pa, pb = video(s51 / "cam A.mp4"), video(s51 / "cam B.mp4")
    m = video(s51 / "merged.mp4")
    transcript(s51 / "merged", "merged", {"video": str(m)},
               {"inputs": [{"path": str(pb)}, {"path": str(pa)}],
                "processing": {"merge": {"inputs_in_order": [str(pb), str(pa)]}}})
    # Day 2 (no date in the folder): a recording without a transcript, its date in the video
    s42 = root / "Day 2" / "Group 4" / "Session Recordings"
    video(s42 / "GX010042.mp4", recorded="2026-08-11T09:30:00Z")
    # an interview clip, whose transcript says where it came from
    i41 = root / "Day 1 2026-08-10" / "Group 4" / "Semi-structured Interviews"
    c = video(i41 / "G4D1_I2.mp4")
    transcript(i41 / "G4D1_I2", "G4D1_I2", {"video": str(c)}, {"inputs": [], "from_session": str(d.path)})
    return root


def by_old(rows):
    return {r["old"]: r for r in rows}


def test_plan(study):
    rows = by_old(rn.plan(study))
    assert rows["final video"]["new"] == "G4_Day1_2026.08.10_Session" and rows["final video"]["ok"]
    assert rows["final video"]["date_from"] == "folders"
    assert rows["merged"]["new"] == "G5_Day1_2026.08.10_Session"                    # the merged video: no part
    assert rows["cam B"]["new"] == "G5_Day1_2026.08.10_Session_part1"               # merged first …
    assert rows["cam A"]["new"] == "G5_Day1_2026.08.10_Session_part2"               # … then this one
    if FFMPEG:
        assert rows["GX010042"]["new"] == "G4_Day2_2026.08.11_Session" and rows["GX010042"]["date_from"] == "video"
    assert not rows["G4D1_I2"]["ok"] and rows["G4D1_I2"]["note"] == "already named so"  # the project's clip pattern
    rows2 = by_old(rn.plan(study, {"interview": "G{group}_Day{day}_{date}_I{n}"}))
    assert rows2["G4D1_I2"]["new"] == "G4_Day1_2026.08.10_I2" and rows2["G4D1_I2"]["ok"]
    no_date = by_old(rn.plan(study, probe_dates=False))["GX010042"]
    assert not no_date["ok"] and "{date}" in no_date["note"]                          # no date: write it yourself
    rn.set_name(no_date, "G4_Day2_2026.08.11_Session")
    assert no_date["ok"]
    clash = by_old(rn.plan(study, {"session": "G{group}_Session"}))
    assert sum(1 for r in clash.values() if r["role"] == "session" and not r["ok"] and "same name" in r["note"]) >= 1
    assert rn.clean_name('a/b: "c"  d__e_') == "a-b- -c- d_e"


def test_apply_and_undo(study):
    before = {p: p.read_text(encoding="utf-8") for p in proj.transcripts_under(study)}
    rows = rn.plan(study, {"interview": "G{group}_Day{day}_{date}_I{n}"})
    res = rn.apply(study, rows)
    assert res["renamed"] >= 5 and res["relinked"] >= 3
    s41 = study / "Day 1 2026-08-10" / "Group 4" / "Session Recordings"
    new = "G4_Day1_2026.08.10_Session"
    results = s41 / new
    assert (s41 / f"{new}.mp4").exists() and not (s41 / "final video.mp4").exists()
    assert (s41 / f"{new}_MAXQDA.mp4").exists()                                     # named after it: along
    assert (results / f"{new}.transcript.json").exists() and (results / f"{new}_compressed.mp4").exists()
    assert (results / f"{new}.srt").exists() and not (s41 / "final video").exists()
    d = TD.load(results / f"{new}.transcript.json")
    assert d.base_name == new and d.list_backups() and d.list_backups()[0].name.startswith(new + ".")
    assert d.media_path("video") == results / f"{new}_compressed.mp4" and d.media_path("video").exists()
    assert d.media_path("playback").exists() and d.find_video().exists()
    assert Path(d.data["provenance"]["inputs"][0]["path"]).name == f"{new}.mp4"
    merged = TD.load(next((study / "Day 1 2026-08-10" / "Group 5" / "Session Recordings").glob("*/*.transcript.json")))
    order = merged.data["provenance"]["processing"]["merge"]["inputs_in_order"]
    assert [Path(p).name for p in order] == ["G5_Day1_2026.08.10_Session_part1.mp4", "G5_Day1_2026.08.10_Session_part2.mp4"]
    assert all(Path(p).exists() for p in order)
    clip = next((study / "Day 1 2026-08-10" / "Group 4" / "Semi-structured Interviews").glob("*/*.transcript.json"))
    assert clip.name == "G4_Day1_2026.08.10_I2.transcript.json"
    assert TD.load(clip).data["provenance"]["from_session"] == str(results / f"{new}.transcript.json")  # its link too
    assert rn.last_was_rename(study)
    n = len(rn.undo(study))
    assert n == res["files"] and not rn.last_was_rename(study)
    after = {p: p.read_text(encoding="utf-8") for p in proj.transcripts_under(study)}
    assert set(after) == set(before)
    for p in before:  # the same paths inside (the file may be written in another layout)
        a, b = json.loads(before[p]), json.loads(after[p])
        assert a["media"] == b["media"] and a["provenance"] == b["provenance"] and a["base_name"] == b["base_name"], p
    assert (s41 / "final video.mp4").exists() and (s41 / "final video" / "_backups").is_dir()


def test_case_only_rename(study, tmp_path):
    rows = by_old(rn.plan(study, {"session": "{name}"}))
    r = rows["final video"]
    rn.set_name(r, "Final Video")
    assert r["ok"], r["note"]
    out = rn.apply(study, [r])
    s41 = study / "Day 1 2026-08-10" / "Group 4" / "Session Recordings"
    assert out["files"] >= 3 and any(p.name == "Final Video.mp4" for p in s41.iterdir())


def test_new_clips_follow_the_new_pattern(study):
    pattern = "G{group}_Day{day}_{date}_I{n}"
    rn.apply(study, rn.plan(study, {"interview": pattern}, roles=("interview",)))
    assert rn.use_for_new_clips(study, pattern)
    st = proj.project_settings(study)["structure"]
    assert st.name_alts == [pattern, "G{group}D{day}_I{n}"]                          # the old names stay recognised
    assert st.parse_clip_name("G4_Day1_2026.08.10_I2")["n"] == 2 and st.parse_clip_name("G4D1_I7")["n"] == 7
    clip = study / "Day 1 2026-08-10" / "Group 4" / "Semi-structured Interviews" / "x.mp4"
    assert st.next_clip_name(study, clip) == "G4_Day1_2026.08.10_I3"                # numbered after the renamed I2
