"""Runs each end-to-end scenario in its own process with a fake Tk, generated media and fake Whisper/HandBrake/
Keychain tools, so the whole app (worker + GUI logic) is tested without a display, a GPU or real recordings."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
ROOT = TESTS.parent
SCENARIOS = sorted((TESTS / "scenarios").glob("scenario_*.py"))


@pytest.mark.parametrize("script", SCENARIOS, ids=lambda p: p.stem.replace("scenario_", ""))
def test_scenario(script, pristine_media, tmp_path):
    media = tmp_path / "media"
    shutil.copytree(pristine_media, media)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, CONDENSE_TEST_MEDIA=str(media), CONDENSE_FAKES=str(TESTS / "fakes"), CONDENSE_TESTS=str(TESTS),
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(TESTS)]), HOME=str(home))
    res = subprocess.run([sys.executable, str(script)], cwd=str(media), env=env, capture_output=True, text=True, timeout=900)
    out = res.stdout + res.stderr
    assert res.returncode == 0, out[-5000:]
    assert "ALL TESTS PASSED" in out or "✓" in out, out[-5000:]
