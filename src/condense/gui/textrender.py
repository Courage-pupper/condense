"""Drawing long transcripts into a Tk Text widget quickly.

The text is first built in Python as lines of (chars, tags) runs (Lines), then sent to the widget (show_lines). The
first time everything is inserted with a few large insert calls; after that only the block of lines that differs from
what the widget shows is replaced. A correction in a 5-hour session then touches one or two lines instead of 30 000
words, and the view does not jump.
"""
import tkinter as tk
from typing import List, Optional, Tuple

Run = Tuple[str, Tuple[str, ...]]
INSERT_BATCH = 400  # runs per Tk insert call


class Lines:
    """Text being built: lines of runs, and the position (line from 1, column from 0) where the next chars go."""

    def __init__(self):
        self.lines: List[List[Run]] = [[]]
        self.line = 1
        self.col = 0

    @property
    def empty(self) -> bool:
        return self.line == 1 and self.col == 0

    def _add(self, chunk: str, tags: Tuple[str, ...]):
        cur = self.lines[-1]
        if cur and cur[-1][1] == tags:
            cur[-1] = (cur[-1][0] + chunk, tags)  # same tags as the run before: one run
        else:
            cur.append((chunk, tags))

    def put(self, s: str, *tags: str):
        start = 0
        while True:
            j = s.find("\n", start)
            if j < 0:
                if start < len(s):
                    self._add(s[start:], tags)
                    self.col += len(s) - start
                return
            self._add(s[start:j + 1], tags)  # the newline keeps the tags it was inserted with, as in Tk
            self.lines.append([])
            self.line += 1
            self.col = 0
            start = j + 1


def insert_runs(widget, index: str, runs: List[Run]):
    """Insert runs in order at index with few Tk calls. Every run carries its own tags (possibly none), so nothing is
    inherited from the text around it."""
    widget.mark_set("render_at", index)
    widget.mark_gravity("render_at", "right")  # stays after what is inserted at it
    for k in range(0, len(runs), INSERT_BATCH):
        flat: List = []
        for chars, tags in runs[k:k + INSERT_BATCH]:
            flat += [chars, tags]
        widget.insert("render_at", *flat)


def show_lines(widget, old: Optional[List[List[Run]]], lines: List[List[Run]]) -> List[List[Run]]:
    """Make the widget show `lines`. old: what it shows now (as returned by the previous call), or None to redraw
    everything. Returns lines, to pass as old next time. Leaves the widget's state as it was."""
    state = widget.cget("state")
    widget.configure(state=tk.NORMAL)
    if old is None:
        widget.delete("1.0", tk.END)
        insert_runs(widget, tk.END, [run for line in lines for run in line])
    else:
        n_old, n_new = len(old), len(lines)
        p = 0
        while p < n_old and p < n_new and old[p] == lines[p]:
            p += 1
        q = 0
        while q < n_old - p and q < n_new - p and old[n_old - 1 - q] == lines[n_new - 1 - q]:
            q += 1
        if p < n_old - q or p < n_new - q:
            widget.delete(f"{p + 1}.0", f"{n_old - q + 1}.0")
            insert_runs(widget, f"{p + 1}.0", [run for line in lines[p:n_new - q] for run in line])
    if state:
        widget.configure(state=state)
    return lines
