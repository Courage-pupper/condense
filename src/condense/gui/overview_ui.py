"""Project ▸ Overview: every transcript of the study as one table — day, group, type, date, length, how much is checked
and coded, speakers, and the project's own attributes. Sort by a column, filter by typing (e.g. "group 3 interview
clay"), set attributes for many transcripts at once, keep the names list (rename throughout, pseudonyms) and export
copies with pseudonyms."""
import hashlib
import queue as _queue
import re
import threading
from datetime import date

from condense.core import attributes as attrlib
from condense.core import names as nameslib
from condense.core import project as proj
from condense.gui._common import *  # noqa: F401,F403


class OverviewMixin:
    # ================================================================== the table
    def _overview_cache(self, project: Path) -> "proj.RowCache":
        key = hashlib.sha1(str(Path(project).resolve()).encode("utf-8")).hexdigest()[:16]
        caches = self.__dict__.setdefault("_overview_caches", {})
        if key not in caches:
            caches[key] = proj.RowCache(APP_SUPPORT_DIR / "cache" / "overview" / f"{key}.json")
        return caches[key]

    def _overview_rows(self, project: Path, stop=None) -> List[Dict]:
        return proj.overview(project, stop=stop, cache=self._overview_cache(project))

    @staticmethod
    def _row_value(r: Dict, col: str):
        """A cell's value for display, filtering and sorting."""
        if col == "name":
            return r.get("name", "")
        if col == "length":
            return r.get("length_s", 0.0)
        if col == "checked":
            return r.get("checked_pct", 0)
        if col == "codings":
            return r.get("codings", 0)
        if col == "speakers":
            return ", ".join(n for n in r.get("speaker_names") or [] if not n.startswith(proj.GENERIC_SPEAKER))
        if col.startswith("a:"):
            name = col[2:]
            return (r.get("auto") or {}).get(name) or (r.get("attributes") or {}).get(name) or ""
        return ""

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
        frow = tk.Frame(win, bg=T["bg_panel"])
        frow.pack(fill=tk.X, pady=(8, 0))
        self._label(frow, "Filter", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        v_filter = tk.StringVar(value="")
        self._entry(frow, v_filter, font="small", width=40).pack(side=tk.LEFT, padx=6)
        self._label(frow, "e.g.  group 3 interview   ·   day 2   ·   clay", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        lbl_total = self._label(win, "", font="small_bold", bg=T["bg_panel"])
        lbl_total.pack(anchor="w", pady=(8, 4))
        body = tk.Frame(win, bg=T["bg_panel"])
        body.pack(fill=tk.BOTH, expand=True)
        state: Dict = {"rows": [], "shown": [], "stop": threading.Event(), "project": project, "sort": ("a:Day", False), "iids": {}}

        def columns() -> List[Tuple[str, str, int]]:
            settings = proj.project_settings(project)
            cols = [("name", "Transcript", 220)]
            cols += [(f"a:{lv}", lv, 120) for lv in settings["levels"]]
            cols += [("a:Type", "Type", 80), ("a:Date", "Date", 90), ("length", "Length", 70), ("checked", "Checked", 70),
                     ("codings", "Codings", 70), ("speakers", "Speakers", 180)]
            cols += [(f"a:{d['name']}", d["name"], 110) for d in attrlib.definitions(proj.load_project(project / proj.PROJECT_FILE))]
            return cols

        def build_tree():
            for w in body.winfo_children():
                w.destroy()
            cols = columns()
            state["cols"] = cols
            tree = ttk.Treeview(body, columns=[c for c, _, _ in cols[1:]], show="tree headings", height=18, style="Queue.Treeview",
                                selectmode="extended")
            tree.heading("#0", text=cols[0][1], command=lambda: sort_by("name"))
            tree.column("#0", width=cols[0][2], stretch=True)
            for c, title, w in cols[1:]:
                tree.heading(c, text=title, command=lambda c=c: sort_by(c))
                tree.column(c, width=w, anchor="w" if c.startswith("a:") or c == "speakers" else "center", stretch=c == "speakers")
            sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            tree.bind("<Double-Button-1>", open_selected)
            tree.bind("<Return>", open_selected)
            state["tree"] = tree
            return tree

        def cell(r: Dict, c: str) -> str:
            v = self._row_value(r, c)
            if c == "length":
                return short_ts(v)[:-2] if v else ""
            if c == "checked":
                return f"{v}%"
            if c == "codings":
                return str(v) if v else ""
            return str(v)

        def matches(r: Dict, words: List[str]) -> bool:
            """Every word of the filter must start a word of the row; a word followed by a number must be followed by
            it there too ("group 3" is Group 3, not Group 1 of a 13-minute session)."""
            if not words:
                return True
            hay = " | ".join(cell(r, c) for c, _, _ in state["cols"]).lower()
            i = 0
            while i < len(words):
                w = words[i]
                if i + 1 < len(words) and not w.isdigit() and words[i + 1].isdigit():
                    if not re.search(r"(?<![\w])" + re.escape(w) + r"\s+" + re.escape(words[i + 1]) + r"(?!\d)", hay):
                        return False
                    i += 2
                    continue
                if not re.search(r"(?<![\w])" + re.escape(w), hay):
                    return False
                i += 1
            return True

        def sort_by(col: str):
            cur, desc = state["sort"]
            state["sort"] = (col, not desc if cur == col else False)
            show(state["rows"])

        def sort_key(r: Dict):
            col = state["sort"][0]
            v = self._row_value(r, col)
            tie = [proj.natural_key(str(self._row_value(r, c))) for c in ("a:Day", "a:Group", "name")]
            return ((0, v) if isinstance(v, (int, float)) else (1, proj.natural_key(str(v))), tie)

        def show(rows):
            state["rows"] = rows
            tree = state.get("tree") or build_tree()
            for item in tree.get_children():
                tree.delete(item)
            words = v_filter.get().lower().split()
            ok_rows = [r for r in rows if "error" not in r]
            shown = sorted((r for r in ok_rows if matches(r, words)), key=sort_key, reverse=state["sort"][1])
            state["shown"] = shown
            state["iids"] = {}
            for i, r in enumerate(shown):
                iid = f"r{i}"
                state["iids"][iid] = r
                tree.insert("", tk.END, iid=iid, text=r["name"], values=[cell(r, c) for c, _, _ in state["cols"][1:]])
                for j, ep in enumerate(r.get("episodes") or []):
                    eid = f"{iid}:{j}"
                    state["iids"][eid] = dict(r, episode=ep)
                    tree.insert(iid, tk.END, iid=eid, text=f"▸ {ep['type']} {ep.get('label') or ''}",
                                values=[""] * (len(state["cols"]) - 1))
            for r in rows:
                if "error" in r:
                    tree.insert("", tk.END, text=f"⚠ {r['name']} (could not read)", values=[""] * (len(state["cols"]) - 1))
            total_len = sum(r["length_s"] for r in shown)
            done = sum(r["length_s"] * r["checked_pct"] / 100 for r in shown)
            speakers = proj.load_project(project / proj.PROJECT_FILE)["speakers"]
            if not rows:
                lbl_total.config(text="No transcripts in this folder yet. Set a task's output to this folder "
                                      "(Task Details ▸ Output), or move finished session folders into it.")
                return
            count = f"{len(shown)} of {len(ok_rows)} transcript(s)" if words else f"{len(ok_rows)} transcript(s)"
            lbl_total.config(text=f"{count}  ·  {short_ts(total_len)[:-2]} recorded  ·  "
                                  f"{(done * 100 / total_len) if total_len else 0:.0f}% checked  ·  "
                                  f"{sum(r.get('codings', 0) for r in shown)} codings  ·  "
                                  f"speakers: {', '.join(speakers) or 'none yet (Project speakers…)'}")

        def refresh(background: bool = True):
            state["stop"].set()
            stop = state["stop"] = threading.Event()
            state["tree"] = None
            if not background:
                rows = self._overview_rows(project, stop=stop)
                show(rows)
                return rows
            lbl_total.config(text="Looking for transcripts…")
            results: "_queue.Queue" = _queue.Queue()
            threading.Thread(target=lambda: results.put(self._overview_rows(project, stop=stop)), daemon=True).start()

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

        def selected() -> List[Dict]:
            tree = state.get("tree")
            sel = tree.selection() if tree is not None else ()
            out = []
            for iid in sel:
                r = state["iids"].get(iid.split(":")[0])
                if r is not None and r not in out:
                    out.append(r)
            return out

        def open_selected(event=None):
            tree = state.get("tree")
            sel = tree.selection() if tree is not None else ()
            if not sel or sel[0] not in state["iids"]:
                return
            r = state["iids"][sel[0]]
            self._open_at(r["path"])
            if r.get("episode") and self.active_doc is not None:
                idx = self.active_doc.episode_segments(r["episode"])
                if idx:
                    self._open_at(r["path"], idx[0])

        def export_csv():
            if not state["rows"]:
                return
            target = project / "condense-overview.csv"
            proj.write_overview_csv(state["shown"] or state["rows"], target,
                                    extra=[d["name"] for d in attrlib.definitions(proj.load_project(project / proj.PROJECT_FILE))])
            self._append_log(f"Project overview saved: {target}", "SUCCESS")
            lbl_total.config(text=lbl_total.cget("text") + f"  ·  saved {target.name}")

        def set_attr():
            rows = selected() or state["shown"]
            if rows:
                self._set_attribute_dialog(project, [r["path"] for r in rows], on_done=lambda: refresh(background=False))

        def close():
            state["stop"].set()
            win.destroy()

        v_filter.trace_add("write", lambda *a: show(state["rows"]))
        self._tool(top, "Refresh", refresh).pack(side=tk.RIGHT)
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        self._tool(bar, "Open", open_selected, primary=True).pack(side=tk.LEFT)
        self._tool(bar, "Set attribute…", set_attr).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Attributes…", lambda: self._attributes_dialog(project, on_saved=lambda: refresh(background=False))).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Names & pseudonyms…", lambda: self._names_dialog(project, [r["path"] for r in (selected() or state["shown"])],
                                                                          on_saved=lambda: refresh(background=False))).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Project speakers…", lambda: self._project_speakers_dialog(project, on_saved=refresh)).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Save as CSV", export_csv).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Settings…", lambda: self._project_settings_dialog(on_saved=refresh)).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", close).pack(side=tk.RIGHT)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: (close(), "break")[-1])
        build_tree()
        state.update(refresh=refresh, open=open_selected, export=export_csv, lbl=lbl_total, close=close, filter=v_filter,
                     sort_by=sort_by, selected=selected, show=show)
        refresh()
        return win, state

    # ================================================================== attributes
    def _apply_attribute(self, paths: List[Path], name: str, value: str) -> int:
        """Set an attribute on transcripts (the open one through its undo history). Returns how many changed."""
        n = 0
        active = self.active_doc
        for p in paths:
            try:
                if active is not None and Path(active.path).resolve() == Path(p).resolve():
                    self._autosave_edit(rerender=False)
                    if attrlib.set_value(active, name, value) and self._commit(f"{name}: {value or '(none)'}") is not None:
                        n += 1
                    continue
                doc = TranscriptDocument.load(Path(p))
                if attrlib.set_value(doc, name, value):
                    doc.save(backup=True)
                    n += 1
            except Exception as exc:
                self._append_log(f"Could not set {name} on {Path(p).name}: {exc}", "ERROR")
        return n

    def _set_attribute_dialog(self, project: Path, paths: List[Path], on_done=None):
        T = THEME
        defs = attrlib.definitions(proj.load_project(project / proj.PROJECT_FILE))
        win = tk.Toplevel(self)
        win.title("Set attribute")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, f"For {len(paths)} transcript(s)", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=2, sticky="w")
        v_name = tk.StringVar(value=defs[0]["name"] if defs else "")
        v_val = tk.StringVar(value="")
        self._label(win, "Attribute", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1, column=0, sticky="w", pady=(10, 0))
        cb_name = ttk.Combobox(win, textvariable=v_name, values=[d["name"] for d in defs], width=28, font=self.f["small"])
        cb_name.grid(row=1, column=1, sticky="we", padx=(8, 0), pady=(10, 0))
        self._label(win, "Value", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=2, column=0, sticky="w", pady=(6, 0))
        cb_val = ttk.Combobox(win, textvariable=v_val, width=28, font=self.f["small"])
        cb_val.grid(row=2, column=1, sticky="we", padx=(8, 0), pady=(6, 0))
        self._label(win, "A new attribute name is added to the project. An empty value removes it from these transcripts.",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=360, justify=tk.LEFT).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        lbl = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl.grid(row=4, column=0, columnspan=2, sticky="w")

        def offer(*_):
            name = v_name.get().strip()
            d = next((d for d in defs if d["name"] == name), None)
            rows = [proj.overview_row(Path(p)) for p in paths[:50]]
            cb_val.configure(values=(d["choices"] if d and d["choices"] else []) + [v for v in attrlib.used_values(rows, name)
                                                                                     if not d or v not in d["choices"]])

        def apply():
            name = " ".join(v_name.get().split())
            if not name:
                lbl.config(text="Name the attribute.")
                return None
            if name in attrlib.AUTO:
                lbl.config(text=f"{name} comes from the folders and cannot be set here.")
                return None
            if all(d["name"] != name for d in defs):
                proj.update_project(project, attributes=defs + [{"name": name, "choices": []}])
            n = self._apply_attribute(paths, name, v_val.get())
            self.lbl_status.config(text=f"{name} = {attrlib.clean_value(v_val.get()) or '(none)'} for {n} transcript(s).")
            win.destroy()
            if on_done:
                on_done()
            return n

        v_name.trace_add("write", offer)
        offer()
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT)
        self._tool(bar, f"Set for {len(paths)}", apply, primary=True).pack(side=tk.RIGHT, padx=(0, 6))
        return win, {"name": v_name, "value": v_val, "apply": apply}

    def _attributes_dialog(self, project: Path, on_saved=None):
        """The project's own attributes: names and (optionally) the values to choose from."""
        T = THEME
        defs = attrlib.definitions(proj.load_project(project / proj.PROJECT_FILE))
        win = tk.Toplevel(self)
        win.title("Attributes")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Facts about each transcript to filter and compare by — like MAXQDA's document variables. Day, "
                         "group, type and date come from the folders; add your own here (Activity, Condition, "
                         "Facilitator …), optionally with the values to choose from.", font="small", fg=T["fg_secondary"],
                    bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).pack(anchor="w")
        lst = tk.Listbox(win, height=8, font=self.f["small"], bg=T["bg_input"], fg=T["fg_primary"], selectbackground="#3b4a63",
                         relief=tk.FLAT, highlightthickness=0, exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True, pady=(8, 4))
        form = tk.Frame(win, bg=T["bg_panel"])
        form.pack(fill=tk.X)
        v_name, v_choices = tk.StringVar(value=""), tk.StringVar(value="")
        self._label(form, "Name", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=0, column=0, sticky="w")
        self._entry(form, v_name, font="small", width=30).grid(row=0, column=1, sticky="we", padx=6)
        self._label(form, "Values (optional, comma-separated)", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._entry(form, v_choices, font="small", width=30).grid(row=1, column=1, sticky="we", padx=6, pady=(4, 0))
        lbl = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        lbl.pack(anchor="w", pady=(4, 0))
        st = {"defs": defs}

        def fill():
            lst.delete(0, tk.END)
            for d in st["defs"]:
                lst.insert(tk.END, d["name"] + (f"   ({', '.join(d['choices'])})" if d["choices"] else ""))

        def pick(_e=None):
            sel = lst.curselection()
            if sel:
                d = st["defs"][sel[0]]
                v_name.set(d["name"])
                v_choices.set(", ".join(d["choices"]))

        def save_defs():
            proj.update_project(project, attributes=st["defs"])
            fill()
            if on_saved:
                on_saved()

        def follow(old: str, new: Optional[str]) -> int:
            n = 0
            for p in proj.transcripts_under(project):
                if self.active_doc is not None and Path(self.active_doc.path).resolve() == Path(p).resolve():
                    if attrlib.rename_key(self.active_doc, old, new) and self._commit(f"Attribute {old} → {new or '(deleted)'}") is not None:
                        n += 1
                    continue
                try:
                    doc = TranscriptDocument.load(p)
                    if attrlib.rename_key(doc, old, new):
                        doc.save(backup=True)
                        n += 1
                except Exception as exc:
                    self._append_log(f"Could not change {Path(p).name}: {exc}", "ERROR")
            return n

        def add_or_update():
            name = " ".join(v_name.get().split())
            if not name or name in attrlib.AUTO:
                lbl.config(text="Choose a name (Day, Group, Type and Date come from the folders).")
                return None
            choices = [c.strip() for c in v_choices.get().split(",") if c.strip()]
            sel = lst.curselection()
            old = st["defs"][sel[0]]["name"] if sel else None
            if old is not None and old != name:
                if any(d["name"] == name for d in st["defs"]):
                    lbl.config(text=f"There is already an attribute {name}.")
                    return None
                n = follow(old, name)
                st["defs"][sel[0]] = {"name": name, "choices": choices}
                lbl.config(text=f"Renamed {old} to {name} (in {n} transcript(s)).")
            elif old is not None:
                st["defs"][sel[0]] = {"name": name, "choices": choices}
                lbl.config(text=f"Saved {name}.")
            elif any(d["name"] == name for d in st["defs"]):
                idx = next(i for i, d in enumerate(st["defs"]) if d["name"] == name)
                st["defs"][idx] = {"name": name, "choices": choices}
                lbl.config(text=f"Saved {name}.")
            else:
                st["defs"].append({"name": name, "choices": choices})
                lbl.config(text=f"Added {name}.")
            save_defs()
            return True

        def delete():
            sel = lst.curselection()
            if not sel:
                return None
            name = st["defs"][sel[0]]["name"]
            if not messagebox.askyesno("Delete attribute", f"Delete {name} and its values in every transcript?", parent=win):
                return None
            n = follow(name, None)
            del st["defs"][sel[0]]
            save_defs()
            lbl.config(text=f"Deleted {name} (from {n} transcript(s)).")
            return n

        def new():
            lst.selection_clear(0, tk.END)
            v_name.set("")
            v_choices.set("")

        lst.bind("<<ListboxSelect>>", pick)
        fill()
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(10, 0))
        self._tool(bar, "New", new).pack(side=tk.LEFT)
        self._tool(bar, "Save", add_or_update, primary=True).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Delete…", delete).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        return win, {"name": v_name, "choices": v_choices, "save": add_or_update, "delete": delete, "new": new, "list": lst, "state": st}

    # ================================================================== names and pseudonyms
    def _project_people(self, project: Path, rows: Optional[List[Dict]] = None) -> List[str]:
        data = proj.load_project(project / proj.PROJECT_FILE)
        people = list(data.get("speakers") or [])
        for lst in (data.get("group_speakers") or {}).values():
            people += lst
        for r in rows if rows is not None else self._overview_rows(project):
            people += r.get("speaker_names") or []
        return people

    def _names_dialog(self, project: Path, paths: Optional[List[Path]] = None, on_saved=None):
        """The study's names: rename someone throughout, give pseudonyms, export copies with pseudonyms."""
        T = THEME
        rows = self._overview_rows(project)
        data = proj.load_project(project / proj.PROJECT_FILE)
        st = {"entries": nameslib.merge_people(nameslib.name_list(data), self._project_people(project, rows)),
              "paths": [Path(p) for p in (paths or [r["path"] for r in rows if "error" not in r])]}
        win = tk.Toplevel(self)
        win.title("Names & pseudonyms")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Everyone in the study, from the speaker lists and transcripts. Rename throughout changes a name in "
                         "every transcript (speakers and where it is said); pseudonyms change nothing here and are used "
                         "by exports with pseudonyms.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=560, justify=tk.LEFT).pack(anchor="w")
        tree = ttk.Treeview(win, columns=("pseudonym",), show="tree headings", height=12, style="Queue.Treeview", selectmode="browse")
        tree.heading("#0", text="Name")
        tree.heading("pseudonym", text="Pseudonym")
        tree.column("#0", width=240)
        tree.column("pseudonym", width=200)
        tree.pack(fill=tk.BOTH, expand=True, pady=(8, 4))
        form = tk.Frame(win, bg=T["bg_panel"])
        form.pack(fill=tk.X)
        v_ps, v_prefix = tk.StringVar(value=""), tk.StringVar(value="P")
        self._label(form, "Pseudonym", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        ent = self._entry(form, v_ps, font="small", width=22)
        ent.pack(side=tk.LEFT, padx=6)
        lbl = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=560, justify=tk.LEFT)

        def fill():
            sel = tree.selection()
            for i in tree.get_children():
                tree.delete(i)
            for k, e in enumerate(st["entries"]):
                tree.insert("", tk.END, iid=str(k), text=e["name"], values=(e.get("pseudonym") or "",))
            if sel and sel[0] in tree.get_children():
                tree.selection_set(sel[0])

        def current() -> Optional[Dict]:
            sel = tree.selection()
            return st["entries"][int(sel[0])] if sel else None

        def save_list():
            proj.update_project(project, names=[e for e in st["entries"] if e["name"]])
            if on_saved:
                on_saved()

        def set_pseudonym(_e=None):
            e = current()
            if e is None:
                return None
            ps = nameslib.clean(v_ps.get())
            clash = next((o["name"] for o in st["entries"] if o is not e and ps and (o.get("pseudonym") == ps or o["name"] == ps)), None)
            if clash:
                lbl.config(text=f"{ps} is already used for {clash}: pseudonyms must tell people apart.", fg=T["warn"])
                return None
            e["pseudonym"] = ps
            save_list()
            fill()
            lbl.config(text=f"{e['name']} → {ps or '(none)'}", fg=T["fg_secondary"])
            return True

        def suggest():
            st["entries"] = nameslib.suggest(st["entries"], prefix=v_prefix.get().strip() or "P")
            save_list()
            fill()
            lbl.config(text="Pseudonyms given to everyone without one.", fg=T["fg_secondary"])

        def pick(_e=None):
            e = current()
            v_ps.set((e or {}).get("pseudonym") or "")

        def rename(new: Optional[str] = None):
            e = current()
            if e is None:
                return None
            old = e["name"]
            if new is None:
                new = simpledialog.askstring("Rename throughout", f"New name for {old} — in every transcript of the study:",
                                             initialvalue=old, parent=win)
            new = nameslib.clean(new)
            if not new or new == old:
                return None
            all_paths = [r["path"] for r in self._overview_rows(project) if "error" not in r]
            protect = nameslib.protect_for(old, st["entries"])
            preview = nameslib.rename_preview(all_paths, old, protect)
            n_spk = sum(v["speaker"] for v in preview.values())
            n_txt = sum(v["text"] for v in preview.values())
            kept = f"\n\nLeft alone where it is part of: {', '.join(protect)}." if protect else (
                "\n\nIf someone else has this name as part of theirs (e.g. a full name said in the text), add that "
                "person with Add name… first, so it is left alone.")
            if not messagebox.askyesno("Rename throughout", f"Rename {old} to {new} in {len(preview)} transcript(s): "
                                       f"{n_spk} speaker label(s) and {n_txt} time(s) said in the text, and in the project's "
                                       f"speaker lists.{kept}\n\nEach transcript keeps a backup.", parent=win):
                return None
            changed = self._rename_throughout(project, list(preview), old, new, protect)
            nameslib.rename_in_project_file(project, old, new)
            st["entries"] = nameslib.merge_people(nameslib.name_list(proj.load_project(project / proj.PROJECT_FILE)),
                                                  self._project_people(project))
            fill()
            lbl.config(text=f"Renamed {old} to {new} in {changed} transcript(s).", fg=T["ok"])
            if on_saved:
                on_saved()
            return changed

        def export(target: Optional[Path] = None):
            repl = nameslib.mapping(st["entries"])
            missing = [e["name"] for e in st["entries"] if not e.get("pseudonym")]
            if missing and target is None and not messagebox.askyesno(
                    "Export with pseudonyms", f"{len(missing)} name(s) have no pseudonym and stay as they are: "
                    f"{', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}.\n\nExport anyway?", parent=win):
                return None
            if target is None:
                default = project / f"Pseudonymised {date.today().isoformat()}"
                d = self._ask_directory("Export copies with pseudonyms into", str(default.parent))
                if not d:
                    return None
                target = Path(d) / default.name if Path(d) == project else Path(d)
            done = self._export_pseudonymised(st["paths"], repl, Path(target))
            lbl.config(text=f"{done} transcript(s) exported with pseudonyms into {target}.", fg=T["ok"])
            return done

        def add(name: Optional[str] = None):
            if name is None:
                name = simpledialog.askstring("Add name", "A person who is named in the transcripts (e.g. a full name, "
                                              "a teacher who is talked about):", parent=win)
            name = nameslib.clean(name)
            if not name or any(e["name"] == name for e in st["entries"]):
                return None
            st["entries"].append({"name": name, "pseudonym": ""})
            save_list()
            fill()
            lbl.config(text=f"Added {name}.", fg=T["fg_secondary"])
            return True

        tree.bind("<<TreeviewSelect>>", pick)
        ent.bind("<Return>", set_pseudonym)
        self._tool(form, "Set", set_pseudonym).pack(side=tk.LEFT)
        self._tool(form, "Give the rest pseudonyms", suggest).pack(side=tk.LEFT, padx=(12, 4))
        self._entry(form, v_prefix, font="small", width=5).pack(side=tk.LEFT)
        lbl.pack(anchor="w", pady=(6, 0))
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(10, 0))
        self._tool(bar, "Add name…", lambda: add()).pack(side=tk.LEFT)
        self._tool(bar, "Rename throughout…", lambda: rename()).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, f"Export {len(st['paths'])} with pseudonyms…", lambda: export(), primary=True).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        fill()
        if st["entries"] != nameslib.name_list(data):
            save_list()  # people found in the speaker lists and transcripts join the list
        return win, {"state": st, "tree": tree, "pseudonym": v_ps, "set": set_pseudonym, "suggest": suggest, "rename": rename,
                     "export": export, "prefix": v_prefix, "add": add}

    def _rename_throughout(self, project: Path, paths: List, old: str, new: str, protect: List[str] = ()) -> int:
        """Rename a person in these transcripts (speakers and text); each gets a backup and new exports. The open
        transcript gets an undo step."""
        changed = 0
        active = self.active_doc
        for p in paths:
            try:
                if active is not None and Path(active.path).resolve() == Path(p).resolve():
                    self._autosave_edit(rerender=False)
                    res = nameslib.rename_in_doc(active, old, new, protect=protect)
                    if (res["speaker"] or res["text"]) and self._commit(f"Renamed {old} to {new} throughout") is not None:
                        changed += 1
                    continue
                doc = TranscriptDocument.load(Path(p))
                res = nameslib.rename_in_doc(doc, old, new, protect=protect)
                if res["speaker"] or res["text"]:
                    doc.save(backup=True)
                    doc.export_all()
                    changed += 1
            except Exception as exc:
                self._append_log(f"Could not rename in {Path(p).name}: {exc}", "ERROR")
        if active is not None:
            self._render_doc(TranscriptDocument.load(Path(active.path)))
        self._append_log(f"Renamed {old} to {new} in {changed} transcript(s).", "SUCCESS")
        return changed

    def _export_pseudonymised(self, paths: List[Path], repl: Dict[str, str], target: Path) -> int:
        """Copies with pseudonyms (transcript file and its exports) into target/<name>/, the originals untouched."""
        n = 0
        target.mkdir(parents=True, exist_ok=True)
        for p in paths:
            try:
                doc = TranscriptDocument.load(Path(p))
                twin = nameslib.pseudonymised(doc, repl)
                base = doc.base_name
                for old, new in sorted(repl.items(), key=lambda kv: len(kv[0]), reverse=True):
                    base = nameslib.name_rx(old).sub(new, base)
                folder = target / base
                folder.mkdir(parents=True, exist_ok=True)
                twin.path = folder / f"{base}{TranscriptDocument.SUFFIX}"
                twin.data["base_name"] = base
                twin.save()
                twin.export_all()
                n += 1
            except Exception as exc:
                self._append_log(f"Could not export {Path(p).name} with pseudonyms: {exc}", "ERROR")
        self._append_log(f"Exported {n} transcript(s) with pseudonyms into {target}", "SUCCESS")
        return n
