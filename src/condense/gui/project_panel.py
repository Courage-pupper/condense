"""The project's recordings, always in view (a tab next to Tasks): Day ▸ Group ▸ sessions, interviews and other
recordings — transcribed or not — with each transcript's progress and episodes. Double-click opens a transcript, or
offers to transcribe a recording that has none yet."""
import queue as _queue
import threading

from condense.core import project as proj
from condense.gui._common import *  # noqa: F401,F403

ROLE_TITLES = (("session", "Sessions"), ("interview", "Interviews"), ("other", "Other recordings"))


class ProjectPanelMixin:
    def _build_project_panel(self, panel):
        T = THEME
        top = tk.Frame(panel, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self.lbl_pp_name = self._label(top, "", font="body_bold", bg=T["bg_panel"])
        self.lbl_pp_name.pack(side=tk.LEFT)
        self._tool(top, "↻", lambda: self._refresh_project_panel()).pack(side=tk.RIGHT)
        self._tool(top, "Overview…", self._project_button).pack(side=tk.RIGHT, padx=(0, 4))
        self.lbl_pp_info = self._wrapping_label(panel, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        self.lbl_pp_info.pack(fill=tk.X, pady=(2, 6))
        self.pp_none = tk.Frame(panel, bg=T["bg_panel"])
        self._wrapping_label(self.pp_none, "No project open. A project shows all recordings of a study here — transcribed or "
                                           "not — by day and group.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]
                             ).pack(fill=tk.X)
        row = tk.Frame(self.pp_none, bg=T["bg_panel"])
        row.pack(fill=tk.X, pady=6)
        self._tool(row, "New Project…", self._new_project_dialog, primary=True).pack(side=tk.LEFT)
        self._tool(row, "Open Project…", self._open_project_dialog).pack(side=tk.LEFT, padx=(6, 0))
        frame = tk.Frame(panel, bg=T["bg_panel"])
        self.pp_frame = frame
        tree = ttk.Treeview(frame, columns=("status",), show="tree", style="Queue.Treeview", selectmode="extended")
        tree.column("#0", width=210, stretch=True)
        tree.column("status", width=130, anchor="e", stretch=False)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.tag_configure("todo", foreground=T["fg_dim"])
        tree.tag_configure("queued", foreground=T["warn"])
        tree.tag_configure("done", foreground=T["ok"])
        tree.tag_configure("open", background="#2b3348")
        tree.tag_configure("section", foreground=T["fg_secondary"])
        tree.bind("<Double-Button-1>", lambda e: self._pp_activate())
        tree.bind("<Return>", lambda e: self._pp_activate())
        tree.bind("<Button-3>", self._pp_menu)
        if IS_MAC:
            tree.bind("<Button-2>", self._pp_menu)
            tree.bind("<Control-Button-1>", self._pp_menu)
        self.pp_tree = tree
        self.pp_items: Dict[str, Dict] = {}
        self._pp_state = {"stop": threading.Event()}
        self._refresh_project_panel()

    # ----- filling -----
    def _refresh_project_panel(self, background: bool = True):
        tree = self.__dict__.get("pp_tree")
        if tree is None:
            return None
        project = self._project_dir()
        self._pp_state["stop"].set()
        if project is None:
            self.lbl_pp_name.config(text="")
            self.lbl_pp_info.config(text="")
            self.pp_frame.pack_forget()
            self.pp_none.pack(fill=tk.X)
            return None
        self.pp_none.pack_forget()
        self.pp_frame.pack(fill=tk.BOTH, expand=True)
        self.lbl_pp_name.config(text=proj.project_name(project))
        stop = self._pp_state["stop"] = threading.Event()
        if not background:
            items = proj.inventory(project, stop=stop)
            self._pp_show(project, items)
            return items
        self.lbl_pp_info.config(text="Looking through the study's folders…")
        results: "_queue.Queue" = _queue.Queue()
        threading.Thread(target=lambda: results.put(proj.inventory(project, stop=stop)), daemon=True).start()

        def poll():
            if stop.is_set():
                return
            try:
                items = results.get_nowait()
            except _queue.Empty:
                self.after(150, poll)
                return
            self._pp_show(project, items)
        self.after(150, poll)
        return None

    def _pp_status(self, it: Dict) -> Tuple[str, str]:
        row = it.get("row")
        if row is None:
            media = str(it["media"]) if it.get("media") else None
            busy = any(media in t["inputs"] for t in self.jobs if t["status"] in ("Pending", "Running")) if media else False
            return ("in Tasks", "queued") if busy else ("not transcribed", "todo")
        if "error" in row:
            return ("⚠ unreadable", "todo")
        pct = row["checked_pct"]
        return (f"✓ {pct}% checked" if pct >= 100 else f"{pct}% checked", "done" if pct >= 100 else "")

    def _pp_show(self, project: Path, items: List[Dict]):
        tree = self.pp_tree
        open_before = {iid for iid in self.pp_items if iid.startswith("L:") and tree.item(iid, "open")} if self.pp_items else None
        for iid in tree.get_children():
            tree.delete(iid)
        self.pp_items = {}
        nodes: Dict[str, List[Dict]] = {}
        sections: Dict[str, str] = {}
        order = {"session": 0, "interview": 1, "other": 2}
        items = sorted(items, key=lambda it: ([n for _, n in it["levels"]], order[it["role"]],
                                              (it["media"] or it["transcript"]).name.lower()))
        for k, it in enumerate(items):
            parent = ""
            for depth in range(len(it["levels"])):
                iid = "L:" + "/".join(n for _, n in it["levels"][: depth + 1])
                if iid not in nodes:
                    is_open = (iid in open_before) if open_before is not None else True
                    tree.insert(parent, tk.END, iid=iid, text=it["levels"][depth][1], open=is_open)
                    nodes[iid] = []
                nodes[iid].append(it)
                parent = iid
            sec = f"S:{parent}:{it['role']}"
            if sec not in sections:
                title = dict(ROLE_TITLES)[it["role"]]
                tree.insert(parent, tk.END, iid=sec, text=title, open=it["role"] != "other", tags=("section",))
                sections[sec] = title
            name = it["media"].name if it.get("media") else f"{it['row'].get('name', it['transcript'].name)} (recording not found)"
            status, tag = self._pp_status(it)
            iid = f"I:{k}"
            tree.insert(sec, tk.END, iid=iid, text=name, values=(status,), tags=(tag,) if tag else ())
            self.pp_items[iid] = it
            for j, ep in enumerate((it.get("row") or {}).get("episodes") or []):
                eid = f"E:{k}:{j}"
                tree.insert(iid, tk.END, iid=eid, text=f"▸ {ep['type']} {ep.get('label') or ''}",
                            values=(f"{short_ts(ep['start'])[:-2]}",))
                self.pp_items[eid] = dict(it, episode=ep)
        for iid, below in nodes.items():
            n_rec = sum(1 for it in below if it.get("media"))
            n_tr = sum(1 for it in below if it.get("row"))
            tree.item(iid, values=(f"{n_tr}/{n_rec} transcribed" if n_rec else f"{n_tr} transcript(s)",))
            self.pp_items[iid] = {"node": True}
        n_rec = sum(1 for it in items if it.get("media"))
        todo = [it for it in items if it.get("media") and it.get("row") is None and it["role"] != "other"]
        self.lbl_pp_info.config(text=f"{n_rec} recording(s), {sum(1 for it in items if it.get('row'))} transcribed"
                                     + (f" · {len(todo)} session/interview recording(s) not yet transcribed" if todo else ""))
        self._pp_mark_open()

    def _pp_mark_open(self):
        tree = self.__dict__.get("pp_tree")
        doc = self.active_doc
        if tree is None:
            return
        for iid, it in self.pp_items.items():
            if iid.startswith("I:"):
                cur = tree.item(iid, "tags")
                tags = [t for t in (cur if isinstance(cur, (list, tuple)) else str(cur).split()) if t != "open"]
                if doc is not None and it.get("transcript") is not None and Path(it["transcript"]).resolve() == Path(doc.path).resolve():
                    tags.append("open")
                    try:
                        tree.see(iid)
                    except Exception:
                        pass
                tree.item(iid, tags=tuple(tags))

    def _pp_update_doc(self, doc):
        """After an edit: the open transcript's progress, without searching the folders again."""
        tree = self.__dict__.get("pp_tree")
        if tree is None:
            return
        for iid, it in self.pp_items.items():
            if iid.startswith("I:") and it.get("transcript") is not None and Path(it["transcript"]).resolve() == Path(doc.path).resolve():
                pct = round(doc.checked_fraction() * 100)
                if it.get("row"):
                    it["row"]["checked_pct"] = pct
                status, tag = self._pp_status(it)
                tree.item(iid, values=(status,), tags=tuple(t for t in (tag, "open") if t))

    # ----- acting -----
    def _pp_selected(self) -> List[Dict]:
        return [self.pp_items[i] for i in self.pp_tree.selection() if i in self.pp_items and not self.pp_items[i].get("node")]

    def _pp_activate(self, item: Optional[Dict] = None):
        sel = [item] if item else self._pp_selected()
        if not sel:
            return None
        it = sel[0]
        if it.get("row") and "error" not in it["row"]:
            self._autosave_edit(rerender=False)
            self._render_doc(TranscriptDocument.load(it["transcript"]))
            self.notebook.select(self.tab_preview)
            ep = it.get("episode")
            if ep is not None:
                idx = self.active_doc.episode_segments(ep)
                if idx:
                    self._goto_segment(idx[0])
            return "opened"
        media = [s["media"] for s in sel if s.get("media") and not s.get("row")]
        if media:
            return self._pp_transcribe(media)
        return None

    def _pp_transcribe(self, media: List[Path]):
        if not messagebox.askyesno("Transcribe", f"Add {len(media)} recording(s) to Tasks, with the current Settings?\n\n"
                                   + "\n".join(f"• {m.name}" for m in media[:8]) + ("\n…" if len(media) > 8 else "")
                                   + "\n\nThe results are saved next to each recording, so they appear here when done.", parent=self):
            return None
        self._enqueue_video_paths([str(m) for m in media])
        self.left_tabs.select(self.tab_tasks)
        self._refresh_project_panel()
        return "queued"

    def _pp_menu(self, event):
        tree = self.pp_tree
        iid = tree.identify_row(event.y)
        if iid and iid not in tree.selection():
            tree.selection_set(iid)
        sel = self._pp_selected()
        m = tk.Menu(self, tearoff=0)
        first = sel[0] if sel else None
        if first and first.get("row"):
            m.add_command(label="Open Transcript", command=lambda: self._pp_activate(first))
            if first["role"] == "session":
                m.add_command(label="Find Interview Clips in This Session…",
                              command=lambda: (self._pp_activate(first), self._link_clips_dialog()))
        done = [s["transcript"] for s in sel if s.get("row") and "error" not in s["row"] and not s.get("episode")]
        if done:
            m.add_command(label=f"Mark All Checked (100 %){'' if len(done) == 1 else f' — {len(done)} transcripts'}",
                          command=lambda: self._mark_transcripts_checked(done, True))
            m.add_command(label="Mark All Not Checked", command=lambda: self._mark_transcripts_checked(done, False))
        todo = [s["media"] for s in sel if s.get("media") and not s.get("row")]
        if todo:
            m.add_command(label=f"Transcribe {len(todo)} Recording(s)…", command=lambda: self._pp_transcribe(todo))
        if first:
            target = first.get("media") or first.get("transcript")
            m.add_command(label="Show in Folder", command=lambda: self._open_externally(Path(target).parent))
        m.add_separator()
        m.add_command(label="Refresh", command=lambda: self._refresh_project_panel())
        m.add_command(label="Project Settings…", command=lambda: self._project_settings_dialog(on_saved=self._refresh_project_panel))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return m
