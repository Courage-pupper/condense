"""Transcript editor: the transcript view and the segment editor. Navigation, history and review marks are in
navigation.py, history.py and review_marks.py."""
from condense.core import cuts as cutlib
from condense.core import episodes as eplib
from condense.gui._common import *  # noqa: F401,F403


class EditorMixin:
    # ------------------------------------------------------------------ transcript editor
    PLAYER_LATENCY_S = 0.25  # ffplay start-up delay; keeps the follow-along highlight in sync
    SHOW_FILTERS = ["All segments", "Uncertain", "Not checked", "With notes", "Cut"]
    EDIT_HINT = "Unchanged words keep their timing; corrected words are timed within the ones they replace. Every change is logged."

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
        bar.add(self._tool(bar, "Episodes…", self._episodes_dialog))
        bar.add(self._label(bar, "Show", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]))
        self.var_show_filter = tk.StringVar(value="All segments")
        cmb_show = ttk.Combobox(bar, textvariable=self.var_show_filter, state="readonly", width=15, font=self.f["small"],
                                values=self.SHOW_FILTERS)
        self.cmb_show = cmb_show
        cmb_show.bind("<<ComboboxSelected>>", lambda e: (self._autosave_edit(rerender=False), self._rerender()))
        bar.add(cmb_show)
        bar.add(self._tool(bar, "Find…", self._toggle_find))
        bar.add(self._separator(bar))
        bar.add(self._label(bar, "Text", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]))
        bar.add(self._tool(bar, "A−", lambda: self._transcript_size_step(-1)))
        self.lbl_text_size = self._label(bar, f"{self.transcript_scale * 100:.0f}%", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        bar.add(self.lbl_text_size)
        bar.add(self._tool(bar, "A+", lambda: self._transcript_size_step(+1)))
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
        player.add(self._label(player, kbd("Esc play/pause · ⌘[ ⌘] ±5 s · ⌘⇧. ⌘⇧, speed"), font="small", fg=T["fg_dim"]))

        self.timeline = tk.Canvas(tab, height=30, bg=T["bg_input"], highlightthickness=0, bd=0, cursor="hand2")
        self.timeline.pack(fill=tk.X, pady=(4, 0))
        self.timeline.bind("<Configure>", lambda e: self._timeline_redraw())
        self.timeline.bind("<Button-1>", self._timeline_click)
        self.timeline.bind("<B1-Motion>", self._timeline_click)
        self._tl_playhead = None

        info = tk.Frame(tab, bg=T["bg_panel"])
        info.pack(fill=tk.X, pady=(6, 4))
        # Fixed heights for the lines that change on every ↑/↓: if they grew or shrank, the transcript would jump.
        self.lbl_ci_summary = self._label(info, kbd("No transcript open. Select a finished job or use Open… (⇧⌘O)."), font="small_bold",
                                          bg=T["bg_panel"], height=1, anchor="w", justify=tk.LEFT)
        self.lbl_ci_summary.pack(fill=tk.X)
        self.lbl_word = self._label(info, "", font="mono_small", fg=T["fg_secondary"], bg=T["bg_panel"], anchor="w", justify=tk.LEFT, height=1)
        self.lbl_word.pack(fill=tk.X)

        # --- segment editor (bottom) ---
        ep = tk.Frame(tab, bg=T["bg_card"], padx=10, pady=8)
        ep.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        row = self._flow(ep, padx=6, pady=4, bg=T["bg_card"])
        row.pack(fill=tk.X)
        self.edit_row = row
        self.lbl_edit_title = self._label(row, "Click a word to edit its segment", font="small_bold", width=46, anchor="w")
        row.add(self.lbl_edit_title)
        row.add(self._label(row, "Speaker", font="small", fg=T["fg_secondary"]))
        self.var_seg_speaker = tk.StringVar(value="")
        self.cmb_seg_speaker = ttk.Combobox(row, textvariable=self.var_seg_speaker, values=[], width=16, font=self.f["small"])
        row.add(self.cmb_seg_speaker)
        row.add(self._tool(row, "Split before word", self._split_at_current_word))
        row.add(self._tool(row, "Merge with next", self._merge_with_next))
        self.btn_checked = self._tool(row, "Mark checked", self._toggle_checked)
        self.btn_checked.configure(width=13)  # "✓ Checked" and "Mark checked" take the same room
        row.add(self.btn_checked)
        row.add(self._tool(row, "Markers ▾", self._show_marker_menu))
        row.add(self._tool(row, "+ Segment", self._insert_segment_dialog))
        row.add(self._tool(row, "Re-transcribe…", self._retranscribe_dialog))
        row.add(self._tool(row, "Delete", self._delete_segment))
        row.add(self._tool(row, "Revert", self._revert_segment_edit))
        row.add(self._tool(row, kbd("Save  ⌘S"), self._save_segment_edit))
        row.add(self._tool(row, kbd("✓ Save & next  ⌘↩"), self._check_and_next, primary=True))
        self.edit_text = tk.Text(ep, height=3, wrap=tk.WORD, font=self.f["transcript"], bg=T["bg_input"], fg=T["fg_primary"],
                                 insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                                 highlightcolor=T["accent"], padx=8, pady=6, undo=True)
        self.spk_chips = self._flow(ep, padx=4, pady=4, bg=T["bg_card"])
        self.spk_chips.pack(fill=tk.X, pady=(6, 0))
        self.edit_text.pack(fill=tk.X, pady=(6, 2))
        for seq in (f"<{MOD}-Return>", f"<{MOD}-KP_Enter>"):  # main and number-pad Enter
            self.edit_text.bind(seq, lambda e: (self._check_and_next(), "break")[-1])
        for d in range(1, 10):
            self.bind_all(f"<{EDIT_MOD}-Key-{d}>", lambda e, d=d: (self._assign_speaker_number(d), "break")[-1])
        note_row = tk.Frame(ep, bg=T["bg_card"])
        note_row.pack(fill=tk.X, pady=(0, 2))
        self._label(note_row, "✎ Note", font="small", fg="#c4b5fd").pack(side=tk.LEFT, padx=(0, 6))
        self.var_seg_note = tk.StringVar(value="")
        self.ent_seg_note = self._entry(note_row, self.var_seg_note, font="small")
        self.ent_seg_note.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ent_seg_note.bind("<Return>", lambda e: (self._save_segment_edit(), "break")[-1])
        for seq in (f"<{MOD}-Return>", f"<{MOD}-KP_Enter>"):
            self.ent_seg_note.bind(seq, lambda e: (self._check_and_next(), "break")[-1])
        self.edit_note_original = ""
        self.lbl_edit_hint = self._label(ep, self.EDIT_HINT, font="small", fg=T["fg_dim"], height=1, anchor="w", justify=tk.LEFT)
        self.lbl_edit_hint.pack(fill=tk.X)
        self.edit_seg_idx: Optional[int] = None
        self.edit_original = ("", "")

        self.tx_paned = tk.PanedWindow(tab, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.FLAT, bd=0, bg=T["bg_panel"], showhandle=False)
        self.tx_paned.pack(fill=tk.BOTH, expand=True)
        self._build_find_bar(tab, before=self.tx_paned)
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
        pt.tag_config("NOTE", foreground="#c4b5fd", font=self.f["small"])
        pt.tag_config("FOUND", background="#5c4a12")
        pt.tag_config("CUT_HDR", foreground="#f59e0b", font=self.f["small_bold"])
        for i, color in enumerate(eplib.PALETTE):
            pt.tag_config(f"EPC{i}", foreground=color, font=self.f["small_bold"], spacing1=6, spacing3=2)
        pt.tag_config("MULTISEL", background="#3a2f5c")
        pt.bind("<ButtonRelease-1>", self._on_text_click)
        pt.bind("<Shift-ButtonRelease-1>", self._on_shift_click)
        pt.bind("<Double-Button-1>", lambda e: (self._on_text_click(e), self._play_from_current_word(), "break")[-1])
        # A plain <Key-c> binding also fires for ⌘C / Ctrl+C: step aside when a modifier is held, so copy,
        # ⌘1–3 (tabs), ⌘R (start queue) and ⌃1–9 (Alt+1–9) keep working from the transcript. Shift, Caps Lock and
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
        for k in ("e", "E"):
            pt.bind(f"<Key-{k}>", brk(self._mark_episode))
        pt.bind("<Return>", brk(self._focus_editor))
        # Ctrl/⌘ + mouse wheel over the transcript: its text size (like a browser)
        for widget in (pt, self.edit_text):
            widget.bind(f"<{MOD}-MouseWheel>", lambda e: (self._transcript_size_step(+1 if e.delta > 0 else -1), "break")[-1])
            if not IS_MAC:
                widget.bind("<Control-Button-4>", lambda e: (self._transcript_size_step(+1), "break")[-1])
                widget.bind("<Control-Button-5>", lambda e: (self._transcript_size_step(-1), "break")[-1])
        pt.bind(f"<{MOD}-Key-a>", lambda e: (self._select_all_segments(), "break")[-1])
        self.edit_text.bind("<Tab>", brk(self._focus_transcript))  # a tab character is never wanted in a transcript
        # Esc while typing: save and go back to the transcript (↑/↓ move on, Return edits again). Outside the text
        # boxes Esc still plays and pauses; "break" keeps it from doing both.
        for w in (self.edit_text, self.ent_seg_note):
            w.bind("<Escape>", lambda e: (self._focus_transcript(), "break")[-1])
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
            keep = (sp["seg"], sp["w"], sp["start"])
        anchor = self._view_anchor() if keep_view else None
        top = self.preview_text.yview()[0] if keep_view else 0.0
        self._render_doc(self.active_doc, reset_editor=False)
        if not (anchor and self._restore_view_anchor(anchor)):
            try:
                self.preview_text.yview_moveto(top)
            except Exception:
                pass
        if keep is not None:
            idx = self.span_lookup.get(keep[:2])
            if idx is None or abs(self.word_spans[idx]["start"] - keep[2]) > 0.01:  # segments merged/split: by time
                idx = self._span_near(keep[2])
            if idx is not None:
                self._select_span(idx, scroll=False, load_editor=False)
        self._highlight_edit_segment()

    # A redraw can regroup paragraphs (a segment joins another speaker's turn, two segments merge, a cut header
    # appears). Keeping the same *fraction* of the text in view then lands somewhere else, so the view is anchored to
    # a word instead: the open segment's (or the first word on screen) stays at the same height on screen.
    def _span_near(self, t: float) -> Optional[int]:
        import bisect
        if not self.span_starts:
            return None
        i = bisect.bisect_left(self.span_starts, t - 1e-6)
        cands = [j for j in (i - 1, i) if 0 <= j < len(self.span_starts)]
        return min(cands, key=lambda j: abs(self.span_starts[j] - t)) if cands else None

    def _view_anchor(self) -> Optional[Tuple[float, int]]:
        pt = self.preview_text
        try:
            idx = self.current_span
            if idx is not None and idx < len(self.word_spans):
                sp = self.word_spans[idx]
                bb = pt.bbox(f"{sp['line']}.{sp['c0']}")
                if isinstance(bb, tuple) and len(bb) == 4:
                    return sp["start"], int(bb[1])
            top_line = int(str(pt.index("@0,0")).split(".")[0])
            for line in range(top_line, top_line + 60):
                for i in self.line_spans.get(line, []):
                    sp = self.word_spans[i]
                    bb = pt.bbox(f"{sp['line']}.{sp['c0']}")
                    if isinstance(bb, tuple) and len(bb) == 4:
                        return sp["start"], int(bb[1])
        except Exception:
            return None
        return None

    def _restore_view_anchor(self, anchor: Tuple[float, int]) -> bool:
        t, y = anchor
        i = self._span_near(t)
        if i is None:
            return False
        sp = self.word_spans[i]
        pt = self.preview_text
        try:
            pt.yview(f"{sp['line']}.{sp['c0']}")  # that word's display line at the top …
            if y > 0:
                pt.yview_scroll(-int(y), "pixels")  # … then back down to where it was on screen
        except Exception:
            return False
        return True

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
        if new_doc:
            self._project_follow_doc(doc)
            self._forget_project_cache()
            if self.__dict__.get("pp_tree") is not None:
                self._pp_mark_open()
        roster = self._roster()
        spk_tag = {spk: f"SPK{i % len(SPEAKER_COLORS)}" for i, spk in enumerate(roster)}
        rsegs = report["segments"]
        show = self.var_show_filter.get() if self.__dict__.get("var_show_filter") is not None else "All segments"
        if show != "All segments":  # a filter shows matching segments one by one
            def wanted(i):
                if show == "Uncertain":
                    return any(float(w.get("probability", w.get("prob", 0.85))) < med_t for w in rsegs[i].get("words") or [])
                if show == "Not checked":
                    return not doc.segments[i].get("checked") and i not in cut_set
                if show == "With notes":
                    return bool(doc.segments[i].get("note"))
                if show.startswith("In "):
                    return i in in_episode
                return i in cut_set
            in_episode = set()
            if show.startswith("In "):
                for ep in doc.episodes:
                    if ep["type"] == show[3:]:
                        in_episode.update(doc.episode_segments(ep))
            groups = [[i] for i in range(len(rsegs)) if not is_blank_text(rsegs[i].get("text")) and wanted(i)]
        elif self.var_view_mode.get() == "Paragraphs":
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

        eps, next_ep, open_eps = doc.episodes, 0, []
        for group in groups:
            first, last = rsegs[group[0]], rsegs[group[-1]]
            gstart = float(doc.segments[group[0]].get("start", 0.0))
            for ep in list(open_eps):  # episodes that ended before this paragraph
                if gstart >= ep["end"] - 0.05:
                    put(f"■ end of {ep['type']} {ep.get('label') or ''}\n", self._episode_tag(ep["type"]))
                    open_eps.remove(ep)
            while next_ep < len(eps) and eps[next_ep]["start"] <= gstart + 0.05:
                ep = eps[next_ep]
                next_ep += 1
                if ep["end"] <= gstart:
                    continue
                approx = "≈ " if ep.get("approx") else ""
                put(f"▶ {ep['type']} {ep.get('label') or ''}   {approx}{short_ts(ep['start'])[:-2]} – {short_ts(ep['end'])[:-2]}\n",
                    self._episode_tag(ep["type"]))
                open_eps.append(ep)
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
                note = doc.segments[si].get("note")
                if note:
                    put(f"  ✎ {note}", "NOTE")
            put("\n")
        txt.configure(state=tk.DISABLED)
        for tag in ("CUT", "FOUND", "MULTISEL", "EDITSEG", "PLAYING", "CURRENT"):
            txt.tag_raise(tag)

        self.word_spans, self.line_spans, self.low_conf, self.current_span = spans, line_spans, low, None
        self.span_starts = [sp["start"] for sp in spans]
        self.span_lookup = {(sp["seg"], sp["w"]): i for i, sp in enumerate(spans)}
        self.seg_ranges = seg_ranges
        if new_doc:
            self.sel_segs, self.sel_anchor = [], None
        self._paint_multi_selection()
        self._paint_found()
        total = max(1, len(spans))
        edited = sum(1 for s in doc.segments if s.get("edited"))
        self.lbl_ci_summary.config(
            text=f"{doc.base_name}   ·   {doc.checked_fraction() * 100:.0f}% checked   ·   {len(spans)} words   ·   "
                 f"{len(low)} uncertain ({len(low) * 100 / total:.0f}%)   ·   mean word probability {report['overall_ci'][0] * 100:.0f}%   ·   "
                 f"{len(roster)} speaker(s)" + (f"   ·   {edited} edited" if edited else "")
                 + (f"   ·   ✂ {cutlib.describe(doc.cuts)}" if doc.cuts else "")
                 + (f"   ·   showing {len(groups)} {show.lower()}" if show != "All segments" else "")
        )
        self.cmb_seg_speaker.configure(values=roster)
        if self.__dict__.get("cmb_show") is not None:
            self.cmb_show.configure(values=self.SHOW_FILTERS + [f"In {t}" for t in self._episode_types()])
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

    # ----- segment editor -----
    def _clear_editor(self):
        self.edit_seg_idx = None
        self.edit_original = ("", "")
        self.edit_text.delete("1.0", tk.END)
        self.var_seg_speaker.set("")
        self.edit_note_original = ""
        self.var_seg_note.set("")
        self.lbl_edit_title.config(text="Click a word to edit its segment")
        self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)

    def _editor_dirty(self) -> bool:
        if self.edit_seg_idx is None:
            return False
        text = " ".join(self.edit_text.get("1.0", "end-1c").split())
        note = " ".join(self.var_seg_note.get().split())
        return (text != " ".join(self.edit_original[0].split()) or self.var_seg_speaker.get().strip() != self.edit_original[1]
                or note != self.edit_note_original)

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
        self.edit_note_original = seg.get("note") or ""
        self.var_seg_note.set(self.edit_note_original)
        self._refresh_speaker_chips()
        mark = ("  ✎" if seg.get("edited") else "") + ("  ✓" if seg.get("checked") else "")
        self.btn_checked.configure(text="✓ Checked" if seg.get("checked") else "Mark checked")
        self.lbl_edit_title.config(text=f"Segment {seg_idx + 1}/{len(doc.segments)}  ·  {short_ts(seg.get('start', 0))} – {short_ts(seg.get('end', 0))}{mark}")
        self.after_idle(self.edit_row.relayout)  # the title's width changed
        orig = seg.get("text_whisper")
        rt = seg.get("retranscribed") or {}
        if rt and not seg.get("edited"):
            what = "translated to English" if rt.get("task") == "translate" else "re-transcribed"
            self.lbl_edit_hint.config(text=kbd(f"This segment was {what} with Whisper {rt.get('model')} (language: {rt.get('language')}). "
                                               "Undo (⌘Z) brings back the previous version."), fg="#8fb3ff")
        elif orig and " ".join(str(orig).split()) != " ".join(text.split()) and not seg.get("edited"):
            self.lbl_edit_hint.config(text=f"Whisper's own text for this segment differed: “{str(orig).strip()[:110]}” — listen and correct if needed.",
                                      fg=THEME["warn"])
        else:
            self.lbl_edit_hint.config(text=self.EDIT_HINT, fg=THEME["fg_dim"])
        self._highlight_edit_segment()

    def _highlight_edit_segment(self):
        self.preview_text.tag_remove("EDITSEG", "1.0", tk.END)
        rng = self.seg_ranges.get(self.edit_seg_idx) if self.edit_seg_idx is not None else None
        if rng:
            self.preview_text.tag_add("EDITSEG", *rng)
