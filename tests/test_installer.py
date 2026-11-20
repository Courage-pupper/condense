"""install.sh (the one-line installer / updater) against a fake GitHub served from local files."""
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_PLATFORM_INSTALLER = """#!/usr/bin/env bash
# stand-in for scripts/install-{os}.sh: records how it was called
echo "{os} $*" >> "$INSTALLER_LOG"
echo "platform installer ran"
"""


def make_release(root: Path, tag: str) -> None:
    """A release tarball like GitHub's (top folder condense-<version>/) and its API entry."""
    top = root / "build" / f"condense-{tag.lstrip('v')}"
    (top / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "install.sh", top / "install.sh")
    for os_name in ("mac", "linux"):
        p = top / "scripts" / f"install-{os_name}.sh"
        p.write_text(FAKE_PLATFORM_INSTALLER.replace("{os}", os_name))
        p.chmod(0o755)
    (root / "dl" / "tags").mkdir(parents=True, exist_ok=True)
    with tarfile.open(root / "dl" / "tags" / f"{tag}.tar.gz", "w:gz") as tf:
        tf.add(top, arcname=top.name)
    (root / "api" / "releases" / "tags").mkdir(parents=True, exist_ok=True)
    meta = json.dumps({"tag_name": tag, "body": f"Notes for {tag}", "html_url": "https://example.invalid"}, indent=2)
    (root / "api" / "releases" / "tags" / tag).write_text(meta)
    (root / "api" / "releases" / "latest").write_text(meta)  # the newest one made last


@pytest.fixture
def fake_github(tmp_path):
    for tag in ("v0.9.9", "v0.10.0"):
        make_release(tmp_path, tag)
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "brew").write_text("#!/bin/sh\necho 'export HOMEBREW_PREFIX=/opt/homebrew'\n")
    (fakebin / "brew").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONDENSE_")}
    env.update(HOME=str(home), CONDENSE_API=f"file://{tmp_path}/api", CONDENSE_DOWNLOAD=f"file://{tmp_path}/dl",
               INSTALLER_LOG=str(tmp_path / "installer.log"), PATH=f"{fakebin}{os.pathsep}{env['PATH']}")
    return tmp_path, home, env


def run(env, *args, os_name="Linux", piped=False):
    env = dict(env, CONDENSE_OS=os_name)
    if piped:  # exactly like `curl -fsSL …/install.sh | bash -s -- args`
        cmd = ["bash", "-s", "--", *args]
        res = subprocess.run(cmd, input=(ROOT / "install.sh").read_text(), env=env, capture_output=True, text=True, timeout=60)
    else:
        res = subprocess.run(["bash", str(ROOT / "install.sh"), *args], env=env, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def calls(root):
    return (root / "installer.log").read_text().splitlines()


def test_install_then_update_linux(fake_github):
    root, home, env = fake_github
    out = run(env, "--gpu", "rocm", "--venv", "/opt/env", piped=True)
    cdir = home / ".condense"
    assert "Condense v0.10.0 is installed" in out
    assert calls(root) == ["linux --gpu rocm --venv /opt/env"]
    assert (cdir / "installed-version").read_text().strip() == "v0.10.0"
    assert (cdir / "install-options").read_text().split() == ["--gpu", "rocm", "--venv", "/opt/env"]
    assert os.access(cdir / "install.sh", os.X_OK) and (cdir / "releases" / "v0.10.0" / "scripts" / "install-linux.sh").exists()
    # update: the remembered options, and no sudo (apt) step
    out = run(env, "--update")
    assert calls(root)[-1] == "linux --gpu rocm --venv /opt/env --skip-system"
    assert "Restart Condense" in out
    # the copied updater works on its own too (what the app runs)
    subprocess.run(["bash", str(cdir / "install.sh"), "--update"], env=dict(env, CONDENSE_OS="Linux"), check=True,
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    assert calls(root)[-1] == "linux --gpu rocm --venv /opt/env --skip-system"


def test_install_mac_and_versions(fake_github):
    root, home, env = fake_github
    run(env, "--version", "v0.9.9", os_name="Darwin")
    assert calls(root) == ["mac "]
    assert (home / ".condense" / "installed-version").read_text().strip() == "v0.9.9"
    assert "installed: v0.9.9" in run(env, "--check", os_name="Darwin") and "latest:    v0.10.0" in run(env, "--check", os_name="Darwin")
    run(env, "--update", os_name="Darwin")
    assert calls(root)[-1] == "mac --skip-brew"  # Homebrew is not touched by updates
    assert sorted(p.name for p in (home / ".condense" / "releases").iterdir()) == ["v0.10.0", "v0.9.9"]


def test_update_without_installation_fails(fake_github):
    root, home, env = fake_github
    res = subprocess.run(["bash", str(ROOT / "install.sh"), "--update"], env=dict(env, CONDENSE_OS="Linux"),
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert res.returncode != 0 and "No Condense installation found" in res.stderr
    assert not (root / "installer.log").exists()


def test_missing_release_fails_cleanly(fake_github):
    root, home, env = fake_github
    res = subprocess.run(["bash", str(ROOT / "install.sh"), "--version", "v9.9.9"], env=dict(env, CONDENSE_OS="Linux"),
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert res.returncode != 0 and "Release v9.9.9 not found" in res.stderr


def test_no_release_yet(fake_github):
    root, home, env = fake_github
    (root / "api" / "releases" / "latest").unlink()
    (root / "api" / "index").write_text("{}")
    env = dict(env, CONDENSE_API=f"file://{root}/api/index", CONDENSE_OS="Linux")
    res = subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert res.returncode != 0 and "No Condense release has been published yet" in res.stderr, res.stderr
