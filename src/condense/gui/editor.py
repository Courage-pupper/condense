"""Transcript editor: rendering, segment editing, undo, checked marks, markers, speakers."""
from condense.core import cuts as cutlib
from condense.gui._common import *  # noqa: F401,F403


class EditorMixin:
    # ------------------------------------------------------------------ transcript editor
    PLAYER_LATENCY_S = 0.25
    EDIT_HINT = ("The box shows exactly the highlighted segment. Unchanged words keep their exact timing; corrected words are "
                 "timed within the words they replace and marked as checked. Every change is logged in the .transcript.json.")  # ffplay start-up delay; keeps the follow-along highlight in sync

    def _build_preview_tab(self):
        T = THEME
        tab = self.tab_preview

        bar = self._flow(tab, padx=5, pady=5, bg=T["bg_panel"])
        bar.pack(fill=tk.X)
        bar.add(self._tool(bar, "Open…", self._open_transcript))
        bar.add(self._tool(bar, "Speakers…", self._show_speakers_dialog))
        bar.add(self._tool(bar, "Detect speakers…", self._rediarize_dialog))
        bar.add(self._separator(bar))
        self.var_view_mode = tk.StringVar(value="Paragraphs")
        self._persist["view_mode"] = self.var_view_mode
        self._combo_values["view_mode"] = ["Paragraphs", "Segments"]
        cb = ttk.Combobox(bar, textvariable=self.var_view_mode, values=["Paragraphs", "Segments"], state="readonly", width=10, font=self.f["small"])
        cb.bind("<<ComboboxSelected>>", lambda e: self._rerender())
        bar.add(cb)
        bar.add(self._separator(bar))
        bar.add(self._tool(bar, "◀ Uncertain", lambda: self._jump_low_conf(-1)))
        bar.add(self._tool(bar, "Uncertain ▶", lambda: self._jump_low_conf(+1)))
        bar.add(self._tool(bar, "✂ Cut / restore (X)", self._toggle_cut))
        bar.add(self._tool(bar, "Export trimmed…", self._export_trimmed_dialog))
        self.var_autoplay = tk.BooleanVar(value=True)
        self._persist["autoplay"] = self.var_autoplay
        bar.add(self._check(bar, "Play snippet on jump", self.var_autoplay, bg=T["bg_panel"], font="small"))
        self._persist["show_pct"] = self.var_show_pct

        player = self._flow(tab, padx=5, pady=5, bg=T["bg_card"])
        player.pack(fill=tk.X, pady=(4, 0), ipady=3)
        player.add(self._tool(player, "⟲ 5 s", lambda: self._seek_rel(-5)))
        self.btn_play = self._tool(player, "▶ Play", self._toggle_play, primary=True)
        player.add(self.btn_play)
        player.add(self._tool(player, "5 s ⟳", lambda: self._seek_rel(+5)))
        self.var_speed = tk.StringVar(value="1×")
        self._persist["speed"] = self.var_speed
        self._combo_values["speed"] = ["0.5×", "0.6×", "0.75×", "0.9×", "1×", "1.1×", "1.25×", "1.4×", "1.5×", "1.75×", "2×"]
        sp = ttk.Combobox(player, textvariable=self.var_speed, values=self._combo_values["speed"], state="readonly", width=6, font=self.f["small"])
        sp.bind("<<ComboboxSelected>>", lambda e: self._on_speed_change())
        player.add(sp)
        self.lbl_time = self._label(player, "0:00:00 / 0:00:00", font="mono_small", fg=T["fg_secondary"])
        player.add(self.lbl_time)
        self.var_follow = tk.BooleanVar(value=True)
        self._persist["follow"] = self.var_follow
        player.add(self._check(player, "Follow text", self.var_follow, font="small"))
        self.var_skip_cuts = tk.BooleanVar(value=True)
        self._persist["skip_cuts"] = self.var_skip_cuts
        player.add(self._check(player, "Skip cuts", self.var_skip_cuts, font="small"))
        self._persist["show_video"] = self.var_show_video
        player.add(self._check(player, "Video", self.var_show_video, font="small"))
        self.var_show_video.trace_add("write", lambda *a: self.after_idle(self._toggle_video_panel))
        player.add(self._tool(player, "QuickTime ↗" if IS_MAC_PLATFORM else "Video player ↗", self._open_in_quicktime))
        player.add(self._label(player, "Esc play/pause · ⌘[ ⌘] ±5 s · ⌘⇧. ⌘⇧, speed", font="small", fg=T["fg_dim"]))

        self.timeline = tk.Canvas(tab, height=30, bg=T["bg_input"], highlightthickness=0, bd=0, cursor="hand2")
        self.timeline.pack(fill=tk.X, pady=(4, 0))
        self.timeline.bind("<Configure>", lambda e: self._timeline_redraw())
        self.timeline.bind("<Button-1>", self._timeline_click)
        self.timeline.bind("<B1-Motion>", self._timeline_click)
        self._tl_playhead = None

        info = tk.Frame(tab, bg=T["bg_panel"])
        info.pack(fill=tk.X, pady=(6, 4))
        self.lbl_ci_summary = self._wrapping_label(info, "No transcript open. Select a finished job or use Open… (⇧⌘O).", font="small_bold", bg=T["bg_panel"])
        self.lbl_ci_summary.pack(fill=tk.X)
        self.lbl_word = self._wrapping_label(info, "", font="mono_small", fg=T["fg_secondary"], bg=T["bg_panel"])
        self.lbl_word.pack(fill=tk.X)

        # --- segment editor (bottom) ---
        ep = tk.Frame(tab, bg=T["bg_card"], padx=10, pady=8)
        ep.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        row = self._flow(ep, padx=6, pady=4, bg=T["bg_card"])
        row.pack(fill=tk.X)
        self.edit_row = row
        self.lbl_edit_title = self._label(row, "Click a word to edit its segment", font="small_bold")
        row.add(self.lbl_edit_title)
        row.add(self._label(row, "Speaker", font="small", fg=T["fg_secondary"]))
        self.var_seg_speaker = tk.StringVar(value="")
        self.cmb_seg_speaker = ttk.Combobox(row, textvariable=self.var_seg_speaker, values=[], width=16, font=self.f["small"])
        row.add(self.cmb_seg_speaker)
        row.add(self._tool(row, "Split before word", self._split_at_current_word))
        row.add(self._tool(row, "Merge with next", self._merge_with_next))
        self.btn_checked = self._tool(row, "Mark checked", self._toggle_checked)
        row.add(self.btn_checked)
        row.add(self._tool(row, "Markers ▾", self._show_marker_menu))
        row.add(self._tool(row, "+ Segment", self._insert_segment_dialog))
        row.add(self._tool(row, "Re-transcribe…", self._retranscribe_dialog))
        row.add(self._tool(row, "Delete", self._delete_segment))
        row.add(self._tool(row, "Revert", self._revert_segment_edit))
        row.add(self._tool(row, "Save  ⌘S", self._save_segment_edit))
        row.add(self._tool(row, "✓ Save & next  ⌘↩", self._check_and_next, primary=True))
        self.edit_text = tk.Text(ep, height=3, wrap=tk.WORD, font=self.f["transcript"], bg=T["bg_input"], fg=T["fg_primary"],
                                 insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                                 highlightcolor=T["accent"], padx=8, pady=6, undo=True)
        self.spk_chips = self._flow(ep, padx=4, pady=4, bg=T["bg_card"])
        self.spk_chips.pack(fill=tk.X, pady=(6, 0))
        self.edit_text.pack(fill=tk.X, pady=(6, 2))
        for seq in (f"<{MOD}-Return>", f"<{MOD}-KP_Enter>"):  # main and number-pad Enter
            self.edit_text.bind(seq, lambda e: (self._check_and_next(), "break")[-1])
        for d in range(1, 10):
            self.bind_all(f"<Control-Key-{d}>", lambda e, d=d: (self._assign_speaker_number(d), "break")[-1])
        self.lbl_edit_hint = self._wrapping_label(ep, self.EDIT_HINT, font="small", fg=T["fg_dim"])
        self.lbl_edit_hint.pack(fill=tk.X)
        self.edit_seg_idx: Optional[int] = None
        self.edit_original = ("", "")

        self.tx_paned = tk.PanedWindow(tab, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.FLAT, bd=0, bg=T["bg_panel"], showhandle=False)
        self.tx_paned.pack(fill=tk.BOTH, expand=True)
        frame = tk.Frame(self.tx_paned, bg=T["bg_panel"])
        self.tx_paned.add(frame, minsize=300, stretch="always")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._build_video_panel()
        self.preview_text = tk.Text(frame, bg="#0b0d13", fg=T["fg_primary"], font=self.f["transcript"], relief=tk.FLAT, padx=16, pady=12,
                                    wrap=tk.WORD, highlightthickness=0, cursor="arrow", spacing1=2, spacing3=2, state=tk.DISABLED)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=sb.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        pt = self.preview_text
        pt.tag_config("SEG_HDR", foreground=T["fg_dim"], font=self.f["mono_small"], spacing1=12)
        for i, color in enumerate(SPEAKER_COLORS):
            pt.tag_config(f"SPK{i}", foreground=color, font=self.f["mono_small_bold"], spacing1=12)
        pt.tag_config("HIGH", foreground=T["fg_primary"])
        pt.tag_config("MED", foreground="#f5c56b")
        pt.tag_config("LOW", foreground="#fb923c")
        pt.tag_config("POOR", foreground="#f87171", underline=True)
        pt.tag_config("PCT", foreground="#f87171", font=self.f["mono_small"])
        pt.tag_config("EDITSEG", background="#1c2a40")
        pt.tag_config("PLAYING", background="#1d3a2e")
        pt.tag_config("CURRENT", background="#3b4a63")
        pt.tag_config("MARKER", foreground="#8fb3ff", font=self.f["marker"])
        pt.tag_config("CHECKMARK", foreground=T["ok"], font=self.f["small_bold"])
        pt.tag_config("CUT", overstrike=True, foreground="#5b6475")
        pt.tag_config("CUT_HDR", foreground="#f59e0b", font=self.f["small_bold"])
        pt.tag_config("MULTISEL", background="#3a2f5c")
        pt.bind("<ButtonRelease-1>", self._on_text_click)
        pt.bind("<Shift-ButtonRelease-1>", self._on_shift_click)
        pt.bind("<Double-Button-1>", lambda e: (self._on_text_click(e), self._play_from_current_word(), "break")[-1])
        # A plain <Key-c> binding also fires for ⌘C / Ctrl+C: step aside when a modifier is held, so copy,
        # ⌘1–3 (tabs), ⌘R (start queue) and ⌃1–9 keep working from the transcript. Shift, Caps Lock and
        # Num Lock (Mod2 on Linux) are not modifiers here.
        mods = 0x4 | 0x8 | 0x40 | 0x20000 | (0x10 if IS_MAC else 0)

        def brk(fn):
            def handler(event=None):
                if event is not None and int(getattr(event, "state", 0) or 0) & mods:
                    return None
                fn()
                return "break"
            return handler
        # single keys while the transcript has focus (it is read-only, so they never type anything)
        pt.bind("<Right>", brk(lambda: self._jump_low_conf(+1)))
        pt.bind("<Left>", brk(lambda: self._jump_low_conf(-1)))
        pt.bind("<Down>", brk(lambda: self._move_segment(+1)))
        pt.bind("<Up>", brk(lambda: self._move_segment(-1)))
        pt.bind("<Shift-Down>", brk(lambda: self._move_segment(+1, extend=True)))
        pt.bind("<Shift-Up>", brk(lambda: self._move_segment(-1, extend=True)))
        pt.bind("<space>", brk(self._toggle_play))
        for d in range(1, 10):
            pt.bind(f"<Key-{d}>", brk(lambda d=d: self._assign_speaker_number(d)))
            try:
                pt.bind(f"<KeyPress-KP_{d}>", brk(lambda d=d: self._assign_speaker_number(d)))  # number pad
            except Exception:
                pass
        pt.bind("<Key-0>", brk(self._add_speaker_quick))
        for k in ("c", "C"):
            pt.bind(f"<Key-{k}>", brk(self._toggle_checked))
        for k in ("r", "R"):
            pt.bind(f"<Key-{k}>", brk(self._replay_segment))
        for k in ("x", "X"):
            pt.bind(f"<Key-{k}>", brk(self._toggle_cut))
        pt.bind("<Return>", brk(self._focus_editor))
        pt.bind(f"<{MOD}-Key-a>", lambda e: (self._select_all_segments(), "break")[-1])
        self.edit_text.bind("<Tab>", brk(self._focus_transcript))  # a tab character is never wanted in a transcript
        self.cmb_seg_speaker.bind("<<ComboboxSelected>>", lambda e: self._on_speaker_combo())
        self.sel_segs: List[int] = []
        self.sel_anchor: Optional[int] = None
        self._sel_nseg = 0

        # player state
        self.playing = False
        self.play_pos = 0.0
        self._play_from = 0.0
        self._play_t0 = 0.0
        self._play_until: Optional[float] = None
        self._play_speed = 1.0
        self._tick_job = None
        self.span_starts: List[float] = []
        self.span_lookup: Dict[Tuple[int, int], int] = {}
        self.seg_ranges: Dict[int, Tuple[str, str]] = {}

    # ----- rendering -----
    def _rerender(self, keep_view: bool = True):
        if self.active_doc is None:
            return
        keep = None
        if self.current_span is not None and self.current_span < len(self.word_spans):
            sp = self.word_spans[self.current_span]
            keep = (sp["seg"], sp["w"])
        top = self.preview_text.yview()[0] if keep_view else 0.0
        self._render_doc(self.active_doc, reset_editor=False)
        try:
            self.preview_text.yview_moveto(top)
        except Exception:
            pass
        if keep is not None:
            idx = self.span_lookup.get(keep) or self.span_lookup.get((keep[0], 0))
            if idx is not None:
                self._select_span(idx, scroll=False, load_editor=False)
        self._highlight_edit_segment()

    def _render_doc(self, doc: TranscriptDocument, reset_editor: bool = True):
        if reset_editor and self.active_doc is not None and doc is not self.active_doc:
            self._remember_position()
            self._autosave_edit(rerender=False)  # the old document is about to be replaced
            self._player_pause()
        new_doc = doc is not self.active_doc
        self.active_doc = doc
        if new_doc:
            synced = doc.sync_text_with_words()
            removed = doc.remove_empty_segments()
            if synced or removed:
                try:
                    doc.save()
                    doc.export_all()
                    parts = []
                    if synced:
                        parts.append(f"matched the text of {synced} segment(s) to their timed words")
                    if removed:
                        parts.append(f"removed {removed} empty segment(s)")
                    self._append_log(f"{doc.base_name}: {' and '.join(parts)}; exports regenerated, logged in the edit history.", "INFO")
                except Exception as exc:
                    self._append_log(f"Could not save cleanup: {exc}", "WARN")
        report = doc.confidence_report(all_segments=True)
        cut_set = {i for i in range(len(doc.segments)) if doc.is_segment_cut(i)} if doc.cuts else set()
        high_t, med_t, poor_t = report["high_thresh"], report["med_thresh"], report["poor_thresh"]
        show_pct = bool(self.var_show_pct.get())
        speakers = doc.speakers()
        roster = doc.roster()
        spk_tag = {spk: f"SPK{i % len(SPEAKER_COLORS)}" for i, spk in enumerate(roster)}
        rsegs = report["segments"]
        if self.var_view_mode.get() == "Paragraphs":
            groups = [p["segments"] for p in doc.paragraphs(include_cut=True)]
        else:
            groups = [[i] for i in range(len(rsegs)) if not is_blank_text(rsegs[i].get("text"))]

        txt = self.preview_text
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        spans: List[Dict] = []
        line_spans: Dict[int, List[int]] = {}
        low: List[int] = []
        seg_ranges: Dict[int, Tuple[str, str]] = {}
        pos = {"line": 1, "col": 0}

        def put(s: str, *tags):
            if tags:
                txt.insert(tk.END, s, tags)
            else:
                txt.insert(tk.END, s)
            n = s.count("\n")
            if n:
                pos["line"] += n
                pos["col"] = len(s.rsplit("\n", 1)[1])
            else:
                pos["col"] += len(s)

        for group in groups:
            first, last = rsegs[group[0]], rsegs[group[-1]]
            spk = first.get("speaker")
            group_cut = group[0] in cut_set
            if group_cut:
                put("✂ cut  ", "CUT_HDR")
            if spk:
                put(f"{spk}  ", spk_tag.get(spk, "SPK0"))
            put(f"{short_ts(first['start'])} – {short_ts(last['end'])}\n", "SEG_HDR")
            for si in group:
                seg = rsegs[si]
                seg_start = f"{pos['line']}.{pos['col']}"
                words = seg["words"]
                if doc.segments[si].get("checked"):
                    put((" " if pos["col"] > 0 else "") + "✓", "CHECKMARK")
                in_marker = False
                if words:
                    for wi, w in enumerate(words):
                        w_str = str(w.get("word", ""))
                        if wi == 0 and pos["col"] > 0 and not w_str.startswith(" "):
                            put(" ")
                        prob = float(w.get("probability", w.get("prob", 0.85)))
                        tag = "HIGH" if prob >= high_t else ("MED" if prob >= med_t else ("LOW" if prob >= poor_t else "POOR"))
                        token = w_str.strip()
                        if token.startswith("["):
                            in_marker = True
                        if in_marker:
                            tag = "MARKER"
                            if token.endswith("]"):
                                in_marker = False
                        c0 = pos["col"] + (len(w_str) - len(w_str.lstrip()))
                        if si in cut_set:
                            put(w_str, tag, "CUT")
                        else:
                            put(w_str, tag)
                        c1 = pos["col"]
                        if show_pct and prob < poor_t:
                            put(f"{int(round(prob * 100))}%", "PCT")
                        spans.append({"line": pos["line"], "c0": c0, "c1": c1, "start": float(w.get("start", seg["start"])),
                                      "end": float(w.get("end", seg["end"])), "prob": prob, "word": w_str.strip(),
                                      "speaker": seg.get("speaker"), "seg": si, "w": wi})
                        line_spans.setdefault(pos["line"], []).append(len(spans) - 1)
                        if prob < med_t:
                            low.append(len(spans) - 1)
                else:
                    put((" " if pos["col"] > 0 else "") + (seg["text"] or "…"), *(("HIGH", "CUT") if si in cut_set else ("HIGH",)))
                seg_ranges[si] = (seg_start, f"{pos['line']}.{pos['col']}")
            put("\n")
        txt.configure(state=tk.DISABLED)
        for tag in ("CUT", "MULTISEL", "EDITSEG", "PLAYING", "CURRENT"):
            txt.tag_raise(tag)

        self.word_spans, self.line_spans, self.low_conf, self.current_span = spans, line_spans, low, None
        self.span_starts = [sp["start"] for sp in spans]
        self.span_lookup = {(sp["seg"], sp["w"]): i for i, sp in enumerate(spans)}
        self.seg_ranges = seg_ranges
        if new_doc:
            self.sel_segs, self.sel_anchor = [], None
        self._paint_multi_selection()
        total = max(1, len(spans))
        edited = sum(1 for s in doc.segments if s.get("edited"))
        self.lbl_ci_summary.config(
            text=f"{doc.base_name}   ·   {doc.checked_fraction() * 100:.0f}% checked   ·   {len(spans)} words   ·   "
                 f"{len(low)} uncertain ({len(low) * 100 / total:.0f}%)   ·   mean word probability {report['overall_ci'][0] * 100:.0f}%   ·   "
                 f"{len(roster)} speaker(s)" + (f"   ·   {edited} edited" if edited else "")
                 + (f"   ·   ✂ {cutlib.describe(doc.cuts)}" if doc.cuts else "")
        )
        self.cmb_seg_speaker.configure(values=roster)
        self._refresh_speaker_chips()
        if new_doc:
            self.play_pos = 0.0
            self._clear_editor()
            self._update_task_progress(doc)
            media = self._media()
            if media:
                self.lbl_word.config(text="Click a word to edit its segment · drag or ⇧-click to select several · 1–9 assigns a speaker · "
                                          "↑/↓ segments · ←/→ uncertain words · View ▸ Keyboard Shortcuts", fg=THEME["fg_dim"])
            else:
                self.lbl_word.config(text="⚠ Playback media not found (moved or deleted?) — see 'media' in the .transcript.json", fg=THEME["warn"])
            self._resume_position(doc)
        self._update_time_label()
        self._timeline_redraw()
        if new_doc:
            self._video_stop()
            self._toggle_video_panel()
            self._video_still(self.play_pos)

    def _select_span(self, idx: int, autoplay: bool = False, scroll: bool = True, load_editor: bool = True):
        if not (0 <= idx < len(self.word_spans)):
            return
        sp = self.word_spans[idx]
        if load_editor and sp["seg"] != self.edit_seg_idx and self._editor_dirty():
            target = (sp["start"], sp["word"])  # by time + text: segment numbers may shift if the edit deleted a segment
            self._save_segment_edit(reload=False)  # never lose an edit by clicking elsewhere
            idx = next((i for i, x in enumerate(self.word_spans) if x["start"] == target[0] and x["word"] == target[1]), None)
            if idx is None:
                return
            sp = self.word_spans[idx]
        self.current_span = idx
        a, b = f"{sp['line']}.{sp['c0']}", f"{sp['line']}.{sp['c1']}"
        self.preview_text.tag_remove("CURRENT", "1.0", tk.END)
        self.preview_text.tag_add("CURRENT", a, b)
        if scroll:
            self.preview_text.see(a)
        pos = f"   ·   uncertain {self.low_conf.index(idx) + 1} of {len(self.low_conf)}" if idx in self.low_conf else ""
        spk = f"   ·   {sp['speaker']}" if sp.get("speaker") else ""
        self.lbl_word.config(text=f"“{sp['word']}”   {short_ts(sp['start'])}   ·   p {sp['prob'] * 100:.0f}%{spk}{pos}", fg=THEME["fg_secondary"])
        if load_editor and sp["seg"] != self.edit_seg_idx:
            self._load_segment_into_editor(sp["seg"])
        if not self.playing:
            self.play_pos = max(0.0, sp["start"] - 0.5)
            self._update_time_label()
            self._video_still(sp["start"])
        if autoplay:
            self._player_start(max(0.0, sp["start"] - 1.0), duration=max(3.0, sp["end"] - sp["start"] + 2.5))

    def _span_at(self, event) -> Optional[int]:
        index = self.preview_text.index(f"@{event.x},{event.y}")
        line, col = (int(v) for v in index.split("."))
        for i in self.line_spans.get(line, []):
            sp = self.word_spans[i]
            if sp["c0"] <= col < max(sp["c1"], sp["c0"] + 1):
                return i
        return None

    def _on_text_click(self, event):
        self.preview_text.focus_set()
        dragged = self._segments_in_sel()  # read before a save re-renders the text (which drops the selection)
        doc = self.active_doc
        n_before = len(doc.segments) if doc is not None else 0
        i = self._span_at(event)
        if i is not None:
            self._select_span(i)
            if self.playing:
                self._player_start(max(0.0, self.word_spans[self.current_span]["start"] - 0.5))
        if len(dragged) > 1 and doc is not None and len(doc.segments) == n_before:
            anchor = dragged[-1] if self.edit_seg_idx == dragged[0] else dragged[0]
            self._set_multi_selection(dragged, anchor)
        else:
            self._clear_multi_selection()

    def _on_shift_click(self, event):
        """⇧-click: select every segment from the open one (or the selection's start) to the clicked one."""
        self.preview_text.focus_set()
        anchor = self.sel_anchor if self.sel_anchor is not None else self.edit_seg_idx
        i = self._span_at(event)
        if i is None:
            return None
        self._select_span(i)
        if anchor is not None and self.current_span is not None:
            self._extend_selection(anchor, self.word_spans[self.current_span]["seg"])
        return None  # let Tk's own release handling run (it stops drag auto-scrolling)

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
            "  Return           edit the open segment's text\n"
            f"  {c}Return          save, mark checked, next segment\n"
            "  double-click     play from that word\n\n"
            "While typing in the segment box\n"
            f"  {c}Return          save, mark checked, next segment\n"
            f"  {c}S               save the segment\n"
            "  Tab              save and go back to the transcript\n"
            "  ⌃1 – ⌃9          assign speaker 1–9\n"
            "  ⌃L ⌃I ⌃U ⌃C ⌃P ⌃G ⌃N   markers (laughs, inaudible, unclear, crosstalk, pause, German, gestures)\n\n"
            "Anywhere\n"
            "  Esc              play / pause\n"
            f"  {c}[ / {c}]          back / forward 5 s\n"
            f"  {c}⇧. / {c}⇧,        faster / slower\n"
            f"  {c}Z / {c}⇧Z         undo / redo a transcript change\n"
            f"  {c}⇧R              re-transcribe a passage\n"
            f"  {c}I               insert a missing segment\n"
            f"  {c}1 / {c}2 / {c}3     Settings / Console / Transcript tab\n"
            f"  {c}+ / {c}- / {c}0     zoom")
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

    # ----- segment editor -----
    def _clear_editor(self):
        self.edit_seg_idx = None
        self.edit_original = ("", "")
        self.edit_text.delete("1.0", tk.END)
        self.var_seg_speaker.set("")
        self.lbl_edit_title.config(text="Click a word to edit its segment")
        self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)

    def _editor_dirty(self) -> bool:
        if self.edit_seg_idx is None:
            return False
        text = " ".join(self.edit_text.get("1.0", "end-1c").split())
        return text != " ".join(self.edit_original[0].split()) or self.var_seg_speaker.get().strip() != self.edit_original[1]

    def _load_segment_into_editor(self, seg_idx: int):
        doc = self.active_doc
        if doc is None or not (0 <= seg_idx < len(doc.segments)):
            return
        seg = doc.segments[seg_idx]
        words = seg.get("words") or []
        # exactly what is highlighted in the transcript: the timed words
        text = ("".join(str(w.get("word", "")) for w in words) if words else (seg.get("text") or "")).strip()
        speaker = seg.get("speaker") or ""
        self.edit_seg_idx = seg_idx
        self.edit_original = (text, speaker)
        self.edit_text.delete("1.0", tk.END)
        self.edit_text.insert("1.0", text)
        self.edit_text.edit_reset()
        self.var_seg_speaker.set(speaker)
        self._refresh_speaker_chips()
        mark = ("  ✎" if seg.get("edited") else "") + ("  ✓" if seg.get("checked") else "")
        self.btn_checked.configure(text="✓ Checked" if seg.get("checked") else "Mark checked")
        self.lbl_edit_title.config(text=f"Segment {seg_idx + 1}/{len(doc.segments)}  ·  {short_ts(seg.get('start', 0))} – {short_ts(seg.get('end', 0))}{mark}")
        self.after_idle(self.edit_row.relayout)  # the title's width changed
        orig = seg.get("text_whisper")
        rt = seg.get("retranscribed") or {}
        if rt and not seg.get("edited"):
            what = "translated to English" if rt.get("task") == "translate" else "re-transcribed"
            self.lbl_edit_hint.config(text=f"This segment was {what} with Whisper {rt.get('model')} (language: {rt.get('language')}). "
                                           "Undo (⌘Z) brings back the previous version.", fg="#8fb3ff")
        elif orig and " ".join(str(orig).split()) != " ".join(text.split()) and not seg.get("edited"):
            self.lbl_edit_hint.config(text=f"Whisper's own text for this segment differed: “{str(orig).strip()[:200]}” — listen and correct if needed.",
                                      fg=THEME["warn"])
        else:
            self.lbl_edit_hint.config(text=self.EDIT_HINT, fg=THEME["fg_dim"])
        self._highlight_edit_segment()

    def _highlight_edit_segment(self):
        self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)
        rng = self.seg_ranges.get(self.edit_seg_idx) if self.edit_seg_idx is not None else None
        if rng:
            self.preview_text.tag_add("EDITSEG", *rng)

    def _commit(self, message: str, undoable: bool = True) -> Optional[Dict[str, Path]]:
        """Save the master file (with a throttled backup) and regenerate every export.
        The file as it was before is pushed on the undo stack. Returns the files written, or None."""
        doc = self.active_doc
        try:
            previous = doc.path.read_text(encoding="utf-8") if (undoable and doc.path.exists()) else None
            doc.save(backup=True)
            created = doc.export_all()
        except Exception as exc:
            messagebox.showerror("Could not save transcript", str(exc), parent=self)
            return None
        if previous is not None:
            undo, redo = self._undo_stacks.setdefault(str(doc.path), ([], []))
            undo.append((message, previous))
            del undo[:-100]
            redo.clear()
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

    def _swap_state(self, from_stack: List, to_stack: List, verb: str):
        doc = self.active_doc
        label, text = from_stack.pop()
        to_stack.append((label, doc.path.read_text(encoding="utf-8")))
        keep_seg = self.edit_seg_idx
        doc.data = json.loads(text)
        try:
            doc.save(backup=True)
            doc.export_all()
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
        for i, name in enumerate(doc.roster()):
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
        roster = doc.roster()
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
