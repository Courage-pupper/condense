import os
import json, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(os.environ["HOME"])
API = Path(tempfile.mkdtemp()) / "api"
(API / "releases").mkdir(parents=True)
os.environ["CONDENSE_API"] = f"file://{API}"   # before Condense reads it
from _compat import pipe
from condense import __version__, updates
ok = lambda m: print("  ✓", m)

def publish(tag, notes="- Something new"):
    (API / "releases" / "latest").write_text(json.dumps({"tag_name": tag, "body": notes, "html_url": "https://example.invalid/r"}))

print("[1] versions")
assert updates.is_newer("v0.10.1", "0.10.0") and updates.is_newer("0.11.0", "0.10.9") and not updates.is_newer("v0.10.0", "0.10.0")
assert updates.is_newer("1.0.0", "1.0.0rc2") and not updates.is_newer("1.0.0rc1", "1.0.0") and updates.is_newer("0.10.0", "0.9.12")
assert updates.due({}) and not updates.due({"update_auto_check": False}) and not updates.due({"update_last_check": time.time() - 60})
ok("semantic comparison incl. pre-releases; daily check, can be turned off")

holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
dialogs, infos = [], []
app._show_update_dialog = lambda info: dialogs.append(info)
pipe.messagebox.showinfo = lambda *a, **k: infos.append(a)

def drain(timeout=10):
    deadline = time.time() + timeout
    while app._update_busy and time.time() < deadline:
        app._poll_updates(); time.sleep(0.05)
    app._poll_updates()

print("[2] checking")
publish("v" + __version__)
app._check_updates(manual=True); drain()
assert not dialogs and infos and "latest version" in infos[-1][1] and app.settings.get("update_last_check")
publish("v99.0.0", "### New\n- Faster everything")
app._check_updates(manual=False); drain()
assert dialogs and dialogs[-1]["version"] == "99.0.0" and "Faster everything" in dialogs[-1]["notes"]
assert "Update available: Condense 99.0.0" in app.lbl_status.cget("text")
app._skip_version("99.0.0"); n = len(dialogs)
app._check_updates(manual=False); drain()
assert len(dialogs) == n                      # skipped version: no dialog when checking automatically …
app._check_updates(manual=True); drain()
assert len(dialogs) == n + 1                  # … but a manual check still shows it
(API / "releases" / "latest").unlink()
app._check_updates(manual=False); drain()     # offline / no release: quiet
ok("latest → 'up to date'; newer → dialog with notes; skip-version respected; failures are quiet")

print("[3] the real dialog")
del app.__dict__["_show_update_dialog"]
assert updates.update_command() is None       # not installed with install.sh yet: no Update-now button
win = app._show_update_dialog({"version": "99.0.0", "notes": "x", "url": "u"})
assert win is not None
ok("dialog builds (with the 'not installed with the installer' hint)")

print("[4] one-click update")
upd = HOME / ".condense" / "install.sh"
upd.parent.mkdir(parents=True, exist_ok=True)
upd.write_text('#!/usr/bin/env bash\nprintf "\\033[1m==> Downloading Condense v99.0.0\\033[0m\\n"\necho "args: $*"\nexit 0\n')
assert updates.update_command() == ["bash", str(upd), "--update"]
restarted = []
app._restart_app = lambda: restarted.append(True)
app._start_update(); drain()
log = app.console_text.content
assert "==> Downloading Condense v99.0.0" in log and "\x1b[" not in log and "args: --update" in log and "Update installed." in log
assert restarted == [True]
upd.write_text("#!/usr/bin/env bash\necho 'pip failed'\nexit 3\n")
app._start_update(); drain()
assert "The update failed (exit code 3)" in app.console_text.content and restarted == [True]
ok("update output streamed to the Console (colour codes removed), restart offered; failure reported")
