"""Update check and one-click update.

The check asks GitHub's public API for the latest release (one request; nothing about you or your data is sent
apart from what any web request carries, such as your IP address). It runs at most once a day and can be turned
off (Help ▸ Check for Updates Automatically). Updating runs ~/.condense/install.sh --update, which the one-line
installer puts there; it downloads the release and re-runs the platform installer without sudo or Homebrew.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from condense import __version__

REPO = os.environ.get("CONDENSE_REPO", "Courage-pupper/condense")
API = os.environ.get("CONDENSE_API", f"https://api.github.com/repos/{REPO}")
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 24 * 3600


def parse_version(text: str) -> Tuple:
    """'v0.10.0' -> (0, 10, 0, 1, 0); pre-releases sort before the release: '0.11.0rc1' -> (0, 11, 0, 0, 1)."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-]?(a|b|rc)(\d*))?", str(text).strip())
    if not m:
        return (0, 0, 0, 0, 0)
    major, minor, patch = (int(x or 0) for x in m.group(1, 2, 3))
    if m.group(4):
        return (major, minor, patch, 0, {"a": 0, "b": 100, "rc": 200}[m.group(4)] + int(m.group(5) or 0))
    return (major, minor, patch, 1, 0)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def latest_release(timeout: float = 8.0) -> Dict:
    """{'tag', 'version', 'notes', 'url'} of the newest published (non-draft, non-pre-release) release."""
    req = urllib.request.Request(f"{API}/releases/latest", headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"Condense/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise ValueError("no release found")
    return {"tag": tag, "version": tag.lstrip("v"), "notes": str(data.get("body") or "").strip(),
            "url": str(data.get("html_url") or RELEASES_PAGE)}


def updater_path() -> Path:
    home = Path(os.environ.get("CONDENSE_HOME") or (Path.home() / ".condense"))
    return home / "install.sh"


def update_command() -> Optional[List[str]]:
    """The command that updates this installation, or None if Condense was not installed with install.sh
    (e.g. a git checkout for development — then update with git pull and the platform installer)."""
    path = updater_path()
    return ["bash", str(path), "--update"] if path.is_file() else None


def due(settings: Dict, now: Optional[float] = None) -> bool:
    """Is an automatic check due? (enabled, and the last one was more than a day ago)"""
    if not settings.get("update_auto_check", True):
        return False
    last = float(settings.get("update_last_check") or 0)
    return (now or time.time()) - last >= CHECK_INTERVAL


def run_update(on_line: Callable[[str], None]) -> int:
    """Run the updater, passing each output line to on_line. Returns its exit code (127: no updater)."""
    cmd = update_command()
    if cmd is None:
        return 127
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, errors="replace", env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip("\n"))  # drop terminal colour codes
        if line.strip():
            on_line(line)
    return proc.wait()
