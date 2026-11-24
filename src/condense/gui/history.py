"""Transcript editor: saving changes, undo/redo, backups, resuming where you left off."""
from condense.gui._common import *  # noqa: F401,F403


class HistoryMixin:
    # From about an hour of conversation on, writing the six exports takes long enough to be felt after every
    # correction, so they are written by a worker thread from the file just saved. The master file (.transcript.json)
    # is always saved at once; several saves in a row give one export of the newest state.
    BACKGROUND_EXPORT_SEGMENTS = 1500

    def _export(self, doc: TranscriptDocument, now: bool = False) -> Dict[str, Path]:
        if now or len(doc.segments) < self.BACKGROUND_EXPORT_SEGMENTS:
            self._wait_for_exports()  # never two writers on the same files
            return doc.export_all()
        st = self.__dict__.get("_bg_export")
        if st is None:
            st = self._bg_export = {"lock": threading.Lock(), "pending": {}, "thread": None}
        with st["lock"]:
            st["pending"][str(doc.path)] = doc.path
            if st["thread"] is None:
                st["thread"] = threading.Thread(target=self._export_worker, args=(st,), daemon=True)
                st["thread"].start()
        return {}

    def _export_worker(self, st: Dict):
        while True:
            with st["lock"]:
                if not st["pending"]:
                    st["thread"] = None
                    return
                path = st["pending"].pop(next(iter(st["pending"])))
            try:
                TranscriptDocument.load(path).export_all()  # the saved state; the editor's copy keeps changing
            except Exception as exc:
                self.msg_queue.put({"type": "log", "text": f"Could not update the exports of {path.name}: {exc}", "level": "ERROR"})

    def _wait_for_exports(self, timeout: float = 60.0):
        """Before quitting: let exports that are being written finish."""
        st = self.__dict__.get("_bg_export")
        deadline = time.monotonic() + timeout
        while st is not None and time.monotonic() < deadline:
            with st["lock"]:
                thread = st["thread"]
            if thread is None:
                return
            thread.join(timeout=max(0.1, deadline - time.monotonic()))

    def _commit(self, message: str, undoable: bool = True, export_now: bool = False) -> Optional[Dict[str, Path]]:
        """Save the master file (with a throttled backup) and regenerate every export (long sessions: in the
        background, see _export). The file as it was before is pushed on the undo stack. Returns the files written
        now (empty when they are written in the background), or None."""
        doc = self.active_doc
        try:
            previous = doc.path.read_text(encoding="utf-8") if (undoable and doc.path.exists()) else None
            doc.save(backup=True)
            created = self._export(doc, now=export_now)
        except Exception as exc:
            messagebox.showerror("Could not save transcript", str(exc), parent=self)
            return None
        if previous is not None:
            self._push_undo(doc.path, message, previous)
        self.lbl_status.config(text=message)
        self._update_task_progress(doc)
        self._timeline_redraw()
        return created

    # ----- undo / redo / backups -----
    def _text_input_focused(self) -> bool:
        w = self.focus_get()
        return w is not None and w is not self.preview_text and w.winfo_class() in ("Text", "Entry", "TEntry", "TCombobox", "Spinbox")

    def _undo_key(self, event=None):
        if self._text_input_focused():
            return None  # let the text field undo its own typing
        self._undo()
        return "break"

    def _redo_key(self, event=None):
        if self._text_input_focused():
            return None
        self._redo()
        return "break"

    # An undo step is the whole transcript file as it was. For a 5-hour session that is ~6.5 MB, so 100 steps held
    # ~650 MB of memory; zlib (fastest level) makes a step ~1 MB in ~30 ms.
    UNDO_STEPS = 100

    @staticmethod
    def _pack_state(text: str) -> bytes:
        return zlib.compress(text.encode("utf-8"), 1)

    @staticmethod
    def _unpack_state(packed) -> str:
        return packed if isinstance(packed, str) else zlib.decompress(packed).decode("utf-8")

    def _push_undo(self, path, message: str, previous_text: str):
        """A new undo step (the file as it was before a change); clears redo."""
        undo, redo = self._undo_stacks.setdefault(str(path), ([], []))
        undo.append((message, self._pack_state(previous_text)))
        del undo[:-self.UNDO_STEPS]
        redo.clear()

    def _swap_state(self, from_stack: List, to_stack: List, verb: str):
        doc = self.active_doc
        label, packed = from_stack.pop()
        text = self._unpack_state(packed)
        to_stack.append((label, self._pack_state(doc.path.read_text(encoding="utf-8"))))
        keep_seg = self.edit_seg_idx
        doc.data = json.loads(text)
        try:
            doc.save(backup=True)
            self._export(doc)
        except Exception as exc:
            messagebox.showerror("Could not save transcript", str(exc), parent=self)
            return
        self.edit_original = ("", "")
        self._rerender()
        if doc.segments:
            self._load_segment_into_editor(min(keep_seg or 0, len(doc.segments) - 1))
        self._update_task_progress(doc)
        self.lbl_status.config(text=f"{verb}: {label}")
        self._append_log(f"{verb}: {label}", "INFO")

    def _undo(self):
        doc = self.active_doc
        if doc is None:
            return
        if self._editor_dirty():
            self._revert_segment_edit()
            self.lbl_status.config(text="Undid the unsaved edit in the segment box.")
            return
        undo, redo = self._undo_stacks.get(str(doc.path), ([], []))
        if not undo:
            self.lbl_status.config(text="Nothing to undo for this transcript (since it was opened).")
            return
        self._swap_state(undo, redo, "Undid")

    def _redo(self):
        doc = self.active_doc
        if doc is None:
            return
        undo, redo = self._undo_stacks.get(str(doc.path), ([], []))
        if not redo:
            self.lbl_status.config(text="Nothing to redo.")
            return
        self._swap_state(redo, undo, "Redid")

    def _restore_backup(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        if not doc.list_backups():
            messagebox.showinfo("Backups", "No backups yet for this transcript. They are made automatically while you edit "
                                           f"(at most every {doc.BACKUP_EVERY_S // 60} minutes, the last {doc.BACKUPS_KEPT} are kept).", parent=self)
            return
        path = self._ask_open_file("Restore which backup?", str(doc.backup_dir), [("Transcript backup", f"*{TranscriptDocument.SUFFIX}")])
        if not path:
            return
        try:
            data = TranscriptDocument.load(Path(path)).data
        except Exception as exc:
            messagebox.showerror("Could not read backup", str(exc), parent=self)
            return
        self._autosave_edit()
        data["base_name"] = doc.base_name
        doc.data = data
        doc.log_edit("restore_backup", file=Path(path).name)
        if self._commit(f"Restored backup {Path(path).name}") is not None:
            self.edit_original = ("", "")
            self._rerender()

    def _show_backups(self):
        doc = self.active_doc
        if doc is not None:
            folder = doc.backup_dir if doc.backup_dir.exists() else doc.out_dir
            self._open_externally(folder)

    # ----- resume where you left off -----
    def _remember_position(self):
        doc = self.active_doc
        if doc is None:
            return
        positions = self.settings.setdefault("positions", {})
        positions[str(doc.path)] = {"pos": round(self._current_pos(), 2), "seg": self.edit_seg_idx}
        if len(positions) > 300:
            for k in list(positions)[: len(positions) - 300]:
                positions.pop(k, None)

    def _resume_position(self, doc: TranscriptDocument):
        saved = (self.settings.get("positions") or {}).get(str(doc.path)) or {}
        pos = float(saved.get("pos", 0.0) or 0.0)
        seg = saved.get("seg")
        self._pending_resume = (pos, seg) if (pos or seg is not None) else None
        self.after_idle(self._apply_resume)

    def _apply_resume(self):
        pending = self.__dict__.get("_pending_resume")
        self._pending_resume = None
        doc = self.active_doc
        if not pending or doc is None:
            return
        pos, seg = pending
        if seg is not None and 0 <= seg < len(doc.segments):
            idx = self.span_lookup.get((seg, 0))
            if idx is not None:
                self._select_span(idx)
            else:
                self._load_segment_into_editor(seg)
        self.play_pos = pos
        self._update_time_label()
        if self._media() is not None:
            self.lbl_word.config(text=f"Resumed where you left off: {short_ts(pos)[:-2]} (segment {seg + 1 if seg is not None else '–'}).",
                                 fg=THEME["fg_secondary"])
