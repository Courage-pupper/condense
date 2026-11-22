"""Transcript editor: checked marks, markers, speaker chips and number keys."""
from condense.gui._common import *  # noqa: F401,F403


class ReviewMarksMixin:
    # ----- checked marks -----
    def _update_task_progress(self, doc: TranscriptDocument):
        pct = round(doc.checked_fraction() * 100)
        changed = False
        for t in self.jobs:
            if t.get("doc_path") == str(doc.path):
                t["checked_pct"] = pct
                if t["status"] == "Completed":
                    t["stage"] = f"Done ✓ · {pct}% checked"
                self._update_job_row(t)
                changed = True
        if changed:
            self._save_queue()
        if self.__dict__.get("pp_tree") is not None:
            self._pp_update_doc(doc)

    def _toggle_checked(self):
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or not self._selected_segments():
            self.lbl_status.config(text="Click a word to pick a segment first.")
            return
        self._autosave_edit(rerender=False)
        segs = self._selected_segments()
        if len(segs) > 1:  # several selected: mark them all checked (or all unchecked if they already are)
            now = not all(doc.segments[s].get("checked") for s in segs)
            for s in segs:
                doc.set_checked(s, now)
            if self._commit(f"Marked {len(segs)} segments ({segs[0] + 1}–{segs[-1] + 1}) {'checked' if now else 'not checked'}.") is not None:
                self._rerender()
                if self.edit_seg_idx is not None:
                    self._load_segment_into_editor(self.edit_seg_idx)
            return
        now = not bool(doc.segments[idx].get("checked"))
        doc.set_checked(idx, now)
        if self._commit(f"Segment {idx + 1} marked {'checked' if now else 'not checked'}.") is not None:
            self._rerender()
            self._load_segment_into_editor(idx)

    def _check_and_next_key(self, event=None):
        """Ctrl+Enter anywhere in the main window, but only while the Transcript tab is showing (never from a dialog
        or another tab, where it would check a segment you cannot see)."""
        try:
            if event is not None and event.widget.winfo_toplevel() is not self:
                return None
            if str(self.notebook.select()) != str(self.tab_preview):
                return None
        except Exception:
            return None
        self._check_and_next()
        return "break"

    def _check_and_next(self):
        """Save the segment (text + speaker), mark it checked and move to the next one: the review loop."""
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or idx is None:
            self.lbl_status.config(text="Click a word to pick a segment first.")
            return
        text = self.edit_text.get("1.0", "end-1c")
        if is_blank_text(text) and not is_blank_text(self.edit_original[0]):
            self._delete_segment()
            return
        doc.edit_segment_text(idx, text)
        doc.set_segment_speaker(idx, self.var_seg_speaker.get())
        doc.set_segment_note(idx, self.var_seg_note.get())
        doc.set_checked(idx, True)
        if self._commit(f"Checked segment {idx + 1}.") is None:
            return
        self.edit_original = ("", "")
        self._rerender()
        nxt = next((i for i in range(idx + 1, len(doc.segments)) if not is_blank_text(doc.segments[i].get("text"))), None)
        if nxt is None:
            self._load_segment_into_editor(idx)
            self.lbl_status.config(text=f"Checked segment {idx + 1} — that was the last one. {doc.checked_fraction() * 100:.0f}% checked.")
            return
        first = self.span_lookup.get((nxt, 0))
        if first is not None:
            self._select_span(first)
        else:
            self._load_segment_into_editor(nxt)
        self.edit_text.focus_set()

    # ----- markers -----
    def _show_marker_menu(self):
        self.marker_menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _insert_marker(self, template: str):
        if self.active_doc is None or self.edit_seg_idx is None:
            self.lbl_status.config(text="Click a word to open its segment, then insert the marker where it belongs.")
            return
        text = template.replace("{t}", short_ts(self._current_pos())[:-2])
        box = self.edit_text
        box.focus_set()
        before = box.get("insert-1c", "insert")
        if before and not before.isspace():
            text = " " + text
        box.insert("insert", text + ("" if text.endswith(": ]") else " "))
        if text.endswith(": ]"):  # [unclear: |] — put the cursor where the guess goes
            box.mark_set("insert", "insert-1c")

    # ----- speaker chips in the segment editor -----
    def _refresh_speaker_chips(self):
        chips = self.__dict__.get("spk_chips")
        if chips is None:
            return
        chips.clear()
        doc = self.active_doc
        if doc is None:
            return
        T = THEME
        segs = [s for s in (self.__dict__.get("sel_segs") or []) if s < len(doc.segments)]
        if len(segs) > 1:
            names = {doc.segments[s].get("speaker") or "" for s in segs}
            current = names.pop() if len(names) == 1 else ""
            chips.add(self._label(chips, f"Assign to {len(segs)} selected segments ({segs[0] + 1}–{segs[-1] + 1}):", font="small_bold",
                                  fg="#c4b5fd"))
        else:
            current = self.var_seg_speaker.get().strip()
            chips.add(self._label(chips, "Assign:", font="small", fg=T["fg_secondary"]))
        for i, name in enumerate(self._roster()):
            color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
            active = name == current
            chip = tk.Label(chips, text=(f"{i + 1}  " if i < 9 else "") + name, font=self.f["small_bold"], padx=8, pady=2, cursor="hand2",
                            bg=color if active else T["bg_input"], fg=T["bg_root"] if active else color,
                            highlightthickness=1, highlightbackground=color)
            chip.bind("<Button-1>", lambda e, n=name: self._assign_speaker(n))
            chips.add(chip)
        add = tk.Label(chips, text="0  + Speaker", font=self.f["small"], padx=8, pady=2, cursor="hand2", bg=T["bg_input"], fg=T["fg_secondary"],
                       highlightthickness=1, highlightbackground=T["border"])
        add.bind("<Button-1>", lambda e: self._add_speaker_quick())
        chips.add(add)

    def _assign_speaker(self, name: str):
        if self.active_doc is None or not self._selected_segments():
            self.lbl_status.config(text="Click a word in the transcript first, then pick the speaker for that segment.")
            return
        segs = self._selected_segments()
        if len(segs) > 1:
            self._assign_speaker_many(name, segs)
            return
        self.var_seg_speaker.set(name)
        self._save_segment_edit()
        self._refresh_speaker_chips()

    def _assign_speaker_many(self, name: str, segs: List[int]):
        """One speaker for many segments: a single save, a single undo step."""
        doc = self.active_doc
        self._autosave_edit(rerender=False)  # keep a text edit that was still being typed
        if len(doc.segments) != self._sel_nseg:  # that save deleted a segment: the selection no longer fits
            self._clear_multi_selection()
            self._rerender()
            return
        changed = [s for s in segs if doc.set_segment_speaker(s, name)]
        if not changed:
            self.lbl_status.config(text=f"All {len(segs)} selected segments are already {name}.")
            return
        if self._commit(f"Assigned {name} to {len(changed)} segment(s) between {segs[0] + 1} and {segs[-1] + 1} ({'⌘' if IS_MAC else 'Ctrl+'}Z undoes).") is not None:
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(self.edit_seg_idx)
            self._refresh_speaker_chips()

    def _on_speaker_combo(self):
        """Picking a speaker in the editor's list applies it to the whole selection when several segments are selected."""
        if len(self._selected_segments()) > 1 and self.var_seg_speaker.get().strip():
            self._assign_speaker(self.var_seg_speaker.get().strip())

    def _assign_speaker_number(self, number: int):
        doc = self.active_doc
        if doc is None:
            return
        roster = self._roster()
        if 1 <= number <= len(roster):
            self._assign_speaker(roster[number - 1])

    def _add_speaker_quick(self):
        doc = self.active_doc
        if doc is None:
            return
        name = simpledialog.askstring("Add speaker", "Name of the new speaker (e.g. P03, Facilitator):", parent=self)
        if not name or not name.strip():
            return
        if doc.add_speaker(name) and self._commit(f"Added speaker {name.strip()}.") is not None:
            self._rerender()
            if self._selected_segments():
                self._assign_speaker(name.strip())
