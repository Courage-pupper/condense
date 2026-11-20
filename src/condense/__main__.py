"""`python -m condense` / `condense` command."""
import sys

from condense import __version__


def _log_to_file_if_windowless() -> None:
    """pythonw (the Windows Start-menu shortcut) has no console: keep the output in the log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from condense.platform import log_file
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or fh
        sys.stderr = sys.stderr or fh
    except Exception:
        pass


def main(argv=None) -> int:
    _log_to_file_if_windowless()
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print(f"Condense {__version__}")
        return 0
    if "--install-app" in argv or "--install-desktop" in argv:
        from condense.platform import AppInstaller, IS_MAC_PLATFORM

        info = AppInstaller.install_shortcut()
        print(f"{'App installed' if IS_MAC_PLATFORM else 'Shortcut installed'}: {info['app']}")
        print(f"Launcher log: {info['log']}")
        return 0
    try:
        from condense.gui.app import launch_gui
    except ImportError as exc:
        if "tkinter" in str(exc).lower():
            if sys.platform == "win32":
                print("Error: Python Tkinter is missing. Install Python from python.org or winget (Python.Python.3.12), "
                      "which includes it, and re-run the installer.", file=sys.stderr)
            elif sys.platform == "darwin":
                print("Error: Python Tkinter is not installed. On macOS: brew install python-tk "
                      "(and use a Homebrew/python.org Python, not /usr/bin/python3).", file=sys.stderr)
            else:
                print("Error: Python Tkinter is not installed. On Ubuntu/Debian: sudo apt install python3-tk "
                      "(Fedora: sudo dnf install python3-tkinter).", file=sys.stderr)
            return 1
        raise
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
