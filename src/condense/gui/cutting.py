"""Cutting: leave parts of a recording out (X), and export a trimmed video with a transcript timed to it."""
from condense import __version__
from condense.core import cuts as cutlib
from condense.gui._common import *  # noqa: F401,F403
from condense.media import trim as trimlib


class CuttingMixin:
    # ----- cut / restore -----
    def _toggle_cut(self):
        """X: cut the selected segments (or the open one); if they are all cut already, restore them."""
        doc = self.active_doc
        segs = self._selected_segments() if doc is not None else []
        if not segs:
            self.lbl_status.config(text="Select segments first (click a word; drag or ⇧-click for several), then press X.")
            return
        self._autosave_edit(rerender=False)
        if all(doc.is_segment_cut(i) for i in segs):
            n = doc.restore_segments(segs)
            msg = f"Restored {n} segment(s)."
        else:
            n = doc.cut_segments(segs)
            msg = f"Cut {n} segment(s): left out of the exports and skipped when playing ({cutlib.describe(doc.cuts)} in total)."
        if n and self._commit(msg + f" {'⌘' if IS_MAC else 'Ctrl+'}Z undoes.") is not None:
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(self.edit_seg_idx)

    def _restore_all_cuts(self):
        doc = self.active_doc
        if doc is None or not doc.cuts:
            return
        if not messagebox.askyesno("Restore all cuts", f"Restore all {cutlib.describe(doc.cuts)}?", parent=self):
            return
        doc.set_cuts([])
        doc.log_edit("restore_all_cuts")
        if self._commit("Restored all cuts.") is not None:
            self._rerender()

    def _cut_skip_target(self, previous: float, pos: float) -> Optional[float]:
        """While playing: entering a cut from outside jumps to its end. Starting inside a cut (you clicked a cut word
        to listen to it) plays it."""
        doc = self.active_doc
        if doc is None or not self.__dict__.get("var_skip_cuts") or not self.var_skip_cuts.get():
            return None
        for s, e in doc.cuts:
            if previous < s <= pos + 0.05 and pos < e:
                return e
        return None

    # ----- trimmed export -----
    def _trim_source(self) -> Optional[Path]:
        # a linked video on another timeline (cut differently, started earlier) would be cut in the wrong places
        return self._video_on_timeline() or self._media()

    def _export_trimmed_dialog(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        src = self._trim_source()
        if src is None:
            messagebox.showinfo("Export trimmed", "The recording for this transcript was not found.", parent=self)
            return
        if not doc.cuts:
            messagebox.showinfo("Export trimmed", "Nothing is cut yet. Select segments in the transcript and press X to cut them.",
                                parent=self)
            return
        T = THEME
        info = trimlib.probe(src)
        dur = info["duration"] or self._duration()
        kept = cutlib.keep_intervals(doc.cuts, dur)
        new_len = sum(b - a for a, b in kept)
        win = tk.Toplevel(self)
        win.title("Export trimmed video and transcript")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Export trimmed video and transcript", font="title", bg=T["bg_panel"]).pack(anchor="w")
        self._label(win, f"{cutlib.describe(doc.cuts)} cut from {src.name}: {short_ts(dur)[:-2]} → {short_ts(new_len)[:-2]}.\n"
                         "The transcript is re-timed to the trimmed file, so MAXQDA links it correctly — import the trimmed "
                         "video together with the trimmed transcript.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    justify=tk.LEFT, wraplength=520).pack(anchor="w", pady=(4, 10))
        v_mode = tk.StringVar(value=self.settings.get("trim_mode", "exact"))
        for value, text in (("exact", "Exact — cuts exactly where you cut (re-encodes: H.264, slower)"),
                            ("lossless", "Lossless — fast, no quality loss, but cuts can only start on a keyframe: up to a "
                                         "few seconds of a cut part may remain. Not for removing sensitive passages.")):
            tk.Radiobutton(win, text=text, variable=v_mode, value=value, bg=T["bg_panel"], fg=T["fg_primary"], selectcolor=T["bg_input"],
                           activebackground=T["bg_panel"], font=self.f["small"], justify=tk.LEFT, wraplength=520, anchor="w"
                           ).pack(anchor="w", fill=tk.X)
        v_llc = tk.BooleanVar(value=bool(self.settings.get("trim_save_llc", False)))
        tk.Checkbutton(win, text="Also save the cuts as a LosslessCut project next to the recording", variable=v_llc, bg=T["bg_panel"],
                       fg=T["fg_primary"], selectcolor=T["bg_input"], activebackground=T["bg_panel"], font=self.f["small"]
                       ).pack(anchor="w", pady=(8, 0))
        out_dir = doc.out_dir
        base = f"{doc.base_name}_trimmed"
        self._label(win, f"Writes into {out_dir}:\n  {trimlib.output_path(src, out_dir, base, 'exact', info['video']).name}"
                         f" (or {base}{src.suffix} lossless), {base}.srt / .txt / _paragraphs.rtf …, {base}.cuts.json",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"], justify=tk.LEFT, wraplength=520).pack(anchor="w", pady=(10, 0))
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(14, 0))

        def go():
            self.settings["trim_mode"] = v_mode.get()
            self.settings["trim_save_llc"] = bool(v_llc.get())
            win.destroy()
            self._start_trim_export(src, v_mode.get(), bool(v_llc.get()))

        self._tool(bar, "Export", go, primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win

    def _start_trim_export(self, src: Path, mode: str, save_llc: bool):
        doc = self.active_doc
        if self.__dict__.get("_trim_running"):
            return
        self._trim_running = True
        cuts = list(doc.cuts)
        out_dir, base = doc.out_dir, f"{doc.base_name}_trimmed"
        stop = threading.Event()
        self._trim_stop = stop
        self.notebook.select(self.tab_console)
        self._append_log(f"=== Exporting trimmed {src.name} ({mode}, {cutlib.describe(cuts)}) ===", "STAGE")

        def work():
            try:
                info = trimlib.probe(src)
                kept = cutlib.keep_intervals(cuts, info["duration"])
                dst = trimlib.output_path(src, out_dir, base, mode, info["video"])
                last = {"pct": -10}

                def progress(f):
                    if f * 100 - last["pct"] >= 10:
                        last["pct"] = f * 100
                        self.msg_queue.put({"type": "trim", "event": "progress", "pct": f * 100})

                res = trimlib.write_trimmed(src, dst, kept, mode, on_progress=progress, stop=stop)
                self.msg_queue.put({"type": "trim", "event": "done", "dst": str(dst), "kept": res["kept"], "not_applied": res["not_applied"],
                                    "mode": mode, "src": str(src), "base": base, "llc": save_llc, "cuts": cuts})
            except Exception as exc:
                self.msg_queue.put({"type": "trim", "event": "failed", "error": str(exc)})

        threading.Thread(target=work, daemon=True).start()

    def _on_trim_msg(self, msg: Dict):
        ev = msg["event"]
        if ev == "progress":
            self._append_log(f"  trimmed {msg['pct']:.0f}%", "INFO")
            return
        self._trim_running = False
        if ev == "failed":
            self._append_log(f"Trimmed export failed: {msg['error']}", "ERROR")
            messagebox.showerror("Export trimmed", f"The trimmed export failed:\n\n{msg['error'][-600:]}", parent=self)
            return
        doc = self.active_doc
        if doc is None:
            return
        kept = [tuple(k) for k in msg["kept"]]
        created = doc.export_trimmed(kept, msg["base"])
        record = {
            "created": now_iso(), "app": {"name": "Condense", "version": __version__}, "source": msg["src"], "output": msg["dst"],
            "mode": msg["mode"], "cuts": [list(c) for c in msg["cuts"]], "kept_as_written": [list(k) for k in kept],
            "cuts_not_applied": msg["not_applied"],
            "note": "Times are on the source recording's timeline; the trimmed transcript's times are on the trimmed file.",
        }
        rec_path = doc.out_dir / f"{msg['base']}.cuts.json"
        rec_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        doc.log_edit("export_trimmed", output=Path(msg["dst"]).name, mode=msg["mode"], cuts=len(msg["cuts"]))
        doc.save()
        if msg.get("llc"):
            self._save_llc(Path(msg["src"]), kept)
        lines = [f"Trimmed video: {msg['dst']}", f"Trimmed transcript: {', '.join(p.name for p in created.values())}"]
        if msg["not_applied"]:
            lines.append(f"{msg['not_applied']} cut(s) were too close to a keyframe to remove losslessly and are still in the "
                         "video (and the transcript). Use Exact to remove them.")
        for line in lines:
            self._append_log(line, "SUCCESS" if not line.startswith(str(msg["not_applied"])) else "WARN")
        self.lbl_status.config(text=f"Exported {Path(msg['dst']).name} with its transcript.")
        messagebox.showinfo("Export trimmed", "\n\n".join(lines), parent=self)

    def _save_llc(self, src: Path, kept) -> Optional[Path]:
        """LosslessCut opens <video>-proj.llc automatically; never overwrite one without asking."""
        target = src.parent / f"{src.stem}-proj.llc"
        if target.exists() and not messagebox.askyesno(
                "LosslessCut project", f"{target.name} already exists (your LosslessCut project?). Replace it with these cuts?", parent=self):
            return None
        target.write_text(cutlib.llc_text(src.name, kept), encoding="utf-8")
        self._append_log(f"Saved the cuts as a LosslessCut project: {target}", "INFO")
        return target
