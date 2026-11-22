"""Episodes in the open transcript (interviews, activity phases): list and edit them, mark them from a selection, export
one as its own clip with a transcript timed to it, and find cut-out interview clips in the recording by their sound."""
import json
import threading

from condense import __version__
from condense.core import align
from condense.core import episodes as eplib
from condense.core import project as proj
from condense.core.util import is_system_junk
from condense.gui._common import *  # noqa: F401,F403
from condense.media import trim as trimlib

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm", ".m4a", ".wav", ".mp3", ".aac", ".flac"}


class EpisodesMixin:
    # ================================================================== types and colours
    def _episode_types(self) -> List[str]:
        doc = self.active_doc
        types: List[str] = []
        f = proj.find_project_file(Path(doc.path).parent) if doc is not None else None
        types += proj.project_settings(f.parent)["episode_types"] if f else list(eplib.DEFAULT_TYPES)
        for ep in (doc.episodes if doc is not None else []):
            if ep["type"] not in types:
                types.append(ep["type"])
        return types

    def _structure_ctx(self):
        """(project folder, Structure) for the open transcript, or (None, None)."""
        doc = self.active_doc
        f = proj.find_project_file(Path(doc.path).parent) if doc is not None else None
        st = proj.project_settings(f.parent)["structure"] if f else None
        return (f.parent, st) if st is not None else (None, None)

    def _next_interview_name(self) -> Optional[str]:
        project, st = self._structure_ctx()
        if st is None:
            return None
        labels = [e.get("label") or "" for e in self.active_doc.episodes if e["type"] == "Interview"]
        return st.next_clip_name(project, self.active_doc.path, labels)

    def _episode_tag(self, type_: str) -> str:
        types = self._episode_types()
        return f"EPC{(types.index(type_) if type_ in types else len(types)) % len(eplib.PALETTE)}"

    def _episode_color(self, type_: str) -> str:
        return eplib.PALETTE[int(self._episode_tag(type_)[3:])]

    # ================================================================== list / edit
    def _mark_episode(self):
        """E in the transcript: the selected segments (or the open one) become a new episode."""
        if self.active_doc is None:
            return None
        segs = self._selected_segments()
        if not segs:
            self.lbl_status.config(text="Select the segments of the interview or phase first (drag, or ⇧↓), then press E.")
            return None
        doc = self.active_doc
        start = min(float(doc.segments[i]["start"]) for i in segs)
        end = max(float(doc.segments[i]["end"]) for i in segs)
        type_ = self._episode_types()[0]
        label = (self._next_interview_name() or "") if type_ == "Interview" else ""
        return self._episodes_dialog(preset={"type": type_, "label": label, "start": start, "end": end})

    def _episodes_dialog(self, preset: Optional[Dict] = None):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Episodes — {doc.base_name}")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Episodes: interviews, activity phases … in this recording. Nothing is removed; they label time, start "
                         "new paragraphs, show as bands on the timeline, and can be exported as clips.", font="small",
                    fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=620, justify=tk.LEFT).pack(anchor="w")
        cols = ("type", "label", "start", "end", "length", "source")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=9, style="Queue.Treeview")
        for c, w, title in (("type", 90, "Type"), ("label", 150, "Label"), ("start", 85, "Start"), ("end", 85, "End"),
                            ("length", 70, "Length"), ("source", 170, "From")):
            tree.heading(c, text=title)
            tree.column(c, width=w, anchor="w" if c in ("type", "label", "source") else "center")
        tree.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        form = tk.Frame(win, bg=T["bg_panel"])
        form.pack(fill=tk.X)
        v_type, v_label, v_start, v_end, v_note = (tk.StringVar(value="") for _ in range(5))
        v_approx = tk.BooleanVar(value=False)
        row1 = tk.Frame(form, bg=T["bg_panel"])
        row1.pack(fill=tk.X)
        self._label(row1, "Type", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        cmb = ttk.Combobox(row1, textvariable=v_type, values=self._episode_types(), width=12, font=self.f["small"])
        cmb.pack(side=tk.LEFT, padx=(4, 10))
        self._label(row1, "Label", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._entry(row1, v_label, font="small", width=16).pack(side=tk.LEFT, padx=(4, 10))
        self._label(row1, "From", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._entry(row1, v_start, font="small", width=10).pack(side=tk.LEFT, padx=(4, 4))
        self._label(row1, "to", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._entry(row1, v_end, font="small", width=10).pack(side=tk.LEFT, padx=(4, 10))
        self._check(row1, "approximate", v_approx, bg=T["bg_panel"], font="small").pack(side=tk.LEFT)
        row2 = tk.Frame(form, bg=T["bg_panel"])
        row2.pack(fill=tk.X, pady=(4, 0))
        self._label(row2, "Note", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
        self._entry(row2, v_note, font="small", width=60).pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
        lbl_msg = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl_msg.pack(anchor="w", pady=(4, 0))
        st: Dict = {"id": None}

        def fill():
            for item in tree.get_children():
                tree.delete(item)
            for ep in self.active_doc.episodes:
                src = (ep.get("source") or {}).get("clip")
                approx = "≈ " if ep.get("approx") else ""
                tree.insert("", tk.END, iid=ep["id"], values=(ep["type"], ep.get("label") or "", approx + short_ts(ep["start"])[:-2],
                                                             approx + short_ts(ep["end"])[:-2], short_ts(ep["end"] - ep["start"])[:-2],
                                                             f"clip {Path(src).name}" if src else ""))

        def load(ep: Optional[Dict]):
            st["id"] = ep.get("id") if ep else None
            v_type.set((ep or {}).get("type") or self._episode_types()[0])
            v_label.set((ep or {}).get("label") or "")
            v_start.set(short_ts((ep or {}).get("start", 0.0)) if ep else "")
            v_end.set(short_ts((ep or {}).get("end", 0.0)) if ep else "")
            v_approx.set(bool((ep or {}).get("approx")))
            v_note.set((ep or {}).get("note") or "")
            btn_apply.config(text="Save changes" if st["id"] else "Add episode")
            lbl_msg.config(text="")

        def on_select(event=None):
            sel = tree.selection()
            if sel:
                load(self.active_doc.get_episode(sel[0]))

        def from_selection():
            segs = self._selected_segments()
            if not segs:
                lbl_msg.config(text="Select segments in the transcript first (drag, ⇧-click or ⇧↓).")
                return
            d = self.active_doc
            load({"type": v_type.get() or self._episode_types()[0], "label": "",
                  "start": min(float(d.segments[i]["start"]) for i in segs), "end": max(float(d.segments[i]["end"]) for i in segs)})

        def apply():
            a, b = eplib.parse_time(v_start.get()), eplib.parse_time(v_end.get())
            if a is None or b is None or b <= a:
                lbl_msg.config(text="Start and end as h:mm:ss (or seconds); the end after the start.")
                return None
            if not v_type.get().strip():
                lbl_msg.config(text="Give the episode a type (e.g. Interview).")
                return None
            d = self.active_doc
            if v_type.get().strip() == "Interview" and not v_label.get().strip() and not st["id"]:
                v_label.set(self._next_interview_name() or "")
            fields = dict(type=v_type.get().strip(), label=v_label.get().strip(), start=a, end=b, approx=bool(v_approx.get()),
                          note=v_note.get().strip())
            if st["id"]:
                d.update_episode(st["id"], **fields)
                msg = f"Episode changed: {fields['type']} {fields['label']}"
            else:
                st["id"] = d.add_episode(fields["type"], fields["label"], a, b, fields["approx"], note=fields["note"])
                msg = f"Episode added: {fields['type']} {fields['label']} ({short_ts(a)[:-2]} – {short_ts(b)[:-2]})"
            self._commit(msg)
            self._rerender()
            fill()
            tree.selection_set(st["id"])
            return st["id"]

        def delete():
            if not st["id"]:
                return
            ep = self.active_doc.get_episode(st["id"])
            self.active_doc.remove_episode(st["id"])
            self._commit(f"Episode removed: {ep['type']} {ep.get('label') or ''}")
            self._rerender()
            fill()
            load(None)

        def goto():
            ep = self.active_doc.get_episode(st["id"]) if st["id"] else None
            if ep is None:
                return
            idx = self.active_doc.episode_segments(ep)
            if idx:
                self._goto_segment(idx[0])
            self.notebook.select(self.tab_preview)

        def export():
            ep = self.active_doc.get_episode(st["id"]) if st["id"] else None
            if ep is None:
                lbl_msg.config(text="Choose an episode in the list first.")
                return None
            return self._export_episode_dialog(ep)

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(10, 0))
        self._tool(bar, "New from selection", from_selection).pack(side=tk.LEFT)
        btn_apply = self._tool(bar, "Add episode", apply, primary=True)
        btn_apply.pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Delete", delete).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Go to", goto).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Export clip…", export).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Find interview clips…", lambda: self._link_clips_dialog(on_done=fill)).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-Button-1>", lambda e: goto())
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        fill()
        load(preset)
        st.update(tree=tree, type=v_type, label=v_label, start=v_start, end=v_end, approx=v_approx, note=v_note, apply=apply,
                  delete=delete, load=load, select=lambda i: (tree.selection_set(i), on_select()), from_selection=from_selection,
                  export=export, goto=goto, msg=lbl_msg, fill=fill)
        return win, st

    # ================================================================== export one episode as a clip
    def _episode_default_dir(self, ep: Dict) -> Path:
        """Interviews go next to the other cut-out interviews of this group (a folder named like '…Interview…'), else
        next to the transcript."""
        doc = self.active_doc
        project, st = self._structure_ctx()
        if st is not None and ep["type"] == "Interview":
            target = st.role_folder(project, "interviews", st.values_for(project, doc.path))
            if target is not None:
                return target
        saved = (self.settings.get("episode_dirs") or {}).get(ep["type"])
        f = proj.find_project_file(Path(doc.path).parent)
        group = proj.level_folder(f.parent, doc.path, proj.project_settings(f.parent)["levels"]) if f else None
        if saved and group is not None and Path(saved).is_dir():
            # the same kind of folder in this group, e.g. Day 2/Group 3/Semi-structured Interviews
            twin = group / Path(saved).name
            if twin.is_dir():
                return twin
        if group is not None:
            for d in sorted(group.iterdir()):
                if d.is_dir() and ep["type"].lower() in d.name.lower() and "unsorted" not in d.name.lower():
                    return d
        return doc.out_dir

    def _export_episode_dialog(self, ep: Dict):
        doc = self.active_doc
        src = self._trim_source()
        if src is None:
            messagebox.showinfo("Export clip", "The recording for this transcript was not found.", parent=self)
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title("Export episode as a clip")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, eplib.describe(ep, short_ts), font="body_bold", bg=T["bg_panel"]).pack(anchor="w")
        self._label(win, "Writes the clip and a transcript timed to it (subtitles, TXT, paragraphs for MAXQDA). Import the two "
                         "together.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=520, justify=tk.LEFT
                    ).pack(anchor="w", pady=(4, 8))
        project, st = self._structure_ctx()
        if st is not None and ep["type"] == "Interview" and not st.parse_clip_name(ep.get("label") or ""):
            default_name = self._next_interview_name() or f"{doc.base_name}_{ep['type']}"
        else:
            default_name = ep.get("label") or f"{doc.base_name}_{ep['type']}"
        v_name = tk.StringVar(value=default_name)
        v_dir = tk.StringVar(value=str(self._episode_default_dir(ep)))
        v_mode = tk.StringVar(value=self.settings.get("episode_mode", "exact"))
        grid = tk.Frame(win, bg=T["bg_panel"])
        grid.pack(fill=tk.X)
        self._label(grid, "Name", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=0, column=0, sticky="w")
        self._entry(grid, v_name, font="small", width=40).grid(row=0, column=1, sticky="we", padx=6, pady=2)
        self._label(grid, "Folder", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1, column=0, sticky="w")
        self._entry(grid, v_dir, font="small", width=40).grid(row=1, column=1, sticky="we", padx=6, pady=2)
        self._tool(grid, "Choose…", lambda: v_dir.set(self._ask_directory("Save the clip in", v_dir.get()) or v_dir.get())
                   ).grid(row=1, column=2)
        for value, text in (("exact", "Exact — starts and ends exactly there (re-encodes; a few seconds for an interview)"),
                            ("lossless", "Lossless — no re-encoding, but starts at the keyframe before (up to a few seconds early)")):
            tk.Radiobutton(win, text=text, variable=v_mode, value=value, bg=T["bg_panel"], fg=T["fg_primary"], selectcolor=T["bg_input"],
                           activebackground=T["bg_panel"], font=self.f["small"], anchor="w").pack(anchor="w", fill=tk.X)
        lbl = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl.pack(anchor="w")

        def go():
            name = "".join(c for c in v_name.get().strip() if c not in '/\\:*?"<>|') or default_name
            out = Path(v_dir.get()).expanduser()
            target = trimlib.output_path(src, out, name, v_mode.get(), True)
            if target.resolve() == Path(src).resolve():
                lbl.config(text="That would overwrite the recording itself. Choose another name or folder.")
                return None
            if target.exists() and not messagebox.askyesno("Export clip", f"{target.name} already exists. Replace it?", parent=win):
                return None
            self.settings["episode_mode"] = v_mode.get()
            self.settings.setdefault("episode_dirs", {})[ep["type"]] = str(out)
            win.destroy()
            return self._start_episode_export(src, ep, name, out, v_mode.get())

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(12, 0))
        self._tool(bar, "Export", go, primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        return win, {"name": v_name, "dir": v_dir, "mode": v_mode, "go": go}

    def _start_episode_export(self, src: Path, ep: Dict, name: str, out_dir: Path, mode: str, wait: bool = False):
        doc_path = str(self.active_doc.path)
        self._append_log(f"=== Exporting {ep['type']} {ep.get('label') or ''} as {name} ({mode}) ===", "STAGE")

        def work():
            try:
                info = trimlib.probe(src)
                end = min(ep["end"], info["duration"] or ep["end"])
                dst = trimlib.output_path(src, out_dir, name, mode, info["video"])
                res = trimlib.write_trimmed(src, dst, [(ep["start"], end)], mode)
                self.msg_queue.put({"type": "episode", "event": "done", "dst": str(dst), "kept": res["kept"], "ep": ep, "name": name,
                                    "out_dir": str(out_dir), "src": str(src), "mode": mode, "doc": doc_path})
            except Exception as exc:
                self.msg_queue.put({"type": "episode", "event": "failed", "error": str(exc)})

        if wait:
            work()
            return None
        threading.Thread(target=work, daemon=True).start()
        return None

    def _on_episode_msg(self, msg: Dict):
        if msg["event"] == "failed":
            self._append_log(f"Clip export failed: {msg['error']}", "ERROR")
            messagebox.showerror("Export clip", f"The export failed:\n\n{msg['error'][-600:]}", parent=self)
            return
        doc = self.active_doc
        if doc is None or str(doc.path) != msg["doc"]:
            doc = TranscriptDocument.load(Path(msg["doc"]))
        kept = [tuple(k) for k in msg["kept"]]
        out_dir = Path(msg["out_dir"])
        created = doc.export_episode(msg["ep"], kept, msg["name"], out_dir)
        record = {"created": now_iso(), "app": {"name": "Condense", "version": __version__}, "source": msg["src"],
                  "transcript": msg["doc"], "episode": msg["ep"], "mode": msg["mode"], "kept_as_written": [list(k) for k in kept],
                  "note": "Times in 'episode' are on the source recording; the clip's transcript starts at 0."}
        (out_dir / f"{msg['name']}.episode.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        doc.log_edit("export_episode", id=msg["ep"]["id"], output=Path(msg["dst"]).name, mode=msg["mode"])
        if msg["ep"]["type"] == "Interview":  # the episode now carries the clip's name and knows its clip
            doc.update_episode(msg["ep"]["id"], label=msg["name"], source={"clip": msg["dst"], "exported": now_iso()})
        doc.save()
        if self.active_doc is not None and str(self.active_doc.path) == msg["doc"]:
            self.active_doc = doc
            self._rerender()
        line = f"Clip: {msg['dst']} — transcript: {', '.join(p.name for p in created.values())}"
        self._append_log(line, "SUCCESS")
        self.lbl_status.config(text=f"Exported {Path(msg['dst']).name} with its transcript.")

    # ================================================================== find cut-out clips in the recording
    def _candidate_clips(self) -> List[Path]:
        """Videos in this group's interview folders (…Interview…), when the transcript is in a project with levels."""
        doc = self.active_doc
        media = self._media()
        project, st = self._structure_ctx()
        if st is not None:
            folder = st.role_folder(project, "interviews", st.values_for(project, doc.path))
            if folder is None or not folder.is_dir():
                return []
            return [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
                    and not p.name.startswith(".") and (media is None or p.resolve() != Path(media).resolve())]
        f = proj.find_project_file(Path(doc.path).parent)
        if f is None:
            return []
        group = proj.level_folder(f.parent, doc.path, proj.project_settings(f.parent)["levels"])
        if group is None:
            return []
        media = self._media()
        out = []
        for d in sorted(p for p in group.iterdir() if p.is_dir() and "interview" in p.name.lower() and "unsorted" not in p.name.lower()):
            for p in sorted(d.rglob("*")):
                if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not is_system_junk(p.name) and not p.name.startswith(".") \
                        and (media is None or p.resolve() != Path(media).resolve()):
                    out.append(p)
        return out

    def _link_clips_dialog(self, on_done=None):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open the session transcript first.")
            return None
        media = self._media()
        if media is None:
            messagebox.showinfo("Find interview clips", "The recording for this transcript was not found.", parent=self)
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Find interview clips in {doc.base_name}")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Interviews you cut out of this recording earlier are found again by their sound and marked as "
                         "Interview episodes at the right place.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=640, justify=tk.LEFT).pack(anchor="w")
        cols = ("found", "score", "status")
        tree = ttk.Treeview(win, columns=cols, show="tree headings", height=8, style="Queue.Treeview")
        tree.heading("#0", text="Clip")
        tree.column("#0", width=260)
        for c, w, title in (("found", 170, "Found at"), ("score", 70, "Match"), ("status", 220, "")):
            tree.heading(c, text=title)
            tree.column(c, width=w, anchor="center" if c != "status" else "w")
        tree.pack(fill=tk.BOTH, expand=True, pady=8)
        lbl = self._label(win, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        lbl.pack(anchor="w")
        st: Dict = {"clips": [], "results": {}, "stop": threading.Event(), "busy": False}
        linked = {str(Path((e.get("source") or {}).get("clip", "")).resolve()) for e in doc.episodes if (e.get("source") or {}).get("clip")}

        def show():
            for item in tree.get_children():
                tree.delete(item)
            for i, p in enumerate(st["clips"]):
                r = st["results"].get(str(p))
                if r is None:
                    vals = ("", "", "already an episode" if str(p.resolve()) in linked else "")
                elif "error" in r:
                    vals = ("", "", f"⚠ {r['error'][:60]}")
                else:
                    vals = (f"{short_ts(r['start'])[:-2]} – {short_ts(r['end'])[:-2]}", f"{r['score'] * 100:.0f}%",
                            "✓ found" if r["confident"] else "? unsure — check by listening")
                tree.insert("", tk.END, iid=str(i), text=p.name, values=vals)
            found = sum(1 for r in st["results"].values() if r.get("confident"))
            if st["results"]:
                lbl.config(text=f"{found} of {len(st['clips'])} clip(s) found.")
            elif not st["clips"]:
                lbl.config(text="No clips yet: Add clips… (or keep them in this group's '…Interviews' folder in a project).")
            else:
                lbl.config(text=f"{len(st['clips'])} clip(s). Find them in {Path(media).name}.")

        def add_clips():
            start = str(st["clips"][-1].parent) if st["clips"] else str(Path(media).parent)
            files = self._ask_open_files("Interview clips cut from this recording", start, [("Video or audio", " ".join(f"*{s}" for s in sorted(VIDEO_SUFFIXES)))])
            for f in files:
                p = Path(f)
                if p not in st["clips"] and not is_system_junk(p.name):
                    st["clips"].append(p)
            show()

        def find(wait: bool = False):
            if st["busy"] or not st["clips"]:
                return
            st["busy"] = True
            clips = [p for p in st["clips"] if str(p) not in st["results"]]
            cache = APP_SUPPORT_DIR / "cache" / "audio"
            stop = st["stop"]
            q = st["q"] = __import__("queue").Queue()

            def work():
                try:
                    q.put(("status", f"Reading the sound of {Path(media).name} (once; a few seconds per hour of recording)…"))
                    session = align.SessionAudio(Path(media), cache_dir=cache, stop=stop, duration=self._duration())
                    for p in clips:
                        q.put(("status", f"Looking for {p.name}…"))
                        try:
                            q.put(("result", str(p), align.find_clip(session, p, stop=stop)))
                        except InterruptedError:
                            raise
                        except Exception as exc:
                            q.put(("result", str(p), {"error": str(exc)}))
                except InterruptedError:
                    pass
                except Exception as exc:
                    q.put(("status", f"⚠ {exc}"))
                q.put(("done",))

            def drain():
                import queue as _q
                while True:
                    try:
                        item = q.get_nowait()
                    except _q.Empty:
                        break
                    if item[0] == "status":
                        lbl.config(text=item[1])
                    elif item[0] == "result":
                        st["results"][item[1]] = item[2]
                        show()
                    elif item[0] == "done":
                        st["busy"] = False
                        show()
                        return True
                return False

            if wait:
                work()
                drain()
                return None

            def poll():
                if not win.winfo_exists():
                    stop.set()
                    return
                if not drain():
                    win.after(150, poll)
            threading.Thread(target=work, daemon=True).start()
            win.after(150, poll)
            return None

        def add_episodes(include_unsure: bool = False):
            d = self.active_doc
            added = updated = 0
            by_clip = {str(Path((e.get("source") or {}).get("clip", "")).resolve()): e for e in d.episodes if (e.get("source") or {}).get("clip")}
            for p in st["clips"]:
                r = st["results"].get(str(p))
                if not r or "error" in r or not (r["confident"] or include_unsure):
                    continue
                source = {"clip": str(p), "match": r["score"], "found": now_iso()}
                old = by_clip.get(str(p.resolve()))
                if old is not None:
                    d.update_episode(old["id"], start=r["start"], end=r["end"], source=source)
                    updated += 1
                else:
                    d.add_episode("Interview", p.stem, r["start"], r["end"], source=source)
                    added += 1
            if added or updated:
                self._commit(f"Interview clips placed: {added} new, {updated} updated")
                self._rerender()
            lbl.config(text=f"{added} episode(s) added, {updated} updated.")
            on_done and on_done()
            return added, updated

        def close():
            st["stop"].set()
            win.destroy()

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(10, 0))
        self._tool(bar, "Add clips…", add_clips).pack(side=tk.LEFT)
        self._tool(bar, "Find in recording", find, primary=True).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Add found as episodes", add_episodes).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", close).pack(side=tk.RIGHT)
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: (close(), "break")[-1])
        st["clips"] = self._candidate_clips()
        show()
        st.update(tree=tree, lbl=lbl, find=find, add=add_episodes, add_clips=add_clips, show=show, close=close)
        return win, st
