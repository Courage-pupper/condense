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
        for w in self._items:
            w.destroy()
        self._items = []
        self.relayout()

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
