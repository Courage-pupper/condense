"""Transcript ▾ ▸ Find untranscribed stretches…: where the recording has voices but the transcript has nothing (what
Whisper skipped), with listening and re-transcribing them one after another. And the transcript's right-click menu,
which re-transcribes what is highlighted."""
import threading

from condense.core import align
from condense.core import gaps as gaplib
from condense.gui._common import *  # noqa: F401,F403


class GapsMixin:
    # ================================================================== right-click in the transcript
    def _selection_time_range(self) -> Optional[Tuple[float, float]]:
        """The time of what is highlighted: the selected words (from the first to the last), else the selected segments;
        None if nothing is (the right-click menu then takes the segment under the mouse)."""
        pt = self.preview_text
        try:
            a, b = pt.index("sel.first"), pt.index("sel.last")
        except Exception:
            a = b = None
        if a and b and a != b:
            (l0, c0), (l1, c1) = self._ix(a), self._ix(b)
            hit = []
            for line in range(l0, l1 + 1):
                for i in self.line_spans.get(line, []):
                    sp = self.word_spans[i]
                    if (line, sp["c1"]) > (l0, c0) and (line, sp["c0"]) < (l1, c1):
                        hit.append(sp)
            if hit:
                return min(sp["start"] for sp in hit), max(sp["end"] for sp in hit)
        doc = self.active_doc
        segs = [i for i in (self.__dict__.get("sel_segs") or []) if doc is not None and 0 <= i < len(doc.segments)]
        if segs:  # segments selected with ⇧↓ / drag
            return (min(float(doc.segments[i]["start"]) for i in segs), max(float(doc.segments[i]["end"]) for i in segs))
        return None

    def _transcript_menu(self, event):
        """Right-click in the transcript: re-transcribe or play what is highlighted (else the segment under the mouse)."""
        if self.active_doc is None:
            return None
        rng = self._selection_time_range()
        if rng is None:
            idx = self._span_at(event)
            if idx is not None:
                seg = self.active_doc.segments[self.word_spans[idx]["seg"]]
                rng = (float(seg["start"]), float(seg["end"]))
        m = PopupMenu(self)
        if rng is not None:
            a, b = rng
            when = f"{short_ts(a)[:-2]} – {short_ts(b)[:-2]}"
            m.add_command(label=f"Re-transcribe this passage…  ({when})", command=lambda: self._retranscribe_dialog(rng))
            m.add_command(label="Play this passage", command=lambda: self._player_start(a, duration=max(0.5, b - a)))
            m.add_separator()
        m.add_command(label="Find untranscribed stretches…", command=self._gaps_dialog)
        m.tk_popup(event.x_root, event.y_root)
        return m

    # ================================================================== find untranscribed stretches
    def _gaps_dialog(self, wait: bool = False):
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return None
        media = self._media()
        if media is None:
            messagebox.showinfo("Untranscribed stretches", "The recording of this transcript was not found.", parent=self)
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Untranscribed stretches — {doc.base_name}")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Stretches where the recording has voices as loud as the transcribed talk, but the transcript has "
                         "nothing — what Whisper skipped. Listen to one, then re-transcribe the ones with talk you need.",
                    font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=620, justify=tk.LEFT).pack(anchor="w")
        cols = ("from", "to", "length", "sound", "status")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=10, style="Queue.Treeview", selectmode="extended")
        for c, w, title in (("from", 85, "From"), ("to", 85, "To"), ("length", 70, "Length"), ("sound", 120, "Voices"), ("status", 220, "")):
            tree.heading(c, text=title)
            tree.column(c, width=w, anchor="w" if c == "status" else "center")
        tree.pack(fill=tk.BOTH, expand=True, pady=8)
        lbl = self._label(win, "Reading the recording's sound (once; a few seconds per hour)…", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        lbl.pack(anchor="w")
        st: Dict = {"gaps": [], "status": {}, "stop": threading.Event(), "doc": str(doc.path)}

        def show():
            for item in tree.get_children():
                tree.delete(item)
            for i, g in enumerate(st["gaps"]):
                tree.insert("", tk.END, iid=str(i), values=(short_ts(g["start"])[:-2], short_ts(g["end"])[:-2],
                                                           short_ts(g["end"] - g["start"])[:-2],
                                                           f"{g['sound_s']:.0f} s ({g['share'] * 100:.0f} %)", st["status"].get(i, "")))
            total = sum(g["end"] - g["start"] for g in st["gaps"])
            lbl.config(text=(f"{len(st['gaps'])} stretch(es), {short_ts(total)[:-2]} in all." if st["gaps"]
                             else "Nothing found: every stretch with talk-loud sound has a transcript."))

        def chosen() -> List[int]:
            return [int(i) for i in tree.selection()] or list(range(len(st["gaps"])))

        def listen():
            sel = [int(i) for i in tree.selection()][:1]
            if sel:
                g = st["gaps"][sel[0]]
                self._player_start(g["start"], duration=min(15.0, g["end"] - g["start"]))

        def goto():
            sel = [int(i) for i in tree.selection()][:1]
            if sel:
                self._timeline_jump(st["gaps"][sel[0]]["start"])

        def retranscribe(indices: Optional[List[int]] = None):
            idx = indices if indices is not None else chosen()
            if not idx:
                return None
            if self._rerun_busy():
                return None
            queue_ = []
            for i in idx:
                g = st["gaps"][i]
                for a, b in gaplib.parts(g["start"], g["end"]):
                    queue_.append((i, a, b))
                st["status"][i] = "waiting…"
            show()
            self._gap_queue = {"items": queue_, "state": st, "show": show, "found": 0}
            return self._next_gap()

        def work():
            try:
                cache = APP_SUPPORT_DIR / "cache" / "audio"
                session = align.SessionAudio(Path(media), cache_dir=cache, stop=st["stop"], duration=self._duration())
                cur = TranscriptDocument.load(Path(st["doc"])) if Path(st["doc"]).exists() else doc
                st["gaps"] = gaplib.find_untranscribed(session.env, cur.segments)
                st["done"] = True
            except InterruptedError:
                st["done"] = True
            except Exception as exc:
                st["error"] = str(exc)
                st["done"] = True

        def poll():
            if not st.get("done"):
                win.after(200, poll)
                return
            if st.get("error"):
                lbl.config(text=f"⚠ {st['error']}", fg=T["warn"])
            else:
                show()

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(8, 0))
        self._tool(bar, "▶ Listen", listen).pack(side=tk.LEFT)
        self._tool(bar, "Go to", goto).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Re-transcribe selected (or all)", lambda: retranscribe(), primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Close", lambda: (st["stop"].set(), win.destroy())).pack(side=tk.RIGHT, padx=(0, 6))
        tree.bind("<Double-Button-1>", lambda e: listen())
        api = {"state": st, "retranscribe": retranscribe, "show": show, "tree": tree}
        if wait:
            work()
            poll()
            return win, api
        threading.Thread(target=work, daemon=True).start()
        win.after(200, poll)
        return win, api

    def _next_gap(self):
        """Start re-transcribing the next queued stretch (called again when one has finished)."""
        q = self.__dict__.get("_gap_queue")
        if not q:
            return None
        st, show = q["state"], q["show"]
        if not q["items"]:
            self._gap_queue = None
            self.lbl_status.config(text=f"Untranscribed stretches: done — {q['found']} new segment(s). ⌘Z undoes each one."
                                   if IS_MAC else f"Untranscribed stretches: done — {q['found']} new segment(s). Ctrl+Z undoes each one.")
            return None
        doc = self.active_doc
        if doc is None or str(doc.path) != st["doc"]:
            for i, _, _ in q["items"]:
                st["status"][i] = "stopped: another transcript was opened"
            self._gap_queue = None
            self.lbl_status.config(text="Re-transcribing stopped: another transcript was opened.")
            try:
                show()
            except Exception:
                pass
            return None
        i, a, b = q["items"].pop(0)
        q["current"] = i
        st["status"][i] = "re-transcribing…"
        show()
        model = self._doc_setting("model", self.var_whisper_model.get())
        lang = self._doc_setting("language_setting", "auto")
        if lang == "auto" and doc is not None and doc.result.get("language"):
            lang = str(doc.result.get("language"))
        prompt = self._doc_setting("vocabulary_prompt", "") or self.var_whisper_prompt.get()
        err = self._launch_retranscription(short_ts(a), short_ts(b), model, lang, "Transcribe", "(keep)", prompt, quiet=True)
        if err:
            st["status"][i] = f"⚠ {err}"
            show()
            return self._next_gap()
        return None

    def _gap_finished(self, n_new: Optional[int]):
        """A re-transcription ended (n_new segments, or None if it failed): note it and go on with the next."""
        q = self.__dict__.get("_gap_queue")
        if not q or "current" not in q:
            return
        i = q.pop("current")
        st = q["state"]
        counts = st.setdefault("counts", {})
        if n_new is None:
            st["status"][i] = "⚠ failed (see Console)"
        else:
            q["found"] += n_new
            counts[i] = counts.get(i, 0) + n_new
            more = any(j == i for j, _, _ in q["items"])  # a long stretch: more parts of it follow
            st["status"][i] = "re-transcribing…" if more else (f"✓ {counts[i]} segment(s)" if counts[i] else "nothing recognised")
        try:
            q["show"]()
        except Exception:
            pass  # the dialog was closed: keep going
        self._next_gap()
