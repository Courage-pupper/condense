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
    proj.update_project(study, levels=["Day", "Group"], skip=list(proj.DEFAULT_SKIP), episode_types=["Interview", "Phase"])
    named = {"SPEAKER_00": "Facilitator", "SPEAKER_01": "Robin", "SPEAKER_02": "Sam", "SPEAKER_03": "Kim"}
    d1 = _doc(day1 / "Group 1" / "Session Recordings" / "demo_G1D1", "demo_G1D1", SESSION_1, 1, video, named)
    for i in range(0, 6):
        d1.set_checked(i, True)
    d1.add_episode("Phase", "Intro & Icebreaker", 0.0, 24.0, approx=True)
    d1.add_episode("Phase", "Dioramas", 24.0, 120.0, approx=True)
    d1.save()
    d2 = _doc(day1 / "Group 2" / "Session Recordings" / "demo_G2D1", "demo_G2D1", SESSION_2, 2, video, {"SPEAKER_00": "Facilitator"})
    d3 = _doc(day2 / "Group 1" / "Session Recordings" / "demo_G1D2", "demo_G1D2", SESSION_2[:4], 3, video, {})
    return study, video, [d1, d2, d3]


def long_session(work: Path):
    """About three hours of invented dialogue."""
    lines = SESSION_1 * 110
    folder = Path(work) / "Long"
    video = _video(Path(work) / "short.mp4", 5)
    return _doc(folder, "demo_long_session", lines, 9, video,
                {"SPEAKER_00": "Facilitator", "SPEAKER_01": "Robin", "SPEAKER_02": "Sam"})
