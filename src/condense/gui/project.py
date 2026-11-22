"""Project overview (all transcripts of a study folder) and the project's speaker list."""
from condense.core import project as proj
from condense.gui._common import *  # noqa: F401,F403


class ProjectMixin:
    def _project_speakers(self) -> List[str]:
        doc = self.active_doc
        if doc is None:
            return []
        key = str(doc.path)
        cache = self.__dict__.setdefault("_project_cache", {})
        if key not in cache:
            cache[key] = proj.project_speakers_for(Path(doc.path))
        return cache[key]

    def _roster(self) -> List[str]:
        """Speakers in the order used for number keys and colours: the project's list first, then this transcript's others."""
        doc = self.active_doc
        if doc is None:
            return []
        return proj.effective_roster(doc.roster(), self._project_speakers())

    def _forget_project_cache(self):
        self.__dict__["_project_cache"] = {}

    # ----- overview -----
    def _project_folder_default(self) -> str:
        doc = self.active_doc
        if doc is not None:
            f = proj.find_project_file(Path(doc.path).parent)
            if f:
                return str(f.parent)
            return str(doc.out_dir.parent)
        return self.settings.get("project_dir") or str(Path.home())

    def _project_overview(self):
        T = THEME
        win = tk.Toplevel(self)
        win.title("Project overview")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        v_dir = tk.StringVar(value=self.settings.get("project_dir") or self._project_folder_default())
        top = tk.Frame(win, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self._label(top, "Folder", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._entry(top, v_dir, font="small", width=64).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)
        lbl_total = self._label(win, "", font="small_bold", bg=T["bg_panel"])
        cols = ("length", "checked", "uncertain", "speakers", "cuts", "notes", "modified")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=16)
        tree.heading("#0", text="Transcript")
        tree.column("#0", width=280)
        for c, w, title in (("length", 80, "Length"), ("checked", 80, "Checked"), ("uncertain", 90, "Uncertain"),
                            ("speakers", 80, "Speakers"), ("cuts", 70, "Cut"), ("notes", 60, "Notes"), ("modified", 130, "Last change")):
            tree.heading(c, text=title)
            tree.column(c, width=w, anchor="center")
        state = {"rows": []}

        def refresh():
            folder = Path(v_dir.get()).expanduser()
            if not folder.is_dir():
                lbl_total.config(text="Choose an existing folder.")
                return []
            self.settings["project_dir"] = str(folder)
            rows = proj.overview(folder)
            state["rows"] = rows
            for item in tree.get_children():
                tree.delete(item)
            ok_rows = [r for r in rows if "error" not in r]
            for i, r in enumerate(rows):
                if "error" in r:
                    tree.insert("", tk.END, iid=str(i), text=f"⚠ {r['name']}", values=("", "", "", "", "", "", "could not read"))
                    continue
                spk = f"{r['speakers']}" + (f" ({r['generic_speakers']} unnamed)" if r["generic_speakers"] else "")
                tree.insert("", tk.END, iid=str(i), text=r["name"], values=(
                    short_ts(r["length_s"])[:-2], f"{r['checked_pct']}%", f"{r['uncertain_pct']}%", spk,
                    short_ts(r["cut_s"])[:-2] if r["cut_s"] else "", r["notes"] or "", r["modified"]))
            total_len = sum(r["length_s"] for r in ok_rows)
            done = sum(r["length_s"] * r["checked_pct"] / 100 for r in ok_rows)
            f = proj.find_project_file(folder)
            spk_info = f"  ·  project speakers: {', '.join(proj.load_project(f)['speakers']) or 'none'}" if f else "  ·  no project speaker list yet"
            lbl_total.config(text=f"{len(ok_rows)} transcript(s)  ·  {short_ts(total_len)[:-2]} recorded  ·  "
                                  f"{(done * 100 / total_len) if total_len else 0:.0f}% checked{spk_info}")
            return rows

        def open_selected(event=None):
            sel = tree.selection()
            if not sel:
                return
            r = state["rows"][int(sel[0])]
            if "error" in r:
                return
            self._autosave_edit(rerender=False)
            self._render_doc(TranscriptDocument.load(r["path"]))
            self.notebook.select(self.tab_preview)

        def export_csv():
            if not state["rows"]:
                return
            target = Path(v_dir.get()).expanduser() / "condense-overview.csv"
            proj.write_overview_csv(state["rows"], target)
            self._append_log(f"Project overview saved: {target}", "SUCCESS")
            lbl_total.config(text=lbl_total.cget("text") + f"  ·  saved {target.name}")

        self._tool(top, "Choose…", lambda: (v_dir.set(self._ask_directory("Project folder", v_dir.get()) or v_dir.get()), refresh())
                   ).pack(side=tk.LEFT)
        self._tool(top, "Refresh", refresh).pack(side=tk.LEFT, padx=(6, 0))
        lbl_total.pack(anchor="w", pady=(8, 4))
        tree.pack(fill=tk.BOTH, expand=True)
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        self._tool(bar, "Open", open_selected, primary=True).pack(side=tk.LEFT)
        self._tool(bar, "Project speakers…", lambda: self._project_speakers_dialog(Path(v_dir.get()).expanduser(), on_saved=refresh)
                   ).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Save as CSV", export_csv).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        tree.bind("<Double-Button-1>", open_selected)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        refresh()
        state.update(refresh=refresh, open=open_selected, export=export_csv, tree=tree, v_dir=v_dir, lbl=lbl_total)
        return win, state

    # ----- project speakers -----
    def _project_speakers_dialog(self, folder: Path, on_saved=None):
        T = THEME
        f = proj.find_project_file(folder)
        if f is not None and f.parent != folder.resolve():
            folder = f.parent  # edit the list that applies here
        names = list(proj.load_project(f)["speakers"]) if f else []
        win = tk.Toplevel(self)
        win.title("Project speakers")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, f"Speakers of the project in {folder}.\nThey keep the same number key (1–9) and colour in every "
                         "transcript in this folder.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"], justify=tk.LEFT
                    ).pack(anchor="w")
        lst = tk.Listbox(win, height=10, width=36, bg=T["bg_input"], fg=T["fg_primary"], font=self.f["small"], relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=T["border"], exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True, pady=8)
        v_new = tk.StringVar(value="")
        row = tk.Frame(win, bg=T["bg_panel"])
        row.pack(fill=tk.X)
        ent = self._entry(row, v_new, font="small", width=24)
        ent.pack(side=tk.LEFT)
        st = {"names": names}

        def show():
            lst.delete(0, tk.END)
            for i, n in enumerate(st["names"]):
                lst.insert(tk.END, f"{i + 1 if i < 9 else ' '}  {n}")

        def add(name=None):
            n = " ".join((name if name is not None else v_new.get()).split())
            if n and n not in st["names"]:
                st["names"].append(n)
            v_new.set("")
            show()

        def move(d):
            sel = lst.curselection()
            if not sel:
                return
            i = sel[0]
            j = i + d
            if 0 <= j < len(st["names"]):
                st["names"][i], st["names"][j] = st["names"][j], st["names"][i]
                show()
                lst.selection_set(j)

        def remove():
            sel = lst.curselection()
            if sel:
                del st["names"][sel[0]]
                show()

        def from_transcripts():
            for p in proj.transcripts_under(folder):
                try:
                    for n in TranscriptDocument.load(p).roster():
                        if not n.startswith(proj.GENERIC_SPEAKER):
                            add(n)
                except Exception:
                    pass

        def save():
            path = proj.save_project(folder, st["names"])
            self._forget_project_cache()
            self._append_log(f"Project speakers saved ({path}): {', '.join(st['names']) or 'none'}", "INFO")
            win.destroy()
            if self.active_doc is not None:
                self._rerender()
            on_saved and on_saved()

        self._tool(row, "Add", add).pack(side=tk.LEFT, padx=(6, 0))
        ent.bind("<Return>", lambda e: add())
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        for text, cmd in (("↑", lambda: move(-1)), ("↓", lambda: move(+1)), ("Remove", remove), ("Add names from transcripts", from_transcripts)):
            self._tool(bar, text, cmd).pack(side=tk.LEFT, padx=(0, 4))
        bar2 = tk.Frame(win, bg=T["bg_panel"])
        bar2.pack(fill=tk.X, pady=(8, 0))
        self._tool(bar2, "Save", save, primary=True).pack(side=tk.RIGHT)
        self._tool(bar2, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        show()
        st.update(add=add, move=move, remove=remove, from_transcripts=from_transcripts, save=save, lst=lst)
        return win, st
