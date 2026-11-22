"""The app on a real Tk (the other GUI tests use a stand-in). Needs tkinter and a display: CI runs it under Xvfb on
Linux and on the desktop session of the macOS and Windows runners."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
ROOT = TESTS.parent


def _has_tk() -> bool:
    try:
        import tkinter  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _has_tk(), reason="tkinter is not installed")
@pytest.mark.skipif(sys.platform.startswith("linux") and not os.environ.get("DISPLAY"), reason="no display (use xvfb-run)")
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_real_tk(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_") and k not in ("CONDENSE_PLATFORM", "CONDENSE_WHISPER_PYTHON")}
    env.update(HOME=str(home), USERPROFILE=str(home), CONDENSE_WORK=str(tmp_path / "work"), PYTHONUTF8="1",
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(TESTS / "realtk")]))
    for var in ("APPDATA", "LOCALAPPDATA"):
        env.pop(var, None)
    if sys.platform == "win32":
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    res = subprocess.run([sys.executable, str(TESTS / "realtk" / "smoke.py")], cwd=str(tmp_path), env=env, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=900)
    out = res.stdout + res.stderr
    print(out[-3000:])
    assert res.returncode == 0 and "REAL TK PASSED" in out, out[-6000:]
