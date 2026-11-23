"""Which key presses count as shortcuts on each platform, and when a saved window position can be restored."""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_SAVED = ("tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "tkinter.font", "tkinter.simpledialog",
          "condense.gui.widgets", "stub_tk2")


@pytest.fixture(autouse=True)
def _tk_and_platform():
    """Reloads widgets for a pretended platform; uses the fake tkinter where Tk is not installed, and puts every
    module back afterwards so other tests see the real ones."""
    before = {n: sys.modules.get(n) for n in _SAVED}
    platform = sys.platform
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        try:
            import stub_tk2  # noqa: F401
        finally:
            sys.path.pop(0)
    yield
    sys.platform = platform
    for n, mod in before.items():
        if mod is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = mod


def _widgets(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    sys.modules.pop("condense.gui.widgets", None)
    return importlib.import_module("condense.gui.widgets")


def ev(state):
    return SimpleNamespace(state=state)


def test_mac_arrows_with_fn_and_keypad_bits_are_plain_keys(monkeypatch):
    w = _widgets(monkeypatch, "darwin")
    assert not w.is_shortcut(ev(0x40 | 0x20))  # ↑/↓ as a Mac sends them
    assert not w.is_shortcut(ev(0x1 | 0x40))  # ⇧↓ grows the selection
    assert not w.is_shortcut(ev(0x2))  # Caps Lock
    for bit in (0x4, 0x8, 0x10):  # ⌃ ⌘ ⌥
        assert w.is_shortcut(ev(bit | 0x40))


def test_windows_num_lock_is_not_a_modifier(monkeypatch):
    w = _widgets(monkeypatch, "win32")
    assert not w.is_shortcut(ev(0x8))  # Num Lock on
    assert w.is_shortcut(ev(0x4 | 0x8))  # Ctrl
    assert w.is_shortcut(ev(0x20000))  # Alt


def test_linux(monkeypatch):
    w = _widgets(monkeypatch, "linux")
    assert not w.is_shortcut(ev(0x10))  # Num Lock (Mod2)
    assert w.is_shortcut(ev(0x8))  # Alt
    assert w.is_shortcut(ev(0x4))
    assert not w.is_shortcut(SimpleNamespace())  # synthetic events without a state


def test_saved_window_on_a_large_external_monitor_is_restored():
    from condense.gui.widgets import geometry_fits
    laptop = (0, 0, 2056, 1329)
    monitor = (2056, -200, 2560, 1440)
    # bigger than the laptop screen, on the monitor to its right
    assert geometry_fits(2500, 1400, 2080, -180, [laptop, monitor])
    # the same window once the monitor is unplugged: its title bar would be off every screen
    assert not geometry_fits(2500, 1400, 2080, -180, [laptop])
    assert geometry_fits(1360, 860, 100, 60, [laptop])
    assert not geometry_fits(800, 860, 100, 60, [laptop])  # smaller than the minimum
    assert not geometry_fits(6000, 860, 0, 60, [laptop, monitor])  # wider than both screens together
