"""`python -m condense` / `condense` command."""
import sys

from condense import __version__


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print(f"Condense {__version__}")
        return 0
    if "--install-app" in argv or "--install-desktop" in argv:
        from condense.platform import MacAppInstaller

        info = MacAppInstaller.install_shortcut()
        print(f"App installed: {info['app']}")
        return 0
    try:
        from condense.gui.app import launch_gui
    except ImportError as exc:
        if "tkinter" in str(exc).lower():
            print("Error: Python Tkinter is not installed. On macOS: brew install python-tk "
                  "(and use a Homebrew/python.org Python, not /usr/bin/python3).", file=sys.stderr)
            return 1
        raise
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
