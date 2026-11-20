"""Settings tab and console."""
from condense.gui._common import *  # noqa: F401,F403


class SettingsTabMixin:
    # ------------------------------------------------------------------ settings
    def _card(self, parent, title: str):
        card = tk.Frame(parent, bg=THEME["bg_card"], padx=12, pady=10)
        self._label(card, title, font="section", fg=THEME["accent"]).pack(anchor="w", pady=(0, 6))
        form = tk.Frame(card, bg=THEME["bg_card"])
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        card.form = form
        card.row = 0
        return card

    def _form_label(self, card, text: str):
        self._label(card.form, text, font="small", fg=THEME["fg_secondary"]).grid(row=card.row, column=0, sticky="w", padx=(0, 10), pady=3)

    def _row_widget(self, card, label: str, widget):
        self._form_label(card, label)
        widget.grid(row=card.row, column=1, sticky="ew", pady=3)
        card.row += 1
        return widget

    def _row_combo(self, card, label: str, var, values: List[str], key: str):
        cb = ttk.Combobox(card.form, textvariable=var, values=values, state="readonly", font=self.f["body"], width=18)
        self._persist[key] = var
        self._combo_values[key] = list(values)
        return self._row_widget(card, label, cb)

    def _row_full(self, card, widget, pady=(3, 3)):
        widget.grid(row=card.row, column=0, columnspan=2, sticky="ew", pady=pady)
        card.row += 1
        return widget

    @staticmethod
    def _quality_text(v) -> str:
        rf = int(float(v))
        hw = SystemProbe.rf_to_videotoolbox_q(rf) if IS_MAC_PLATFORM else rf
        return f"RF {rf}  ·  {HW_QUALITY_NAME} {'q' if IS_MAC_PLATFORM else ''}{hw}"

    def _build_settings_tab(self):
        self.settings_scroll = ScrollFrame(self.tab_settings, bg=THEME["bg_panel"])
        self.settings_scroll.pack(fill=tk.BOTH, expand=True)
        self.cards_frame = tk.Frame(self.settings_scroll.inner, bg=THEME["bg_panel"], padx=10, pady=10)
        self.cards_frame.pack(fill=tk.BOTH, expand=True)
        T = THEME

        # --- Output
        out = self._card(self.cards_frame, "NEW TASKS  ·  DEFAULTS")
        self.var_stage_compress = tk.BooleanVar(value=True)
        self.var_stage_audio = tk.BooleanVar(value=True)
        self.var_stage_whisper = tk.BooleanVar(value=True)
        self._persist.update(stage_compress=self.var_stage_compress, stage_audio=self.var_stage_audio, stage_whisper=self.var_stage_whisper)
        dsteps = self._flow(out.form, padx=12, bg=T["bg_card"])
        for label, var in (("Compress", self.var_stage_compress), ("Extract audio", self.var_stage_audio), ("Transcribe", self.var_stage_whisper)):
            dsteps.add(self._check(dsteps, label, var))
        self._row_widget(out, "Steps", dsteps)
        self._row_full(out, self._wrapping_label(out.form, "New tasks start with these steps and a copy of all Settings below. Change a single task "
                                                           "under Task Details; 'Use current Settings' updates a pending task.", font="small", fg=T["fg_dim"]), pady=(0, 6))
        self.var_respect_llc = tk.BooleanVar(value=True)
        self.var_auto_subfolder = tk.BooleanVar(value=True)
        self._persist.update(respect_llc=self.var_respect_llc, auto_subfolder=self.var_auto_subfolder)
        self._row_full(out, self._check(out.form, "Save results in a subfolder next to the (first) video", self.var_auto_subfolder))
        self._row_full(out, self._wrapping_label(out.form, "Interviews/P01.mp4 → Interviews/P01/P01.srt, P01.transcript.json …", font="small", fg=T["fg_dim"]), pady=(0, 4))
        self._row_full(out, self._check(out.form, "Apply LosslessCut (.llc) trims if present", self.var_respect_llc))
        folder = tk.Frame(out.form, bg=T["bg_card"])
        folder.columnconfigure(0, weight=1)
        self.var_output_dir = tk.StringVar(value=str(default_output_dir()))
        self._persist["output_dir"] = self.var_output_dir
        self._entry(folder, self.var_output_dir).grid(row=0, column=0, sticky="ew", ipady=2)
        self._tool(folder, "Browse…", self._browse_output).grid(row=0, column=1, padx=(6, 0))
        self._row_widget(out, "Output folder", folder)
        self._row_full(out, self._wrapping_label(out.form, "Used when subfolders are off (merge tasks get their own subfolder inside it).", font="small", fg=T["fg_dim"]), pady=(0, 0))

        # --- Video
        hb = self._card(self.cards_frame, "VIDEO  ·  STAGES 1–2")
        self.var_hb_encoder = tk.StringVar(value=DEFAULT_ENCODER)
        self._row_combo(hb, "Encoder", self.var_hb_encoder, list(VIDEO_ENCODERS), "hb_encoder")
        self.var_hb_quality = tk.IntVar(value=22)
        self._persist["hb_quality"] = self.var_hb_quality
        qf = tk.Frame(hb.form, bg=T["bg_card"])
        qf.columnconfigure(0, weight=1)
        self.lbl_hb_q = self._label(qf, self._quality_text(22), font="mono_small", fg=T["fg_secondary"])
        tk.Scale(qf, from_=14, to=32, orient=tk.HORIZONTAL, variable=self.var_hb_quality, showvalue=False, bg=T["bg_card"], fg=T["fg_primary"],
                 highlightthickness=0, troughcolor=T["bg_input"], sliderlength=16, width=10, bd=0,
                 command=lambda v: self.lbl_hb_q.config(text=self._quality_text(v))).grid(row=0, column=0, sticky="ew")
        self.lbl_hb_q.grid(row=0, column=1, padx=(8, 0))
        self._row_widget(hb, "Quality", qf)
        self.var_hb_res = tk.StringVar(value="Original")
        self._row_combo(hb, "Max resolution", self.var_hb_res, ["Original", "2160p (4K)", "1440p (2K)", "1080p (FHD)", "720p (HD)"], "hb_res")
        self.var_hb_fps = tk.StringVar(value="Same as source")
        self._row_combo(hb, "Frame rate", self.var_hb_fps, ["Same as source", "60 fps", "30 fps", "24 fps"], "hb_fps")
        self.var_hb_container = tk.StringVar(value="MKV")
        self._row_combo(hb, "Container", self.var_hb_container, ["MKV", "MP4"], "hb_container")
        self.var_hb_abitrate = tk.StringVar(value="192")
        self._row_combo(hb, "Audio kbps", self.var_hb_abitrate, ["128", "160", "192", "256", "320"], "hb_abitrate")

        # --- Audio & Whisper
        aw = self._card(self.cards_frame, "AUDIO & WHISPER  ·  STAGES 3–4")
        self.var_audio_format = tk.StringVar(value="WAV (16kHz Mono - Optimal for Whisper)")
        self._row_combo(aw, "Audio format", self.var_audio_format, ["WAV (16kHz Mono - Optimal for Whisper)", "WAV (48kHz Stereo)", "MP3 (192kbps)", "FLAC", "M4A"], "audio_format")
        self.var_speech_filter = tk.BooleanVar(value=False)
        self._persist["speech_filter"] = self.var_speech_filter
        self._row_full(aw, self._check(aw.form, "Voice bandpass filter (80 Hz – 8 kHz)", self.var_speech_filter))
        self.var_whisper_backend = tk.StringVar(value=DEFAULT_BACKEND)
        self._row_combo(aw, "Backend", self.var_whisper_backend, list(WHISPER_BACKENDS), "whisper_backend")
        self.var_whisper_model = tk.StringVar(value="large-v3-turbo")
        self._row_combo(aw, "Model", self.var_whisper_model, ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"], "whisper_model")
        self.var_whisper_lang = tk.StringVar(value="auto (Auto-Detect)")
        self._row_combo(aw, "Language", self.var_whisper_lang, WHISPER_LANGUAGES, "whisper_lang")
        self.var_whisper_prompt = tk.StringVar(value="")
        self._persist["whisper_prompt"] = self.var_whisper_prompt
        self._row_widget(aw, "Vocabulary", self._entry(aw.form, self.var_whisper_prompt, font="small"))
        self._row_full(aw, self._wrapping_label(aw.form, "Names, places, activity words Whisper should expect, comma-separated "
                                                          "(e.g. Robin, Sam, Riverside Club, tablet). Keep it short; it is saved per task.",
                                                font="small", fg=T["fg_dim"]), pady=(0, 4))
        self._row_full(aw, self._wrapping_label(aw.form, "Set the language for single-language interviews; auto-detect only listens to the first 30 s.",
                                                font="small", fg=T["fg_dim"]), pady=(2, 0))

        # --- Speakers & exports
        se = self._card(self.cards_frame, "SPEAKERS & EXPORTS")
        self.var_diarize_mode = tk.StringVar(value=GPU_DIARIZATION)
        self._row_combo(se, "Diarization", self.var_diarize_mode, [
            GPU_DIARIZATION,
            "Pyannote.audio 3.1 Neural (Requires HF Token)",
            "Disabled (No Speaker Labels)",
        ], "diarize_mode")
        self.var_num_speakers = tk.IntVar(value=0)
        self._persist["num_speakers"] = self.var_num_speakers
        sp = tk.Frame(se.form, bg=T["bg_card"])
        tk.Spinbox(sp, from_=0, to=12, increment=1, textvariable=self.var_num_speakers, width=4, bg=T["bg_input"], fg=T["fg_primary"],
                   buttonbackground=T["bg_card"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"], font=self.f["body"]).pack(side=tk.LEFT)
        self._label(sp, "0 = detect automatically", font="small", fg=T["fg_dim"]).pack(side=tk.LEFT, padx=(8, 0))
        self._row_widget(se, "Speakers", sp)
        self.var_hf_token = tk.StringVar(value="")
        self.var_remember_token = tk.BooleanVar(value=True)
        self._persist["remember_token"] = self.var_remember_token
        self._stored_token = ""
        tok = tk.Frame(se.form, bg=T["bg_card"])
        tok.columnconfigure(0, weight=1)
        self._entry(tok, self.var_hf_token, show="•").grid(row=0, column=0, sticky="ew", ipady=2)
        self._check(tok, "Remember in keyring" if PLATFORM == "linux" else "Remember", self.var_remember_token, font="small").grid(row=0, column=1, padx=(8, 0))
        self.var_remember_token.trace_add("write", lambda *a: self.after_idle(self._sync_token))
        self._row_widget(se, "HF token", tok)
        if SecretStore.available():
            note = f"Only needed for pyannote. Stored in your {SecretStore.LABEL}, never in the settings file."
        elif IS_MAC_PLATFORM:
            note = "Only needed for pyannote. The Keychain is not available here, so it is not saved."
        else:
            note = "Only needed for pyannote. Not saved: install libsecret-tools (secret-tool) to keep it in your keyring."
        self._row_full(se, self._wrapping_label(se.form, note, font="small", fg=T["fg_dim"]), pady=(0, 6))
        fmt = self._flow(se.form, padx=12, bg=T["bg_card"])
        self.var_fmt_srt = tk.BooleanVar(value=True)
        self.var_fmt_vtt = tk.BooleanVar(value=False)
        self.var_fmt_txt = tk.BooleanVar(value=True)
        self.var_fmt_rtf = tk.BooleanVar(value=True)
        self.var_fmt_para_rtf = tk.BooleanVar(value=True)
        self.var_fmt_para_txt = tk.BooleanVar(value=False)
        self._persist.update(fmt_srt=self.var_fmt_srt, fmt_vtt=self.var_fmt_vtt, fmt_txt=self.var_fmt_txt, fmt_rtf=self.var_fmt_rtf,
                             fmt_para_rtf=self.var_fmt_para_rtf, fmt_para_txt=self.var_fmt_para_txt)
        for t, var in [("Paragraphs RTF (Word / MAXQDA)", self.var_fmt_para_rtf), ("Paragraphs TXT", self.var_fmt_para_txt),
                       ("SRT subtitles", self.var_fmt_srt), ("VTT subtitles", self.var_fmt_vtt), ("Segments TXT", self.var_fmt_txt),
                       ("RTF + confidence colours", self.var_fmt_rtf)]:
            fmt.add(self._check(fmt, t, var))
        self._row_widget(se, "Exports", fmt)
        self._row_full(se, self._wrapping_label(se.form, "Paragraphs = one block per speaker turn, for reading and coding. Subtitles (SRT/VTT) keep the "
                                                          "short timed segments for video. A <name>.transcript.json master copy is always saved; "
                                                          "all exports are regenerated from it after every edit.", font="small", fg=T["fg_dim"]), pady=(4, 0))

        self._cards = [out, hb, aw, se]
        self._card_cols = None
        self.cards_frame.bind("<Configure>", self._layout_cards)
        self._layout_cards()

    def _layout_cards(self, event=None):
        width = event.width if event else self.cards_frame.winfo_width()
        cols = 2 if width >= 760 else 1
        if cols == self._card_cols:
            return
        self._card_cols = cols
        for c in self._cards:
            c.grid_forget()
        for i in range(2):
            self.cards_frame.columnconfigure(i, weight=1 if i < cols else 0, uniform="cards" if i < cols else "")
        for i, card in enumerate(self._cards):
            r, c = (i // 2, i % 2) if cols == 2 else (i, 0)
            padx = ((0, 5) if c == 0 else (5, 0)) if cols == 2 else (0, 0)
            card.grid(row=r, column=c, sticky="nsew", padx=padx, pady=(0, 10))

    def _on_mousewheel(self, event, units_override: int = 0):
        """Mouse wheel: scroll the Settings cards, and never change a combobox value by scrolling over it.
        X11 (Linux) reports the wheel as Button-4/5 instead of <MouseWheel>; app.py passes those as ±1."""
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        if widget is not None and self.settings_scroll.contains(widget):
            d = getattr(event, "delta", 0) or 0
            if units_override:
                units = units_override
            else:
                units = (-d if abs(d) < 120 else -int(d / 120)) if IS_MAC else (-int(d / 120) if d else 0)
            if units:
                self.settings_scroll.scroll(units)
            return "break"
        if "combobox" in str(event.widget) and "popdown" not in str(event.widget):
            return "break"
        return None

    # ------------------------------------------------------------------ console
    def _build_console_tab(self):
        frame = tk.Frame(self.tab_console, bg=THEME["bg_panel"])
        frame.pack(fill=tk.BOTH, expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.console_text = tk.Text(frame, bg="#0a0c10", fg=THEME["fg_primary"], font=self.f["mono_small"], relief=tk.FLAT, padx=10, pady=8,
                                    wrap=tk.WORD, highlightthickness=0, spacing1=1)
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=sb.set)
        self.console_text.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.console_text.tag_config("INFO", foreground="#c9ced8")
        self.console_text.tag_config("STAGE", foreground="#38bdf8", font=self.f["mono_small_bold"])
        self.console_text.tag_config("CMD", foreground="#8b80c9")
        self.console_text.tag_config("SUCCESS", foreground=THEME["ok"], font=self.f["mono_small_bold"])
        self.console_text.tag_config("WARN", foreground=THEME["warn"])
        self.console_text.tag_config("ERROR", foreground=THEME["err"], font=self.f["mono_small_bold"])

    def _append_log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        at_bottom = self.console_text.yview()[1] >= 0.999
        self.console_text.insert(tk.END, f"{ts}  {msg}\n", level)
        lines = int(self.console_text.index("end-1c").split(".")[0])
        if lines > 8000:
            self.console_text.delete("1.0", f"{lines - 6000}.0")
        if at_bottom:
            self.console_text.see(tk.END)
