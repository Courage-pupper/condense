"""Comments — notes on a passage or a stretch of the recording, without a code — and the commentary export.

A comment is made from what is selected in the mode shown: in Code mode the selected words (else a range marked on
the timeline, else the clicked word's segment); in Edit mode a range marked on the timeline, else the selected
segments or the open one; on the timeline also a whole block ("Comment on this 5-minute block"). M in Code mode and on
the timeline, ⌘K / Ctrl+Shift+K anywhere. Comments are codings with the COMMENT code (core/coding.py), so they follow
the words through edits, have a lane on the timeline and are listed with the codings.
"""
import queue
import threading

from condense.core import coding as cod
from condense.core import commentary as cm
from condense.core import project as proj
from condense.gui._common import *  # noqa: F401,F403


class CommentsMixin:
    def _bind_comment_keys(self):
        seqs = ("<Command-Key-k>",) if IS_MAC else ("<Control-Shift-Key-K>", "<Control-Shift-Key-k>")
        for seq in seqs:
            self.bind_all(seq, lambda e: (self._comment_dialog(), "break")[-1])

    def _comment_minutes(self) -> int:
        try:
            m = int(self.settings.get("commentary_minutes", 5))
        except (TypeError, ValueError):
            m = 5
        return m if m in cm.BLOCK_MINUTES else 5

    # ================================================================== what a comment is about
    def _comment_span_of_segments(self, doc, a: int, b: int) -> Optional[Dict]:
        segs = doc.segments
        first = next((w for i in range(a, b + 1) for w in (segs[i].get("words") or [])[:1]), None)
        last = next((w for i in range(b, a - 1, -1) for w in (segs[i].get("words") or [])[-1:]), None)
        if first is None or last is None:
            return {"start": float(segs[a].get("start", 0.0)), "end": float(segs[b].get("end", 0.0))}
        return {"start": float(first["start"]), "end": float(last["end"]), "swid": first.get("wid"), "ewid": last.get("wid")}

    def _comment_target(self) -> Optional[Dict]:
        """{start, end, swid?, ewid?}: what is selected in the mode shown (see the module's docstring)."""
        doc = self.active_doc
        if doc is None:
            return None
        if self._cd_visible():
            span = self._cd_selection_words()
            words = self._cd.get("words") or []
            if span is not None and self._cd.get("doc") is doc:
                a, b = words[span[0]], words[span[1]]
                wa, wb = doc.segments[a[3]]["words"][a[4]], doc.segments[b[3]]["words"][b[4]]
                return {"start": float(wa["start"]), "end": float(wb["end"]), "swid": wa.get("wid"), "ewid": wb.get("wid")}
            mark = self._cdt_mark()
            if mark:
                return {"start": mark[0], "end": mark[1]}
            segs = self._cd_speaker_segments()
            return self._comment_span_of_segments(doc, segs[0], segs[-1]) if segs else None
        mark = self._cdt_mark()
        if mark:
            return {"start": mark[0], "end": mark[1]}
        segs = self._selected_segments()
        segs = [s for s in segs if s is not None and 0 <= s < len(doc.segments)]
        return self._comment_span_of_segments(doc, min(segs), max(segs)) if segs else None

    # ================================================================== the dialog
    def _comment_dialog(self, kid: Optional[str] = None, target: Optional[Dict] = None):
        """Write (or change) a comment. Returns (window, {"text", "save", "delete"}) or None."""
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return None
        c = cod.get_coding(doc, kid) if kid else None
        if kid and c is None:
            return None
        if c is None:
            target = target or self._comment_target()
            if target is None:
                self.lbl_status.config(text="Select words or segments, or mark a range on the timeline (drag, I / O), then "
                                            "comment on it.")
                return None
            start, end = sorted((float(target["start"]), float(target["end"])))
        else:
            res = next((r for r in cod.resolve(doc) if r["id"] == kid), dict(c))
            start, end = float(res["start"]), float(res["end"])
        if c is None:
            words = doc.resolve_span(target.get("swid"), target.get("ewid"), start, end) if target.get("swid") else None
        else:
            words = res.get("words")
        r = {"start": start, "end": end, "words": words}
        passage = cod.coded_text(doc, r) if r.get("words") else ""
        who = ", ".join(cod.speakers_of(doc, r))
        T = THEME
        win = tk.Toplevel(self)
        win.title("Comment" if c is None else "Edit comment")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        head = f"{short_ts(start)[:-2]} – {short_ts(end)[:-2]}  ({cm.hms(end - start)})" + (f"  ·  {who}" if who else "")
        self._label(win, head, font="small_bold", bg=T["bg_panel"]).pack(anchor="w")
        if passage:
            show = passage if len(passage) <= 220 else passage[:220] + " …"
            self._wrapping_label(win, f"“{show}”", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(fill=tk.X, pady=(4, 0))
        elif c is None or c.get("kind") == "time":
            self._label(win, "A stretch of the recording (by time)", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).pack(anchor="w")
        txt = tk.Text(win, height=7, width=64, wrap=tk.WORD, font=self.f["body"], bg=T["bg_input"], fg=T["fg_primary"],
                      insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                      highlightcolor=T["accent"], padx=8, pady=6, undo=True)
        txt.pack(fill=tk.BOTH, expand=True, pady=(8, 4))
        if c is not None and c.get("memo"):
            txt.insert("1.0", c["memo"])
        self._label(win, kbd("What happens here, what you notice. ⌘↩ saves."), font="small", fg=T["fg_dim"],
                    bg=T["bg_panel"]).pack(anchor="w")
        row = tk.Frame(win, bg=T["bg_panel"])
        row.pack(fill=tk.X, pady=(10, 0))
        st: Dict = {"text": txt, "win": win, "kid": kid}

        def save():
            text = txt.get("1.0", tk.END).strip()
            cur = self.active_doc
            if cur is not doc:
                win.destroy()
                return None
            if c is None:
                if not text:
                    win.destroy()
                    return None
                new = cod.add_coding(doc, cod.COMMENT, start, end, target.get("swid"), target.get("ewid"), memo=text)
                self._cd_commit(f"Comment at {short_ts(start)[:-2]}")
                st["kid"] = new
            elif not text:
                return delete()
            elif cod.update_coding(doc, kid, memo=text):
                self._cd_commit("Comment changed")
            win.destroy()
            self._comment_refresh(doc, st["kid"], by_time=not (r.get("words")))
            return st["kid"]

        def delete():
            if kid and cod.remove_coding(doc, kid):
                self._cd_commit("Comment deleted")
                st["kid"] = None
            win.destroy()
            self._comment_refresh(doc, None, by_time=True)
            return None

        st["save"], st["delete"] = save, delete
        self._tool(row, "Save", save, primary=True).pack(side=tk.RIGHT)
        self._tool(row, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        if c is not None:
            self._tool(row, "Delete comment", delete).pack(side=tk.LEFT)
        for seq in (f"<{MOD}-Return>", f"<{MOD}-KP_Enter>"):
            txt.bind(seq, lambda e: (save(), "break")[-1])
        win.bind("<Escape>", lambda e: win.destroy())
        txt.focus_set()
        return win, st

    def _comment_refresh(self, doc, kid: Optional[str], by_time: bool):
        """After a comment changed: Code mode shows it at once (a comment by time is a ◆ line: a new layout); the
        timeline in both modes."""
        if self._cd_visible():
            self._cd_render(doc) if by_time else self._cd_refresh_codings(doc)
            if kid:
                self._cd_select_coding(kid)
        self._cdt_draw()
        n = sum(1 for x in cod.codings(doc) if x.get("code") == cod.COMMENT)
        self.lbl_status.config(text=f"{n} comment(s) in this transcript · Export ▸ Commentary over time gives them as a spreadsheet.")

    def _comment_on_block(self, t: float):
        """A comment on the block (Commentary's block length, 5 minutes by default) that time t is in."""
        m = self._comment_minutes() * 60
        a = int(t // m) * m
        b = min(a + m, max(self._duration(), a + 1))
        return self._comment_dialog(target={"start": float(a), "end": float(b)})

    # ================================================================== export
    def _commentary_sessions(self, scope: str) -> Tuple[List[Dict], List[str]]:
        """The transcripts to export, with Day / Group / Type and attributes when they are in a project; and the names
        of the project's attributes (columns)."""
        doc = self.active_doc
        project = self._cd_project()
        if project is None:
            return ([{"doc": doc, "type": "", "levels": []}] if doc is not None else []), []
        rows = proj.overview(project, cache=self._overview_cache(project))
        rows = [r for r in rows if "error" not in r]
        if scope == "doc":
            rows = [r for r in rows if doc is not None and Path(r["path"]).resolve() == Path(doc.path).resolve()] or []
            if not rows and doc is not None:
                return [{"doc": doc, "type": "", "levels": []}], []
        rows.sort(key=lambda r: ([v for _n, v in r.get("levels") or []], str(r.get("name", ""))))
        attrs: List[str] = []
        for r in rows:
            for a in (r.get("attributes") or {}):
                if a not in attrs:
                    attrs.append(a)
        out = []
        for r in rows:
            s = {"path": r["path"], "type": (r.get("auto") or {}).get("Type", ""), "levels": r.get("levels") or [],
                 "attributes": r.get("attributes") or {}}
            if doc is not None and Path(r["path"]).resolve() == Path(doc.path).resolve():
                s["doc"] = doc  # the open one as it is now
            out.append(s)
        return out, attrs

    def _radio(self, parent, text: str, var, value: str):
        T = THEME
        return tk.Radiobutton(parent, text=text, variable=var, value=value, bg=T["bg_panel"], fg=T["fg_primary"],
                              selectcolor=T["bg_input"], activebackground=T["bg_panel"], font=self.f["small"], anchor="w")

    def _commentary_dialog(self):
        doc = self.active_doc
        project = self._cd_project()
        if doc is None and project is None:
            self.lbl_status.config(text="Open a transcript (or a project) first.")
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title("Commentary over time")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        self._wrapping_label(win, "Your comments, coding memos and segment notes along the recording's time, in blocks: a "
                                  "spreadsheet with a row for every block (also the empty ones, to fill in) and a video frame "
                                  "for each.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(fill=tk.X)
        st: Dict = {"win": win}
        st["scope"] = tk.StringVar(value="doc" if doc is not None else "project")
        box = tk.Frame(win, bg=T["bg_panel"])
        box.pack(fill=tk.X, pady=(10, 0))
        rb1 = self._radio(box, f"This transcript ({doc.base_name})" if doc is not None else "This transcript", st["scope"], "doc")
        rb1.pack(anchor="w")
        rb2 = self._radio(box, f"The whole project ({project.name}): all its transcripts in one sheet" if project
                          else "The whole project (open a project first)", st["scope"], "project")
        rb2.pack(anchor="w")
        if doc is None:
            rb1.configure(state=tk.DISABLED)
        if project is None:
            rb2.configure(state=tk.DISABLED)
        row = tk.Frame(win, bg=T["bg_panel"])
        row.pack(fill=tk.X, pady=(10, 0))
        self._label(row, "Blocks of", font="small", bg=T["bg_panel"]).pack(side=tk.LEFT)
        st["minutes"] = tk.StringVar(value=str(self._comment_minutes()))
        ttk.Combobox(row, textvariable=st["minutes"], values=[str(m) for m in cm.BLOCK_MINUTES], width=4, state="readonly",
                     font=self.f["small"]).pack(side=tk.LEFT, padx=6)
        self._label(row, "minutes", font="small", bg=T["bg_panel"]).pack(side=tk.LEFT)
        inc = self.settings.get("commentary_include") or list(cm.KINDS)
        st["include"] = {}
        line = tk.Frame(win, bg=T["bg_panel"])
        line.pack(fill=tk.X, pady=(8, 0))
        self._label(line, "With", font="small", bg=T["bg_panel"]).pack(side=tk.LEFT)
        for key, label in (("comment", "comments"), ("memo", "coding memos"), ("note", "segment notes")):
            v = tk.BooleanVar(value=key in inc)
            st["include"][key] = v
            self._check(line, label, v, bg=T["bg_panel"], font="small").pack(side=tk.LEFT, padx=(8, 0))
        st["frames"] = tk.BooleanVar(value=bool(self.settings.get("commentary_frames", True)))
        self._check(win, "A video frame for each block (from the middle of it)", st["frames"], bg=T["bg_panel"],
                    font="small").pack(anchor="w", pady=(8, 0))
        st["per_sheet"] = tk.BooleanVar(value=bool(self.settings.get("commentary_per_sheet", False)))
        self._check(win, "Project: also a sheet per transcript", st["per_sheet"], bg=T["bg_panel"], font="small").pack(anchor="w")
        st["fmt"] = tk.StringVar(value="xlsx" if cm.can_write_xlsx() else "csv")
        frow = tk.Frame(win, bg=T["bg_panel"])
        frow.pack(fill=tk.X, pady=(8, 0))
        rx = self._radio(frow, "Excel (.xlsx)", st["fmt"], "xlsx")
        rx.pack(side=tk.LEFT)
        self._radio(frow, "CSV (no pictures)", st["fmt"], "csv").pack(side=tk.LEFT, padx=(10, 0))
        if not cm.can_write_xlsx():
            rx.configure(state=tk.DISABLED)
            self._label(win, "Excel needs openpyxl: run the installer again (Check for Updates) to add it.", font="small",
                        fg=T["warn"], bg=T["bg_panel"]).pack(anchor="w")
        st["progress"] = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        st["progress"].pack(anchor="w", pady=(8, 0))
        brow = tk.Frame(win, bg=T["bg_panel"])
        brow.pack(fill=tk.X, pady=(10, 0))

        def go(target: Optional[Path] = None, wait: bool = False, open_after: bool = True):
            scope = st["scope"].get()
            fmt = st["fmt"].get()
            minutes = int(st["minutes"].get() or 5)
            include = [k for k, v in st["include"].items() if v.get()]
            if not include:
                st["progress"].config(text="Choose what to include.")
                return None
            if target is None:
                base = (doc.base_name if scope == "doc" and doc is not None else (project.name if project else "commentary"))
                folder = (Path(doc.path).parent if scope == "doc" and doc is not None else project) or Path.home()
                name = filedialog.asksaveasfilename(parent=win, title="Save the commentary", initialdir=str(folder),
                                                    initialfile=f"{base}_commentary_{minutes}min.{fmt}", defaultextension=f".{fmt}",
                                                    filetypes=[("Excel", "*.xlsx")] if fmt == "xlsx" else [("CSV", "*.csv")])
                if not name:
                    return None
                target = Path(name)
            self.settings.update(commentary_minutes=minutes, commentary_include=include, commentary_frames=bool(st["frames"].get()),
                                 commentary_per_sheet=bool(st["per_sheet"].get()))
            sessions, attrs = self._commentary_sessions(scope)
            return self._commentary_export(sessions, target, minutes, include, bool(st["frames"].get()) and fmt == "xlsx",
                                           bool(st["per_sheet"].get()) and scope == "project", attrs, label=st["progress"],
                                           wait=wait, open_after=open_after)

        btn = self._tool(brow, "Export…", lambda: go(), primary=True)
        btn.pack(side=tk.RIGHT)
        self._tool(brow, "Close", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        st["go"] = go
        win.bind("<Escape>", lambda e: win.destroy())
        return win, st

    def _commentary_export(self, sessions, target: Path, minutes: int, include, frames: bool, per_sheet: bool, attrs,
                           label=None, wait: bool = False, open_after: bool = True, done=None):
        """Write it in the background (frames take ffmpeg a moment each); the status line says how far it is."""
        msgs: "queue.Queue" = queue.Queue()
        cb = self._cd_codebook()
        out: Dict = {}

        def work():
            try:
                out["res"] = cm.export(sessions, target, minutes, cb, include, frames, per_sheet, attrs,
                                       progress=lambda m: msgs.put(("progress", m)))
            except Exception as exc:  # e.g. the file is open in Excel (Windows locks it)
                out["error"] = str(exc)
            msgs.put(("done", None))

        def show(text):
            self.lbl_status.config(text=text)
            if label is not None:
                try:
                    label.config(text=text)
                except Exception:
                    pass

        def finish():
            if "error" in out:
                show(f"Could not write the commentary: {out['error']}")
            else:
                res = out["res"]
                show(f"Commentary: {res['rows']} rows from {res['sessions']} transcript(s)"
                     + (f", {res['frames']} frames" if res.get("frames") else "") + f" → {Path(target).name}")
                if open_after:
                    self._open_externally(Path(target))
            if done is not None:
                done(out.get("res"))
            return out.get("res")

        def poll():
            try:
                while True:
                    kind, m = msgs.get_nowait()
                    if kind == "progress":
                        show(m)
                    else:
                        return finish()
            except queue.Empty:
                pass
            self.after(150, poll)
            return None

        if wait:
            work()
            while True:
                kind, m = msgs.get()
                if kind == "done":
                    return finish()
        show("Writing the commentary…")
        threading.Thread(target=work, daemon=True).start()
        self.after(150, poll)
        return None
