import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


@pytest.fixture(scope="session")
def pristine_media(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    d = tmp_path_factory.mktemp("media_pristine")
    subprocess.run([sys.executable, str(TESTS / "make_media.py"), str(d)], check=True)
    return d
