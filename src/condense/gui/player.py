"""Timeline strip and audio player."""
from condense.gui._common import *  # noqa: F401,F403


class PlayerMixin:
    # ----- timeline strip -----
    @staticmethod
    def _dim(color: str, f: float = 0.45, bg: str = "#11141b") -> str:
        c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{int(b[k] + (c[k] - b[k]) * f):02x}" for k in range(3))

    def _timeline_items(self, width: int, height: int = 30) -> List[Tuple]:
        """Shapes to draw: ('band', x0, x1, colour), ('check', x0, x1), ('tick', x). Pure geometry, so it can be tested."""
        doc = self.active_doc
        dur = self._duration()
        if doc is None or dur <= 0 or width <= 2:
            return []
        sx = width / dur
        roster = doc.roster()
        items: List[Tuple] = []
        for seg in doc.segments:
            x0, x1 = float(seg.get("start", 0.0)) * sx, max(float(seg.get("start", 0.0)) * sx + 1, float(seg.get("end", 0.0)) * sx)
            spk = seg.get("speaker")
            color = SPEAKER_COLORS[roster.index(spk) % len(SPEAKER_COLORS)] if spk in roster else "#64748b"
            items.append(("band", x0, x1, color))
            if seg.get("checked"):
                items.append(("check", x0, x1))
        for i in self.low_conf:
            items.append(("tick", self.word_spans[i]["start"] * sx))
        for s, e in doc.cuts:
            items.append(("cut", s * sx, max(s * sx + 2, e * sx)))
        return items

    def _timeline_redraw(self):
        tl = self.__dict__.get("timeline")
        if tl is None:
            return
        tl.delete("all")
        width, height = tl.winfo_width(), int(tl.cget("height") or 30)
        for item in self._timeline_items(width, height):
            if item[0] == "band":
                _, x0, x1, color = item
                tl.create_rectangle(x0, 9, x1, height - 7, fill=self._dim(color), outline="")
            elif item[0] == "check":
                _, x0, x1 = item
                tl.create_rectangle(x0, height - 5, x1, height - 2, fill=THEME["ok"], outline="")
            elif item[0] == "cut":
                _, x0, x1 = item
                tl.create_rectangle(x0, 0, x1, height, fill="#000000", stipple="gray50", outline="#f59e0b")
            else:
                tl.create_line(item[1], 1, item[1], 7, fill="#f87171")
        self._tl_playhead = tl.create_line(0, 0, 0, height, fill="#ffffff", width=2)
        self._timeline_playhead()

    def _timeline_playhead(self):
        tl, ph = self.__dict__.get("timeline"), self.__dict__.get("_tl_playhead")
        dur = self._duration() if self.active_doc else 0
        if tl is None or ph is None or dur <= 0:
            return
        x = self._current_pos() / dur * tl.winfo_width()
        tl.coords(ph, x, 0, x, int(tl.cget("height") or 30))

    def _timeline_click(self, event):
        dur = self._duration()
        width = self.timeline.winfo_width()
        if self.active_doc is None or dur <= 0 or width <= 2:
            return
        import bisect

        pos = max(0.0, min(dur, event.x / width * dur))
        i = max(0, bisect.bisect_right(self.span_starts, pos) - 1) if self.span_starts else None
        if i is not None and i < len(self.word_spans):
            self._select_span(i)
        if self.playing:
            self._player_start(pos)
        else:
            self.play_pos = pos
            self._update_time_label()
            self._video_still(pos)



    @staticmethod
    def _parse_time(text: str) -> Optional[float]:
        """'83.4', '1:23.4' or '0:01:23.4' -> seconds."""
        text = (text or "").strip().replace(",", ".")
        if not text:
            return None
        try:
            parts = [float(p) for p in text.split(":")]
        except ValueError:
            return None
        if len(parts) > 3 or any(p < 0 for p in parts):
            return None
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return total

    def _insert_defaults(self) -> Tuple[float, float, str]:
        """Start = where you paused; end = start of the next segment (if close) or +3 s; speaker = the one speaking before."""
        doc = self.active_doc
        pos = self._current_pos()
        starts = sorted(float(sg.get("start", 0.0)) for sg in doc.segments)
        nxt = next((t for t in starts if t > pos + 0.05), None)
        end = nxt if nxt is not None and nxt - pos <= 15 else pos + 3.0
        before = [sg for sg in doc.segments if float(sg.get("start", 0.0)) <= pos and sg.get("speaker")]
        if self.edit_seg_idx is not None and self.edit_seg_idx < len(doc.segments) and doc.segments[self.edit_seg_idx].get("speaker"):
            speaker = doc.segments[self.edit_seg_idx]["speaker"]
        else:
            speaker = before[-1]["speaker"] if before else (doc.roster()[0] if doc.roster() else "")
        return pos, end, speaker

    def _insert_segment_dialog(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        self._autosave_edit()
        if self.playing:
            self._player_pause()
        start, end, speaker = self._insert_defaults()
        T = THEME
        win = tk.Toplevel(self)
        win.title("Insert missing segment")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Add speech the transcript missed", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self._label(win, "Start is where you paused. Keep listening with Esc (play/pause) and press 'End = now' when the missing part ends.",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=440, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        var_start = tk.StringVar(value=short_ts(start))
        var_end = tk.StringVar(value=short_ts(end))
        var_spk = tk.StringVar(value=speaker)
        for r, (label, var, which) in enumerate((("Start", var_start, "start"), ("End", var_end, "end")), start=2):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
            self._entry(win, var, width=12, font="mono_small").grid(row=r, column=1, sticky="w", pady=3, padx=(10, 6))
            self._tool(win, f"{label} = now", lambda v=var: v.set(short_ts(self._current_pos()))).grid(row=r, column=2, sticky="w")
        self._label(win, "Speaker", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(win, textvariable=var_spk, values=doc.roster(), width=20, font=self.f["small"]).grid(row=4, column=1, columnspan=2, sticky="w", pady=3, padx=(10, 0))
        self._label(win, "Text", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=5, column=0, sticky="nw", pady=3)
        text_box = tk.Text(win, height=4, width=48, wrap=tk.WORD, font=self.f["transcript"], bg=T["bg_input"], fg=T["fg_primary"],
                           insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"],
                           highlightcolor=T["accent"], padx=6, pady=4, undo=True)
        text_box.grid(row=5, column=1, columnspan=2, sticky="nsew", pady=3, padx=(10, 0))
        win.columnconfigure(2, weight=1)
        win.rowconfigure(5, weight=1)
        msg = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        msg.grid(row=6, column=0, columnspan=3, sticky="w")
        btns = tk.Frame(win, bg=T["bg_panel"])
        btns.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))

        def do_insert():
            error = self._do_insert_segment(var_start.get(), var_end.get(), var_spk.get(), text_box.get("1.0", "end-1c"))
            if error:
                msg.configure(text=error)
            else:
                win.destroy()
            return "break"

        self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self._tool(btns, "Insert  ⌘↩", do_insert, primary=True).pack(side=tk.RIGHT)
        for seq in (f"<{MOD}-Return>", f"<{MOD}-KP_Enter>"):
            text_box.bind(seq, lambda e: (do_insert(), "break")[-1])  # break: must not also reach the window-wide Ctrl+Enter
        text_box.focus_set()

    def _do_insert_segment(self, start_text: str, end_text: str, speaker: str, text: str) -> Optional[str]:
        """Validate and insert; returns an error message for the dialog, or None on success."""
        doc = self.active_doc
        if doc is None:
            return "No transcript open."
        start, end = self._parse_time(start_text), self._parse_time(end_text)
        if start is None or (end is None and (end_text or "").strip()):
            return "Times must look like 1:23.4 or 0:01:23.4."
        end = end if end is not None else start  # End is optional
        if is_blank_text(text):
            return "Type the missing words first."
        if end <= start:
            end = start + max(1.0, 0.35 * len(text.split()))  # sensible length if End wasn't set
        idx = doc.insert_segment(start, end, text, speaker)
        if self._commit(f"Inserted segment {idx + 1} at {short_ts(start)}.") is None:
            return "Could not save the transcript."
        self._rerender()
        self._load_segment_into_editor(idx)
        first = self.span_lookup.get((idx, 0))
        if first is not None:
            self._select_span(first, load_editor=False)
        self._append_log(f"Inserted a missed segment at {short_ts(start)} ({speaker or 'no speaker'}); logged in the edit history.", "SUCCESS")
        return None

    def _delete_segment(self, ask: bool = True):
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or idx is None or idx >= len(doc.segments):
            return
        text = (doc.segments[idx].get("text") or "").strip()
        if ask and not is_blank_text(text) and not messagebox.askyesno(
                "Delete segment?", f"Delete segment {idx + 1}?\n\n“{text[:160]}”\n\nThe deletion is logged in the transcript's edit history.", parent=self):
            return
        self.edit_original = ("", "")  # nothing left to autosave
        self.edit_text.delete("1.0", tk.END)
        if doc.delete_segment(idx) and self._commit(f"Deleted segment {idx + 1}.") is not None:
            self._clear_editor()
            self._rerender()
            if doc.segments:
                self._load_segment_into_editor(min(idx, len(doc.segments) - 1))

    def _save_segment_edit(self, rerender: bool = True, reload: bool = True):
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or idx is None or idx >= len(doc.segments):
            return
        if is_blank_text(self.edit_text.get("1.0", "end-1c")) and not is_blank_text(self.edit_original[0]):
            self._delete_segment()  # saving an emptied segment = delete it (asks first)
            return
        changed = doc.edit_segment_text(idx, self.edit_text.get("1.0", "end-1c"))
        changed = doc.set_segment_speaker(idx, self.var_seg_speaker.get()) or changed
        changed = doc.set_segment_note(idx, self.var_seg_note.get()) or changed
        if not changed:
            return
        if self._commit(f"Saved segment {idx + 1} — transcript and exports updated.") is not None:
            self.edit_original = ((doc.segments[idx].get("text") or "").strip(), doc.segments[idx].get("speaker") or "")
            self.edit_note_original = doc.segments[idx].get("note") or ""
            if rerender:
                self._rerender()
            if reload:
                self._load_segment_into_editor(idx)

    def _autosave_edit(self, rerender: bool = True):
        if self._editor_dirty():
            self._save_segment_edit(rerender=rerender, reload=rerender)

    def _revert_segment_edit(self):
        if self.edit_seg_idx is None:
            return
        self.edit_text.delete("1.0", tk.END)
        self.edit_text.insert("1.0", self.edit_original[0])
        self.var_seg_speaker.set(self.edit_original[1])
        self.var_seg_note.set(self.edit_note_original)

    def _split_at_current_word(self):
        doc = self.active_doc
        if doc is None or self.current_span is None:
            self.lbl_status.config(text="Click the word that should start the new segment, then Split.")
            return
        self._autosave_edit()
        if self.current_span is None:
            return
        sp = self.word_spans[self.current_span]
        if doc.split_segment(sp["seg"], sp["w"]) and self._commit(f"Split segment {sp['seg'] + 1} before “{sp['word']}”.") is not None:
            self._rerender()
            self._load_segment_into_editor(sp["seg"] + 1)
        else:
            self.lbl_status.config(text="Pick a word in the middle of a segment to split before it.")

    def _merge_with_next(self):
        doc, idx = self.active_doc, self.edit_seg_idx
        if doc is None or idx is None:
            return
        self._autosave_edit()
        if doc.merge_with_next(idx) and self._commit(f"Merged segments {idx + 1} and {idx + 2}.") is not None:
            self._rerender()
            self._load_segment_into_editor(idx)

    # ----- player -----
    def _media(self) -> Optional[Path]:
        doc = self.active_doc
        media = doc.media_path("playback") if doc else None
        return media if media and media.exists() else None

    def _speed(self) -> float:
        try:
            return float(self.var_speed.get().rstrip("×"))
        except Exception:
            return 1.0

    def _duration(self) -> float:
        segs = self.active_doc.segments if self.active_doc else []
        return max((float(s.get("end", 0.0)) for s in segs), default=0.0)

    def _kill_player(self):
        if self._play_proc is not None and self._play_proc.poll() is None:
            self._play_proc.terminate()
        self._play_proc = None

    def _player_start(self, pos: float, duration: Optional[float] = None):
        media = self._media()
        if media is None:
            self.lbl_word.config(text="⚠ Playback media not found (moved or deleted?) — see 'media' in the .transcript.json", fg=THEME["warn"])
            return
        self._kill_player()
        pos = max(0.0, pos)
        speed = self._speed()
        tempo = ["-af", f"atempo={speed}"] if abs(speed - 1.0) > 1e-3 else []
        if shutil.which("ffplay"):
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{pos:.2f}"]
            if duration:
                cmd += ["-t", f"{duration:.2f}"]
            cmd += tempo + [str(media)]
        elif shutil.which("ffmpeg"):
            chunk = Path(tempfile.gettempdir()) / "condense_play.wav"
            length = duration or 600.0  # afplay can't seek: play up to 10 min, Pause/Play continues from there
            try:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{pos:.2f}", "-t", f"{length:.2f}", "-i", str(media)]
                               + tempo + [str(chunk)], capture_output=True, timeout=60, check=True)
            except Exception as exc:
                self._append_log(f"Could not prepare audio for playback: {exc}", "ERROR")
                return
            cmd = ["afplay", str(chunk)]
        else:
            self._append_log("ffmpeg/ffplay not found (brew install ffmpeg).", "ERROR")
            return
        self._play_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.playing = True
        self._play_from = pos
        self._play_speed = speed
        self._play_t0 = time.monotonic() + self.PLAYER_LATENCY_S
        self._play_until = pos + duration if duration else None
        self.btn_play.configure(text="❚❚ Pause")
        self._video_start(pos)
        self._schedule_tick()

    def _current_pos(self) -> float:
        if self.playing:
            return self._play_from + max(0.0, time.monotonic() - self._play_t0) * self._play_speed
        return self.play_pos

    def _player_pause(self):
        if self.playing:
            self.play_pos = self._current_pos()
            if self._play_until is not None:
                self.play_pos = min(self.play_pos, self._play_until)
        was_streaming = self._video_proc is not None
        self.playing = False
        self._kill_player()
        self._video_stop()
        if was_streaming:
            self._video_still(self.play_pos)
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        self.btn_play.configure(text="▶ Play")
        self._update_time_label()

    def _toggle_play(self, event=None):
        if event is not None and "popdown" in str(getattr(event, "widget", "")):
            return None
        if self.active_doc is None:
            return "break"
        if self.playing:
            self._player_pause()
        else:
            self._player_start(self.play_pos)
        return "break"

    def _seek_rel(self, delta: float):
        pos = max(0.0, min(self._duration(), self._current_pos() + delta))
        if self.playing:
            self._player_start(pos)
        else:
            self.play_pos = pos
            self._follow(pos)
            self._update_time_label()
            self._video_still(pos)

    def _play_from_current_word(self):
        if self.current_span is None:
            return
        self._player_start(max(0.0, self.word_spans[self.current_span]["start"] - 1.0))

    def _on_speed_change(self):
        if self.playing:
            self._player_start(self._current_pos())
        self._update_time_label()

    def _speed_step(self, direction: int):
        """⌘⇧. faster / ⌘⇧, slower — through the steps in the speed menu."""
        steps = self._combo_values["speed"]
        cur = self._speed()
        values = [float(v.rstrip("×")) for v in steps]
        i = min(range(len(values)), key=lambda k: abs(values[k] - cur))
        j = max(0, min(len(steps) - 1, i + direction))
        if j != i:
            self.var_speed.set(steps[j])
            self._on_speed_change()
            self.lbl_status.config(text=f"Playback speed {steps[j]}")

    def _schedule_tick(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
        self._tick_job = self.after(150, self._player_tick)

    def _player_tick(self):
        self._tick_job = None
        if not self.playing:
            return
        pos = self._current_pos()
        jump = self._cut_skip_target(self.__dict__.get("_last_tick_pos", pos), pos)
        if jump is not None:
            self._last_tick_pos = jump
            self._player_start(jump)
            return
        self._last_tick_pos = pos
        ended = self._play_proc is None or self._play_proc.poll() is not None
        if ended or (self._play_until is not None and pos >= self._play_until):
            self._player_pause()
            return
        self._follow(pos)
        self._update_time_label()
        self._schedule_tick()

    def _follow(self, pos: float):
        import bisect

        if not self.word_spans:
            return
        i = max(0, bisect.bisect_right(self.span_starts, pos) - 1)
        sp = self.word_spans[i]
        self.preview_text.tag_remove("PLAYING", "1.0", tk.END)
        self.preview_text.tag_add("PLAYING", f"{sp['line']}.{sp['c0']}", f"{sp['line']}.{sp['c1']}")
        if self.var_follow.get():
            self.preview_text.see(f"{sp['line']}.{sp['c0']}")

    def _update_time_label(self):
        speed = self._speed()
        extra = f"  ·  {self.var_speed.get()}" if abs(speed - 1.0) > 1e-3 else ""
        self.lbl_time.configure(text=f"{short_ts(self._current_pos())[:-2]} / {short_ts(self._duration())[:-2]}{extra}")
        self._timeline_playhead()

    def _open_transcript(self):
        initial = self.settings.get("last_transcript_dir") or str(Path.home())
        path = self._ask_open_file("Open transcript", initial, [("Condense transcript", "*.transcript.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            doc = TranscriptDocument.load(Path(path))
        except Exception as exc:
            messagebox.showerror("Could not open transcript", str(exc), parent=self)
            return
        self.settings["last_transcript_dir"] = str(Path(path).parent)
        self._render_doc(doc)
        self.notebook.select(self.tab_preview)

    def _reexport_open_transcript(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        self._autosave_edit()
        doc.data["export_formats"] = self._export_formats()
        created = self._commit(f"Re-exported {doc.base_name} with the current export settings.", undoable=False)
        if created is not None:
            self._append_log(f"Re-exported {', '.join(p.name for p in created.values()) or 'nothing (all formats off)'}", "SUCCESS")

    def _show_speakers_dialog(self):
        """Rename, add and remove speakers. Renames apply to every segment and export; swaps work."""
        doc = self.active_doc
        if doc is None:
            self.lbl_word.config(text="Open a transcript first.")
            return
        self._autosave_edit()
        T = THEME
        counts = doc.speakers()
        roster = doc.roster()
        win = tk.Toplevel(self)
        win.title("Speakers")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Speakers in this transcript", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=4, sticky="w")
        self._label(win, "Rename by typing a new name (an existing name merges the two). Add speakers that detection missed, then assign them "
                         "in the segment editor with the speaker chips or ⌃1–⌃9.", font="small", fg=T["fg_dim"], bg=T["bg_panel"],
                    wraplength=460, justify=tk.LEFT).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))
        entries: Dict[str, tk.StringVar] = {}
        remove_vars: Dict[str, tk.BooleanVar] = {}
        r = 2
        for i, spk in enumerate(roster):
            color = SPEAKER_COLORS[i % len(SPEAKER_COLORS)]
            self._label(win, f"⌃{i + 1}" if i < 9 else "", font="mono_small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 6))
            self._label(win, spk, font="mono_small_bold", fg=color, bg=T["bg_panel"]).grid(row=r, column=1, sticky="w", pady=3)
            var = tk.StringVar(value=spk)
            self._entry(win, var, width=22, font="body").grid(row=r, column=2, sticky="ew", pady=3, padx=(10, 6))
            entries[spk] = var
            n = counts.get(spk, 0)
            if n:
                self._label(win, f"{n} segment{'s' if n != 1 else ''}", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=3, sticky="w")
            else:
                rv = tk.BooleanVar(value=False)
                remove_vars[spk] = rv
                self._check(win, "unused · remove", rv, bg=T["bg_panel"], font="small").grid(row=r, column=3, sticky="w")
            r += 1
        self._label(win, "Add speaker", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
        var_new = tk.StringVar(value="")
        new_entry = self._entry(win, var_new, width=22, font="body")
        new_entry.grid(row=r, column=2, sticky="ew", pady=(10, 0), padx=(10, 6))
        self._label(win, "e.g. P03, Facilitator", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=3, sticky="w", pady=(10, 0))
        win.columnconfigure(2, weight=1)
        btns = tk.Frame(win, bg=T["bg_panel"])
        btns.grid(row=r + 1, column=0, columnspan=4, sticky="e", pady=(14, 0))

        def apply():
            mapping = {old: v.get().strip() for old, v in entries.items() if v.get().strip() and v.get().strip() != old}
            removals = [n for n, v in remove_vars.items() if v.get() and n not in mapping]
            new_names = [n.strip() for n in re.split(r"[,;]", var_new.get()) if n.strip()]
            win.destroy()
            self._apply_speaker_changes(mapping, removals, new_names)
            return "break"

        self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self._tool(btns, "Apply", apply, primary=True).pack(side=tk.RIGHT)
        win.bind("<Return>", lambda e: apply())
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        if not roster:
            new_entry.focus_set()

    def _apply_speaker_changes(self, mapping: Dict[str, str], removals: List[str], new_names: List[str]):
        doc = self.active_doc
        if doc is None:
            return
        changed = []
        if mapping and doc.rename_speakers(mapping):
            changed.append(", ".join(f"{o} → {n}" for o, n in mapping.items()))
        for name in removals:
            if doc.remove_speaker(name):
                changed.append(f"removed {name}")
        for name in new_names:
            if doc.add_speaker(name):
                changed.append(f"added {name}")
        if not changed:
            return
        summary = "; ".join(changed)
        if self._commit(f"Speakers: {summary}.") is not None:
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(self.edit_seg_idx)
            self._append_log(f"Speakers: {summary}. Transcript and exports updated.", "SUCCESS")
