"""Projects: one study = one folder with a condense-project.json. Its transcripts are everything in that folder and its
subfolders. New / Open / Close Project, the overview of all sessions, and the project's speaker list."""
import queue as _queue
import threading

from condense.core import project as proj
from condense.core.util import is_within
from condense.gui._common import *  # noqa: F401,F403

INTRO = ("A project is the folder of one study: the transcripts in it and in its subfolders belong to it. Condense keeps "
         "the project's speaker list there, so the same people have the same number keys and colours in every session.")


class ProjectMixin:
    # ================================================================== which project is open
    def _project_dir(self) -> Optional[Path]:
        d = self.settings.get("project_dir")
        if d and (Path(d) / proj.PROJECT_FILE).is_file():
            return Path(d)
        return None

    def _set_project(self, folder: Optional[Path], announce: bool = True):
        if folder is None:
            self.settings["project_dir"] = ""
        else:
            folder = Path(folder).resolve()
            self.settings["project_dir"] = str(folder)
            recent = [str(folder)] + [r for r in self.settings.get("recent_projects") or [] if r != str(folder)]
            self.settings["recent_projects"] = recent[:8]
        try:
            SettingsStore.save(self.settings)
        except Exception:
            pass
        self._forget_project_cache()
        self._refresh_project_ui()
        if announce:
            self._append_log(f"Project: {proj.project_name(folder)} ({folder})" if folder else "Project closed.", "INFO")

    def _start_dir_in_project(self, last: Optional[str]) -> str:
        """Where file choosers start: the last folder if it is inside the open project, else the project, else the last."""
        project = self._project_dir()
        if project is not None and not (last and Path(last).is_dir() and is_within(Path(last), project)):
            return str(project)
        return last or str(Path.home())

    def _project_follow_doc(self, doc):
        """Opening a transcript that belongs to a project makes that project the open one."""
        f = proj.find_project_file(Path(doc.path).parent)
        current = self._project_dir()
        if f is not None and (current is None or f.parent.resolve() != current.resolve()):
            self._set_project(f.parent)

    # ----- speakers of the project (number keys and colours) -----
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

    # ================================================================== menu and header
    def _build_project_menu(self, mb):
        pm = tk.Menu(mb, tearoff=0)
        pm.add_command(label="New Project…", command=self._new_project_dialog)
        pm.add_command(label="Open Project…", command=self._open_project_dialog)
        self.recent_project_menu = tk.Menu(pm, tearoff=0)
        pm.add_cascade(label="Open Recent", menu=self.recent_project_menu)
        pm.add_command(label="Close Project", command=lambda: self._set_project(None))
        pm.add_separator()
        pm.add_command(label="Project Overview…", command=self._project_overview)
        pm.add_command(label="Project Speakers…", command=lambda: self._project_speakers_dialog())
        pm.add_command(label="Project Settings…", command=lambda: self._project_settings_dialog())
        mb.add_cascade(label="Project", menu=pm)
        self.project_menu = pm

    def _build_project_button(self, header):
        self.btn_project = self._tool(header, "Project…", self._project_button)
        self.btn_project.pack(side=tk.RIGHT, padx=(0, 8))
        self._refresh_project_ui()

    def _refresh_project_ui(self):
        d = self._project_dir()
        btn = self.__dict__.get("btn_project")
        if btn is not None:
            btn.config(text=f"Project: {proj.project_name(d)}" if d else "No project open")
        menu = self.__dict__.get("recent_project_menu")
        if menu is not None:
            menu.delete(0, tk.END)
            recent = [r for r in self.settings.get("recent_projects") or [] if (Path(r) / proj.PROJECT_FILE).is_file()]
            for r in recent:
                menu.add_command(label=f"{proj.project_name(Path(r))}   —   {r}", command=lambda r=r: self._open_project_folder(Path(r)))
            if not recent:
                menu.add_command(label="(none yet)", state=tk.DISABLED)

    def _project_button(self):
        return self._project_overview() if self._project_dir() else self._project_start_dialog()

    # ================================================================== start / new / open
    def _project_start_dialog(self):
        T = THEME
        win = tk.Toplevel(self)
        win.title("Projects")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "No project open", font="body_bold", bg=T["bg_panel"]).pack(anchor="w")
        self._label(win, INTRO, font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).pack(anchor="w", pady=(4, 10))
        row = tk.Frame(win, bg=T["bg_panel"])
        row.pack(fill=tk.X)
        self._tool(row, "New Project…", lambda: (win.destroy(), self._new_project_dialog()), primary=True).pack(side=tk.LEFT)
        self._tool(row, "Open Project…", lambda: (win.destroy(), self._open_project_dialog())).pack(side=tk.LEFT, padx=(6, 0))
        recent = [r for r in self.settings.get("recent_projects") or [] if (Path(r) / proj.PROJECT_FILE).is_file()]
        if recent:
            self._label(win, "Recent", font="small_bold", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(anchor="w", pady=(12, 2))
            for r in recent[:6]:
                self._tool(win, f"{proj.project_name(Path(r))}  —  {r}",
                           lambda r=r: (win.destroy(), self._open_project_folder(Path(r)))).pack(anchor="w", fill=tk.X, pady=1)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win, {"recent": recent}

    def _new_project_dialog(self):
        T = THEME
        win = tk.Toplevel(self)
        win.title("New project")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "New project", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self._label(win, "Choose the study's folder — the one that holds, or will hold, its sessions. Transcripts already in "
                         "it or its subfolders become part of the project; nothing is moved. To start an empty folder, "
                         "create it in the folder chooser.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=480, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))
        v_folder = tk.StringVar(value="")
        v_name = tk.StringVar(value="")
        v_spk = tk.StringVar(value="")
        v_levels = tk.StringVar(value="")
        v_skip = tk.StringVar(value=", ".join(proj.DEFAULT_SKIP))
        lbl_err = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])

        def detect():
            f = Path(v_folder.get()).expanduser()
            if f.is_dir():
                skip = [x.strip() for x in v_skip.get().split(",") if x.strip()]
                v_levels.set(", ".join(proj.detect_levels(f, skip)))

        def choose():
            start = v_folder.get() or self.settings.get("last_add_dir") or str(Path.home())
            f = self._ask_directory("Folder of the study", start)
            if f:
                v_folder.set(f)
                if not v_name.get().strip():
                    v_name.set(Path(f).name)
                detect()

        for r, (label, var) in enumerate((("Folder", v_folder), ("Name", v_name), ("Speakers", v_spk)), start=2):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            self._entry(win, var, font="small", width=46).grid(row=r, column=1, sticky="we", pady=3)
        self._tool(win, "Choose…", choose).grid(row=2, column=2, padx=(6, 0))
        self._label(win, "Optional, comma-separated, in key order — e.g. Facilitator, Robin, Sam. You can change them later.",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=5, column=1, columnspan=2, sticky="w")
        self._label(win, "Levels", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=6, column=0, sticky="w", padx=(0, 8), pady=3)
        self._entry(win, v_levels, font="small", width=46).grid(row=6, column=1, sticky="we", pady=3)
        self._label(win, "The folder levels, read from your folder names — e.g. Day, Group for Day 1 …/Group 1/. Leave empty "
                         "if the folders have no levels.", font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=420,
                    justify=tk.LEFT).grid(row=7, column=1, columnspan=2, sticky="w")
        self._label(win, "Skip folders", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=8, column=0, sticky="w", padx=(0, 8), pady=3)
        self._entry(win, v_skip, font="small", width=46).grid(row=8, column=1, sticky="we", pady=3)
        self._label(win, "Never opened or listed (e.g. consent forms). * matches anything.", font="small", fg=T["fg_dim"],
                    bg=T["bg_panel"]).grid(row=9, column=1, columnspan=2, sticky="w")
        lbl_err.grid(row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def create():
            folder = v_folder.get().strip()
            if not folder:
                lbl_err.config(text="Choose the study's folder first.")
                return None
            folder = Path(folder).expanduser()
            outer = proj.find_project_file(folder.parent) if folder.parent != folder else None
            if outer is not None and outer.parent.resolve() != folder.resolve():
                lbl_err.config(text=f"This folder is inside the project “{proj.project_name(outer.parent)}”. "
                                    "Open that project instead, or choose a folder outside it.")
                return None
            speakers = [s for s in (x.strip() for x in v_spk.get().split(",")) if s]
            try:
                proj.create_project(folder, v_name.get().strip() or folder.name, speakers)
                skip = [x.strip() for x in v_skip.get().split(",") if x.strip()]
                levels = [x.strip() for x in v_levels.get().split(",") if x.strip()] or proj.detect_levels(folder, skip)
                proj.update_project(folder, levels=levels, skip=skip)
            except OSError as exc:
                lbl_err.config(text=f"Could not create the project: {exc}")
                return None
            win.destroy()
            self._set_project(folder)
            return self._project_overview()

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=11, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(bar, "Create Project", create, primary=True).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win, {"folder": v_folder, "name": v_name, "speakers": v_spk, "levels": v_levels, "skip": v_skip, "create": create,
                     "choose": choose, "detect": detect, "error": lbl_err}

    def _open_project_dialog(self):
        start = str(self._project_dir() or self.settings.get("last_add_dir") or Path.home())
        f = self._ask_directory("Open project: choose the study's folder", start)
        return self._open_project_folder(Path(f)) if f else None

    def _open_project_folder(self, folder: Path):
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            messagebox.showerror("Open project", f"The folder is not available:\n{folder}\n\n(An external drive not connected?)", parent=self)
            return None
        f = proj.find_project_file(folder)
        if f is None:
            if not messagebox.askyesno("Open project", f"“{folder.name}” is not a project yet.\n\nMake it one? Transcripts already in "
                                       "it and its subfolders become part of it; nothing is moved.", parent=self):
                return None
            proj.create_project(folder)
            proj.update_project(folder, levels=proj.detect_levels(folder), skip=list(proj.DEFAULT_SKIP))
            f = folder / proj.PROJECT_FILE
        self._set_project(f.parent)
        return self._project_overview()

    # ================================================================== overview
    def _project_overview(self):
        project = self._project_dir()
        if project is None and self.active_doc is not None:
            f = proj.find_project_file(Path(self.active_doc.path).parent)
            if f is not None:
                self._set_project(f.parent)
                project = f.parent
        if project is None:
            return self._project_start_dialog()
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Project — {proj.project_name(project)}")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        top = tk.Frame(win, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self._label(top, proj.project_name(project), font="body_bold", bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._label(top, f"   {project}", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        lbl_total = self._label(win, "", font="small_bold", bg=T["bg_panel"])
        cols = ("length", "checked", "uncertain", "speakers", "cuts", "notes", "modified")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=16, style="Queue.Treeview")
        tree.heading("#0", text="Transcript")
        tree.column("#0", width=320)
        for c, w, title in (("length", 80, "Length"), ("checked", 80, "Checked"), ("uncertain", 90, "Uncertain"),
                            ("speakers", 130, "Speakers"), ("cuts", 70, "Cut"), ("notes", 60, "Notes"), ("modified", 160, "Last change / time")):
            tree.heading(c, text=title)
            tree.column(c, width=w, anchor="center")
        state = {"rows": [], "stop": threading.Event(), "project": project}

        def show(rows):
            state["rows"] = rows
            for item in tree.get_children():
                tree.delete(item)
            ok_rows = [r for r in rows if "error" not in r]
            nodes: Dict[str, List[Dict]] = {}  # level node iid -> rows below it (for the totals)
            for i, r in enumerate(rows):
                parent = ""
                for depth, (_lv, name) in enumerate(r.get("levels") or []):
                    iid = "L:" + "/".join(n for _, n in r["levels"][: depth + 1])
                    if iid not in nodes:
                        tree.insert(parent, tk.END, iid=iid, text=name, open=True)
                        nodes[iid] = []
                    if "error" not in r:
                        nodes[iid].append(r)
                    parent = iid
                if "error" in r:
                    tree.insert(parent, tk.END, iid=str(i), text=f"⚠ {r['name']}", values=("", "", "", "", "", "", "could not read"))
                    continue
                spk = f"{r['speakers']}" + (f" ({r['generic_speakers']} unnamed)" if r["generic_speakers"] else "")
                tree.insert(parent, tk.END, iid=str(i), text=r["name"], open=False, values=(
                    short_ts(r["length_s"])[:-2], f"{r['checked_pct']}%", f"{r['uncertain_pct']}%", spk,
                    short_ts(r["cut_s"])[:-2] if r["cut_s"] else "", r["notes"] or "", r["modified"]))
                for j, ep in enumerate(r.get("episodes") or []):
                    approx = "≈ " if ep.get("approx") else ""
                    tree.insert(str(i), tk.END, iid=f"{i}:{j}", text=f"▸ {ep['type']} {ep.get('label') or ''}",
                                values=(short_ts(ep["end"] - ep["start"])[:-2], "", "", "", "", "",
                                        f"{approx}{short_ts(ep['start'])[:-2]} – {short_ts(ep['end'])[:-2]}"))
            for iid, below in nodes.items():
                length = sum(r["length_s"] for r in below)
                done = sum(r["length_s"] * r["checked_pct"] / 100 for r in below)
                n_ep = sum(len(r.get("episodes") or []) for r in below)
                tree.item(iid, values=(short_ts(length)[:-2], f"{done * 100 / length:.0f}%" if length else "", "",
                                       f"{len(below)} transcript(s)", "", "", f"{n_ep} episode(s)" if n_ep else ""))
            total_len = sum(r["length_s"] for r in ok_rows)
            done = sum(r["length_s"] * r["checked_pct"] / 100 for r in ok_rows)
            speakers = proj.load_project(project / proj.PROJECT_FILE)["speakers"]
            if not rows:
                lbl_total.config(text="No transcripts in this folder yet. Set a task's output to this folder "
                                      "(Task Details ▸ Output), or move finished session folders into it.")
            else:
                lbl_total.config(text=f"{len(ok_rows)} transcript(s)  ·  {short_ts(total_len)[:-2]} recorded  ·  "
                                      f"{(done * 100 / total_len) if total_len else 0:.0f}% checked  ·  "
                                      f"speakers: {', '.join(speakers) or 'none yet (Project speakers…)'}")

        def refresh(background: bool = True):
            state["stop"].set()
            stop = state["stop"] = threading.Event()
            if not background:
                rows = proj.overview(project, stop=stop)
                show(rows)
                return rows
            lbl_total.config(text="Looking for transcripts…")
            results: "_queue.Queue" = _queue.Queue()
            threading.Thread(target=lambda: results.put(proj.overview(project, stop=stop)), daemon=True).start()

            def poll():
                if stop.is_set() or not win.winfo_exists():
                    return
                try:
                    rows = results.get_nowait()
                except _queue.Empty:
                    win.after(120, poll)
                    return
                show(rows)
            win.after(120, poll)
            return None

        def open_selected(event=None):
            sel = tree.selection()
            if not sel or sel[0].startswith("L:"):
                return
            i, _, j = sel[0].partition(":")
            r = state["rows"][int(i)]
            if "error" in r:
                return
            self._autosave_edit(rerender=False)
            self._render_doc(TranscriptDocument.load(r["path"]))
            self.notebook.select(self.tab_preview)
            if j:  # an episode: go to its first segment
                ep = r["episodes"][int(j)]
                idx = self.active_doc.episode_segments(ep)
                if idx:
                    self._goto_segment(idx[0])

        def export_csv():
            if not state["rows"]:
                return
            target = project / "condense-overview.csv"
            proj.write_overview_csv(state["rows"], target)
            self._append_log(f"Project overview saved: {target}", "SUCCESS")
            lbl_total.config(text=lbl_total.cget("text") + f"  ·  saved {target.name}")

        def close():
            state["stop"].set()
            win.destroy()

        self._tool(top, "Refresh", refresh).pack(side=tk.RIGHT)
        lbl_total.pack(anchor="w", pady=(8, 4))
        tree.pack(fill=tk.BOTH, expand=True)
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        self._tool(bar, "Open", open_selected, primary=True).pack(side=tk.LEFT)
        self._tool(bar, "Project speakers…", lambda: self._project_speakers_dialog(project, on_saved=refresh)).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Save as CSV", export_csv).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Settings…", lambda: self._project_settings_dialog(on_saved=refresh)).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", close).pack(side=tk.RIGHT)
        tree.bind("<Double-Button-1>", open_selected)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: (close(), "break")[-1])
        state.update(refresh=refresh, open=open_selected, export=export_csv, tree=tree, lbl=lbl_total, close=close)
        refresh()
        return win, state

    # ================================================================== project speakers
    def _project_speakers_dialog(self, folder: Optional[Path] = None, on_saved=None):
        T = THEME
        folder = Path(folder) if folder else self._project_dir()
        if folder is None:
            return self._project_start_dialog()
        f = proj.find_project_file(folder)
        if f is not None and f.parent != folder.resolve():
            folder = f.parent  # edit the list that applies here
        names = list(proj.load_project(f)["speakers"]) if f else []
        win = tk.Toplevel(self)
        win.title("Project speakers")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, f"Speakers of “{proj.project_name(folder)}”, in key order. They keep the same number key (1–9) and "
                         "colour in every transcript of the project.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=380, justify=tk.LEFT).pack(anchor="w")
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

    # ================================================================== project settings
    def _project_settings_dialog(self, folder: Optional[Path] = None, on_saved=None):
        T = THEME
        folder = Path(folder) if folder else self._project_dir()
        if folder is None:
            return self._project_start_dialog()
        cfg = proj.project_settings(folder)
        win = tk.Toplevel(self)
        win.title("Project settings")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, f"Project settings — {folder}", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
        v_name = tk.StringVar(value=cfg["name"])
        v_levels = tk.StringVar(value=", ".join(cfg["levels"]))
        v_skip = tk.StringVar(value=", ".join(cfg["skip"]))
        v_types = tk.StringVar(value=", ".join(cfg["episode_types"]))
        rows = (("Name", v_name, ""),
                ("Levels", v_levels, "Folder levels below the project, e.g. Day, Group. The overview groups transcripts by them."),
                ("Skip folders", v_skip, "Never opened or listed (consent forms, unsorted material). * matches anything."),
                ("Episode types", v_types, "Kinds of episodes, e.g. Interview, Phase, Plenary. Each gets its own colour."))
        for r, (label, var, hint) in enumerate(rows):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1 + 2 * r, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            self._entry(win, var, font="small", width=50).grid(row=1 + 2 * r, column=1, sticky="we", pady=(6, 0))
            if hint:
                self._label(win, hint, font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=440, justify=tk.LEFT
                            ).grid(row=2 + 2 * r, column=1, sticky="w")
        self._tool(win, "Detect", lambda: v_levels.set(", ".join(proj.detect_levels(folder, [x.strip() for x in v_skip.get().split(",") if x.strip()])))
                   ).grid(row=3, column=2, padx=(6, 0), pady=(6, 0))

        def save():
            split = (lambda v: [x.strip() for x in v.get().split(",") if x.strip()])
            proj.update_project(folder, name=v_name.get().strip() or folder.name, levels=split(v_levels), skip=split(v_skip),
                                episode_types=split(v_types) or ["Interview"])
            self._refresh_project_ui()
            self._append_log(f"Project settings saved: {folder / proj.PROJECT_FILE}", "INFO")
            win.destroy()
            if self.active_doc is not None:
                self._rerender()
            on_saved and on_saved()

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=20, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(bar, "Save", save, primary=True).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win, {"name": v_name, "levels": v_levels, "skip": v_skip, "types": v_types, "save": save}
