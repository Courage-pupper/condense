"""install.ps1 (Windows one-line installer / updater) against a fake GitHub served from local files.
Runs with Windows PowerShell 5.1 — what every Windows PC has."""
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")
ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''
param([string]$Source = "", [string]$Gpu = "auto", [string]$Venv = "", [string]$TorchIndex = "", [string[]]$AmdWheels = @(),
      [switch]$ReinstallTorch, [switch]$WithPyannote, [switch]$SkipSystem, [switch]$NoApp)
$rec = [ordered]@{ Source = $Source; Gpu = $Gpu; Venv = $Venv; WithPyannote = [bool]$WithPyannote; SkipSystem = [bool]$SkipSystem }
Add-Content -Path $env:INSTALLER_LOG -Value ($rec | ConvertTo-Json -Compress)
Write-Host "fake windows installer ran"
'''


def uri(p: Path) -> str:
    return "file:///" + p.as_posix().lstrip("/")


def make_release(root: Path, tag: str) -> None:
    top = root / "build" / f"condense-{tag.lstrip('v')}"
    (top / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "install.ps1", top / "install.ps1")
    (top / "scripts" / "install-windows.ps1").write_text(FAKE)
    (top / "pyproject.toml").write_text("[project]\nname='condense'\n")
    (root / "dl" / "tags").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(root / "dl" / "tags" / f"{tag}.zip", "w") as z:
        for f in top.rglob("*"):
            z.write(f, f"{top.name}/{f.relative_to(top).as_posix()}")
    (root / "api" / "releases" / "tags").mkdir(parents=True, exist_ok=True)
    meta = json.dumps({"tag_name": tag, "body": "notes"})
    (root / "api" / "releases" / "tags" / tag).write_text(meta)
    (root / "api" / "releases" / "latest").write_text(meta)


@pytest.fixture
def gh(tmp_path):
    for tag in ("v0.9.9", "v0.11.0"):
        make_release(tmp_path, tag)
    (tmp_path / "api" / "index").write_text("{}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONDENSE_")}
    env.update(CONDENSE_API=uri(tmp_path / "api"), CONDENSE_DOWNLOAD=uri(tmp_path / "dl"),
               CONDENSE_HOME=str(tmp_path / "home" / ".condense"), INSTALLER_LOG=str(tmp_path / "log.jsonl"))
    return tmp_path, env


def ps_file(env, *args):
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1"), *args],
                         env=env, capture_output=True, text=True, timeout=180)
    return res


def calls(root):
    return [json.loads(line) for line in (root / "log.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def test_install_and_update(gh):
    root, env = gh
    res = ps_file(env, "-Gpu", "amd", "-Venv", r"C:\envs\rocm", "-WithPyannote")
    assert res.returncode == 0, res.stdout + res.stderr
    first = calls(root)[-1]
    assert first["Gpu"] == "amd" and first["Venv"] == r"C:\envs\rocm" and first["WithPyannote"] and not first["SkipSystem"]
    home = Path(env["CONDENSE_HOME"])
    assert (home / "installed-version").read_text().strip() == "v0.11.0" and (home / "install.ps1").exists()
    assert Path(first["Source"]) == home / "releases" / "v0.11.0"
    res = ps_file(env, "-Update")
    assert res.returncode == 0, res.stdout + res.stderr
    upd = calls(root)[-1]
    assert upd["Gpu"] == "amd" and upd["Venv"] == r"C:\envs\rocm" and upd["WithPyannote"] and upd["SkipSystem"]
    assert "Restart Condense" in res.stdout


def test_one_liner_style_and_errors(gh):
    root, env = gh
    # exactly what `irm …/install.ps1 | iex` does: run the text, not a file
    cmd = f"Get-Content -Raw '{ROOT / 'install.ps1'}' | Invoke-Expression"
    res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], env=env, capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stdout + res.stderr
    assert calls(root)[-1]["Gpu"] == "auto"
    res = ps_file(dict(env, CONDENSE_HOME=str(root / "nohome")), "-Update")
    assert res.returncode == 1 and "No Condense installation found" in res.stdout
    res = ps_file(dict(env, CONDENSE_API=uri(root / "api" / "index")))
    assert res.returncode == 1 and "No Condense release has been published yet" in res.stdout
