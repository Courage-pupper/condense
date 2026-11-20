"""Checks that need a real Windows (GitHub's windows-latest runner)."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_credential_manager_roundtrip():
    from condense.platform.windows import CredentialManagerSecret as S
    S._backend = None
    S.TARGET = f"Condense/test-{uuid.uuid4().hex[:8]}"   # never touch a real saved token
    try:
        assert S.available()
        assert S.save("hf_TestToken123") and S.load() == "hf_TestToken123"
    finally:
        S.delete()
    assert S.load() == ""


def test_no_console_and_utf8():
    from condense.platform.windows import CREATE_NO_WINDOW, hide_console_windows
    hide_console_windows()
    assert getattr(subprocess.Popen, "_condense_no_window", False)
    out = subprocess.run([sys.executable, "-c", "print('caf\\u00e9 \\u2713')"], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONUTF8="1")).stdout
    assert out.strip() == "caf\u00e9 \u2713"
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert CREATE_NO_WINDOW == 0x08000000


def test_runner_goes_through_a_file(tmp_path):
    from condense.asr.runner import WHISPER_RUNNER, runner_command
    cmd = runner_command(sys.executable, tmp_path)
    assert len(cmd) == 2 and Path(cmd[1]).read_text(encoding="utf-8") == WHISPER_RUNNER
    compile(WHISPER_RUNNER, cmd[1], "exec")


def test_cli_and_paths(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), APPDATA=str(tmp_path / "Roaming"), LOCALAPPDATA=str(tmp_path / "Local"))
    out = subprocess.run([sys.executable, "-m", "condense", "--version"], capture_output=True, text=True, env=env, check=True).stdout
    assert out.startswith("Condense ")
    code = "import condense.platform as p; print(p.PLATFORM); print(p.APP_SUPPORT_DIR); print(p.SecretStore.LABEL)"
    lines = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout.splitlines()
    assert lines[0] == "windows" and lines[1] == str(tmp_path / "Roaming" / "Condense") and lines[2] == "Windows Credential Manager"


def test_icon_and_gpu_names():
    from condense.platform.windows import build_ico, gpu_names
    assert build_ico()[:4] == b"\0\0\1\0"
    assert isinstance(gpu_names(), list)
