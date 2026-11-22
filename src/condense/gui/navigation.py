"""Transcript editor: moving between segments, selecting several, keyboard shortcuts."""
from condense.gui._common import *  # noqa: F401,F403


class NavigationMixin:
    # ----- selecting several segments -----
    def _ix(self, index) -> Tuple[int, int]:
        line, col = str(self.preview_text.index(str(index))).split(".")
        return int(line), int(col)

    def _segments_in_sel(self) -> List[int]:
        """Segments touched by the mouse selection in the transcript (empty if nothing is selected)."""
        try:
            rng = self.preview_text.tag_ranges("sel")
        except Exception:
            return []
        if not rng or len(rng) < 2:
            return []
        a, b = self._ix(rng[0]), self._ix(rng[-1])
        return sorted(si for si, (s, e) in self.seg_ranges.items() if self._ix(s) < b and self._ix(e) > a)

    def _visible_segments(self) -> List[int]:
        return sorted(self.seg_ranges)

    def _extend_selection(self, anchor: int, to: int):
        lo, hi = min(anchor, to), max(anchor, to)
        segs = [s for s in self._visible_segments() if lo <= s <= hi]
        if len(segs) > 1:
            self._set_multi_selection(segs, anchor)
        else:
            self._clear_multi_selection()

    def _set_multi_selection(self, segs: List[int], anchor: Optional[int] = None):
        self.sel_segs = sorted(set(segs))
        self.sel_anchor = anchor if anchor is not None else self.sel_segs[0]
        self._sel_nseg = len(self.active_doc.segments) if self.active_doc is not None else 0
        self._paint_multi_selection()
        self._refresh_speaker_chips()

    def _clear_multi_selection(self):
        had = bool(self.__dict__.get("sel_segs"))
        self.sel_segs, self.sel_anchor = [], None
        self.preview_text.tag_remove("MULTISEL", "1.0", tk.END)
        if had:
            self._refresh_speaker_chips()

    def _paint_multi_selection(self):
        self.preview_text.tag_remove("MULTISEL", "1.0", tk.END)
        segs = self.__dict__.get("sel_segs") or []
        if not segs:
            return
        doc = self.active_doc
        # split / merge / insert / delete renumber segments: then the old selection would point at the wrong ones
        if doc is None or len(doc.segments) != self._sel_nseg or any(s not in self.seg_ranges for s in segs):
            self.sel_segs, self.sel_anchor = [], None
            return
        for s in segs:
            self.preview_text.tag_add("MULTISEL", *self.seg_ranges[s])

    def _selected_segments(self) -> List[int]:
        """What an action applies to: the selected segments, or the one open in the editor."""
        segs = self.__dict__.get("sel_segs") or []
        if len(segs) > 1:
            return list(segs)
        return [self.edit_seg_idx] if self.edit_seg_idx is not None else []

    def _select_all_segments(self):
        segs = self._visible_segments()
        if len(segs) > 1:
            self._set_multi_selection(segs, segs[0])
            self.lbl_status.config(text=f"Selected all {len(segs)} segments — press 1–9 to assign them all, click a word to cancel.")

    def _goto_segment(self, si: int):
        first = self.span_lookup.get((si, 0))
        if first is not None:
            self._select_span(first)
        else:
            self._load_segment_into_editor(si)
        rng = self.seg_ranges.get(si)
        if rng:
            self.preview_text.see(rng[0])

    def _move_segment(self, direction: int, extend: bool = False):
        """↑/↓ open the previous/next segment; with ⇧ they grow or shrink the selection."""
        segs = self._visible_segments()
        if self.active_doc is None or not segs:
            return
        cur = self.edit_seg_idx
        if cur is None:
            target = segs[0] if direction > 0 else segs[-1]
        else:
            later = [s for s in segs if (s > cur if direction > 0 else s < cur)]
            if not later:
                return
            target = later[0] if direction > 0 else later[-1]
        anchor = (self.sel_anchor if self.sel_anchor is not None else cur) if extend else None
        self._goto_segment(target)
        if extend and anchor is not None and self.edit_seg_idx is not None:
            self._extend_selection(anchor, self.edit_seg_idx)
        else:
            self._clear_multi_selection()

    def _focus_editor(self):
        if self.edit_seg_idx is None:
            self._move_segment(+1)
        if self.edit_seg_idx is not None:
            self.edit_text.focus_set()
            self.edit_text.mark_set("insert", "end-1c")

    def _focus_transcript(self):
        self._autosave_edit()
        self.preview_text.focus_set()

    def _replay_segment(self):
        """R: play the open segment from its start (a little lead-in, stops just after it ends)."""
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or idx is None or idx >= len(doc.segments):
            self.lbl_status.config(text="Click a word to pick a segment first.")
            return
        seg = doc.segments[idx]
        start, end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        self._player_start(max(0.0, start - 0.3), duration=max(1.0, end - start + 0.6))

    def _show_shortcuts(self):
        c = "⌘" if IS_MAC else "Ctrl+"
        text = (
            "In the transcript (click it first)\n"
            "  1 – 9            assign speaker 1–9 (to every selected segment)\n"
            "  0                add a new speaker and assign it\n"
            "  ↑ / ↓            previous / next segment\n"
            "  ⇧↑ / ⇧↓          select several segments\n"
            "  drag, ⇧-click    select several segments with the mouse\n"
            f"  {c}A               select all segments\n"
            "  ← / →            previous / next uncertain word\n"
            "  Space            play / pause\n"
            "  R                replay the open segment\n"
            "  C                mark checked / not checked (all selected)\n"
            "  X                cut / restore the selected segments\n"
            "  E                mark the selected segments as an episode (interview, phase)\n"
            "  Return           edit the open segment's text\n"
            f"  {c}Return          save, mark checked, next segment\n"
            f"  {c}F               find & replace\n"
            "  double-click     play from that word\n\n"
            "While typing in the segment box\n"
            f"  {c}Return          save, mark checked, next segment\n"
            f"  {c}S               save the segment\n"
            "  Tab              save and go back to the transcript\n"
            + ("  ⌃1 – ⌃9          assign speaker 1–9\n"
               "  ⌃L ⌃I ⌃U ⌃C ⌃P ⌃G ⌃N   markers (laughs, inaudible, unclear, crosstalk, pause, German, gestures)\n\n"
               if IS_MAC else
               "  Alt+1 – Alt+9    assign speaker 1–9\n"
               "  Ctrl+Shift+ L I U C P G N   markers (laughs, inaudible, unclear, crosstalk, pause, German, gestures)\n\n") +
            "Anywhere\n"
            "  Esc              play / pause\n"
            f"  {c}[ / {c}]          back / forward 5 s\n"
            f"  {c}⇧. / {c}⇧,        faster / slower\n"
            f"  {c}Z / {c}⇧Z         undo / redo a transcript change\n"
            f"  {c}⇧R              re-transcribe a passage\n"
            f"  {c}I               insert a missing segment\n"
            f"  {c}1 / {c}2 / {c}3     Settings / Console / Transcript tab\n"
            f"  {c}+ / {c}- / {c}0     zoom (everything)\n"
            + ("  ⌘⌥+ / ⌘⌥-        transcript text larger / smaller (or ⌘ + scroll wheel)" if IS_MAC else
               "  Ctrl+Alt++ / Ctrl+Alt+-   transcript text larger / smaller (or Ctrl + scroll wheel)"))
        if not IS_MAC:
            text = text.replace("⌃", "Ctrl+").replace("⇧", "Shift+")
        T = THEME
        win = tk.Toplevel(self)
        win.title("Keyboard shortcuts")
        win.configure(bg=T["bg_panel"])
        win.transient(self)
        tk.Label(win, text=text, font=self.f["mono_small"], justify=tk.LEFT, anchor="w", bg=T["bg_panel"], fg=T["fg_primary"],
                 padx=18, pady=14).pack(fill=tk.BOTH, expand=True)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win

    def _jump_low_conf(self, direction: int):
        if not self.low_conf:
            self.lbl_word.config(text="No uncertain words in this transcript.")
            return
        cur = self.current_span if self.current_span is not None else (-1 if direction > 0 else len(self.word_spans))
        if direction > 0:
            nxt = next((i for i in self.low_conf if i > cur), self.low_conf[0])
        else:
            nxt = next((i for i in reversed(self.low_conf) if i < cur), self.low_conf[-1])
        self._select_span(nxt, autoplay=bool(self.var_autoplay.get()))
