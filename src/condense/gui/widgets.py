"""Layout widgets: FlowFrame (wrapping toolbar) and ScrollFrame."""
import sys
import tkinter as tk
from tkinter import ttk
from typing import List

IS_MAC = sys.platform == "darwin"
MOD = "Command" if IS_MAC else "Control"
# Shortcuts used while typing in the segment box (speaker numbers): ⌃ on the Mac, where it is free next to ⌘;
# Alt elsewhere, because Ctrl+1–3 switch tabs there.
EDIT_MOD = "Control" if IS_MAC else "Alt"
EDIT_MOD_LABEL = "⌃" if IS_MAC else "Alt+"

# Bits of a key event's state that make a key a shortcut (⌘C, Ctrl+1 …) rather than a plain key. Each platform
# packs different things into those bits:
#   Mac:     Control 0x4, ⌘ (Mod1) 0x8, ⌥ (Mod2) 0x10. Tk also sets Mod3 (keypad) and Mod4 (fn) on the arrow,
#            forward-delete, Home/End and keypad keys, so those two must not count, or ↑/↓ stop working.
#   Windows: Control 0x4, Alt 0x20000. Mod1 (0x8) there is Num Lock.
#   Linux:   Control 0x4, Alt (Mod1) 0x8, Super (Mod4) 0x40. Num Lock is Mod2 (0x10).
if IS_MAC:
    SHORTCUT_MODS = 0x4 | 0x8 | 0x10
elif sys.platform == "win32":
    SHORTCUT_MODS = 0x4 | 0x20000
else:
    SHORTCUT_MODS = 0x4 | 0x8 | 0x40


def is_shortcut(event) -> bool:
    """True when Control, ⌘, ⌥ or Alt is held with the key (Shift, Caps Lock, Num Lock and the Mac's fn do not count)."""
    try:
        return bool(int(getattr(event, "state", 0) or 0) & SHORTCUT_MODS)
    except (TypeError, ValueError):
        return False


def screen_rects(root) -> List[tuple]:
    """(x, y, width, height) of every display, in the coordinates Tk uses for window positions. Tk itself only
    knows the main display, which is why windows used to stop growing at its size on a larger external monitor."""
    rects: List[tuple] = []
    try:
        if IS_MAC:
            import ctypes
            import ctypes.util

            class _Point(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            class _Size(ctypes.Structure):
                _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

            class _Rect(ctypes.Structure):
                _fields_ = [("origin", _Point), ("size", _Size)]

            path = (ctypes.util.find_library("CoreGraphics")
                    or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg = ctypes.cdll.LoadLibrary(path)
            cg.CGGetActiveDisplayList.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                                  ctypes.POINTER(ctypes.c_uint32)]
            cg.CGGetActiveDisplayList.restype = ctypes.c_int32
            cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            cg.CGDisplayBounds.restype = _Rect
            ids, n = (ctypes.c_uint32 * 16)(), ctypes.c_uint32(0)
            if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(n)) == 0:
                for i in range(min(n.value, 16)):
                    r = cg.CGDisplayBounds(ids[i])  # global points, main display at 0,0, y downwards, like Tk
                    rects.append((int(r.origin.x), int(r.origin.y), int(r.size.width), int(r.size.height)))
        elif sys.platform == "win32":
            import ctypes
            m = ctypes.windll.user32.GetSystemMetrics  # the virtual screen spanning every monitor
            x, y, w, h = m(76), m(77), m(78), m(79)
            if w > 0 and h > 0:
                rects.append((x, y, w, h))
        else:
            w, h = root.winfo_vrootwidth(), root.winfo_vrootheight()  # X11: the root window covers every monitor
            if w > 0 and h > 0:
                rects.append((0, 0, w, h))
    except Exception:
        rects = []
    rects = [r for r in rects if r[2] > 0 and r[3] > 0]
    return rects or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def geometry_fits(w: int, h: int, x: int, y: int, rects: List[tuple], min_w: int = 900, min_h: int = 600) -> bool:
    """A saved window can be restored when it is big enough, no bigger than the screens put together, and its title
    bar (the strip you drag it by) lies on a display that is still connected."""
    if not rects or w < min_w or h < min_h:
        return False
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    if w > right - left or h > bottom - top:
        return False
    grab_x, grab_y = x + min(w // 2, 200), y + 10
    return any(rx <= grab_x < rx + rw and ry <= grab_y < ry + rh for rx, ry, rw, rh in rects)


def kbd(text: str) -> str:
    """Shortcut labels are written with Mac symbols; on Linux and Windows they read Ctrl+S, Ctrl+Shift+O, Ctrl+Enter."""
    if IS_MAC:
        return text
    for a, b in (("⌘-click", "Ctrl+click"), ("⇧-click", "Shift+click"), ("⇧⌘", "Ctrl+Shift+"), ("⌘⇧", "Ctrl+Shift+"),
                 ("⌘↩", "Ctrl+Enter"), ("⌘", "Ctrl+"), ("⇧", "Shift+"), ("⌃", "Ctrl+"), ("↩", "Enter")):
        text = text.replace(a, b)
    return text


class FlowFrame(tk.Frame):
    """Lays its children out left-to-right and wraps them onto new rows when space runs out."""

    def __init__(self, master, padx: int = 6, pady: int = 4, **kw):
        super().__init__(master, **kw)
        self._items: List = []
        self._padx, self._pady = padx, pady
        self._last_width = None
        self.bind("<Configure>", self._on_configure)

    def add(self, widget):
        self._items.append(widget)
        self.after_idle(self.relayout)
        return widget

    def clear(self):
        """Remove the children but keep the current height until the new ones are laid out: shrinking to nothing and
        growing back would make everything below jump on each refresh (e.g. the speaker chips on every ↑/↓)."""
        for w in self._items:
            w.destroy()
        self._items = []
        self.after_idle(self.relayout)

    def _on_configure(self, event):
        if event.width != self._last_width:
            self._last_width = event.width
            self.relayout()

    def relayout(self):
        width = self.winfo_width()
        if width <= 1:
            width = 10_000
        x = y = row_h = 0
        for w in self._items:
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x > 0 and x + ww > width:
                x, y, row_h = 0, y + row_h + self._pady, 0
            w.place(in_=self, x=x, y=y)
            x += ww + self._padx
            row_h = max(row_h, wh)
        total_h = max(1, y + row_h)
        if int(self.cget("height")) != total_h:
            self.configure(height=total_h)

class ScrollFrame(tk.Frame):
    """Vertically scrollable container; put content into .inner."""

    def __init__(self, master, bg: str, **kw):
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))

    def contains(self, widget) -> bool:
        path = str(widget)
        return path.startswith(str(self.canvas)) and "popdown" not in path

    def scroll(self, units: int) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")
