"""Generate the small synthetic test videos (no real recordings ever go into the repository)."""
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def video(name: str, seconds: int, freq: int, rate: int) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out / name)], check=True)


video("Interview O'Brien.mp4", 4, 300, 44100)   # apostrophe on purpose
video("P02.mp4", 3, 500, 44100)
video("P03_48k.mp4", 3, 500, 48000)              # differs only in audio sample rate
(out / "P04_trimmed.mp4").write_bytes((out / "P02.mp4").read_bytes())
(out / "P04_trimmed.llc").write_text("{\n  version: 1,\n  cutSegments: [\n    { start: 0.5, end: 2.0, name: '' },\n  ],\n}\n")
(out / "Broken.mp4").write_text("not a video")
print("media in", out)
