"""Task queue: adding, merging, details panel, running."""
from condense.gui._common import *  # noqa: F401,F403
from condense.gui.trim_dialog import describe_trim


class QueueMixin:
    # ------------------------------------------------------------------ task queue
    def _build_queue_panel(self, panel):
        T = THEME
        top = tk.Frame(panel, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self._label(top, "TASKS", font="section", fg=T["fg_dim"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self.lbl_queue_counts = self._label(top, "empty", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        self.lbl_queue_counts.pack(side=tk.RIGHT)

        bar = self._flow(panel, padx=4, pady=4, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(6, 2))
        bar.add(self._tool(bar, "+ Videos", self._add_videos, primary=True))
        bar.add(self._tool(bar, "+ Merge Task", self._add_merge_task))
        bar.add(self._tool(bar, "+ Folder", self._add_folder))
        bar.add(self._separator(bar))
        bar.add(self._tool(bar, "↑", self._move_up))
        bar.add(self._tool(bar, "↓", self._move_down))
        bar.add(self._tool(bar, "Remove", self._remove_selected))
        bar.add(self._tool(bar, "⋯", self._show_queue_menu_button))
        self._wrapping_label(panel, "+ Videos: one task per video  ·  + Merge Task: several videos → one merged video and transcript",
                             font="small", fg=T["fg_dim"], bg=T["bg_panel"]).pack(fill=tk.X, pady=(0, 6))

        self._build_task_details(panel)

        tree_frame = tk.Frame(panel, bg=T["bg_input"])
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        cols = ("name", "steps", "status", "stage")
        self.queue_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", style="Queue.Treeview", selectmode="extended")
        for c, w, mw, stretch, heading in [
            ("name", 160, 90, True, "Task"),
            ("steps", 170, 90, True, "Steps"),
            ("status", 74, 60, False, "Status"),
            ("stage", 140, 70, True, "Stage"),
        ]:
            self.queue_tree.heading(c, text=heading, anchor="w")
            self.queue_tree.column(c, width=w, minwidth=mw, stretch=stretch, anchor="w")
        self.queue_tree.tag_configure("Pending", foreground=T["fg_primary"])
        self.queue_tree.tag_configure("Running", foreground="#38bdf8")
        self.queue_tree.tag_configure("Completed", foreground=T["ok"])
        self.queue_tree.tag_configure("Failed", foreground=T["err"])
        self.queue_tree.bind("<<TreeviewSelect>>", self._on_job_selected)
        self.queue_tree.bind("<Double-Button-1>", self._on_task_double_click)
        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            self.queue_tree.bind(seq, self._show_queue_context_menu)
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=vsb.set)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.ctx_menu = tk.Menu(self, tearoff=0)
        self.ctx_menu.add_command(label="Open Transcript", command=self._open_selected_transcript)
        self.ctx_menu.add_command(label="Show in Finder", command=self._open_selected_job_folder)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Combine Selected into One Merge Task", command=self._combine_selected)
        self.ctx_menu.add_command(label="Split Merge Task into Single Tasks", command=self._split_merge_task)
        self.ctx_menu.add_command(label="Use Current Settings", command=self._task_use_current_settings)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Reset to Pending", command=self._reset_selected_jobs)
        self.ctx_menu.add_command(label="Remove", command=self._remove_selected)
        self.ctx_menu.add_command(label="Clear Completed", command=self._clear_completed_jobs)

    def _build_task_details(self, panel):
        """Shows (and, while Pending, edits) exactly what the selected task will do."""
        T = THEME
        card = tk.Frame(panel, bg=T["bg_card"], padx=10, pady=8)
        card.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        self.lbl_task_title = self._label(card, "TASK DETAILS — select a task", font="section", fg=T["accent"])
        self.lbl_task_title.pack(anchor="w", pady=(0, 4))
        form = tk.Frame(card, bg=T["bg_card"])
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        self._loading_details = False
        lab = lambda r, t: self._label(form, t, font="small", fg=T["fg_secondary"]).grid(row=r, column=0, sticky="nw", padx=(0, 8), pady=2)

        lab(0, "Name")
        self.var_task_name = tk.StringVar(value="")
        self.ent_task_name = self._entry(form, self.var_task_name, font="small")
        self.ent_task_name.grid(row=0, column=1, sticky="ew", pady=2)
        self.var_task_name.trace_add("write", lambda *a: self._on_task_name())

        lab(1, "Videos")
        vids = tk.Frame(form, bg=T["bg_card"])
        vids.grid(row=1, column=1, sticky="ew", pady=2)
        vids.columnconfigure(0, weight=1)
        self.lst_inputs = tk.Listbox(vids, height=3, bg=T["bg_input"], fg=T["fg_primary"], selectbackground=T["accent"], selectforeground="#ffffff",
                                     font=self.f["small"], activestyle="none", highlightthickness=1, highlightbackground=T["border"], relief=tk.FLAT,
                                     exportselection=False)
        self.lst_inputs.grid(row=0, column=0, sticky="ew")
        vbtn = tk.Frame(vids, bg=T["bg_card"])
        vbtn.grid(row=0, column=1, sticky="n", padx=(4, 0))
        self._task_buttons = []
        for text, cmd in (("↑", lambda: self._task_input_move(-1)), ("↓", lambda: self._task_input_move(+1)),
                          ("+", self._task_input_add), ("−", self._task_input_remove)):
            b = self._tool(vbtn, text, cmd)
            b.pack(side=tk.LEFT if text in "↑↓" else tk.LEFT, padx=(0, 2))
            self._task_buttons.append(b)

        lab(2, "Steps")
        self.var_task_compress = tk.BooleanVar(value=False)
        self.var_task_audio = tk.BooleanVar(value=False)
        self.var_task_whisper = tk.BooleanVar(value=False)
        steps = self._flow(form, padx=10, pady=2, bg=T["bg_card"])
        steps.grid(row=2, column=1, sticky="ew", pady=2)
        self.lbl_task_merge = self._label(steps, "", font="small_bold", fg=T["fg_primary"])
        steps.add(self.lbl_task_merge)
        self._task_checks = []
        for text, var in (("Compress", self.var_task_compress), ("Extract audio", self.var_task_audio), ("Transcribe", self.var_task_whisper)):
            chk = self._check(steps, text, var, font="small")
            steps.add(chk)
            self._task_checks.append(chk)
            var.trace_add("write", lambda *a: self._on_task_steps())

        lab(3, "Settings")
        srow = tk.Frame(form, bg=T["bg_card"])
        srow.grid(row=3, column=1, sticky="ew", pady=2)
        srow.columnconfigure(0, weight=1)
        self.lbl_task_settings = self._wrapping_label(srow, "", font="small", fg=T["fg_secondary"])
        self.lbl_task_settings.grid(row=0, column=0, sticky="ew")
        self.btn_task_settings = self._tool(srow, "Use current Settings", self._task_use_current_settings)
        self.btn_task_settings.grid(row=0, column=1, sticky="ne", padx=(6, 0))

        lab(4, "Output")
        orow = tk.Frame(form, bg=T["bg_card"])
        orow.grid(row=4, column=1, sticky="ew", pady=2)
        orow.columnconfigure(0, weight=1)
        self.lbl_task_output = self._wrapping_label(orow, "", font="small", fg=T["fg_secondary"])
        self.lbl_task_output.grid(row=0, column=0, sticky="ew")
        self.btn_task_output = self._tool(orow, "Change…", self._task_change_output)
        self.btn_task_output.grid(row=0, column=1, sticky="ne", padx=(6, 0))

        lab(5, "Trim")
        trow = tk.Frame(form, bg=T["bg_card"])
        trow.grid(row=5, column=1, sticky="ew", pady=2)
        trow.columnconfigure(0, weight=1)
        self.lbl_task_trim = self._wrapping_label(trow, "", font="small", fg=T["fg_secondary"])
        self.lbl_task_trim.grid(row=0, column=0, sticky="ew")
        self.btn_task_trim = self._tool(trow, "Trim…", self._trim_task_video)
        self.btn_task_trim.grid(row=0, column=1, sticky="ne", padx=(6, 0))

        self.lbl_task_note = self._wrapping_label(card, "", font="small", fg=T["fg_dim"])
        self.lbl_task_note.pack(fill=tk.X, pady=(4, 0))
        self._show_task_details(None)

    def _show_queue_context_menu(self, event):
        row = self.queue_tree.identify_row(event.y)
        if row and row not in self.queue_tree.selection():
            self.queue_tree.selection_set(row)
        self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _show_queue_menu_button(self):
        self.ctx_menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    # ================================================================== task handling
    @property
    def _queue_path(self) -> Path:
        return APP_SUPPORT_DIR / "queue.json"

    def _save_queue(self):
        try:
            APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self._queue_path.with_name("queue.json.tmp")
            tmp.write_text(json.dumps({"counter": self.job_counter, "tasks": self.jobs}, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._queue_path)
        except Exception as exc:
            self._append_log(f"Could not save the task queue: {exc}", "WARN")

    def _load_queue(self):
        try:
            data = json.loads(self._queue_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.job_counter = int(data.get("counter", 0))
        self.jobs = []
        for t in data.get("tasks") or []:
            if not isinstance(t, dict) or not t.get("inputs"):
                continue
            if t.get("status") == "Running":  # app was closed mid-run
                t["status"], t["stage"], t["progress"] = "Pending", "Interrupted — will run again", 0.0
            t.setdefault("steps", self._default_steps())
            t.setdefault("settings", self._settings_snapshot())
            self.jobs.append(t)
        self._refresh_queue_tree()
        if self.jobs:
            self._append_log(f"Restored {len(self.jobs)} task(s) from the last session.", "INFO")

    def _settings_snapshot(self) -> Dict:
        try:
            num_speakers = int(self.var_num_speakers.get())
        except Exception:
            num_speakers = 0
        return {
            "respect_llc": self.var_respect_llc.get(),
            "hb_encoder": self.var_hb_encoder.get(), "hb_quality": self.var_hb_quality.get(), "hb_resolution": self.var_hb_res.get(),
            "hb_fps": self.var_hb_fps.get(), "hb_container": self.var_hb_container.get(), "hb_audio_bitrate": self.var_hb_abitrate.get(),
            "audio_format": self.var_audio_format.get(), "speech_filter": self.var_speech_filter.get(),
            "audio_cleanup": self.var_audio_cleanup.get(),
            "whisper_backend": self.var_whisper_backend.get(), "whisper_model": self.var_whisper_model.get(),
            "whisper_lang": self.var_whisper_lang.get(), "diarize_mode": self.var_diarize_mode.get(), "num_speakers": num_speakers,
            "whisper_prompt": self.var_whisper_prompt.get().strip(),
            **{f"fmt_{k}": v for k, v in self._export_formats().items()},
        }

    def _default_steps(self) -> Dict[str, bool]:
        return {"compress": bool(self.var_stage_compress.get()), "audio": bool(self.var_stage_audio.get()),
                "whisper": bool(self.var_stage_whisper.get())}

    def _default_output_dir(self, inputs: List[str], name: str) -> str:
        if self.var_auto_subfolder.get():
            first = Path(inputs[0])
            return str(first.parent / (name if len(inputs) > 1 else first.stem))
        return str(Path(self.var_output_dir.get()).expanduser() / name) if len(inputs) > 1 else str(Path(self.var_output_dir.get()).expanduser())

    @staticmethod
    def _merge_name(inputs: List[str]) -> str:
        stems = [Path(p).stem for p in inputs]
        prefix = os.path.commonprefix(stems).rstrip(" _-.")
        return f"{prefix}_merged" if len(prefix) >= 3 else f"{stems[0]}_merged"

    def _new_task(self, inputs: List[str], name: Optional[str] = None) -> Dict:
        self.job_counter += 1
        name = name or (self._merge_name(inputs) if len(inputs) > 1 else Path(inputs[0]).stem)
        return {
            "id": f"task_{self.job_counter:04d}", "name": name, "inputs": list(inputs), "steps": self._default_steps(),
            "settings": self._settings_snapshot(), "output_dir": self._default_output_dir(inputs, name), "output_auto": True,
            "status": "Pending", "stage": "Queued", "progress": 0.0, "info": "", "doc_path": None, "created": now_iso(),
        }

    def _probe_quick_media_info(self, video_path: Path) -> str:
        if not shutil.which("ffprobe"):
            return ""
        try:
            res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(video_path)],
                                 capture_output=True, text=True, timeout=10)
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            h = streams[0].get("height") if streams else None
            dur_s = float((data.get("format") or {}).get("duration", 0.0) or 0.0)
            hrs, rem = divmod(int(dur_s), 3600)
            mins, secs = divmod(rem, 60)
            dur_str = (f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}") if dur_s > 0 else ""
            return " · ".join(x for x in (f"{h}p" if h else "", dur_str) if x)
        except Exception:
            return ""

    def _probe_tasks_async(self, tasks: List[Dict]):
        todo = [(t["id"], list(t["inputs"])) for t in tasks]

        def probe():
            for tid, inputs in todo:
                infos = [self._probe_quick_media_info(Path(p)) for p in inputs[:20]]
                self.msg_queue.put({"type": "job_info", "job_id": tid, "info": infos[0] if len(infos) == 1 else f"{len(inputs)} videos"})

        threading.Thread(target=probe, daemon=True).start()

    def _add_tasks(self, tasks: List[Dict], select: bool = True):
        if not tasks:
            return
        self.jobs.extend(tasks)
        self._refresh_queue_tree()
        self._save_queue()
        self._probe_tasks_async(tasks)
        if select:
            self.queue_tree.selection_set(tasks[-1]["id"])
            self._on_job_selected()

    def _enqueue_video_paths(self, paths: List[str]):
        queued = {p for t in self.jobs if t["status"] in ("Pending", "Running") for p in t["inputs"]}
        files = [str(Path(f)) for f in paths if Path(f).is_file() and str(Path(f)) not in queued]
        tasks = [self._new_task([f]) for f in files]
        self._add_tasks(tasks)
        if tasks:
            self._append_log(f"Added {len(tasks)} task(s), one per video.", "INFO")

    def _ask_videos(self, title: str) -> List[str]:
        files = self._ask_open_files(title, self.settings.get("last_add_dir"), MEDIA_FILETYPES)
        if files:
            self.settings["last_add_dir"] = str(Path(files[0]).parent)
        return list(files or [])

    def _add_videos(self):
        files = self._ask_videos("Add videos (one task per video)")
        if files:
            self._enqueue_video_paths(files)

    def _add_merge_task(self):
        files = self._ask_videos("Choose the videos to merge (you can reorder them afterwards)")
        if not files:
            return
        files = sorted(files, key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).name)])
        task = self._new_task(files)
        self._add_tasks([task])
        self._append_log(f"Added merge task '{task['name']}' with {len(files)} video(s). Check the order under Task Details.", "INFO")
        self.ent_task_name.focus_set()

    def _add_folder(self):
        folder = self._ask_directory("Add every video in a folder (one task per video)", self.settings.get("last_add_dir"))
        if folder:
            self.settings["last_add_dir"] = folder
            found = sorted(str(p) for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
            if found:
                self._enqueue_video_paths(found)
            else:
                self._append_log(f"No video files found in {folder}.", "WARN")

    @staticmethod
    def _steps_text(task: Dict) -> str:
        st = task.get("steps") or {}
        parts = [f"Merge {len(task['inputs'])}"] if len(task.get("inputs") or []) > 1 else []
        parts += [label for key, label in (("compress", "Compress"), ("audio", "Audio"), ("whisper", "Transcribe")) if st.get(key)]
        return " → ".join(parts) or "nothing selected"

    @staticmethod
    def _settings_summary(task: Dict) -> str:
        s, st = task.get("settings") or {}, task.get("steps") or {}
        parts = []
        if st.get("compress") or len(task.get("inputs") or []) > 1:
            parts.append(f"Video: {str(s.get('hb_encoder', '')).split()[0]} RF {s.get('hb_quality')}, {s.get('hb_container')}")
        if st.get("audio"):
            parts.append(f"Audio: {str(s.get('audio_format', '')).split(' (')[0]}")
        if st.get("whisper"):
            b = str(s.get("whisper_backend", ""))
            backend = "MLX" if b.startswith("MLX") else ("GPU" if b.startswith("PyTorch") else "CPU")
            diar = str(s.get("diarize_mode", "")).split()[0].replace("PyTorch", "spectral").replace("Pyannote.audio", "pyannote")
            lang = str(s.get("whisper_lang", "auto")).split()[0]
            fmts = [n for k, n in (("fmt_para_rtf", "¶RTF"), ("fmt_para_txt", "¶TXT"), ("fmt_srt", "SRT"), ("fmt_vtt", "VTT"),
                                   ("fmt_txt", "TXT"), ("fmt_rtf", "RTF")) if s.get(k)]
            vocab = [v for v in str(s.get("whisper_prompt") or "").split(",") if v.strip()]
            parts.append(f"Whisper {s.get('whisper_model')} ({backend}), language {lang}, speakers {diar}"
                         + (f", vocabulary {len(vocab)} term{'s' if len(vocab) != 1 else ''}" if vocab else "")
                         + (f", audio cleanup: {s['audio_cleanup'].lower()}" if s.get("audio_cleanup") not in (None, "", "Off") else "")
                         + f"; exports {', '.join(fmts) or 'none'}")
        return "  ·  ".join(parts) or "—"

    def _job_values(self, task: Dict) -> Tuple:
        st = task["status"]
        status = {"Pending": "Pending", "Completed": "✓ Done", "Failed": "✖ Failed"}.get(st, f"{task['progress']:.0f}%" if st == "Running" else st)
        name = task["name"] + (f"  ({task['info']})" if task.get("info") else "")
        return (name, self._steps_text(task), status, task["stage"])

    def _refresh_queue_tree(self):
        sel = set(self.queue_tree.selection())
        self.queue_tree.delete(*self.queue_tree.get_children())
        for task in self.jobs:
            self.queue_tree.insert("", tk.END, iid=task["id"], values=self._job_values(task), tags=(task["status"],))
            if task["id"] in sel:
                self.queue_tree.selection_add(task["id"])
        self._update_queue_counts()

    def _update_job_row(self, task: Dict):
        if not self.queue_tree.exists(task["id"]):
            self._refresh_queue_tree()
            return
        self.queue_tree.item(task["id"], values=self._job_values(task), tags=(task["status"],))
        self._update_queue_counts()

    def _update_queue_counts(self):
        if not self.jobs:
            self.lbl_queue_counts.config(text="empty")
            return
        pending = sum(j["status"] == "Pending" for j in self.jobs)
        done = sum(j["status"] == "Completed" for j in self.jobs)
        failed = sum(j["status"] == "Failed" for j in self.jobs)
        parts = [f"{len(self.jobs)} task{'s' if len(self.jobs) != 1 else ''}"]
        parts += [f"{n} {label}" for n, label in ((pending, "pending"), (done, "done"), (failed, "failed")) if n]
        self.lbl_queue_counts.config(text=" · ".join(parts))

    def _get_selected_indices(self) -> List[int]:
        sel = set(self.queue_tree.selection())
        return [i for i, j in enumerate(self.jobs) if j["id"] in sel]

    def _selected_task(self) -> Optional[Dict]:
        ind = self._get_selected_indices()
        return self.jobs[ind[0]] if len(ind) == 1 else None

    def _move_up(self):
        ind = self._get_selected_indices()
        if not ind or ind[0] == 0:
            return
        for i in ind:
            self.jobs[i - 1], self.jobs[i] = self.jobs[i], self.jobs[i - 1]
        self._refresh_queue_tree()
        self._save_queue()

    def _move_down(self):
        ind = self._get_selected_indices()
        if not ind or ind[-1] >= len(self.jobs) - 1:
            return
        for i in reversed(ind):
            self.jobs[i + 1], self.jobs[i] = self.jobs[i], self.jobs[i + 1]
        self._refresh_queue_tree()
        self._save_queue()

    def _remove_selected(self):
        sel = set(self.queue_tree.selection())
        self.jobs = [j for j in self.jobs if j["id"] not in sel or j["status"] == "Running"]
        self._refresh_queue_tree()
        self._show_task_details(None)
        self._save_queue()

    def _reset_selected_jobs(self):
        sel = set(self.queue_tree.selection())
        for j in self.jobs:
            if (not sel or j["id"] in sel) and j["status"] != "Running":
                j["status"], j["stage"], j["progress"] = "Pending", "Queued", 0.0
        self._refresh_queue_tree()
        self._on_job_selected()
        self._save_queue()

    def _clear_completed_jobs(self):
        self.jobs = [j for j in self.jobs if j["status"] != "Completed"]
        self._refresh_queue_tree()
        self._show_task_details(self._selected_task())
        self._save_queue()

    def _combine_selected(self):
        """Turn several selected (pending) tasks into one merge task, in queue order."""
        ind = self._get_selected_indices()
        chosen = [self.jobs[i] for i in ind if self.jobs[i]["status"] == "Pending"]
        if len(chosen) < 2:
            self.lbl_status.config(text=kbd("Select two or more pending tasks to combine (⌘-click or ⇧-click)."))
            return
        inputs = [p for t in chosen for p in t["inputs"]]
        task = self._new_task(inputs)
        task["steps"] = dict(chosen[0]["steps"])
        task["settings"] = dict(chosen[0]["settings"])
        first = ind[0]
        ids = {t["id"] for t in chosen}
        self.jobs = [j for j in self.jobs if j["id"] not in ids]
        self.jobs.insert(min(first, len(self.jobs)), task)
        self._refresh_queue_tree()
        self._save_queue()
        self._probe_tasks_async([task])
        self.queue_tree.selection_set(task["id"])
        self._on_job_selected()
        self._append_log(f"Combined {len(chosen)} tasks into merge task '{task['name']}'.", "INFO")

    def _split_merge_task(self):
        task = self._selected_task()
        if task is None or len(task["inputs"]) < 2 or task["status"] == "Running":
            self.lbl_status.config(text="Select one merge task to split it into single-video tasks.")
            return
        pos = self.jobs.index(task)
        singles = []
        for p in task["inputs"]:
            t = self._new_task([p])
            t["steps"], t["settings"] = dict(task["steps"]), dict(task["settings"])
            singles.append(t)
        self.jobs[pos:pos + 1] = singles
        self._refresh_queue_tree()
        self._save_queue()
        self._probe_tasks_async(singles)
        self._show_task_details(None)

    # ----- task details panel -----
    def _show_task_details(self, task: Optional[Dict]):
        self._loading_details = True
        try:
            editable = task is not None and task["status"] == "Pending"
            state = tk.NORMAL if editable else tk.DISABLED
            self.lst_inputs.delete(0, tk.END)
            if task is None:
                n = len(self._get_selected_indices()) if self.__dict__.get("queue_tree") is not None else 0  # tree is built after this panel
                self.lbl_task_title.config(text=f"{n} TASKS SELECTED — right-click to combine" if n > 1 else "TASK DETAILS — select a task")
                self.var_task_name.set("")
                for v in (self.var_task_compress, self.var_task_audio, self.var_task_whisper):
                    v.set(False)
                self.lbl_task_merge.config(text="")
                self.lbl_task_settings.config(text="")
                self.lbl_task_output.config(text="")
                self.lbl_task_trim.config(text="")
                self.lbl_task_note.config(text="")
            else:
                merge = len(task["inputs"]) > 1
                self.lbl_task_title.config(text=("MERGE TASK" if merge else "TASK") + f"  ·  {task['status'].upper()}")
                self.var_task_name.set(task["name"])
                for p in task["inputs"]:
                    self.lst_inputs.insert(tk.END, Path(p).name)
                st = task.get("steps") or {}
                self.var_task_compress.set(bool(st.get("compress")))
                self.var_task_audio.set(bool(st.get("audio")))
                self.var_task_whisper.set(bool(st.get("whisper")))
                self.lbl_task_merge.config(text=f"Merge {len(task['inputs'])} videos  →" if merge else "")
                self.lbl_task_settings.config(text=self._settings_summary(task))
                self.lbl_task_output.config(text=task["output_dir"])
                if merge:
                    trims = [describe_trim(Path(p)) for p in task["inputs"]]
                    n = sum(1 for t in trims if t != "none")
                    self.lbl_task_trim.config(text=(f"{n} of {len(trims)} videos trimmed" if n else "none")
                                              + " — select a video above, then Trim…")
                else:
                    self.lbl_task_trim.config(text=describe_trim(Path(task["inputs"][0])) + " — parts left out before transcribing")
                if task["status"] == "Failed":
                    self.lbl_task_note.config(text=task["stage"], fg=THEME["err"])
                elif not editable:
                    self.lbl_task_note.config(text="Finished tasks can't be edited — ↻ Reset to Pending (right-click) to run again with changes.", fg=THEME["fg_dim"])
                elif merge:
                    self.lbl_task_note.config(text="Videos are merged top to bottom. Name = merged file and transcript name.", fg=THEME["fg_dim"])
                else:
                    self.lbl_task_note.config(text="+ adds another video and turns this into a merge task.", fg=THEME["fg_dim"])
            self.ent_task_name.configure(state=state if (task and len(task["inputs"]) > 1) else tk.DISABLED)
            self.lst_inputs.configure(state=tk.NORMAL)
            for w in (*self._task_buttons, *self._task_checks, self.btn_task_settings, self.btn_task_output, self.btn_task_trim):
                w.configure(state=state)
        finally:
            self._loading_details = False

    def _editable_task(self) -> Optional[Dict]:
        task = self._selected_task()
        return task if task is not None and task["status"] == "Pending" else None

    def _task_changed(self, task: Dict, refresh_details: bool = False):
        self._update_job_row(task)
        self._save_queue()
        if refresh_details:
            self._show_task_details(task)
        else:
            self.lbl_task_settings.config(text=self._settings_summary(task))

    def _on_task_name(self):
        if self._loading_details:
            return
        task = self._editable_task()
        name = self.var_task_name.get().strip()
        if task is None or not name or len(task["inputs"]) < 2:
            return
        task["name"] = re.sub(r'[\\/:*?"<>|]', "_", name)
        if task.get("output_auto", True):
            task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
            self.lbl_task_output.config(text=task["output_dir"])
        self._task_changed(task)

    def _on_task_steps(self):
        if self._loading_details:
            return
        task = self._editable_task()
        if task is None:
            return
        task["steps"] = {"compress": bool(self.var_task_compress.get()), "audio": bool(self.var_task_audio.get()),
                         "whisper": bool(self.var_task_whisper.get())}
        self._task_changed(task)

    def _task_input_move(self, direction: int):
        task = self._editable_task()
        sel = self.lst_inputs.curselection()
        if task is None or not sel:
            return
        i = sel[0]
        j = i + direction
        if not (0 <= j < len(task["inputs"])):
            return
        task["inputs"][i], task["inputs"][j] = task["inputs"][j], task["inputs"][i]
        self._task_changed(task, refresh_details=True)
        self.lst_inputs.selection_set(j)

    def _task_input_add(self):
        task = self._editable_task()
        if task is None:
            return
        files = [f for f in self._ask_videos("Add videos to this task (they will be merged)") if f not in task["inputs"]]
        if not files:
            return
        was_single = len(task["inputs"]) == 1
        task["inputs"].extend(files)
        if was_single:
            task["name"] = self._merge_name(task["inputs"])
        if task.get("output_auto", True):
            task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
        task["info"] = f"{len(task['inputs'])} videos"
        self._task_changed(task, refresh_details=True)

    def _task_input_remove(self):
        task = self._editable_task()
        sel = self.lst_inputs.curselection()
        if task is None or not sel or len(task["inputs"]) <= 1:
            return
        del task["inputs"][sel[0]]
        if len(task["inputs"]) == 1:
            task["name"] = Path(task["inputs"][0]).stem
            task["info"] = ""
            self._probe_tasks_async([task])
        else:
            task["info"] = f"{len(task['inputs'])} videos"
        if task.get("output_auto", True):
            task["output_dir"] = self._default_output_dir(task["inputs"], task["name"])
        self._task_changed(task, refresh_details=True)

    def _task_use_current_settings(self):
        ind = self._get_selected_indices()
        tasks = [self.jobs[i] for i in ind if self.jobs[i]["status"] == "Pending"]
        if not tasks:
            return
        snap = self._settings_snapshot()
        for t in tasks:
            t["settings"] = dict(snap)
            self._update_job_row(t)
        self._save_queue()
        self._show_task_details(self._selected_task())
        self.lbl_status.config(text=f"Applied the current Settings to {len(tasks)} task(s).")

    def _task_change_output(self):
        task = self._editable_task()
        if task is None:
            return
        f = self._ask_directory("Output folder for this task", task["output_dir"])
        if f:
            task["output_dir"] = f
            task["output_auto"] = False
            self._task_changed(task, refresh_details=True)

    # ----- selection / opening -----
    def _open_selected_job_folder(self):
        task = self._selected_task()
        if task is not None:
            p = Path(task["output_dir"])
        elif self.active_doc is not None:
            p = self.active_doc.out_dir
        else:
            p = Path(self.var_output_dir.get()).expanduser()
        while not p.exists() and p != p.parent:
            p = p.parent
        self._open_externally(p)

    def _open_selected_transcript(self):
        task = self._selected_task()
        if task is None or not task.get("doc_path"):
            self.lbl_status.config(text="This task has no transcript yet.")
            return
        try:
            self._render_doc(TranscriptDocument.load(Path(task["doc_path"])))
            self.notebook.select(self.tab_preview)
        except Exception as exc:
            self._append_log(f"Could not load transcript: {exc}", "ERROR")

    def _on_task_double_click(self, event=None):
        task = self._selected_task()
        if task is not None and task.get("doc_path"):
            self._open_selected_transcript()

    def _on_job_selected(self, _event=None):
        task = self._selected_task()
        self._show_task_details(task)
        if task is None:
            return
        doc_path = task.get("doc_path")
        if doc_path and Path(doc_path).exists() and (self.active_doc is None or str(self.active_doc.path) != doc_path):
            try:
                self._render_doc(TranscriptDocument.load(Path(doc_path)))
            except Exception as exc:
                self._append_log(f"Could not load transcript: {exc}", "ERROR")

    def _browse_output(self):
        f = self._ask_directory("Default output folder", self.var_output_dir.get())
        if f:
            self.var_output_dir.set(f)

    def _export_formats(self) -> Dict[str, bool]:
        return {"srt": bool(self.var_fmt_srt.get()), "vtt": bool(self.var_fmt_vtt.get()), "txt": bool(self.var_fmt_txt.get()),
                "rtf": bool(self.var_fmt_rtf.get()), "para_rtf": bool(self.var_fmt_para_rtf.get()), "para_txt": bool(self.var_fmt_para_txt.get())}

    def _sync_token(self):
        """Keep the keyring (macOS Keychain / Secret Service) in step with the token field and the 'Remember' checkbox."""
        if not SecretStore.available():
            return
        token = self.var_hf_token.get().strip()
        if self.var_remember_token.get():
            if token and token != self._stored_token:
                if SecretStore.save(token):
                    self._stored_token = token
                    self._append_log(f"Hugging Face token saved in the {SecretStore.LABEL}.", "SUCCESS")
                else:
                    self._append_log(f"Could not save the token in the {SecretStore.LABEL} (unexpected characters, or the keyring is locked?).", "WARN")
        elif self._stored_token:
            SecretStore.delete()
            self._stored_token = ""
            self._append_log(f"Hugging Face token removed from the {SecretStore.LABEL}.", "INFO")

    # ================================================================== running
    def _start_pipeline(self):
        if self.worker is not None and self.worker.is_alive():
            self._append_log("Pipeline is already running.", "WARN")
            return
        pending = [j for j in self.jobs if j["status"] == "Pending"]
        if not pending:
            self.lbl_status.config(text=kbd("No pending tasks. Add videos (⌘O) or a merge task first."))
            return
        empty = [t["name"] for t in pending if len(t["inputs"]) < 2 and not any((t.get("steps") or {}).values())]
        if empty:
            self.lbl_status.config(text=f"'{empty[0]}' has no steps ticked — choose at least one step in Task Details.")
            return
        if not self._gpu_preflight(pending):
            self.lbl_status.config(text="Not started. Help ▸ Test GPU shows why the GPU is not available.")
            return
        config = {
            "tasks": json.loads(json.dumps(pending)),
            "output_dir": self.var_output_dir.get(),
            "hf_token": self.var_hf_token.get(),
            "conf_high": 0.85, "conf_med": 0.60, "conf_poor": 0.40,
        }
        self._sync_token()
        self._save_settings()
        self._save_queue()
        self.stop_event.clear()
        self.worker = PipelineWorker(config, self.msg_queue, self.stop_event)
        self.worker.start()
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_run.config(state=tk.DISABLED)
        self.notebook.select(self.tab_console)

    def _stop_pipeline(self):
        if self.worker is not None and self.worker.is_alive():
            self.stop_event.set()
            self._append_log("Stopping pipeline…", "WARN")

    def _poll_queue(self):
        try:
            for _ in range(500):
                msg = self.msg_queue.get_nowait()
                mtype = msg.get("type")
                if mtype == "log":
                    self._append_log(msg["text"], msg.get("level", "INFO"))
                elif mtype == "progress":
                    self.progress_var.set(msg["percent"])
                    self.lbl_status.config(text=msg["status"])
                elif mtype in ("job_update", "job_info"):
                    for j in self.jobs:
                        if j["id"] == msg.get("job_id"):
                            status_changed = "status" in msg and msg["status"] != j["status"]
                            for k in ("status", "stage", "progress", "output_dir", "info"):
                                if k in msg:
                                    j[k] = msg[k]
                            self._update_job_row(j)
                            if status_changed or mtype == "job_info":
                                self._save_queue()
                                if self._selected_task() is j:
                                    self._show_task_details(j)
                elif mtype == "transcript_ready":
                    names = []
                    for j in self.jobs:
                        if j["id"] in (msg.get("job_ids") or []):
                            j["doc_path"] = msg["doc_path"]
                            names.append(j["name"])
                    self._save_queue()
                    if self.active_doc is None:
                        try:
                            self._render_doc(TranscriptDocument.load(Path(msg["doc_path"])))
                        except Exception as exc:
                            self._append_log(f"Could not load transcript: {exc}", "ERROR")
                    else:
                        # Don't pull the editor away from what you're working on.
                        self._append_log(f"✓ Transcript ready: {', '.join(names) or Path(msg['doc_path']).name} — double-click it in the queue to open.", "SUCCESS")
                elif mtype in ("rerun_progress", "rerun_done", "rerun_failed"):
                    self._handle_rerun_message(msg)
                elif mtype == "trim_ui":
                    self._on_trim_ui_msg(msg)
                elif mtype == "trim":
                    self._on_trim_msg(msg)
                elif mtype == "gpu_test":
                    self._on_gpu_test(msg)
                elif mtype == "probe":
                    self.gpu_info, self.deps = msg["gpu"], msg["deps"]
                    self._update_header()
                    missing = [k for k in ("ffmpeg", "whisper") if not self.deps.get(k)]
                    if missing:
                        self._append_log(f"Missing: {', '.join(missing)}. See Setup & Status.", "WARN")
                elif mtype == "done":
                    self.btn_stop.config(state=tk.DISABLED)
                    self.btn_run.config(state=tk.NORMAL)
                    self._save_queue()
                    ok = msg.get("success")
                    self._append_log("Pipeline finished." if ok else "Pipeline finished with errors or was stopped.", "SUCCESS" if ok else "ERROR")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _probe_system(self):
        try:
            gpu = SystemProbe.detect_gpu()
            deps = SystemProbe.check_dependencies()
        except Exception as exc:
            gpu, deps = {"gpu_name": "Unknown", "warning": f"Hardware probe failed: {exc}"}, {}
        self.msg_queue.put({"type": "probe", "gpu": gpu, "deps": deps})
