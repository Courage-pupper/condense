"""Project ▸ Name Recordings by Pattern…: names that say what a recording is (G4_Day3_2026.08.10_Session.mp4) without
looking at the folders. Only when asked: a preview to check and edit first, then Rename; Undo takes it back."""
import queue
import threading

from condense.core import project as proj
from condense.core import renaming as rn
from condense.core.transcript import TranscriptDocument
from condense.gui._common import *  # noqa: F401,F403

TOKENS_HELP = ("{group} {day}: the numbers of the folders  ·  {Group} {Day}: their names  ·  {date}: 2026.08.10 (from the "
               "folder names, else the video)  ·  {type}: Session / Interview  ·  {n}: the interview number  ·  {part}: "
               "_part1, _part2 … when a session has several recordings  ·  {name}: the current name")


class RenamingMixin:
    def _rename_dialog(self):
        project = self._cd_project() if hasattr(self, "_cd_project") else None
        if project is None:
            self.lbl_status.config(text="Open the study's project first (Project ▸ Open Project…).")
            return None
        T = THEME
        data = proj.load_project(project / proj.PROJECT_FILE)
        saved = data.get("rename_patterns") or {}
        st_ = proj.project_settings(project).get("structure")
        clip_pattern = st_.name_alts[0] if st_ is not None and st_.name_alts else ""
        win = tk.Toplevel(self)
        win.title("Name recordings by pattern")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        self._label(win, "Names that say what a recording is — group, day and date — so a file makes sense without its "
                         "folders. Its transcript, results folder and copies are renamed with it, and every link to it is "
                         "updated. Nothing changes until you press Rename, and it can be undone.",
                    font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=760, justify=tk.LEFT,
                    anchor="w").pack(fill=tk.X)
        st: Dict = {"win": win, "rows": [], "project": project}
        grid = tk.Frame(win, bg=T["bg_panel"])
        grid.pack(fill=tk.X, pady=(10, 0))
        st["use_session"] = tk.BooleanVar(value=True)
        st["use_interview"] = tk.BooleanVar(value=False)
        st["session"] = tk.StringVar(value=saved.get("session") or rn.DEFAULT_PATTERNS["session"])
        st["interview"] = tk.StringVar(value=saved.get("interview") or clip_pattern or "G{group}D{day}_I{n}")
        for r, (key, label) in enumerate((("session", "Sessions"), ("interview", "Interviews"))):
            self._check(grid, label, st[f"use_{key}"], bg=T["bg_panel"], font="small").grid(row=r, column=0, sticky="w")
            e = tk.Entry(grid, textvariable=st[key], width=46, font=self.f["mono_small"], bg=T["bg_input"],
                         fg=T["fg_primary"], insertbackground=T["fg_primary"], relief=tk.FLAT)
            e.grid(row=r, column=1, sticky="we", padx=(8, 0), pady=2)
            e.bind("<Return>", lambda ev: preview())
        grid.columnconfigure(1, weight=1)
        st["new_clips"] = tk.BooleanVar(value=False)
        self._check(win, f"Name new interview clips this way too (now: {clip_pattern or '—'})", st["new_clips"],
                    bg=T["bg_panel"], font="small").pack(anchor="w", pady=(4, 0))
        self._label(win, TOKENS_HELP, font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=760, justify=tk.LEFT,
                    anchor="w").pack(fill=tk.X, pady=(6, 0))
        box = tk.Frame(win, bg=T["bg_panel"])
        box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        cols = ("use", "type", "where", "old", "new", "note")
        tree = ttk.Treeview(box, columns=cols, show="headings", height=14, selectmode="browse", style="Queue.Treeview")
        for c, w, label in (("use", 34, "✓"), ("type", 80, "Type"), ("where", 150, "Day · Group"),
                            ("old", 210, "Now"), ("new", 260, "New name (double-click to change)"), ("note", 230, "")):
            tree.heading(c, text=label)
            tree.column(c, width=w, stretch=c in ("old", "new", "note"), anchor="center" if c == "use" else "w")
        sb = ttk.Scrollbar(box, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        st["tree"] = tree
        st["info"] = self._label(win, "Press Preview to see the new names.", font="small", fg=T["fg_secondary"],
                                 bg=T["bg_panel"], anchor="w")
        st["info"].pack(fill=tk.X, pady=(6, 0))
        brow = tk.Frame(win, bg=T["bg_panel"])
        brow.pack(fill=tk.X, pady=(10, 0))

        def show():
            tree.delete(*tree.get_children())
            for i, r in enumerate(st["rows"]):
                where = " · ".join(v for _lv, v in r.get("levels") or [])
                use = "✓" if r.get("ok") and r.get("use", True) else ("–" if r.get("ok") else "")
                tree.insert("", tk.END, iid=str(i), values=(use, r["role"].capitalize(), where, r["old"], r["new"], r.get("note", "")))
            n = sum(1 for r in st["rows"] if r.get("ok") and r.get("use", True))
            st["info"].config(text=f"{n} of {len(st['rows'])} recording(s) will be renamed." if st["rows"]
                              else "No recordings found in the session / interview folders.")
            btn_go.configure(text=f"Rename {n}" if n else "Rename")
            return n

        def roles():
            return [k for k in ("session", "interview") if st[f"use_{k}"].get()]

        def preview(wait: bool = False):
            pats = {"session": st["session"].get().strip(), "interview": st["interview"].get().strip()}
            which = roles()  # (read here: Tk's variables only from the main thread)
            st["info"].config(text="Looking at the recordings…")

            def work():
                try:
                    out["rows"] = rn.plan(project, pats, roles=which)
                except Exception as exc:
                    out["error"] = str(exc)

            out: Dict = {}
            if wait:
                work()
                return finish(out)
            th = threading.Thread(target=work, daemon=True)
            th.start()

            def poll():
                if th.is_alive():
                    win.after(150, poll)
                else:
                    finish(out)
            win.after(150, poll)
            return None

        def finish(out):
            if "error" in out:
                st["info"].config(text=f"Could not read the project: {out['error']}")
                return 0
            st["rows"] = out["rows"]
            return show()

        def toggle(iid: str):
            r = st["rows"][int(iid)]
            if r.get("ok"):
                r["use"] = not r.get("use", True)
                show()

        def edit(iid: str, name: Optional[str] = None):
            r = st["rows"][int(iid)]
            if name is None:
                name = simpledialog.askstring("New name", f"New name for {r['old']} (without the extension):",
                                              initialvalue=r["new"], parent=win)
            if name is None:
                return None
            rn.set_name(r, name)
            rn._check_clashes(st["rows"])
            show()
            return r

        def click(ev):
            iid = tree.identify_row(ev.y)
            if iid and tree.identify_column(ev.x) == "#1":
                toggle(iid)

        def double(ev):
            iid = tree.identify_row(ev.y)
            if iid:
                edit(iid)

        tree.bind("<Button-1>", click, add="+")
        tree.bind("<Double-1>", double)

        def rename(wait: bool = False):
            todo = [r for r in st["rows"] if r.get("ok") and r.get("use", True)]
            if not todo:
                st["info"].config(text="Nothing to rename: press Preview, and tick the recordings to rename.")
                return None
            if any(t.get("status") == "Running" for t in getattr(self, "jobs", []) or []):
                st["info"].config(text="A task is running: rename when the queue is done (it may be using these files).")
                return None
            if not wait and not messagebox.askyesno(
                    "Rename recordings", f"Rename {len(todo)} recording(s) and what belongs to them?\n\nIt can be undone "
                    "here (Undo the last renaming). Programs that link these files by name (e.g. a MAXQDA project) "
                    "would need the new names.", parent=win):
                return None
            self._rename_before()
            proj.update_project(project, rename_patterns={"session": st["session"].get().strip(),
                                                          "interview": st["interview"].get().strip()})
            res = rn.apply(project, st["rows"], progress=lambda m: st["info"].config(text=m))
            if st["new_clips"].get() and st["use_interview"].get():
                rn.use_for_new_clips(project, st["interview"].get().strip())
            self._rename_after(res.get("moves") or [])
            st["info"].config(text=f"Renamed {res['renamed']} recording(s): {res['files']} files and folders, "
                                   f"{res['relinked']} transcript(s) updated.")
            self.lbl_status.config(text=st["info"].cget("text"))
            btn_undo.configure(state=tk.NORMAL)
            st["rows"] = []
            show()
            return res

        def undo():
            self._rename_before()
            moves = rn.undo(project)
            n = len(moves)
            self._rename_after(moves)
            st["info"].config(text=f"Undone: {n} files and folders have their old names again." if n else "Nothing to undo.")
            btn_undo.configure(state=tk.DISABLED)
            return n

        btn_go = self._tool(brow, "Rename", lambda: rename(), primary=True)
        btn_go.pack(side=tk.RIGHT)
        self._tool(brow, "Close", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        self._tool(brow, "Preview", lambda: preview()).pack(side=tk.LEFT)
        btn_undo = self._tool(brow, "Undo the last renaming", lambda: undo())
        btn_undo.pack(side=tk.LEFT, padx=(6, 0))
        if not rn.last_was_rename(project):
            btn_undo.configure(state=tk.DISABLED)
        st.update(preview=preview, toggle=toggle, edit=edit, rename=rename, undo=undo)
        win.bind("<Escape>", lambda e: win.destroy())
        win.after(50, preview)
        return win, st

    def _rename_before(self):
        """The open transcript saved first (renaming rewrites the files)."""
        if self.active_doc is not None:
            try:
                self._autosave_edit(rerender=False)
                self.active_doc.save()
            except Exception:
                pass
            self._rename_open = rn.real(self.active_doc.path)
        else:
            self._rename_open = None

    def _rename_after(self, moves):
        """Open the transcript again where it is now; the project panel shows the new names."""
        old = self.__dict__.get("_rename_open")
        if old and moves:
            new = Path(rn._map(old, moves))
            if os.path.normcase(str(new)) != os.path.normcase(old) and new.exists():
                try:
                    doc = TranscriptDocument.load(new)
                    self.active_doc = None  # (not another document to save first: it was saved, then renamed)
                    self._render_doc(doc)
                except Exception:
                    pass
        try:
            self._refresh_project_panel()
        except Exception:
            pass
