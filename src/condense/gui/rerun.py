"""Re-transcribe a passage / re-run speaker detection."""
from condense.gui._common import *  # noqa: F401,F403


class RerunMixin:
    # ----- re-run Whisper on a passage / speakers on a transcript -----
    LANG_NAMES = {w.split()[0]: w.split("(", 1)[1].rstrip(")") for w in WHISPER_LANGUAGES if "(" in w}

    def _rerun_busy(self) -> bool:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "The task queue is running. Re-runs use the same GPU, so wait until it has finished.", parent=self)
            return True
        if self.__dict__.get("_rerun_thread") is not None and self._rerun_thread.is_alive():
            messagebox.showinfo("Busy", "Another re-run is still in progress.", parent=self)
            return True
        return False

    def _doc_setting(self, key: str, default: str) -> str:
        w = ((self.active_doc.data.get("provenance") or {}).get("whisper") or {}) if self.active_doc else {}
        return str(w.get(key) or default)

    def _start_rerun(self, kind: str, params: Dict, clip: Optional[Tuple[float, float]], segments_json: Optional[List[Dict]] = None):
        """Run WHISPER_RUNNER in the background on (a clip of) the transcript's playback audio."""
        doc = self.active_doc
        media = self._media()
        if doc is None or media is None:
            messagebox.showerror("No audio", "The playback media of this transcript was not found, so nothing can be re-run.", parent=self)
            return
        best_py = MacAppInstaller.find_best_python()
        token = self.var_hf_token.get().strip()
        params = dict(params, doc_path=str(doc.path))
        msgq = self.msg_queue

        def job():
            tmp = Path(tempfile.mkdtemp(prefix="condense_rerun_"))
            try:
                wav = tmp / "clip.wav"
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                if clip:
                    cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1] - clip[0]:.3f}"]
                cmd += ["-i", str(media), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(wav)]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                if res.returncode != 0:
                    raise RuntimeError("Could not cut the audio: " + (res.stderr or "").strip()[-300:])
                out = tmp / "out.json"
                env = os.environ.copy()
                env.update(PYTORCH_ENABLE_MPS_FALLBACK="1", PYTHONUNBUFFERED="1", PIPELINE_HF_TOKEN=token,
                           PIPELINE_TASK=params.get("task", "transcribe"), PIPELINE_PROMPT=params.get("prompt", ""),
                           PIPELINE_MODE="diarize_only" if segments_json is not None else "full")
                if segments_json is not None:
                    seg_file = tmp / "segments.json"
                    seg_file.write_text(json.dumps(segments_json), encoding="utf-8")
                    env["PIPELINE_SEGMENTS_JSON"] = str(seg_file)
                proc = subprocess.Popen([best_py, "-c", WHISPER_RUNNER, str(wav), str(wav), params.get("model", "large-v3"),
                                         params.get("language", "auto"), str(out), params.get("diarize_mode", "Disabled"),
                                         str(params.get("num_speakers", 0)), params.get("backend", DEFAULT_BACKEND)],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                self._rerun_proc = proc
                tail: List[str] = []
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        tail = (tail + [line])[-6:]
                        msgq.put({"type": "rerun_progress", "kind": kind, "text": line})
                if proc.wait() != 0 or not out.exists():
                    raise RuntimeError("Whisper failed:\n" + "\n".join(tail))
                msgq.put({"type": "rerun_done", "kind": kind, "params": params, "result": json.loads(out.read_text(encoding="utf-8"))})
            except Exception as exc:
                msgq.put({"type": "rerun_failed", "kind": kind, "error": str(exc)})
            finally:
                self._rerun_proc = None
                shutil.rmtree(tmp, ignore_errors=True)

        self._rerun_thread = threading.Thread(target=job, daemon=True)
        self._rerun_thread.start()

    def _handle_rerun_message(self, msg: Dict):
        kind = msg.get("kind")
        label = "Re-transcribing" if kind == "retranscribe" else "Detecting speakers"
        if msg["type"] == "rerun_progress":
            text = msg["text"]
            m = re.search(r"(\d+)%\|", text)
            self.lbl_status.config(text=f"{label}… {m.group(1)}%" if m else f"{label}… {text[:120]}")
            if not m:
                self._append_log(f"[{label}] {text}", "INFO")
            return
        if msg["type"] == "rerun_failed":
            self.lbl_status.config(text=f"{label} failed — see the console.")
            self._append_log(f"{label} failed: {msg['error']}", "ERROR")
            messagebox.showerror(f"{label} failed", msg["error"][-800:], parent=self)
            return
        p = msg["params"]
        doc = self.active_doc
        if doc is None or str(doc.path) != p["doc_path"]:
            messagebox.showinfo("Transcript changed", "You switched to another transcript while this was running; the result was not applied.", parent=self)
            return
        self._autosave_edit(rerender=False)
        if kind == "retranscribe":
            self._apply_retranscription(doc, msg["result"], p)
        else:
            self._apply_rediarization(doc, msg["result"], p)

    # --- re-transcribe a passage
    def _retranscribe_dialog(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        if self._rerun_busy():
            return
        self._autosave_edit()
        if self.playing:
            self._player_pause()
        T = THEME
        if self.edit_seg_idx is not None and self.edit_seg_idx < len(doc.segments):
            seg = doc.segments[self.edit_seg_idx]
            start, end = float(seg["start"]), float(seg["end"])
        else:
            start = self._current_pos()
            end = start + 10.0
        win = tk.Toplevel(self)
        win.title("Re-transcribe passage")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "Run Whisper again on part of this recording", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self._label(win, "The segments in this time range are replaced by the new result. Your other edits stay untouched, and ⌘Z undoes it. "
                         "Useful for mumbled speech (bigger model), a language switch (force the language) or an English version (translate).",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        v_start, v_end = tk.StringVar(value=short_ts(start)), tk.StringVar(value=short_ts(end))
        r = 2
        for label, var in (("From", v_start), ("To", v_end)):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
            self._entry(win, var, width=12, font="mono_small").grid(row=r, column=1, sticky="w", padx=(10, 6), pady=3)
            self._tool(win, f"{label} = now", lambda v=var: v.set(short_ts(self._current_pos()))).grid(row=r, column=2, sticky="w")
            r += 1

        def extend(direction: int):
            a, b = self._parse_time(v_start.get()), self._parse_time(v_end.get())
            if a is None or b is None:
                return
            if direction < 0:
                prev = [sg for sg in doc.segments if float(sg["end"]) <= a + 0.05]
                if prev:
                    v_start.set(short_ts(float(prev[-1]["start"])))
            else:
                nxt = [sg for sg in doc.segments if float(sg["start"]) >= b - 0.05]
                if nxt:
                    v_end.set(short_ts(float(nxt[0]["end"])))

        ext = tk.Frame(win, bg=T["bg_panel"])
        ext.grid(row=r, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(0, 6))
        self._tool(ext, "+ previous segment", lambda: extend(-1)).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(ext, "+ next segment", lambda: extend(+1)).pack(side=tk.LEFT)
        r += 1
        v_model = tk.StringVar(value="large-v3")
        lang_default = self._doc_setting("language_setting", "auto")
        v_lang = tk.StringVar(value=next((w for w in WHISPER_LANGUAGES if w.split()[0] == lang_default), WHISPER_LANGUAGES[0]))
        v_task = tk.StringVar(value="Transcribe (keep the spoken language)")
        v_spk = tk.StringVar(value="(keep the speakers of the replaced segments)")
        v_prompt = tk.StringVar(value=self.var_whisper_prompt.get())
        for label, var, values in (
            ("Model", v_model, ["large-v3", "large-v3-turbo", "medium", "small"]),
            ("Language", v_lang, WHISPER_LANGUAGES),
            ("Output", v_task, ["Transcribe (keep the spoken language)", "Translate to English"]),
            ("Speaker", v_spk, ["(keep the speakers of the replaced segments)"] + doc.roster()),
        ):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Combobox(win, textvariable=var, values=values, state="readonly", width=36, font=self.f["small"]).grid(row=r, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=3)
            r += 1
        self._label(win, "Vocabulary", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=3)
        self._entry(win, v_prompt, width=40, font="small").grid(row=r, column=1, columnspan=2, sticky="ew", padx=(10, 0), pady=3)
        r += 1
        self._label(win, "large-v3 is the most accurate (first use downloads ~3 GB); large-v3-turbo can't translate well.",
                    font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        msg = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        msg.grid(row=r + 1, column=0, columnspan=3, sticky="w")
        btns = tk.Frame(win, bg=T["bg_panel"])
        btns.grid(row=r + 2, column=0, columnspan=3, sticky="e", pady=(10, 0))

        def run():
            err = self._launch_retranscription(v_start.get(), v_end.get(), v_model.get(), v_lang.get(), v_task.get(), v_spk.get(), v_prompt.get())
            if err:
                msg.configure(text=err)
            else:
                win.destroy()

        self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self._tool(btns, "Re-transcribe", run, primary=True).pack(side=tk.RIGHT)

    def _launch_retranscription(self, start_text, end_text, model, lang_label, task_label, speaker_label, prompt) -> Optional[str]:
        start, end = self._parse_time(start_text), self._parse_time(end_text)
        if start is None or end is None or end <= start:
            return "Check the times: From must be before To (e.g. 1:23.4)."
        if end - start > 30 * 60:
            return "Please pick at most 30 minutes at a time."
        translate = task_label.startswith("Translate")
        if translate and model == "large-v3-turbo":
            model = "large-v3"  # turbo was not trained for translation
        lang = lang_label.split()[0]
        pad = 0.3
        params = {
            "start": start, "end": end, "clip_start": max(0.0, start - pad), "model": model, "language": lang,
            "task": "translate" if translate else "transcribe", "prompt": prompt.strip(),
            "speaker": "" if speaker_label.startswith("(") else speaker_label,
            "backend": self._doc_setting("backend_setting", self.var_whisper_backend.get()), "diarize_mode": "Disabled",
        }
        self._start_rerun("retranscribe", params, clip=(params["clip_start"], end + pad))
        what = "Translating" if translate else "Re-transcribing"
        self.lbl_status.config(text=f"{what} {short_ts(start)[:-2]}–{short_ts(end)[:-2]} with Whisper {model}…")
        self._append_log(f"{what} {short_ts(start)}–{short_ts(end)} with Whisper {model} (language {lang}).", "STAGE")
        return None

    def _apply_retranscription(self, doc: TranscriptDocument, result: Dict, p: Dict):
        off, start, end = float(p["clip_start"]), float(p["start"]), float(p["end"])
        new: List[Dict] = []
        for sg in result.get("segments") or []:
            words = []
            for w in sg.get("words") or []:
                w = dict(w, start=round(float(w.get("start", 0)) + off, 3), end=round(float(w.get("end", 0)) + off, 3))
                if start - 0.15 <= (w["start"] + w["end"]) / 2 <= end + 0.15:  # drop words from the padding
                    words.append(w)
            if not words:
                continue
            seg = {"start": words[0]["start"], "end": words[-1]["end"], "words": words, "text": "".join(str(w.get("word", "")) for w in words)}
            for k in ("avg_logprob", "no_speech_prob"):
                if k in sg:
                    seg[k] = sg[k]
            new.append(seg)
        new, _ = drop_empty_segments(new)
        if not new:
            messagebox.showinfo("Nothing recognised", "Whisper found no speech in that range, so the transcript was left unchanged.", parent=self)
            self.lbl_status.config(text="Re-transcription found no speech; nothing changed.")
            return
        if p.get("task") == "translate" and p.get("language") != "en":
            src = self.LANG_NAMES.get(p.get("language"), "another language") if p.get("language") != "auto" else "another language"
            note = f"[translated from {src}]"
            for seg in new:  # keep translations identifiable in every export
                seg["words"].insert(0, {"word": " " + note, "start": seg["start"], "end": seg["start"], "probability": 1.0, "edited": True})
                seg["text"] = "".join(str(w.get("word", "")) for w in seg["words"])
        n_old, n_new = doc.replace_range(start, end, new, p)
        if self._commit(f"Re-transcribed {short_ts(start)[:-2]}–{short_ts(end)[:-2]}: {n_old} segment(s) → {n_new}.") is None:
            return
        self.edit_original = ("", "")
        self._rerender()
        first = next((i for i, sg in enumerate(doc.segments) if sg is new[0]), None)
        if first is not None:
            self._load_segment_into_editor(first)  # always reload: the editor may still show the old text of this segment
            idx = self.span_lookup.get((first, 0))
            if idx is not None:
                self._select_span(idx, load_editor=False)
        self._append_log(f"Re-transcribed {short_ts(start)}–{short_ts(end)} with {p['model']} ({p['task']}): {n_old} → {n_new} segment(s). ⌘Z undoes it.", "SUCCESS")

    # --- re-run speaker detection
    def _rediarize_dialog(self):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        if self._rerun_busy():
            return
        self._autosave_edit()
        T = THEME
        win = tk.Toplevel(self)
        win.title("Re-run speaker detection")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        manual = sum(1 for sg in doc.segments if sg.get("speaker_manual"))
        self._label(win, "Detect speakers again for this transcript", font="body_bold", bg=T["bg_panel"]).grid(row=0, column=0, columnspan=2, sticky="w")
        self._label(win, "Your text stays as it is; only speaker labels change. New labels are matched to the names you already use "
                         "wherever they overlap most. ⌘Z undoes the whole re-run.", font="small", fg=T["fg_dim"], bg=T["bg_panel"],
                    wraplength=440, justify=tk.LEFT).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        v_method = tk.StringVar(value=self.var_diarize_mode.get() if not self.var_diarize_mode.get().startswith("Disabled")
                                else GPU_DIARIZATION)
        v_n = tk.IntVar(value=max(2, len(doc.roster())))
        v_keep = tk.BooleanVar(value=True)
        self._label(win, "Method", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(win, textvariable=v_method, values=[GPU_DIARIZATION, "Pyannote.audio 3.1 Neural (Requires HF Token)"],
                     state="readonly", width=40, font=self.f["small"]).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=3)
        self._label(win, "Number of speakers", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=3, column=0, sticky="w", pady=3)
        tk.Spinbox(win, from_=0, to=12, textvariable=v_n, width=4, bg=T["bg_input"], fg=T["fg_primary"], buttonbackground=T["bg_card"], relief=tk.FLAT,
                   highlightthickness=1, highlightbackground=T["border"], font=self.f["body"]).grid(row=3, column=1, sticky="w", padx=(10, 0), pady=3)
        self._label(win, "Counting everyone who speaks, including you (0 = guess).", font="small", fg=T["fg_dim"], bg=T["bg_panel"]).grid(row=4, column=1, sticky="w", padx=(10, 0))
        self._check(win, f"Keep speakers I assigned by hand ({manual} segment{'s' if manual != 1 else ''})", v_keep, bg=T["bg_panel"], font="small").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        btns = tk.Frame(win, bg=T["bg_panel"])
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def run():
            try:
                n = int(v_n.get())
            except Exception:
                n = 0
            win.destroy()
            self._launch_rediarization(v_method.get(), n, bool(v_keep.get()))

        self._tool(btns, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        self._tool(btns, "Detect speakers", run, primary=True).pack(side=tk.RIGHT)

    def _launch_rediarization(self, method: str, num_speakers: int, keep_manual: bool):
        doc = self.active_doc
        if doc is None:
            return
        if method.startswith("Pyannote") and not self.var_hf_token.get().strip():
            messagebox.showinfo("Token needed", "pyannote needs your Hugging Face token (Settings ▸ Speakers & Exports). "
                                                "Without it, the offline spectral method is used.", parent=self)
        segs = [{"start": float(sg.get("start", 0)), "end": float(sg.get("end", 0)), "text": sg.get("text", "")} for sg in doc.segments]
        params = {"method": method, "diarize_mode": method, "num_speakers": num_speakers, "keep_manual": keep_manual,
                  "n_segments": len(segs), "fingerprint": [(round(s["start"], 2), round(s["end"], 2)) for s in segs]}
        self._start_rerun("rediarize", params, clip=None, segments_json=segs)
        self.lbl_status.config(text=f"Detecting speakers ({'pyannote' if method.startswith('Pyannote') else 'spectral'}, "
                                    f"{num_speakers or 'auto'} speakers)…")
        self._append_log("Re-running speaker detection on the whole recording.", "STAGE")

    def _apply_rediarization(self, doc: TranscriptDocument, result: Dict, p: Dict):
        segs = result.get("segments") or []
        current = [(round(float(sg.get("start", 0)), 2), round(float(sg.get("end", 0)), 2)) for sg in doc.segments]
        if len(segs) != len(doc.segments) or current != [tuple(x) for x in p["fingerprint"]]:
            messagebox.showinfo("Transcript changed", "Segments were edited while speakers were being detected, so the result "
                                                      "was not applied. Please run it again.", parent=self)
            return
        labels = [sg.get("speaker") for sg in segs]
        if not any(labels):
            messagebox.showinfo("No speakers found", f"Speaker detection returned no labels ({result.get('diarization_method')}).", parent=self)
            return
        stats = doc.apply_speaker_labels(labels, p["keep_manual"], p)
        if self._commit(f"Speakers re-detected: {stats['changed']} segment(s) changed, {stats['kept']} kept.") is None:
            return
        self.edit_original = ("", "")
        self._rerender()
        if self.edit_seg_idx is not None:
            self._load_segment_into_editor(min(self.edit_seg_idx, len(doc.segments) - 1))
        self._append_log(f"Speaker detection ({result.get('diarization_method')}): {stats['changed']} segment(s) relabelled, "
                         f"{stats['kept']} hand-assigned kept, {stats['speakers']} speaker(s). Rename new ones under Speakers…; ⌘Z undoes it.", "SUCCESS")
