"""Find & replace in the open transcript (⌘F / Ctrl+F), and search or replace across all transcripts in a folder."""
from condense.gui._common import *  # noqa: F401,F403


def segment_text(seg: Dict) -> str:
    """What the editor shows for a segment: its timed words (or its text)."""
    words = seg.get("words") or []
    return ("".join(str(w.get("word", "")) for w in words) if words else (seg.get("text") or "")).strip()


def build_regex(query: str, match_case: bool, whole_words: bool) -> Optional["re.Pattern"]:
    query = query.strip()
    if not query:
        return None
    pat = re.escape(query)
    if whole_words:
        pat = rf"(?<!\w){pat}(?!\w)"
    return re.compile(pat, 0 if match_case else re.IGNORECASE)


def find_in_document(doc, rx) -> List[Tuple[int, int]]:
    """[(segment index, number of matches)]."""
    out = []
    for i, seg in enumerate(doc.segments):
        n = len(rx.findall(segment_text(seg)))
        if n:
            out.append((i, n))
    return out


def replace_in_document(doc, rx, replacement: str, indices=None) -> int:
    """Replace in the given segments (default: all). Returns the number of replacements."""
    total = 0
    for i, seg in enumerate(doc.segments):
        if indices is not None and i not in indices:
            continue
        text = segment_text(seg)
        new, n = rx.subn(lambda m: replacement, text)
        if n and doc.edit_segment_text(i, new):
            total += n
    return total


class FindMixin:
    def _build_find_bar(self, parent, before):
        T = THEME
        bar = tk.Frame(parent, bg=T["bg_card"], padx=8, pady=6)
        self.find_bar, self._find_before = bar, before
        self.var_find = tk.StringVar(value="")
        self.var_replace = tk.StringVar(value="")
        self.var_find_case = tk.BooleanVar(value=False)
        self.var_find_words = tk.BooleanVar(value=False)
        row = self._flow(bar, padx=6, pady=3, bg=T["bg_card"])
        row.pack(fill=tk.X)
        row.add(self._label(row, "Find", font="small", fg=T["fg_secondary"]))
        self.ent_find = self._entry(row, self.var_find, font="small", width=24)
        row.add(self.ent_find)
        row.add(self._tool(row, "◀", lambda: self._find_step(-1)))
        row.add(self._tool(row, "▶", lambda: self._find_step(+1)))
        self.lbl_find_count = self._label(row, "", font="small", fg=T["fg_dim"])
        row.add(self.lbl_find_count)
        row.add(self._label(row, "Replace with", font="small", fg=T["fg_secondary"]))
        self.ent_replace = self._entry(row, self.var_replace, font="small", width=20)
        row.add(self.ent_replace)
        row.add(self._tool(row, "Replace", self._replace_current))
        row.add(self._tool(row, "Replace all", self._replace_all))
        row.add(self._check(row, "Match case", self.var_find_case, font="small"))
        row.add(self._check(row, "Whole words", self.var_find_words, font="small"))
        row.add(self._tool(row, "All transcripts…", self._search_all_dialog))
        row.add(self._tool(row, "✕", self._hide_find))
        for v in (self.var_find, self.var_find_case, self.var_find_words):
            v.trace_add("write", lambda *a: self._paint_found())
        for w in (self.ent_find, self.ent_replace):
            w.bind("<Return>", lambda e: (self._find_step(+1), "break")[-1])
            w.bind("<Shift-Return>", lambda e: (self._find_step(-1), "break")[-1])
            w.bind("<Escape>", lambda e: (self._hide_find(), "break")[-1])
        self._find_visible = False

    def _toggle_find(self):
        if self.__dict__.get("_find_visible"):
            self.ent_find.focus_set()
            self.ent_find.select_range(0, tk.END)
            return
        self.notebook.select(self.tab_preview)
        self.find_bar.pack(fill=tk.X, pady=(0, 4), before=self._find_before)
        self._find_visible = True
        self.ent_find.focus_set()
        self._paint_found()

    def _hide_find(self):
        self.find_bar.pack_forget()
        self._find_visible = False
        self.preview_text.tag_remove("FOUND", "1.0", tk.END)
        self.preview_text.focus_set()

    def _find_rx(self):
        return build_regex(self.var_find.get(), bool(self.var_find_case.get()), bool(self.var_find_words.get()))

    def _find_matches(self) -> List[Tuple[int, int]]:
        doc, rx = self.active_doc, self._find_rx()
        if doc is None or rx is None:
            return []
        shown = set(self.__dict__.get("seg_ranges") or {})
        return [(i, n) for i, n in find_in_document(doc, rx) if i in shown]

    def _paint_found(self):
        pt = self.preview_text
        pt.tag_remove("FOUND", "1.0", tk.END)
        if not self.__dict__.get("_find_visible"):
            return
        matches = self._find_matches()
        for i, _ in matches:
            pt.tag_add("FOUND", *self.seg_ranges[i])
        total = sum(n for _, n in matches)
        self.lbl_find_count.config(text=(f"{total} in {len(matches)} segment(s)" if matches else
                                         ("no match" if self.var_find.get().strip() else "")))

    def _find_step(self, direction: int):
        matches = [i for i, _ in self._find_matches()]
        if not matches:
            self._paint_found()
            return
        cur = self.edit_seg_idx
        if cur is None:
            target = matches[0] if direction > 0 else matches[-1]
        else:
            later = [i for i in matches if (i > cur if direction > 0 else i < cur)]
            target = (later[0] if direction > 0 else later[-1]) if later else (matches[0] if direction > 0 else matches[-1])
        self._autosave_edit(rerender=False)
        self._goto_segment(target)
        self._paint_found()

    def _replace_current(self):
        doc, rx, idx = self.active_doc, self._find_rx(), self.edit_seg_idx
        if doc is None or rx is None:
            return
        if idx is None or not rx.search(segment_text(doc.segments[idx])):
            self._find_step(+1)
            return
        self._autosave_edit(rerender=False)
        n = replace_in_document(doc, rx, self.var_replace.get(), {idx})
        if n and self._commit(f"Replaced {n} occurrence(s) in segment {idx + 1}.") is not None:
            self._rerender()
            self._load_segment_into_editor(idx)
            self._find_step(+1)

    def _replace_all(self):
        doc, rx = self.active_doc, self._find_rx()
        if doc is None or rx is None:
            return
        self._autosave_edit(rerender=False)
        matches = find_in_document(doc, rx)
        if not matches:
            self._paint_found()
            return
        total = sum(n for _, n in matches)
        if not messagebox.askyesno("Replace all", f"Replace {total} occurrence(s) of “{self.var_find.get().strip()}” in "
                                                  f"{len(matches)} segment(s) with “{self.var_replace.get()}”?\n\nOne undo step reverts it.",
                                   parent=self):
            return
        n = replace_in_document(doc, rx, self.var_replace.get())
        if n and self._commit(f"Replaced {n} occurrence(s) in {len(matches)} segment(s).") is not None:
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(self.edit_seg_idx)

    # ----- across transcripts -----
    @staticmethod
    def _transcripts_under(folder: Path) -> List[Path]:
        from condense.core.project import transcripts_under
        return transcripts_under(folder)  # skips hidden folders, backups and links; bounded on huge drives

    def _search_all(self, folder: Path, rx) -> List[Dict]:
        results = []
        for path in self._transcripts_under(folder):
            try:
                doc = TranscriptDocument.load(path)
            except Exception:
                continue
            for i, n in find_in_document(doc, rx):
                seg = doc.segments[i]
                results.append({"path": path, "seg": i, "count": n, "start": float(seg.get("start", 0.0)),
                                "speaker": seg.get("speaker") or "", "text": segment_text(seg)})
        return results

    def _replace_all_transcripts(self, folder: Path, rx, replacement: str) -> Dict[str, int]:
        """Replace in every transcript under folder; each file gets a backup, an edit-log entry and new exports.
        The open transcript gets an undo step."""
        changed: Dict[str, int] = {}
        active = self.active_doc
        for path in self._transcripts_under(folder):
            try:
                doc = TranscriptDocument.load(path)
                if not find_in_document(doc, rx):
                    continue
                previous = path.read_text(encoding="utf-8")
                n = replace_in_document(doc, rx, replacement)
                if not n:
                    continue
                doc.log_edit("replace_in_all_transcripts", find=rx.pattern, replace=replacement, count=n)
                doc.save(backup=True)
                doc.export_all()
                changed[str(path)] = n
                if active is not None and Path(active.path).resolve() == path.resolve():
                    self._push_undo(active.path, f"Replaced {n} occurrence(s) in all transcripts", previous)
            except Exception as exc:
                self._append_log(f"Could not change {path.name}: {exc}", "ERROR")
        if active is not None and any(Path(p).resolve() == Path(active.path).resolve() for p in changed):
            self._render_doc(TranscriptDocument.load(Path(active.path)))
        return changed

    def _search_all_dialog(self):
        T = THEME
        doc = self.active_doc
        project = self._project_dir()
        start = (str(project) if project else None) or self.settings.get("search_dir") or (
            str(doc.out_dir.parent) if doc is not None else str(Path.home()))
        win = tk.Toplevel(self)
        win.title("Search all transcripts")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        v_dir = tk.StringVar(value=start)
        v_q = tk.StringVar(value=self.var_find.get())
        v_r = tk.StringVar(value=self.var_replace.get())
        top = tk.Frame(win, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self._label(top, "Folder", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=0, column=0, sticky="w")
        self._entry(top, v_dir, font="small", width=56).grid(row=0, column=1, sticky="ew", padx=6)
        self._tool(top, "Choose…", lambda: v_dir.set(self._ask_directory("Search transcripts in", v_dir.get()) or v_dir.get())
                   ).grid(row=0, column=2)
        self._label(top, "Find", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ent = self._entry(top, v_q, font="small", width=56)
        ent.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        self._label(top, "Replace with", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._entry(top, v_r, font="small", width=56).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        lst = tk.Listbox(win, width=110, height=16, bg=T["bg_input"], fg=T["fg_primary"], font=self.f["mono_small"], relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=T["border"], exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True, pady=(10, 6))
        lbl = self._label(win, "", font="small", fg=T["fg_dim"], bg=T["bg_panel"])
        lbl.pack(anchor="w")
        state = {"results": []}

        def rx():
            return build_regex(v_q.get(), bool(self.var_find_case.get()), bool(self.var_find_words.get()))

        def search():
            r = rx()
            folder = Path(v_dir.get()).expanduser()
            if r is None or not folder.is_dir():
                lbl.config(text="Enter a word or phrase and an existing folder.")
                return
            self.settings["search_dir"] = str(folder)
            state["results"] = self._search_all(folder, r)
            lst.delete(0, tk.END)
            for res in state["results"]:
                lst.insert(tk.END, f"{res['path'].name[:-len(TranscriptDocument.SUFFIX)][:28]:28}  {short_ts(res['start'])[:-2]:>8}  "
                                   f"{res['speaker'][:12]:12}  {res['text'][:90]}")
            files = len({str(r_['path']) for r_ in state["results"]})
            total = sum(r_["count"] for r_ in state["results"])
            lbl.config(text=f"{total} match(es) in {len(state['results'])} segment(s) of {files} transcript(s). Double-click to open.")
            return state["results"]

        def open_result(event=None):
            sel = lst.curselection()
            if not sel:
                return
            res = state["results"][sel[0]]
            self._open_at(res["path"], res["seg"])

        def replace_everywhere():
            r = rx()
            res = search() or []
            if r is None or not res:
                return
            files = len({str(x["path"]) for x in res})
            total = sum(x["count"] for x in res)
            if not messagebox.askyesno("Replace in all transcripts",
                                       f"Replace {total} occurrence(s) of “{v_q.get().strip()}” with “{v_r.get()}” in {files} transcript(s)?\n\n"
                                       "Each transcript gets a backup and new exports, and the change is logged in its edit history.",
                                       parent=win):
                return
            self._autosave_edit(rerender=False)
            changed = self._replace_all_transcripts(Path(v_dir.get()).expanduser(), r, v_r.get())
            n = sum(changed.values())
            self._append_log(f"Replaced {n} occurrence(s) of “{v_q.get().strip()}” in {len(changed)} transcript(s).", "SUCCESS")
            search()
            lbl.config(text=f"Replaced {n} occurrence(s) in {len(changed)} transcript(s).")

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(6, 0))
        self._tool(bar, "Search", search, primary=True).pack(side=tk.LEFT)
        self._tool(bar, "Open", open_result).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Replace in all…", replace_everywhere).pack(side=tk.LEFT, padx=(6, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        lst.bind("<Double-Button-1>", open_result)
        ent.bind("<Return>", lambda e: search())
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        state.update(search=search, open=open_result, replace=replace_everywhere, lst=lst, v_dir=v_dir, v_q=v_q, v_r=v_r, lbl=lbl)
        return win, state
