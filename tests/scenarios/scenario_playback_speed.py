import os
import importlib.util, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"]); import stub_tk2
HOME = Path(tempfile.mkdtemp()); (HOME / "Desktop").mkdir(); os.environ["HOME"] = str(HOME)
from _compat import pipe
media = Path(os.environ["CONDENSE_TEST_MEDIA"])
res = {"segments": [{"start": 0.0, "end": 3.0, "text": " Hallo Welt.", "speaker": "SPEAKER_01",
                     "words": [{"word": " Hallo", "start": 0.0, "end": 1.0, "probability": 0.97}, {"word": " Welt.", "start": 1.0, "end": 3.0, "probability": 0.3}]}]}
d = pipe.TranscriptDocument.create(Path(tempfile.mkdtemp()), "P02", res, "m", {}, {}, {"playback": str(media / "P02.mp4"), "video": None}, {"inputs": []}); d.save()
holder = {}; stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]; app._render_doc(pipe.TranscriptDocument.load(d.path))
launched = []
class P:
    def __init__(self, cmd, **k): self.cmd = cmd; self.alive = True; launched.append(cmd)
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False
pipe.subprocess.Popen = P
clock = [10.0]; pipe.time.monotonic = lambda: clock[0]
seq = []
for _ in range(8):
    app._speed_step(+1); seq.append(app.var_speed.get())
print("faster:", seq)
assert seq[:3] == ["1.1×", "1.25×", "1.4×"] and seq[-1] == "2×"
app._toggle_play()
assert "atempo=2.0" in " ".join(launched[-1])
clock[0] += 0.25 + 3.0
assert abs(app._current_pos() - 6.0) < 1e-6
print("time label:", app.lbl_time.cget("text"))
app._speed_step(-1)
assert app.var_speed.get() == "1.75×" and "atempo=1.75" in " ".join(launched[-1]) and launched[-1][launched[-1].index("-ss") + 1] == "6.00"
app._player_pause()
for _ in range(20): app._speed_step(-1)
assert app.var_speed.get() == "0.5×"
print("✓ steps 0.5×–2×, playback restarts at the right position with the new tempo")
assert [app._parse_speed(t) for t in ("1.3", "1.3×", "1,3x", "130%", " 2 ", "9", "0.1", "abc", "-1")] == [1.3, 1.3, 1.3, 1.3, 2.0, 3.0, 0.5, None, None]
app._toggle_play()
n = len(launched)
app.var_speed.set("1.3"); app._speed_typed()
assert app.var_speed.get() == "1.3×" and "atempo=1.3" in " ".join(launched[-1]) and len(launched) > n
app.var_speed.set("fast please"); app._speed_typed()
assert app.var_speed.get() == "1.3×" and "type a number" in app.lbl_status.cget("text")
n = len(launched)
for v in (1.31, 1.42, 1.58, 1.66):  # dragging the slider: the value follows, playback is not restarted for each move
    app._on_speed_slide(v)
assert app.var_speed.get() == "1.65×" and len(launched) == n
app._speed_apply_now()  # the slider rests / is released
assert "atempo=1.65" in " ".join(launched[-1]) and len(launched) == n + 1
app._speed_apply_now()
assert len(launched) == n + 1  # same speed: not restarted again
app._speed_step(-1)
assert app.var_speed.get() == "1.5×"
app._player_pause()
app.var_speed.set("1.3×")
app._save_settings()
holder.clear(); pipe.subprocess.Popen = subprocess.Popen; pipe.launch_gui(); assert holder["app"].var_speed.get() == "1.3×"
print("✓ typed speeds (1.3, 130 %), nonsense refused, slider restarts playback once when it rests, a typed speed is remembered")
