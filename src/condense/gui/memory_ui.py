"""The memory meter in the status bar, and the Memory window: how much Condense and the programs it started use,
what Condense itself holds, and the two ways out — free memory, or restart (everything is saved, the transcript
reopens where you were)."""
import gc
import time
from collections import deque

from condense.gui._common import *  # noqa: F401,F403
from condense.core import memory as memlib


class MemoryMixin:
    MEMORY_EVERY_MS = 10_000

    # ----- the meter -----
    def _build_memory_meter(self, parent):
        T = THEME
        self.lbl_memory = tk.Label(parent, text="Memory …", font=self.f["small"], fg=T["fg_dim"], bg=T["bg_panel"], cursor="hand2",
                                   padx=6)
        self.lbl_memory.pack(side=tk.RIGHT, padx=(0, 8))
        self.lbl_memory.bind("<Button-1>", lambda e: self._memory_dialog())
        self._memory_history = deque(maxlen=720)  # 2 h of samples
        self._memory_last = None
        self._memory_level = "ok"
        self._memory_q = queue.Queue()
        self.after(2000, self._memory_tick)

    def _memory_tick(self):
        """Measure in the background (ps can take a moment), show it on the main thread."""
        def work():
            try:
                self._memory_q.put(memlib.usage())
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()
        self.after(500, self._memory_drain)
        self.after(self.MEMORY_EVERY_MS, self._memory_tick)

    def _memory_drain(self):
        try:
            while True:
                self._memory_show(self._memory_q.get_nowait())
        except queue.Empty:
            pass

    def _memory_show(self, u: Dict):
        T = THEME
        self._memory_last = u
        self._memory_history.append((time.time(), u["total"]))
        lvl = memlib.level(u["total"], u["ram"])
        share = f" ({u['total'] / u['ram']:.0%})" if u["ram"] else ""
        mark = {"ok": "", "high": "  ▲", "very_high": "  ⚠"}[lvl]
        self.lbl_memory.config(text=f"Memory {memlib.fmt(u['total'])}{mark}",
                               fg={"ok": T["fg_dim"], "high": T["warn"], "very_high": T["err"]}[lvl])
        order = ("ok", "high", "very_high")
        if order.index(lvl) > order.index(self._memory_level):  # tell once each time it gets worse, never a popup
            parts = ", ".join(f"{memlib.role(c['name'])} {memlib.fmt(c['rss'])}" for c in u["children"][:4])
            self._append_log(f"Memory: Condense and its programs use {memlib.fmt(u['total'])}{share} of {memlib.fmt(u['ram'])} "
                             f"(Condense {memlib.fmt(u['self'])}{'; ' + parts if parts else ''}).", "WARN")
            if lvl == "very_high":
                self.lbl_status.config(text=f"Condense is using {memlib.fmt(u['total'])}{share} of the computer's memory. "
                                            "Click Memory (bottom right) to free some or restart.")
        self._memory_level = lvl
        return lvl

    def _memory_growth(self, minutes: float = 30.0) -> Optional[float]:
        """How much the total grew in the last minutes (bytes), or None without enough samples."""
        hist = list(self.__dict__.get("_memory_history") or [])
        if len(hist) < 2:
            return None
        now = hist[-1][0]
        old = next((v for t, v in hist if t >= now - minutes * 60), hist[0][1])
        return hist[-1][1] - old

    # ----- what Condense holds -----
    def _undo_memory(self) -> Dict:
        stacks = self.__dict__.get("_undo_stacks") or {}
        open_path = str(self.active_doc.path) if self.active_doc is not None else None
        out = {"bytes": 0, "steps": 0, "transcripts": 0, "other_bytes": 0, "other_steps": 0}
        for path, (undo, redo) in stacks.items():
            n = sum(len(p) if isinstance(p, (bytes, str)) else 0 for _l, p in list(undo) + list(redo))
            steps = len(undo) + len(redo)
            if not steps:
                continue
            out["bytes"] += n
            out["steps"] += steps
            out["transcripts"] += 1
            if path != open_path:
                out["other_bytes"] += n
                out["other_steps"] += steps
        return out

    def _free_memory(self) -> Dict:
        """Let go of what can be rebuilt or isn't needed: undo history of transcripts that aren't open, the video
        stream (a still is shown again), the project caches. Returns what was freed."""
        held = self._undo_memory()
        stacks = self.__dict__.get("_undo_stacks") or {}
        open_path = str(self.active_doc.path) if self.active_doc is not None else None
        for path in list(stacks):
            if path != open_path:
                stacks.pop(path, None)
        playing = bool(self.__dict__.get("playing", False))
        if not playing:
            self._video_stop()
        self.__dict__["_project_cache"] = {}
        self.__dict__["_overview_caches"] = {}
        gc.collect()
        if not playing and self.active_doc is not None:
            self._video_still(self.__dict__.get("play_pos", 0.0))
        return {"undo_bytes": held["other_bytes"], "undo_steps": held["other_steps"]}

    # ----- restart -----
    def _restart_keeping_place(self):
        """Save, close, start again, and reopen the transcript where you were."""
        doc = self.active_doc
        self.settings["reopen_after_restart"] = str(doc.path) if doc is not None else ""
        self._remember_position()
        self._restart_app()

    def _reopen_after_restart(self):
        path = self.settings.pop("reopen_after_restart", "")
        if path and Path(path).is_file():
            try:
                self._open_at(path)
            except Exception as exc:
                self._append_log(f"Could not reopen {path} after the restart: {exc}", "WARN")

    # ----- the window -----
    def _memory_dialog(self):
        T = THEME
        win = tk.Toplevel(self)
        win.title("Memory")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        head = self._label(win, "", font="body_bold", bg=T["bg_panel"], justify=tk.LEFT)
        head.pack(anchor="w")
        body = self._label(win, "", font="mono_small", fg=T["fg_secondary"], bg=T["bg_panel"], justify=tk.LEFT)
        body.pack(anchor="w", pady=(6, 0))
        advice = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=520, justify=tk.LEFT)
        advice.pack(anchor="w", pady=(8, 0))
        st: Dict = {}

        def show(u: Optional[Dict] = None):
            u = u or memlib.usage()
            self._memory_show(u)
            lvl = memlib.level(u["total"], u["ram"])
            share = f" — {u['total'] / u['ram']:.0%} of this computer's {memlib.fmt(u['ram'])}" if u["ram"] else ""
            head.config(text=f"Condense and the programs it started: {memlib.fmt(u['total'])}{share}",
                        fg={"ok": T["fg_primary"], "high": T["warn"], "very_high": T["err"]}[lvl])
            lines = [f"{'Condense (window, transcript, video frames)':<46}{memlib.fmt(u['self']):>9}"]
            for c in u["children"]:
                lines.append(f"{memlib.role(c['name'])[:44]:<46}{memlib.fmt(c['rss']):>9}")
            held = self._undo_memory()
            if held["steps"]:
                lines.append("")
                lines.append(f"{'Undo history (' + str(held['steps']) + ' steps, ' + str(held['transcripts']) + ' transcript(s))':<46}"
                             f"{memlib.fmt(held['bytes']):>9}")
            growth = self._memory_growth(30)
            if growth is not None and abs(growth) >= 50 * 1024 ** 2:
                lines.append(f"{'Change in the last 30 minutes':<46}{('+' if growth > 0 else '−') + memlib.fmt(abs(growth)):>9}")
            body.config(text="\n".join(lines))
            if lvl == "very_high":
                advice.config(text="This is a lot: the system may start closing other programs. Free memory first; if it stays "
                                   "high, restart Condense — everything is saved and the transcript reopens where you were.",
                              fg=T["warn"])
            elif lvl == "high":
                advice.config(text="Quite a lot. If it keeps growing while you work, free memory or restart Condense.", fg=T["warn"])
            else:
                advice.config(text="Fine.", fg=T["fg_secondary"])
            st["u"] = u
            return u

        def free():
            before = (st.get("u") or memlib.usage())["total"]
            res = self._free_memory()
            u = show()
            msg = f"Freed {memlib.fmt(max(0, before - u['total']))}"
            if res["undo_steps"]:
                msg += f" (incl. the undo history of transcripts that aren't open: {res['undo_steps']} steps)"
            self.lbl_status.config(text=msg + ".")
            return res

        def restart():
            if not messagebox.askyesno("Restart Condense", "Save everything, restart Condense and reopen this transcript where "
                                       "you were? The undo history starts fresh.", parent=win):
                return None
            win.destroy()
            self._restart_keeping_place()
            return True

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(12, 0))
        self._tool(bar, "Free memory", free).pack(side=tk.LEFT)
        self._tool(bar, "Restart Condense…", restart).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Refresh", lambda: show()).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        show(self.__dict__.get("_memory_last"))
        st.update(show=show, free=free, restart=restart, head=head, body=body, advice=advice)
        return win, st
