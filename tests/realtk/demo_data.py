"""Invented demo study for the real-Tk test and the user-guide screenshots. No real recordings or participants:
a generated test-pattern video and made-up workshop dialogue with the stand-in names used everywhere in this repo."""
import random
import subprocess
from pathlib import Path

from condense.core import project as proj
from condense.core.transcript import TranscriptDocument as TD

SPEAKERS = ["Facilitator", "Robin", "Sam", "Kim"]
FORMATS = {"srt": True, "vtt": False, "txt": True, "rtf": False, "para_rtf": True, "para_txt": True}

SESSION_1 = [
    ("SPEAKER_00", "Okay everyone, find a seat around the big table."),
    ("SPEAKER_00", "Can you hear me at the back?"),
    ("SPEAKER_01", "Yes we can hear you."),
    ("SPEAKER_00", "Today we are going to make things that float."),
    ("SPEAKER_02", "Can I make a paper boat?"),
    ("SPEAKER_00", "You can make anything you like, as long as you can tell us about it."),
    ("SPEAKER_03", "I want to make a raft with sticks."),
    ("SPEAKER_01", "Mine is going to have a sail and a flag."),
    ("SPEAKER_02", "What colour is the flag?"),
    ("SPEAKER_01", "Green, because green is the colour of the river."),
    ("SPEAKER_00", "Why do you think the river is green?"),
    ("SPEAKER_01", "Because of the plants under the water, I saw them with my dad."),
    ("SPEAKER_03", "My raft is the fastest, look."),
    ("SPEAKER_02", "That's not fair, you used tape."),
    ("SPEAKER_00", "Tape is allowed. Everything on the table is allowed."),
    ("SPEAKER_02", "Then I need more tape for my paper boat."),
    ("SPEAKER_00", "Let's test them in the water tray when you are ready."),
    ("SPEAKER_03", "Mine floats! It floats!"),
    ("SPEAKER_01", "Mine is leaning, the sail is too heavy maybe."),
    ("SPEAKER_00", "What could you change so it stands up straight?"),
    ("SPEAKER_01", "A smaller sail. Or more weight at the bottom."),
    ("SPEAKER_00", "Great idea. Let's write that down on our idea wall."),
]
SESSION_2 = [
    ("SPEAKER_00", "Welcome back to the Riverside Club."),
    ("SPEAKER_00", "Last week we built boats. Who remembers what happened?"),
    ("SPEAKER_02", "Robin's boat fell over!"),
    ("SPEAKER_01", "It didn't fall over, it was leaning."),
    ("SPEAKER_00", "And what did we learn from that?"),
    ("SPEAKER_03", "That the heavy part should be at the bottom."),
    ("SPEAKER_00", "Exactly. Today we draw a map of the river."),
]


def _speechlike_wav(path: Path, seconds: int, seed: int = 5) -> Path:
    """Bursts of tones and pauses: sound with a shape, so clips can be found again in the recording by their audio."""
    import wave
    import numpy as np
    rng = np.random.default_rng(seed)
    rate = 16000
    out = np.zeros(rate * seconds, np.float32)
    t = 0.0
    while t < seconds - 1:
        d = rng.uniform(0.15, 0.6)
        n, i = int(d * rate), int(t * rate)
        tt = np.arange(n) / rate
        out[i:i + n] += (np.sin(2 * np.pi * rng.uniform(120, 300) * tt) * np.hanning(n) * rng.uniform(0.1, 0.5))[: len(out) - i]
        t += d + rng.exponential(0.25)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(out, -1, 1) * 32000).astype("<i2").tobytes())
    return path


def _video(path: Path, seconds: int) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        wav = _speechlike_wav(path.with_suffix(".wav"), seconds)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={seconds}", "-i", str(wav),
                        "-c:v", "libx264", "-preset", "veryfast", "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(path)], check=True, timeout=300)
        wav.unlink()
    return path


def _clip(src: Path, dst: Path, start: float, length: float) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start}", "-i", str(src), "-t", f"{length}",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(dst)], check=True, timeout=120)
    return dst


def _segments(lines, seed, uncertain=0.12):
    rng = random.Random(seed)
    segs, t = [], 1.2
    for spk, text in lines:
        words = []
        for w in text.split():
            d = 0.18 + 0.05 * len(w.strip(",.!?'"))
            p = rng.uniform(0.35, 0.7) if rng.random() < uncertain else rng.uniform(0.82, 0.995)
            words.append({"word": " " + w, "start": round(t, 2), "end": round(t + d, 2), "probability": round(p, 3)})
            t += d + rng.uniform(0.04, 0.14)
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": " " + text, "speaker": spk, "words": words})
        t += rng.uniform(0.6, 2.2)
    return segs


def _doc(folder: Path, name: str, lines, seed, video: Path, names):
    folder.mkdir(parents=True, exist_ok=True)
    segs = _segments(lines, seed)
    for s in segs:
        s["speaker"] = names.get(s["speaker"], s["speaker"])
    doc = TD.create(folder, name, {"segments": segs, "language": "en"}, "large-v3", FORMATS, {}, {"playback": str(video)},
                    {"inputs": [{"path": str(video)}]})
    doc.data["speakers"] = [n for n in SPEAKERS if any(s["speaker"] == n for s in segs)]
    doc.save()
    return doc


def _demo_codes(study: Path, doc):
    """An invented codebook and a few codings, for the Coding tab screenshot."""
    from condense.core import coding as cod
    cb = cod.Codebook.for_project(study)
    making = cb.add("Making", definition="Building or changing something with the materials", color="#fbbf24")
    floating = cb.add("Floating and sinking", parent=making)
    ideas = cb.add("Ideas for improvement", color="#34d399", definition="Suggestions how to make something work better")
    rules = cb.add("Rules and fairness", color="#60a5fa")
    cb.add("Feelings", color="#f472b6")
    cb.save()

    def code(code_id, seg, w0, w1):
        ws = doc.segments[seg]["words"]
        cod.add_coding(doc, code_id, ws[w0]["start"], ws[w1]["end"], ws[w0]["wid"], ws[w1]["wid"])
    doc.ensure_word_ids()
    code(making, 4, 0, len(doc.segments[4]["words"]) - 1)      # "Can I make a paper boat?"
    code(making, 6, 0, len(doc.segments[6]["words"]) - 1)      # "I want to make a raft with sticks."
    code(rules, 13, 0, len(doc.segments[13]["words"]) - 1)     # "That's not fair, you used tape."
    code(rules, 14, 0, len(doc.segments[14]["words"]) - 1)     # "Tape is allowed …"
    code(floating, 17, 0, len(doc.segments[17]["words"]) - 1)  # "Mine floats! It floats!"
    code(ideas, 20, 0, len(doc.segments[20]["words"]) - 1)     # "A smaller sail. Or more weight at the bottom."
    cod.add_coding(doc, making, 95.0, 101.0, memo="Kim tests the raft in the water tray, pushes it across")


def build(work: Path):
    """Demo study laid out like a real one: Day ▸ Group ▸ media folders, consent forms and unsorted material beside."""
    study = Path(work) / "Demo study"
    day1, day2 = study / "Day 1 2026-03-12", study / "Day 2 2026-03-13"
    for d in (study / "Consent forms", day1 / "Leica Pictures Unsorted", day1 / "Group 1" / "Leica pictures",
              day1 / "Group 1" / "iPad Screen Recordings"):
        d.mkdir(parents=True, exist_ok=True)
    video = _video(day1 / "Group 1" / "Session Recordings" / "demo_G1D1.mp4", 120)
    _clip(video, day1 / "Group 1" / "Semi-structured Interviews" / "G1D1_I1.mp4", 70.0, 26.0)
    proj.save_project(study, SPEAKERS, name="Demo study")
    proj.update_project(study, levels=["Day", "Group"], skip=list(proj.DEFAULT_SKIP), episode_types=["Interview", "Phase"],
                        structure={"sessions": "{Day}/{Group}/Session Recordings",
                                   "interviews": "{Day}/{Group}/Semi-structured Interviews/G{group}D{day}_I{n}"})
    unsorted = day2 / "Semi-structured Interviews Unsorted"
    _clip(video, unsorted / "G1D2_I2.mp4", 10.0, 5.0)
    _clip(video, unsorted / "IMG_0042.mp4", 20.0, 3.0)
    named = {"SPEAKER_00": "Facilitator", "SPEAKER_01": "Robin", "SPEAKER_02": "Sam", "SPEAKER_03": "Kim"}
    d1 = _doc(day1 / "Group 1" / "Session Recordings" / "demo_G1D1", "demo_G1D1", SESSION_1, 1, video, named)
    for i in range(0, 6):
        d1.set_checked(i, True)
    _demo_codes(study, d1)
    d1.add_episode("Phase", "Intro & Icebreaker", 0.0, 24.0, approx=True)
    d1.add_episode("Phase", "Dioramas", 24.0, 120.0, approx=True)
    d1.save()
    import shutil
    v2 = day1 / "Group 2" / "Session Recordings" / "demo_G2D1.mp4"
    v3 = day2 / "Group 1" / "Session Recordings" / "demo_G1D2.mp4"
    for v in (v2, v3, day2 / "Group 2" / "Session Recordings" / "final_video.mp4"):
        v.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(video, v)
    d2 = _doc(v2.parent / "demo_G2D1", "demo_G2D1", SESSION_2, 2, v2, {"SPEAKER_00": "Facilitator"})
    d3 = _doc(v3.parent / "demo_G1D2", "demo_G1D2", SESSION_2[:4], 3, v3, {})
    return study, video, [d1, d2, d3]


def long_session(work: Path):
    """About three hours of invented dialogue."""
    lines = SESSION_1 * 110
    folder = Path(work) / "Long"
    video = _video(Path(work) / "short.mp4", 5)
    return _doc(folder, "demo_long_session", lines, 9, video,
                {"SPEAKER_00": "Facilitator", "SPEAKER_01": "Robin", "SPEAKER_02": "Sam"})
