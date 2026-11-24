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
        if self.__dict__.get("pp_tree") is not None:
            self._refresh_project_panel()
            if folder is not None:
                self.left_tabs.select(self.tab_project)
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
        pm.add_command(label="Attributes…", command=lambda: self._attributes_dialog(self._project_dir()) if self._project_dir() else self._project_start_dialog())
        pm.add_command(label="Names & Pseudonyms…", command=lambda: self._names_dialog(self._project_dir()) if self._project_dir() else self._project_start_dialog())
        pm.add_command(label="Speakers (Project and Groups)…", command=lambda: self._project_speakers_dialog())
        pm.add_command(label="Project Settings…", command=lambda: self._project_settings_dialog())
        pm.add_command(label="Tidy Unsorted Folders…", command=lambda: self._tidy_dialog())
        pm.add_command(label="Storage (Backups, Leftovers)…", command=lambda: self._storage_dialog())
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
                sug = proj.suggest_settings(folder, skip)
                levels = [x.strip() for x in v_levels.get().split(",") if x.strip()] or sug["levels"]
                structure = sug["structure"] if levels == sug["levels"] else None
                proj.update_project(folder, levels=levels, skip=skip, **({"structure": structure} if structure else {}))
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
            proj.update_project(folder, **proj.suggest_settings(folder))
            f = folder / proj.PROJECT_FILE
        self._set_project(f.parent)
        return self._project_overview()

    # ================================================================== overview
    # ================================================================== project speakers
    def _project_speakers_dialog(self, folder: Optional[Path] = None, on_saved=None, group: Optional[str] = None):
        """Speakers of the project (in every group, e.g. facilitators) and of each group (its children). A transcript's
        number keys are the project's speakers first, then its group's."""
        T = THEME
        folder = Path(folder) if folder else self._project_dir()
        if folder is None:
            return self._project_start_dialog()
        f = proj.find_project_file(folder)
        if f is not None and f.parent != folder.resolve():
            folder = f.parent  # edit the list that applies here
        folder = folder.resolve()
        ALL = "Whole project (in every group)"
        groups = proj.groups_in(folder) if f else []
        if group is None and self.active_doc is not None and f is not None:
            group = proj.group_key(folder, self.active_doc.path)
        lists = {ALL: list(proj.load_project(f)["speakers"]) if f else []}
        for g in groups:
            lists[g] = proj.group_speakers(folder, g)
        win = tk.Toplevel(self)
        win.title("Speakers")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Project speakers appear in every group (e.g. facilitators). Group speakers only in that group's "
                         "transcripts, on every day (its children). Number keys: the project's first, then the group's.",
                    font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=420, justify=tk.LEFT).pack(anchor="w")
        v_scope = tk.StringVar(value=group if group in lists else ALL)
        srow = tk.Frame(win, bg=T["bg_panel"])
        srow.pack(fill=tk.X, pady=(8, 0))
        self._label(srow, "Speakers of", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        cmb = ttk.Combobox(srow, textvariable=v_scope, values=[ALL] + groups, state="readonly", width=30, font=self.f["small"])
        cmb.pack(side=tk.LEFT, padx=(6, 0))
        lst = tk.Listbox(win, height=10, width=40, bg=T["bg_input"], fg=T["fg_primary"], font=self.f["small"], relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=T["border"], exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True, pady=8)
        v_new = tk.StringVar(value="")
        row = tk.Frame(win, bg=T["bg_panel"])
        row.pack(fill=tk.X)
        ent = self._entry(row, v_new, font="small", width=24)
        ent.pack(side=tk.LEFT)
        st = {"lists": lists, "changed": set()}

        def cur() -> List[str]:
            return st["lists"][v_scope.get()]

        def show(*_):
            lst.delete(0, tk.END)
            offset = 0 if v_scope.get() == ALL else len(st["lists"][ALL])
            if offset:
                lst.insert(tk.END, f"   (1–{offset}: the project's {', '.join(st['lists'][ALL])})")
                lst.itemconfig(0, foreground=T["fg_dim"])
            for i, n in enumerate(cur()):
                k = offset + i + 1
                lst.insert(tk.END, f"{k if k <= 9 else ' '}  {n}")

        def index_of_selection() -> Optional[int]:
            sel = lst.curselection()
            if not sel:
                return None
            i = sel[0] - (1 if v_scope.get() != ALL and st["lists"][ALL] else 0)
            return i if 0 <= i < len(cur()) else None

        def add(name=None):
            n = " ".join((name if name is not None else v_new.get()).split())
            if n and n not in cur() and (v_scope.get() == ALL or n not in st["lists"][ALL]):
                cur().append(n)
                st["changed"].add(v_scope.get())
            v_new.set("")
            show()

        def move(d):
            i = index_of_selection()
            if i is None:
                return
            j = i + d
            if 0 <= j < len(cur()):
                cur()[i], cur()[j] = cur()[j], cur()[i]
                st["changed"].add(v_scope.get())
                show()
                lst.selection_set(j + (1 if v_scope.get() != ALL and st["lists"][ALL] else 0))

        def remove():
            i = index_of_selection()
            if i is not None:
                del cur()[i]
                st["changed"].add(v_scope.get())
                show()

        def from_transcripts():
            scope = v_scope.get()
            for p in proj.transcripts_under(folder, skip=proj.project_settings(folder)["skip"]):
                if scope != ALL and proj.group_key(folder, p) != scope:
                    continue
                try:
                    for n in TranscriptDocument.load(p).roster():
                        if not n.startswith(proj.GENERIC_SPEAKER):
                            add(n)
                except Exception:
                    pass

        def save():
            for scope in st["changed"]:
                if scope == ALL:
                    proj.save_project(folder, st["lists"][ALL])
                else:
                    proj.save_group_speakers(folder, scope, st["lists"][scope])
            self._forget_project_cache()
            self._append_log(f"Speakers saved for: {', '.join(sorted(st['changed'])) or 'nothing changed'}", "INFO")
            win.destroy()
            if self.active_doc is not None:
                self._rerender()
            on_saved and on_saved()

        cmb.bind("<<ComboboxSelected>>", show)
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
        st.update(add=add, move=move, remove=remove, from_transcripts=from_transcripts, save=save, lst=lst, scope=v_scope,
                  show=show, groups=groups, ALL=ALL, select=lambda i: lst.selection_set(i))
        st["names"] = lists[ALL]  # (kept for older callers: the project's list)
        return win, st

    # ================================================================== project settings
    def _project_settings_dialog(self, folder: Optional[Path] = None, on_saved=None):
        from condense.core import structure as stlib
        T = THEME
        folder = Path(folder) if folder else self._project_dir()
        if folder is None:
            return self._project_start_dialog()
        cfg = proj.project_settings(folder)
        data = proj.load_project(folder / proj.PROJECT_FILE)
        cur = data.get("structure") or proj.suggest_settings(folder, cfg["skip"])["structure"]
        preset = next((k for k, v in stlib.PRESETS.items() if v == cur), stlib.CUSTOM)
        win = tk.Toplevel(self)
        win.title("Project settings")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, f"Project settings — {folder}", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
        v_name = tk.StringVar(value=cfg["name"])
        v_preset = tk.StringVar(value=preset)
        v_sess = tk.StringVar(value=cur.get("sessions", ""))
        v_int = tk.StringVar(value=cur.get("interviews", ""))
        v_skip = tk.StringVar(value=", ".join(cfg["skip"]))
        v_types = tk.StringVar(value=", ".join(cfg["episode_types"]))
        v_names = tk.StringVar(value=proj.format_episode_names(cfg.get("episode_names") or {}))
        lbl_preview = self._label(win, "", font="mono_small", fg=T["fg_secondary"], bg=T["bg_panel"], justify=tk.LEFT)
        grid_rows = [("Name", v_name, ""),
                     ("Session folders", v_sess, "Where session recordings are. {Day}, {Group} … are your folder levels."),
                     ("Interview clips", v_int, "Folder and name of cut-out interviews. {group}, {day} … are the levels' numbers; "
                                                "{n} counts a group's interviews across all days."),
                     ("Skip folders", v_skip, "Never opened or listed (consent forms, unsorted material). * matches anything."),
                     ("Episode types", v_types, "Kinds of episodes, e.g. Interview, Phase, Plenary. Each gets its own colour."),
                     ("Episode names", v_names, "Optional: names for other types, numbered per group like interviews, e.g. "
                                                "Phase: G{group}D{day}_P{n}; Plenary: G{group}D{day}_Plenum{n}. "
                                                "Interviews are named like their clips (above).")]
        r = 1
        self._label(win, "Structure", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        cmb = ttk.Combobox(win, textvariable=v_preset, values=list(stlib.PRESETS) + [stlib.CUSTOM], state="readonly", width=40, font=self.f["small"])
        cmb.grid(row=r, column=1, sticky="w", pady=(8, 0))
        r += 1
        for label, var, hint in grid_rows:
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            self._entry(win, var, font="small", width=56).grid(row=r, column=1, sticky="we", pady=(6, 0))
            r += 1
            if hint:
                self._label(win, hint, font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).grid(row=r, column=1, sticky="w")
                r += 1
            if label == "Interview clips":
                lbl_preview.grid(row=r, column=1, sticky="w", pady=(4, 0))
                r += 1

        def preview(*_):
            st = stlib.Structure(v_sess.get(), v_int.get())
            probs = st.problems()
            if probs:
                lbl_preview.config(text="⚠ " + "\n⚠ ".join(probs), fg=T["warn"])
            else:
                lbl_preview.config(text=f"Levels: {' ▸ '.join(st.levels) or '(none)'}\nSession:   {st.example('sessions')}\n"
                                        f"Interview: {st.example()}", fg=T["fg_secondary"])
            if (not busy["preset"] and v_preset.get() != stlib.CUSTOM
                    and stlib.PRESETS.get(v_preset.get()) != {"sessions": v_sess.get(), "interviews": v_int.get()}):
                v_preset.set(stlib.CUSTOM)

        busy = {"preset": False}

        def on_preset(_e=None):
            p = stlib.PRESETS.get(v_preset.get())
            if p:
                busy["preset"] = True
                v_sess.set(p["sessions"])
                v_int.set(p["interviews"])
                busy["preset"] = False
                preview()

        def detect():
            sug = proj.suggest_settings(folder, [x.strip() for x in v_skip.get().split(",") if x.strip()])["structure"]
            v_sess.set(sug["sessions"])
            v_int.set(sug["interviews"])

        cmb.bind("<<ComboboxSelected>>", on_preset)
        for var in (v_sess, v_int):
            var.trace_add("write", preview)
        self._tool(win, "Read from my folders", detect).grid(row=1, column=2, padx=(6, 0), pady=(8, 0))
        lbl_err = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl_err.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        def save():
            st = stlib.Structure(v_sess.get(), v_int.get())
            if st.problems():
                lbl_err.config(text="Fix the structure first (see the warnings above).")
                return None
            split = (lambda v: [x.strip() for x in v.get().split(",") if x.strip()])
            names = proj.parse_episode_names(v_names.get())
            bad = [f"{k}: {p}" for k, v in names.items() for p in st.for_names(v).problems()]
            if bad:
                lbl_err.config(text="Episode names: " + "; ".join(bad).replace("Interview clips: ", ""))
                return None
            types = split(v_types) or ["Interview"]
            types += [k for k in names if k not in types]  # a type given a name is a type
            proj.update_project(folder, name=v_name.get().strip() or folder.name, levels=st.levels, skip=split(v_skip),
                                episode_types=types, episode_names=names,
                                structure={"sessions": st.sessions, "interviews": st.interviews})
            self._refresh_project_ui()
            self._append_log(f"Project settings saved: {folder / proj.PROJECT_FILE}", "INFO")
            win.destroy()
            if self.active_doc is not None:
                self._rerender()
            on_saved and on_saved()
            return True

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=r, column=0, columnspan=3, sticky="e", pady=(14, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(bar, "Save", save, primary=True).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        preview()
        return win, {"name": v_name, "preset": v_preset, "sessions": v_sess, "interviews": v_int, "skip": v_skip, "types": v_types,
                     "names": v_names, "save": save, "on_preset": on_preset, "detect": detect, "preview": lbl_preview, "error": lbl_err}

    # ================================================================== tidy: a plan first, moves only when confirmed
    def _tidy_dialog(self, folder: Optional[Path] = None):
        from condense.core import structure as stlib
        T = THEME
        folder = Path(folder) if folder else self._project_dir()
        if folder is None:
            return self._project_start_dialog()
        st = proj.project_settings(folder)["structure"]
        win = tk.Toplevel(self)
        win.title("Tidy unsorted folders")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Where the files in your …Unsorted folders belong, read from their names and the project's structure. "
                         "This is only a plan: nothing is moved unless you click Move and confirm. Every move is recorded in "
                         f"{stlib.MOVES_FILE} and can be undone here.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=1060, justify=tk.LEFT).pack(anchor="w")
        cols = ("dst", "why")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=12, style="Queue.Treeview")
        tree.heading("#0", text="File")
        tree.column("#0", width=200)
        tree.heading("dst", text="Would go to")
        tree.column("dst", width=440)
        tree.heading("why", text="Why")
        tree.column("why", width=440)
        tree.pack(fill=tk.BOTH, expand=True, pady=8)
        lbl = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        lbl.pack(anchor="w")
        state: Dict = {"plan": []}

        def rel(p):
            try:
                return str(Path(p).relative_to(folder.resolve()))
            except ValueError:
                return str(p)

        def refresh():
            for item in tree.get_children():
                tree.delete(item)
            if st is None:
                state["plan"] = []
                lbl.config(text="The project has no folder structure yet: set it in Project ▸ Project Settings.")
                return []
            state["plan"] = st.tidy_plan(folder)
            for i, it in enumerate(state["plan"]):
                extra = f"  (+ folder {it['companion'].name})" if it.get("companion") else ""
                tree.insert("", tk.END, iid=str(i), text=("✓ " if it["ok"] else "· ") + it["src"].name,
                            values=(rel(it["dst"].parent) + extra if it["dst"] else "—", it["reason"]))
            movable = sum(1 for it in state["plan"] if it["ok"])
            lbl.config(text=f"{movable} file(s) can be moved; {len(state['plan']) - movable} stay where they are." if state["plan"]
                       else "Nothing to tidy: no files in …Unsorted folders.")
            btn_move.config(text=f"Move {movable} file(s)…")
            return state["plan"]

        def move():
            items = [it for it in state["plan"] if it["ok"]]
            if not items:
                return []
            if not messagebox.askyesno("Tidy unsorted folders", f"Move {len(items)} file(s) into their folders?\n\n"
                                       "Nothing is deleted or overwritten, and you can undo this here.", parent=win):
                return []
            done = stlib.apply_moves(folder, items)
            self._append_log(f"Tidied: moved {len(done)} item(s); undo in Project ▸ Tidy Unsorted Folders.", "SUCCESS")
            refresh()
            return done

        def undo():
            undone = stlib.undo_last_moves(folder)
            self._append_log(f"Undid the last tidy: {len(undone)} item(s) moved back." if undone else "Nothing to undo.", "INFO")
            refresh()
            return undone

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        btn_move = self._tool(bar, "Move…", move, primary=True)
        btn_move.pack(side=tk.LEFT)
        self._tool(bar, "Undo last move", undo).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Refresh", refresh).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        refresh()
        return win, {"plan": lambda: state["plan"], "move": move, "undo": undo, "refresh": refresh, "label": lbl}
