"""The Coding tab: the project's codebook on the left, the open transcript on the right with a coloured stripe for
every coding, and the transcript's codings below.

Code words: select them in the text, then double-click a code (or press Return to use the selected code, or 1–9 for
the recent codes, or N for a new code). Code a moment without speech: drag on the timeline, then choose a code and
describe what happens. Codings are saved in the transcript (with backups and undo); the codebook in the study folder.
"""
import bisect

from condense.core import coding as cod
from condense.core import episodes as eplib
from condense.core import project as proj
from condense.gui import textrender
from condense.gui._common import *  # noqa: F401,F403

STRIPE_W, STRIPE_GAP, MAX_COLS = 5, 3, 8


class CodingMixin:
    # ================================================================== building
    def _build_coding_tab(self):
        T = THEME
        tab = self.tab_coding
        self._cd = {"doc": None, "words": [], "line_words": {}, "resolved": [], "sel": None, "tl_drag": None,
                    "recent": [], "rendered_for": None}
        self.cd_none = tk.Frame(tab, bg=T["bg_panel"])
        self.cd_none_lbl = self._wrapping_label(self.cd_none, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        self.cd_none_lbl.pack(fill=tk.X, pady=(0, 8))
        row = tk.Frame(self.cd_none, bg=T["bg_panel"])
        row.pack(fill=tk.X)
        self._tool(row, "New Project…", self._new_project_dialog, primary=True).pack(side=tk.LEFT)
        self._tool(row, "Open Project…", self._open_project_dialog).pack(side=tk.LEFT, padx=(6, 0))

        self.cd_main = tk.PanedWindow(tab, orient=tk.HORIZONTAL, sashwidth=6, bd=0, bg=T["bg_panel"], showhandle=False)
        # ---- codebook ----
        left = tk.Frame(self.cd_main, bg=T["bg_panel"], padx=4)
        self.cd_main.add(left, minsize=220, width=280)
        hdr = tk.Frame(left, bg=T["bg_panel"])
        hdr.pack(fill=tk.X)
        self._label(hdr, "Codebook", font="body_bold", bg=T["bg_panel"]).pack(side=tk.LEFT)
        self.var_cd_search = tk.StringVar(value="")
        ent = self._entry(left, self.var_cd_search, font="small", width=20)
        ent.pack(fill=tk.X, pady=(6, 4))
        self.var_cd_search.trace_add("write", lambda *a: self._cd_fill_codebook())
        bar = self._flow(left, padx=3, pady=3, bg=T["bg_panel"])
        bar.pack(fill=tk.X)
        bar.add(self._tool(bar, "+ Code", lambda: self._cd_new_code(parent=None)))
        bar.add(self._tool(bar, "+ Subcode", lambda: self._cd_new_code(parent=self._cd_selected_code())))
        bar.add(self._tool(bar, "Edit…", lambda: self._cd_edit_code(self._cd_selected_code())))
        bar.add(self._dropdown(bar, "More ▾", [
            ("Merge into another code…", lambda: self._cd_merge_dialog(self._cd_selected_code())),
            ("Delete code…", lambda: self._cd_delete_code(self._cd_selected_code())),
            ("-",),
            ("Refresh counts", lambda: self._cd_fill_codebook(recount=True)),
        ]))
        tf = tk.Frame(left, bg=T["bg_panel"])
        tf.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        tree = ttk.Treeview(tf, columns=("n",), show="tree", style="Queue.Treeview", selectmode="browse")
        tree.column("#0", width=200, stretch=True)
        tree.column("n", width=44, anchor="e", stretch=False)
        sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind("<Double-Button-1>", lambda e: self._cd_apply(self._cd_selected_code()))
        tree.bind("<<TreeviewSelect>>", lambda e: (self._cd_show_code_info(), self._cd_fill_list()))
        for seq in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if IS_MAC else ()):
            tree.bind(seq, self._cd_code_menu)
        self.cd_tree = tree
        self.lbl_cd_info = self._wrapping_label(left, "", font="small", fg=T["fg_secondary"], bg=T["bg_panel"])
        self.lbl_cd_info.pack(fill=tk.X, pady=(6, 0))

        # ---- transcript with stripes ----
        right = tk.Frame(self.cd_main, bg=T["bg_panel"], padx=4)
        self.cd_main.add(right, minsize=420, stretch="always")
        top = self._flow(right, padx=3, pady=3, bg=T["bg_panel"])
        top.pack(fill=tk.X)
        self.lbl_cd_doc = self._label(top, "", font="small_bold", bg=T["bg_panel"])
        top.add(self.lbl_cd_doc)
        top.add(self._tool(top, "Code selection (↩)", lambda: self._cd_apply(self._cd_selected_code())))
        top.add(self._tool(top, "New code from selection (N)", lambda: self._cd_new_code(parent=self._cd_selected_code(), apply=True)))
        top.add(self._tool(top, "Moment without speech…", lambda: self._cd_time_dialog()))
        top.add(self._tool(top, "▶ Play selection", self._cd_play_selection))
        self.cd_recent = self._flow(right, padx=3, pady=2, bg=T["bg_panel"])
        self.cd_recent.pack(fill=tk.X)
        self.cd_timeline = tk.Canvas(right, height=26, bg=T["bg_input"], highlightthickness=0, bd=0, cursor="crosshair")
        self.cd_timeline.pack(fill=tk.X, pady=(4, 4))
        self.cd_timeline.bind("<Configure>", lambda e: self._cd_draw_timeline())
        self.cd_timeline.bind("<Button-1>", self._cd_tl_press)
        self.cd_timeline.bind("<B1-Motion>", self._cd_tl_drag)
        self.cd_timeline.bind("<ButtonRelease-1>", self._cd_tl_release)
        body = tk.PanedWindow(right, orient=tk.VERTICAL, sashwidth=6, bd=0, bg=T["bg_panel"], showhandle=False)
        body.pack(fill=tk.BOTH, expand=True)
        txt_frame = tk.Frame(body, bg="#0b0d13")
        body.add(txt_frame, minsize=160, stretch="always")
        self.cd_stripes = tk.Canvas(txt_frame, width=STRIPE_W * 3, bg="#0b0d13", highlightthickness=0, bd=0)
        self.cd_stripes.pack(side=tk.LEFT, fill=tk.Y)
        self.cd_text = tk.Text(txt_frame, bg="#0b0d13", fg=T["fg_primary"], font=self.f["transcript"], relief=tk.FLAT, padx=12, pady=10,
                               wrap=tk.WORD, highlightthickness=0, cursor="xterm", spacing1=2, spacing3=2, exportselection=False,
                               selectbackground="#3b4a63", inactiveselectbackground="#3b4a63")
        csb = ttk.Scrollbar(txt_frame, orient=tk.VERTICAL, command=self.cd_text.yview)
        self.cd_text.configure(yscrollcommand=lambda a, b: (csb.set(a, b), self._cd_draw_stripes()))
        csb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cd_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        t = self.cd_text
        t.tag_config("SPK", font=self.f["mono_small_bold"], spacing1=10)
        t.tag_config("TS", foreground=T["fg_dim"], font=self.f["mono_small"])
        t.tag_config("EP", font=self.f["small_bold"], spacing1=8)
        t.tag_config("CODED", underline=True)
        t.tag_config("PLAYING", background="#1d3a2e")
        t.tag_config("KSEL", background="#47395c")
        t.tag_config("MOMENT", font=self.f["small_bold"], spacing1=6, spacing3=4)
        t.tag_raise("sel")
        # read-only, but selectable: block typing, keep selection keys
        t.bind("<Key>", self._cd_key)
        t.bind("<Double-Button-1>", self._cd_play_word)
        t.bind("<ButtonRelease-1>", lambda e: self._cd_click(e))
        for seq in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if IS_MAC else ()):
            t.bind(seq, self._cd_text_menu)
        self.cd_stripes.bind("<Button-1>", self._cd_stripe_click)
        self.cd_text.bind("<Configure>", lambda e: self._cd_draw_stripes())

        # ---- codings of this transcript ----
        lst = tk.Frame(body, bg=T["bg_panel"])
        body.add(lst, minsize=110, height=190)
        lbar = tk.Frame(lst, bg=T["bg_panel"])
        lbar.pack(fill=tk.X, pady=(4, 2))
        self.lbl_cd_list = self._label(lbar, "Codings", font="small_bold", bg=T["bg_panel"])
        self.lbl_cd_list.pack(side=tk.LEFT)
        self.var_cd_only = tk.BooleanVar(value=False)
        self._check(lbar, "only the selected code", self.var_cd_only, bg=T["bg_panel"], font="small").pack(side=tk.LEFT, padx=(10, 0))
        self.var_cd_only.trace_add("write", lambda *a: self._cd_fill_list())
        self._tool(lbar, "Delete coding", lambda: self._cd_remove(self._cd_list_selected())).pack(side=tk.RIGHT)
        self._tool(lbar, "Change code…", lambda: self._cd_change_code(self._cd_list_selected())).pack(side=tk.RIGHT, padx=(0, 4))
        self._tool(lbar, "Memo…", lambda: self._cd_memo(self._cd_list_selected())).pack(side=tk.RIGHT, padx=(0, 4))
        cl = ttk.Treeview(lst, columns=("time", "code", "who", "text"), show="headings", style="Queue.Treeview", height=6, selectmode="browse")
        for c, w, title in (("time", 80, "Time"), ("code", 160, "Code"), ("who", 110, "Speaker"), ("text", 420, "Coded text / memo")):
            cl.heading(c, text=title)
            cl.column(c, width=w, anchor="w", stretch=c == "text")
        cl.pack(fill=tk.BOTH, expand=True)
        cl.bind("<<TreeviewSelect>>", lambda e: self._cd_select_coding(self._cd_list_selected(), from_list=True))
        cl.bind("<Double-Button-1>", lambda e: self._cd_play_coding(self._cd_list_selected()))
        cl.bind("<Delete>", lambda e: self._cd_remove(self._cd_list_selected()))
        self.cd_list = cl
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._cd_on_tab(), add="+")
        self._cd_show_state()

    # ================================================================== state
    def _cd_project(self) -> Optional[Path]:
        doc = self.active_doc
        if doc is not None:
            f = proj.find_project_file(Path(doc.path).parent)
            if f is not None:
                return f.parent
        return self._project_dir()

    def _cd_codebook(self) -> Optional["cod.Codebook"]:
        project = self._cd_project()
        if project is None:
            return None
        cb = self.__dict__.get("_cd_cb")
        if cb is None or cb.path.parent != project:
            cb = self._cd_cb = cod.Codebook.for_project(project)
            self._cd_counts = None
        return cb

    def _cd_show_state(self):
        project = self._cd_project()
        if project is None:
            self.cd_main.pack_forget()
            self.cd_none_lbl.config(text="Coding works in a project: the codebook is kept in the study's folder and shared by all its "
                                         "transcripts. Open or create the project of this study first.")
            self.cd_none.pack(fill=tk.X, padx=10, pady=10)
            return False
        self.cd_none.pack_forget()
        self.cd_main.pack(fill=tk.BOTH, expand=True)
        return True

    def _cd_visible(self) -> bool:
        try:
            return self.notebook.select() == str(self.tab_coding)
        except Exception:
            return False

    def _cd_on_tab(self):
        if self._cd_visible():
            self._cd_refresh()

    def _cd_refresh(self, force: bool = False):
        """(Re)draw everything for the open transcript. Called when the tab is shown and after changes."""
        if not self._cd_show_state():
            return
        self._cd_fill_codebook(recount=self.__dict__.get("_cd_counts") is None)
        doc = self.active_doc
        if doc is None:
            self.lbl_cd_doc.config(text="Open a transcript (Project tab, or Transcript ▸ Open…) to code it.")
            self.cd_text.delete("1.0", tk.END)
            self._cd["shown_lines"] = None
            return
        self._cd_render(doc)

    # ================================================================== codebook panel
    def _cd_selected_code(self) -> Optional[str]:
        sel = self.cd_tree.selection()
        return sel[0] if sel else None

    def _cd_fill_codebook(self, recount: bool = False):
        cb = self._cd_codebook()
        tree = self.cd_tree
        keep = self._cd_selected_code()
        for iid in tree.get_children():
            tree.delete(iid)
        if cb is None:
            return
        if recount or self.__dict__.get("_cd_counts") is None:
            self._cd_counts = cod.counts(cb.path.parent)
        counts = self._cd_counts
        q = self.var_cd_search.get().strip().lower()
        shown = set()
        if q:  # a match and the codes above it
            for c in cb.codes:
                if q in c["name"].lower():
                    cur = c
                    while cur is not None:
                        shown.add(cur["id"])
                        cur = cb.get(cur["parent"]) if cur.get("parent") else None
        for depth, c in cb.ordered():
            if q and c["id"] not in shown:
                continue
            tag = f"col{c['color']}"
            tree.tag_configure(tag, foreground=c["color"])
            parent = c.get("parent") or ""
            if parent and not tree.exists(parent):
                parent = ""
            n = counts.get(c["id"], 0)
            tree.insert(parent, tk.END, iid=c["id"], text=f"■ {c['name']}", values=(n or "",), open=True, tags=(tag,))
        if keep and tree.exists(keep):
            tree.selection_set(keep)
        if not cb.codes:
            self.lbl_cd_info.config(text="No codes yet. Select some words and press N (or + Code) to start the codebook.")

    def _cd_show_code_info(self):
        cb, cid = self._cd_codebook(), self._cd_selected_code()
        c = cb.get(cid) if cb and cid else None
        if c is None:
            self.lbl_cd_info.config(text="")
            return
        parts = [cb.path_name(cid)]
        for k, label in (("definition", "Definition"), ("use_when", "Use when"), ("not_when", "Not when"), ("example", "Example"), ("memo", "Memo")):
            if c.get(k):
                parts.append(f"{label}: {c[k]}")
        self.lbl_cd_info.config(text="\n".join(parts))

    def _cd_code_menu(self, event):
        iid = self.cd_tree.identify_row(event.y)
        if iid:
            self.cd_tree.selection_set(iid)
        m = tk.Menu(self, tearoff=0)
        if iid:
            m.add_command(label="Code the selected text with it", command=lambda: self._cd_apply(iid))
            m.add_command(label="New subcode…", command=lambda: self._cd_new_code(parent=iid))
            m.add_command(label="Edit…", command=lambda: self._cd_edit_code(iid))
            m.add_command(label="Merge into another code…", command=lambda: self._cd_merge_dialog(iid))
            m.add_command(label="Delete…", command=lambda: self._cd_delete_code(iid))
            m.add_separator()
        m.add_command(label="New code…", command=lambda: self._cd_new_code(parent=None))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return m

    def _cd_code_choices(self) -> List[Tuple[str, str]]:
        cb = self._cd_codebook()
        return [(c["id"], ("   " * d) + c["name"]) for d, c in cb.ordered()] if cb else []

    def _cd_new_code(self, parent: Optional[str] = None, apply: bool = False, name: Optional[str] = None):
        cb = self._cd_codebook()
        if cb is None:
            return None
        if apply and not self._cd_selection_words():
            self.lbl_cd_doc.config(text="Select the words to code first.")
            return None
        where = f" under “{cb.get(parent)['name']}”" if parent and cb.get(parent) else ""
        if name is None:
            name = simpledialog.askstring("New code", f"Name of the new code{where}:", parent=self)
        if not name:
            return None
        try:
            cid = cb.add(name, parent=parent)
        except ValueError as exc:
            messagebox.showerror("New code", str(exc), parent=self)
            return None
        cb.save()
        self._cd_fill_codebook()
        self.cd_tree.selection_set(cid)
        if apply:
            self._cd_apply(cid)
        return cid

    def _cd_edit_code(self, cid: Optional[str]):
        cb = self._cd_codebook()
        c = cb.get(cid) if cb and cid else None
        if c is None:
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Code — {c['name']}")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        v_name = tk.StringVar(value=c["name"])
        others = [(i, n) for i, n in self._cd_code_choices() if i != cid and i not in cb.descendants(cid)]
        TOP = "(top level)"
        v_parent = tk.StringVar(value=next((n.strip() for i, n in others if i == c.get("parent")), TOP))
        v_color = tk.StringVar(value=c["color"])
        r = 0
        for label, widget in (("Name", self._entry(win, v_name, font="small", width=44)),
                              ("Under", ttk.Combobox(win, textvariable=v_parent, values=[TOP] + [n.strip() for _, n in others], state="readonly",
                                                     width=42, font=self.f["small"]))):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
            widget.grid(row=r, column=1, sticky="we", pady=3)
            r += 1
        crow = tk.Frame(win, bg=T["bg_panel"])
        self._label(win, "Colour", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=3)
        crow.grid(row=r, column=1, sticky="w", pady=3)
        swatches = []
        for col in cod.COLORS:
            sw = tk.Label(crow, text="  ", bg=col, cursor="hand2", relief=tk.FLAT, highlightthickness=2,
                          highlightbackground=T["fg_primary"] if col == v_color.get() else T["bg_panel"])
            sw.pack(side=tk.LEFT, padx=1)
            sw.bind("<Button-1>", lambda e, col=col: (v_color.set(col), [s.config(highlightbackground=T["fg_primary"] if s.cget("bg") == col else T["bg_panel"]) for s in swatches]))
            swatches.append(sw)
        r += 1
        texts = {}
        for key, label in (("definition", "Definition"), ("use_when", "Use when"), ("not_when", "Not when"), ("example", "Example"), ("memo", "Memo")):
            self._label(win, label, font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=r, column=0, sticky="nw", padx=(0, 8), pady=3)
            box = tk.Text(win, height=4 if key == "memo" else 2, width=52, wrap=tk.WORD, font=self.f["small"], bg=T["bg_input"],
                          fg=T["fg_primary"], insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1,
                          highlightbackground=T["border"])
            box.insert("1.0", c.get(key, ""))
            box.grid(row=r, column=1, sticky="we", pady=3)
            texts[key] = box
            r += 1
        lbl_err = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl_err.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1

        def save():
            parent = next((i for i, n in others if n.strip() == v_parent.get()), None)
            fields = {k: " ".join(t.get("1.0", "end-1c").split()) if k != "memo" else t.get("1.0", "end-1c").strip() for k, t in texts.items()}
            try:
                if parent != c.get("parent"):
                    cb.move(cid, parent)
                cb.update(cid, name=v_name.get(), color=v_color.get(), **fields)
            except ValueError as exc:
                lbl_err.config(text=str(exc))
                return False
            cb.save()
            win.destroy()
            self._cd_fill_codebook()
            self._cd_show_code_info()
            if self.active_doc is not None and self._cd_visible():
                self._cd_render(self.active_doc)
            return True

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=r, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(bar, "Save", save, primary=True).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win, {"name": v_name, "parent": v_parent, "color": v_color, "texts": texts, "save": save, "error": lbl_err}

    def _cd_save_other(self, doc, message: str):
        """Save a transcript changed outside the editor (merging or deleting codes): backup and an undo step."""
        previous = doc.path.read_text(encoding="utf-8") if doc.path.exists() else None
        doc.save(backup=True)
        if previous is not None:
            self._push_undo(doc.path, message, previous)

    def _cd_merge_dialog(self, cid: Optional[str], target: Optional[str] = None):
        cb = self._cd_codebook()
        c = cb.get(cid) if cb and cid else None
        if c is None:
            return None
        choices = [(i, n) for i, n in self._cd_code_choices() if i != cid and i not in cb.descendants(cid)]
        if target is None:
            T = THEME
            win = tk.Toplevel(self)
            win.title("Merge code")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            self._label(win, f"All codings of “{c['name']}” (and its subcodes, which move along) go to:", font="small",
                        bg=T["bg_panel"]).pack(anchor="w")
            v = tk.StringVar(value="")
            ttk.Combobox(win, textvariable=v, values=[n.strip() for _, n in choices], state="readonly", width=40, font=self.f["small"]).pack(anchor="w", pady=8)
            bar = tk.Frame(win, bg=T["bg_panel"])
            bar.pack(fill=tk.X)
            go = lambda: (win.destroy(), self._cd_merge_dialog(cid, next((i for i, n in choices if n.strip() == v.get()), None)))
            self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT)
            self._tool(bar, "Merge", go, primary=True).pack(side=tk.RIGHT, padx=(0, 6))
            return win, {"var": v, "go": go}
        if cb.get(target) is None:
            return None
        n = (self.__dict__.get("_cd_counts") or {}).get(cid, 0)
        if not messagebox.askyesno("Merge code", f"Merge “{c['name']}” into “{cb.get(target)['name']}”?\n\n{n} coding(s) change code. Its "
                                   "subcodes move under the new code. Each changed transcript keeps a backup and can be undone "
                                   "when open; the codebook change is recorded in its log.", parent=self):
            return None
        self._cd_flush_active()
        moved = cod.recode_everywhere(cb.path.parent, {cid: target}, save_doc=self._cd_save_other)
        for child in cb.children(cid):
            cb.move(child["id"], target)
        memo = c.get("memo")
        cb.remove(cid)
        if memo:
            t = cb.get(target)
            cb.update(target, memo=((t.get("memo") or "") + f"\n[merged from {c['name']}] {memo}").strip())
        cb._log("merge", code=cid, into=target, codings=moved)
        cb.save()
        self._cd_after_project_change(f"Merged “{c['name']}” into “{cb.get(target)['name']}” ({moved} coding(s)).")
        return moved

    def _cd_delete_code(self, cid: Optional[str]):
        cb = self._cd_codebook()
        c = cb.get(cid) if cb and cid else None
        if c is None:
            return None
        ids = [cid] + cb.descendants(cid)
        counts = self.__dict__.get("_cd_counts") or {}
        n = sum(counts.get(i, 0) for i in ids)
        sub = f" and its {len(ids) - 1} subcode(s)" if len(ids) > 1 else ""
        if not messagebox.askyesno("Delete code", f"Delete “{c['name']}”{sub}?" + (f"\n\nIts {n} coding(s) are removed from the transcripts "
                                   "(each keeps a backup and can be undone when open)." if n else ""), parent=self):
            return None
        self._cd_flush_active()
        if n:
            from condense.core.transcript import TranscriptDocument as TD
            for p in cod.project_transcripts(cb.path.parent):
                try:
                    doc = TD.load(p)
                except Exception:
                    continue
                before = len(cod.codings(doc))
                doc.data["codings"] = [k for k in cod.codings(doc) if k["code"] not in ids]
                if len(cod.codings(doc)) < before:
                    doc.log_edit("uncode_code", codes=ids, codings=before - len(cod.codings(doc)))
                    self._cd_save_other(doc, f"Code “{c['name']}” deleted")
        cb.remove(cid)
        cb.save()
        self._cd_after_project_change(f"Deleted “{c['name']}”{sub}.")
        return ids

    def _cd_flush_active(self):
        """Pending text edits in the editor are saved before codes are changed in the files."""
        if self.active_doc is not None:
            self._autosave_edit(rerender=False)

    def _cd_after_project_change(self, message: str):
        self._cd_counts = None
        if self.active_doc is not None:
            self.active_doc = TranscriptDocument.load(self.active_doc.path)  # the file was changed on disk
            self._rerender()
        self._cd_refresh()
        self._append_log(message, "INFO")

    # ================================================================== the transcript with stripes
    def _cd_render(self, doc):
        t = self.cd_text
        self._cd["doc"] = doc
        y = t.yview()[0] if self._cd.get("rendered_for") == str(doc.path) else 0.0
        words: List[Tuple] = []  # (line, c0, c1, seg, wi, wid)
        line_words: Dict[int, List[int]] = {}
        roster = self._roster()
        colors = {n: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, n in enumerate(roster)}
        for n, col in colors.items():
            t.tag_config(f"SPKC{roster.index(n)}", foreground=col)
        cb = self._cd_codebook()
        res = cod.resolve(doc)
        moments = [r for r in res if r.get("kind") == "time"]
        eps = doc.episodes
        next_ep, mi = 0, 0
        last_spk = None

        out = textrender.Lines()  # built in Python, then only what changed goes to Tk
        put = out.put

        for i, seg in enumerate(doc.segments):
            if doc.is_segment_cut(i) or is_blank_text(seg.get("text")):
                continue
            s0 = float(seg.get("start", 0.0))
            while next_ep < len(eps) and eps[next_ep]["start"] <= s0 + 0.05:
                ep = eps[next_ep]
                next_ep += 1
                put(f"\n▶ {ep['type']} {ep.get('label') or ''}   {'≈ ' if ep.get('approx') else ''}{short_ts(ep['start'])[:-2]} – "
                    f"{short_ts(ep['end'])[:-2]}\n", "EP", self._episode_tag(ep["type"]))
                last_spk = None
            while mi < len(moments) and moments[mi]["start"] <= s0:
                self._cd_put_moment(put, moments[mi], cb)
                mi += 1
                last_spk = None
            spk = seg.get("speaker") or ""
            if spk != last_spk:
                put("" if out.empty else "\n")
                put(f"{spk or '—'}", "SPK", f"SPKC{roster.index(spk)}" if spk in roster else "TS")
                put(f"  {short_ts(s0)[:-2]}\n", "TS")
                last_spk = spk
            for k, w in enumerate(seg.get("words") or []):
                token = str(w.get("word", "")).strip()
                if not token:
                    continue
                line, col = out.line, out.col
                put(token)
                words.append((line, col, col + len(token), i, k, w.get("wid")))
                line_words.setdefault(line, []).append(len(words) - 1)
                put(" ")
        for r in moments[mi:]:
            self._cd_put_moment(put, r, cb)
        self._cd["shown_lines"] = textrender.show_lines(t, self._cd.get("shown_lines"), out.lines)
        for tag in ("sel", "KSEL", "PLAYING", "CODED"):  # as after a full redraw; painted again below where needed
            t.tag_remove(tag, "1.0", tk.END)
        self._cd.update(words=words, line_words=line_words, resolved=res, rendered_for=str(doc.path), starts=None)
        self._cd["wid_index"] = {w[5]: n for n, w in enumerate(words) if w[5]}
        self._cd["pos_index"] = {(w[3], w[4]): n for n, w in enumerate(words)}
        self._cd_mark_coded()
        t.yview_moveto(y)
        n_word = sum(1 for r in res if r.get("words"))
        n_orph = sum(1 for r in res if r.get("orphan"))
        self.lbl_cd_doc.config(text=f"{doc.base_name} · {len(res)} coding(s)" + (f" · {len(moments)} moment(s) without speech" if moments else "")
                               + (f" · ⚠ {n_orph} coding(s) whose words were deleted" if n_orph else ""))
        self._cd_fill_list()
        self._cd_fill_recent()
        self.after_idle(self._cd_draw_stripes)
        self._cd_draw_timeline()

    def _cd_put_moment(self, put, r, cb):
        c = cb.get(r["code"]) if cb else None
        name = c["name"] if c else "?"
        tag = f"MC{r['id']}"
        self.cd_text.tag_config(tag, foreground=c["color"] if c else THEME["fg_dim"])
        self.cd_text.tag_bind(tag, "<Button-1>", lambda e, k=r["id"]: self._cd_select_coding(k))
        put(f"\n◆ {name}  {short_ts(r['start'])[:-2]} – {short_ts(r['end'])[:-2]}", "MOMENT", tag)
        put(f"   {r['memo']}" if r.get("memo") else "   (no description)", "MOMENT", tag)
        put("\n")

    def _cd_current(self):
        """The open transcript. If the Coding tab still shows an older copy of it (after undo or an edit in the
        Transcript tab), it is shown again first, so codings always go into the transcript as it is now."""
        doc = self.active_doc
        if doc is None:
            return None
        if self._cd.get("doc") is not doc:
            self._cd_render(doc)
        return doc

    def _cd_word_index(self, r: Dict) -> Optional[Tuple[int, int]]:
        if not r.get("words"):
            return None
        a, b = r["words"]
        pi = self._cd.get("pos_index") or {}
        ia, ib = pi.get(tuple(a)), pi.get(tuple(b))
        if ia is None or ib is None:  # an end word is cut or empty: the nearest shown words inside
            shown = [n for (seg, wi), n in pi.items() if tuple(a) <= (seg, wi) <= tuple(b)]
            if not shown:
                return None
            ia, ib = min(shown), max(shown)
        return ia, ib

    def _cd_mark_coded(self):
        t = self.cd_text
        t.tag_remove("CODED", "1.0", tk.END)
        words = self._cd["words"]
        for r in self._cd["resolved"]:
            span = self._cd_word_index(r)
            if span:
                a, b = words[span[0]], words[span[1]]
                t.tag_add("CODED", f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")

    def _cd_draw_stripes(self):
        cv = self.__dict__.get("cd_stripes")
        if cv is None or self._cd.get("doc") is None:
            return
        cv.delete("all")
        t, words = self.cd_text, self._cd["words"]
        cb = self._cd_codebook()
        try:
            h = int(t.winfo_height())
            top_line = int(str(t.index("@0,0")).split(".")[0])
            bot_line = int(str(t.index(f"@0,{h}")).split(".")[0])
        except Exception:
            return  # not on screen yet
        cols: List[float] = []  # bottom y of the last stripe in each column
        items = []
        for r in self._cd["resolved"]:
            span = self._cd_word_index(r)
            if not span:
                continue
            a, b = words[span[0]], words[span[1]]
            if b[0] < top_line or a[0] > bot_line:
                continue
            ia = t.dlineinfo(f"{a[0]}.{a[1]}")
            ib = t.dlineinfo(f"{b[0]}.{b[1]}")
            ia = ia if isinstance(ia, tuple) else None
            ib = ib if isinstance(ib, tuple) else None
            y0 = ia[1] if ia else 0
            y1 = (ib[1] + ib[3]) if ib else h
            items.append((y0, y1, r))
        items.sort(key=lambda x: (x[0], -x[1]))
        self._cd["stripe_hits"] = []
        for y0, y1, r in items:
            col = next((k for k, bottom in enumerate(cols) if bottom < y0 - 1), None)
            if col is None:
                col = len(cols)
                cols.append(0)
            cols[col] = y1
            col = min(col, MAX_COLS - 1)
            x = 2 + col * (STRIPE_W + STRIPE_GAP)
            c = cb.get(r["code"]) if cb else None
            color = c["color"] if c else THEME["fg_dim"]
            sel = self._cd.get("sel") == r["id"]
            cv.create_rectangle(x, y0 + 1, x + STRIPE_W, y1 - 1, fill=color, outline=THEME["fg_primary"] if sel else color)
            self._cd["stripe_hits"].append((x, y0, x + STRIPE_W, y1, r["id"]))
        width = 4 + max(1, min(len(cols), MAX_COLS)) * (STRIPE_W + STRIPE_GAP)
        if int(cv.cget("width")) != width:
            cv.configure(width=width)

    def _cd_stripe_click(self, event):
        for x0, y0, x1, y1, kid in self._cd.get("stripe_hits") or []:
            if x0 - 2 <= event.x <= x1 + 2 and y0 <= event.y <= y1:
                return self._cd_select_coding(kid)
        return None

    # ----- timeline: codings and moments; drag to mark a moment without speech -----
    def _cd_draw_timeline(self):
        cv = self.cd_timeline
        cv.delete("all")
        doc = self._cd.get("doc")
        if doc is None:
            return
        w, h = max(1, cv.winfo_width()), int(cv.cget("height"))
        dur = max(self._duration(), max((float(s.get("end", 0)) for s in doc.segments), default=1.0), 1.0)
        cb = self._cd_codebook()
        for r in self._cd["resolved"]:
            c = cb.get(r["code"]) if cb else None
            col = c["color"] if c else THEME["fg_dim"]
            x0, x1 = r["start"] / dur * w, max(r["start"] / dur * w + 2, r["end"] / dur * w)
            if r.get("kind") == "time":
                cv.create_rectangle(x0, 2, x1, h - 2, fill=self._dim(col, 0.5), outline=col)
            else:
                cv.create_rectangle(x0, h - 9, x1, h - 3, fill=col, outline="")
        drag = self._cd.get("tl_drag")
        if drag and drag.get("t1") is not None:
            a, b = sorted((drag["t0"], drag["t1"]))
            cv.create_rectangle(a / dur * w, 0, b / dur * w, h, outline=THEME["fg_primary"], dash=(3, 2))
        cv.create_text(6, 3, anchor="nw", text="drag here to mark a moment without speech", fill=THEME["fg_dim"], font=self.f["small"])
        self._cd["tl_dur"] = dur

    def _cd_tl_time(self, x) -> float:
        w = max(1, self.cd_timeline.winfo_width())
        return max(0.0, min(1.0, x / w)) * self._cd.get("tl_dur", 1.0)

    def _cd_tl_press(self, event):
        self._cd["tl_drag"] = {"t0": self._cd_tl_time(event.x), "t1": None}

    def _cd_tl_drag(self, event):
        if self._cd.get("tl_drag"):
            self._cd["tl_drag"]["t1"] = self._cd_tl_time(event.x)
            self._cd_draw_timeline()

    def _cd_tl_release(self, event):
        d = self._cd.get("tl_drag")
        self._cd["tl_drag"] = None
        if not d:
            return None
        t1 = self._cd_tl_time(event.x)
        if abs(t1 - d["t0"]) < 0.5:  # a click: play from there
            self._player_start(d["t0"])
            self._cd_draw_timeline()
            return None
        self._cd_draw_timeline()
        a, b = sorted((d["t0"], t1))
        return self._cd_time_dialog(a, b)

    # ================================================================== selecting and coding
    def _cd_key(self, event):
        nav = {"Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End", "Shift_L", "Shift_R", "Control_L", "Control_R"}
        mods = is_shortcut(event)
        if event.keysym in nav or (mods and event.keysym.lower() in ("c", "a")):
            return None  # moving and selecting with the keyboard, copy, select all
        if mods:
            return None  # other shortcuts (undo …) go to the app
        k = event.keysym
        if k in ("Return", "KP_Enter"):
            self._cd_apply(self._cd_selected_code())
        elif k in ("n", "N"):
            self._cd_new_code(parent=self._cd_selected_code(), apply=True)
        elif k in ("Delete", "BackSpace"):
            self._cd_remove(self._cd.get("sel"))
        elif k == "space":
            self._toggle_play()
        elif k.isdigit() and k != "0":
            rec = self._cd["recent"]
            if int(k) <= len(rec):
                self._cd_apply(rec[int(k) - 1])
        return "break"

    def _cd_selection_words(self) -> Optional[Tuple[int, int]]:
        rng = self.cd_text.tag_ranges("sel")
        if not rng:
            return None
        a, b = str(rng[0]), str(rng[-1])
        la, ca = map(int, a.split("."))
        lb, cb_ = map(int, b.split("."))
        words = self._cd["words"]
        inside = [n for n, (line, c0, c1, *_r) in enumerate(words) if (line, c1) > (la, ca) and (line, c0) < (lb, cb_)]
        return (inside[0], inside[-1]) if inside else None

    def _cd_apply(self, code_id: Optional[str]):
        doc = self._cd_current()
        cb = self._cd_codebook()
        if doc is None or cb is None:
            return None
        if not code_id or cb.get(code_id) is None:
            self.lbl_cd_doc.config(text="Choose a code in the codebook first (or press N for a new one).")
            return None
        span = self._cd_selection_words()
        if span is None:
            self.lbl_cd_doc.config(text="Select the words to code first (drag over them).")
            return None
        words = self._cd["words"]
        a, b = words[span[0]], words[span[1]]
        wa = doc.segments[a[3]]["words"][a[4]]
        wb = doc.segments[b[3]]["words"][b[4]]
        kid = cod.add_coding(doc, code_id, float(wa["start"]), float(wb["end"]), wa["wid"], wb["wid"])
        self._cd_commit(f"Coded “{cb.get(code_id)['name']}”: {short_ts(float(wa['start']))[:-2]}")
        self._cd_count(code_id, +1)
        self._cd_use_recent(code_id)
        self._cd_render(doc)
        self._cd_fill_codebook()
        self._cd_select_coding(kid)
        return kid

    def _cd_time_dialog(self, start: Optional[float] = None, end: Optional[float] = None, coding_id: Optional[str] = None):
        """A moment without speech (interaction with the materials, a gesture …): code, time and what happens."""
        doc = self._cd_current()
        cb = self._cd_codebook()
        if doc is None or cb is None:
            return None
        T = THEME
        existing = cod.get_coding(doc, coding_id) if coding_id else None
        if start is None:
            pos = self._current_pos()
            start, end = (existing["start"], existing["end"]) if existing else (pos, pos + 10)
        choices = self._cd_code_choices()
        win = tk.Toplevel(self)
        win.title("Moment without speech")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        self._label(win, "Code a stretch of the recording by time — what happens without words (materials, devices, gestures). "
                         "It shows in the transcript at its time, with your description.", font="small", fg=T["fg_secondary"],
                    bg=T["bg_panel"], wraplength=460, justify=tk.LEFT).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        v_start, v_end = tk.StringVar(value=short_ts(start)), tk.StringVar(value=short_ts(end))
        sel_name = next((n.strip() for i, n in choices if i == ((existing or {}).get("code") or self._cd_selected_code())), "")
        v_code = tk.StringVar(value=sel_name)
        self._label(win, "From", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=1, column=0, sticky="w")
        self._entry(win, v_start, font="small", width=12).grid(row=1, column=1, sticky="w")
        self._label(win, "To", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=2, column=0, sticky="w")
        self._entry(win, v_end, font="small", width=12).grid(row=2, column=1, sticky="w")
        self._tool(win, "▶ Play", lambda: (eplib.parse_time(v_start.get()) is not None and self._player_start(
            eplib.parse_time(v_start.get()), max(1.0, (eplib.parse_time(v_end.get()) or 0) - eplib.parse_time(v_start.get()))))).grid(row=1, column=2, padx=(8, 0))
        self._label(win, "Code", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(win, textvariable=v_code, values=[n.strip() for _, n in choices], state="readonly", width=34,
                     font=self.f["small"]).grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self._label(win, "What happens", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).grid(row=4, column=0, sticky="nw", pady=(6, 0))
        memo = tk.Text(win, height=4, width=48, wrap=tk.WORD, font=self.f["small"], bg=T["bg_input"], fg=T["fg_primary"],
                       insertbackground=T["fg_primary"], relief=tk.FLAT, highlightthickness=1, highlightbackground=T["border"])
        memo.insert("1.0", (existing or {}).get("memo", ""))
        memo.grid(row=4, column=1, columnspan=2, sticky="we", pady=(6, 0))
        lbl = self._label(win, "", font="small", fg=T["warn"], bg=T["bg_panel"])
        lbl.grid(row=5, column=0, columnspan=3, sticky="w")

        def save():
            a, b = eplib.parse_time(v_start.get()), eplib.parse_time(v_end.get())
            code = next((i for i, n in choices if n.strip() == v_code.get()), None)
            if a is None or b is None or b <= a:
                lbl.config(text="From and to as h:mm:ss, the end after the start.")
                return None
            if code is None:
                lbl.config(text="Choose a code (or create one in the codebook first).")
                return None
            text = memo.get("1.0", "end-1c").strip()
            if existing:
                existing["start"], existing["end"] = round(a, 3), round(b, 3)
                cod.update_coding(doc, existing["id"], code=code, memo=text)
                kid = existing["id"]
            else:
                kid = cod.add_coding(doc, code, a, b, memo=text)
                self._cd_count(code, +1)
            self._cd_commit(f"Moment coded “{cb.get(code)['name']}” {short_ts(a)[:-2]}–{short_ts(b)[:-2]}")
            self._cd_use_recent(code)
            win.destroy()
            self._cd_render(doc)
            self._cd_fill_codebook()
            self._cd_select_coding(kid)
            return kid

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(bar, "Save", save, primary=True).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win, {"start": v_start, "end": v_end, "code": v_code, "memo": memo, "save": save, "error": lbl}

    def _cd_commit(self, message: str):
        """Codings don't change the exports: save the transcript with a backup and an undo step, nothing else."""
        doc = self.active_doc
        if doc is None:
            return
        try:
            previous = doc.path.read_text(encoding="utf-8") if doc.path.exists() else None
            doc.save(backup=True)
        except Exception as exc:
            messagebox.showerror("Could not save transcript", str(exc), parent=self)
            return
        if previous is not None:
            self._push_undo(doc.path, message, previous)
        self.lbl_status.config(text=message)

    def _cd_count(self, code_id: str, delta: int):
        counts = self.__dict__.get("_cd_counts")
        if counts is None:
            counts = self._cd_counts = {}
        counts[code_id] = max(0, counts.get(code_id, 0) + delta)

    def _cd_use_recent(self, code_id: str):
        rec = [c for c in self._cd["recent"] if c != code_id]
        self._cd["recent"] = ([code_id] + rec)[:9]
        self._cd_fill_recent()

    def _cd_fill_recent(self):
        fr = self.cd_recent
        fr.clear()
        cb = self._cd_codebook()
        rec = [c for c in self._cd["recent"] if cb and cb.get(c)]
        self._cd["recent"] = rec
        if not rec:
            fr.add(self._label(fr, "Recent codes appear here: 1–9 code the selection with them.", font="small", fg=THEME["fg_dim"], bg=THEME["bg_panel"]))
            return
        fr.add(self._label(fr, "Recent:", font="small", fg=THEME["fg_secondary"], bg=THEME["bg_panel"]))
        for k, cid in enumerate(rec):
            c = cb.get(cid)
            chip = tk.Label(fr, text=f"{k + 1}  {c['name']}", font=self.f["small_bold"], padx=8, pady=1, cursor="hand2", bg=THEME["bg_input"],
                            fg=c["color"], highlightthickness=1, highlightbackground=c["color"])
            chip.bind("<Button-1>", lambda e, cid=cid: self._cd_apply(cid))
            fr.add(chip)

    # ================================================================== codings: select, list, change
    def _cd_click(self, event):
        """A click (not a drag) on coded words selects the innermost coding there."""
        t = self.cd_text
        if t.tag_ranges("sel"):
            return None  # a selection was made: that's for coding
        idx = t.index(f"@{event.x},{event.y}")
        line, col = map(int, idx.split("."))
        ws = self._cd["line_words"].get(line, [])
        n = next((n for n in ws if self._cd["words"][n][1] <= col <= self._cd["words"][n][2]), None)
        if n is None:
            return None
        hits = []
        for r in self._cd["resolved"]:
            span = self._cd_word_index(r)
            if span and span[0] <= n <= span[1]:
                hits.append((span[1] - span[0], r["id"]))
        if hits:
            return self._cd_select_coding(min(hits)[1])
        self._cd_select_coding(None)
        return None

    def _cd_select_coding(self, kid: Optional[str], from_list: bool = False):
        self._cd["sel"] = kid
        t = self.cd_text
        t.tag_remove("KSEL", "1.0", tk.END)
        r = next((r for r in self._cd["resolved"] if r["id"] == kid), None) if kid else None
        if r is not None:
            span = self._cd_word_index(r)
            if span:
                a, b = self._cd["words"][span[0]], self._cd["words"][span[1]]
                t.tag_add("KSEL", f"{a[0]}.{a[1]}", f"{b[0]}.{b[2]}")
                t.see(f"{a[0]}.{a[1]}")
            elif r.get("kind") == "time":
                rng = t.tag_ranges(f"MC{kid}")
                if rng:
                    t.tag_add("KSEL", rng[0], rng[-1])
                    t.see(rng[0])
            if not from_list and self.cd_list.exists(kid):
                self.cd_list.selection_set(kid)
                self.cd_list.see(kid)
        self._cd_draw_stripes()
        return kid

    def _cd_list_selected(self) -> Optional[str]:
        sel = self.cd_list.selection()
        return sel[0] if sel else self._cd.get("sel")

    def _cd_fill_list(self):
        lst = self.__dict__.get("cd_list")
        doc = self._cd.get("doc") if self.__dict__.get("_cd") else None
        if lst is None:
            return
        for iid in lst.get_children():
            lst.delete(iid)
        if doc is None:
            return
        cb = self._cd_codebook()
        only = self._cd_selected_code() if self.var_cd_only.get() else None
        keep = set([only] + cb.descendants(only)) if only and cb else None
        n = 0
        for r in self._cd["resolved"]:
            if keep is not None and r["code"] not in keep:
                continue
            c = cb.get(r["code"]) if cb else None
            text = ("◆ " + (r.get("memo") or "(moment without speech)")) if r.get("kind") == "time" else (
                "⚠ (its words were deleted)" if r.get("orphan") else cod.coded_text(doc, r))
            if r.get("memo") and r.get("kind") != "time":
                text += f"   ✎ {r['memo']}"
            tag = f"col{c['color']}" if c else ""
            if c:
                lst.tag_configure(tag, foreground=c["color"])
            lst.insert("", tk.END, iid=r["id"], values=(short_ts(r["start"])[:-2], cb.path_name(r["code"]) if c else "?",
                                                        ", ".join(cod.speakers_of(doc, r)), text[:300]), tags=(tag,) if tag else ())
            n += 1
        self.lbl_cd_list.config(text=f"Codings ({n})")

    def _cd_remove(self, kid: Optional[str]):
        doc = self._cd_current()
        if doc is None or not kid:
            return False
        c = cod.get_coding(doc, kid)
        if c is None or not cod.remove_coding(doc, kid):
            return False
        self._cd_count(c["code"], -1)
        self._cd_commit("Coding removed")
        self._cd["sel"] = None
        self._cd_render(doc)
        self._cd_fill_codebook()
        return True

    def _cd_change_code(self, kid: Optional[str], code_id: Optional[str] = None):
        """Give a coding another code — also how a code that grew too big is split: make the new code, then move the
        codings that belong to it, one by one from the list."""
        doc, cb = self._cd_current(), self._cd_codebook()
        c = cod.get_coding(doc, kid) if doc is not None and kid else None
        if c is None or cb is None:
            return None
        if c.get("kind") == "time" and code_id is None:
            return self._cd_time_dialog(coding_id=kid)
        if code_id is None:
            choices = self._cd_code_choices()
            T = THEME
            win = tk.Toplevel(self)
            win.title("Change code")
            win.configure(bg=T["bg_panel"], padx=16, pady=14)
            v = tk.StringVar(value=next((n.strip() for i, n in choices if i == c["code"]), ""))
            ttk.Combobox(win, textvariable=v, values=[n.strip() for _, n in choices], state="readonly", width=40, font=self.f["small"]).pack(anchor="w")
            go = lambda: (win.destroy(), self._cd_change_code(kid, next((i for i, n in choices if n.strip() == v.get()), None)))
            self._tool(win, "Change", go, primary=True).pack(anchor="e", pady=(10, 0))
            return win, {"var": v, "go": go}
        if cb.get(code_id) is None or not cod.update_coding(doc, kid, code=code_id):
            return None
        self._cd_count(c["code"], -1)
        self._cd_count(code_id, +1)
        self._cd_commit(f"Coding moved to “{cb.get(code_id)['name']}”")
        self._cd_render(doc)
        self._cd_fill_codebook()
        self._cd_select_coding(kid)
        return kid

    def _cd_memo(self, kid: Optional[str], text: Optional[str] = None):
        doc = self._cd_current()
        c = cod.get_coding(doc, kid) if doc is not None and kid else None
        if c is None:
            return None
        if c.get("kind") == "time" and text is None:
            return self._cd_time_dialog(coding_id=kid)
        if text is None:
            text = simpledialog.askstring("Memo", "Memo for this coding:", initialvalue=c.get("memo", ""), parent=self)
            if text is None:
                return None
        if cod.update_coding(doc, kid, memo=text):
            self._cd_commit("Coding memo changed")
            self._cd_render(doc)
            self._cd_select_coding(kid)
        return kid

    def _cd_text_menu(self, event):
        m = tk.Menu(self, tearoff=0)
        sel = self._cd_selection_words()
        code = self._cd_selected_code()
        cb = self._cd_codebook()
        if sel and code and cb and cb.get(code):
            m.add_command(label=f"Code with “{cb.get(code)['name']}”", command=lambda: self._cd_apply(code))
        if sel:
            m.add_command(label="New code from selection…", command=lambda: self._cd_new_code(parent=code, apply=True))
            for k, cid in enumerate(self._cd["recent"][:9]):
                if cb and cb.get(cid):
                    m.add_command(label=f"{k + 1}  {cb.get(cid)['name']}", command=lambda cid=cid: self._cd_apply(cid))
        kid = self._cd.get("sel")
        if kid:
            m.add_separator()
            m.add_command(label="Memo for this coding…", command=lambda: self._cd_memo(kid))
            m.add_command(label="Change its code…", command=lambda: self._cd_change_code(kid))
            m.add_command(label="Delete this coding", command=lambda: self._cd_remove(kid))
        m.add_separator()
        m.add_command(label="Moment without speech…", command=lambda: self._cd_time_dialog())
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return m

    # ================================================================== playing
    def _cd_play_word(self, event):
        t = self.cd_text
        line, col = map(int, t.index(f"@{event.x},{event.y}").split("."))
        n = next((n for n in self._cd["line_words"].get(line, []) if self._cd["words"][n][1] <= col <= self._cd["words"][n][2]), None)
        if n is not None:
            w = self._cd["words"][n]
            self._player_start(float(self._cd["doc"].segments[w[3]]["words"][w[4]]["start"]))
        return "break"

    def _cd_play_selection(self):
        span = self._cd_selection_words()
        doc = self._cd_current()
        if not span or doc is None:
            r = next((r for r in self._cd["resolved"] if r["id"] == self._cd.get("sel")), None)
            if r is not None:
                return self._cd_play_coding(r["id"])
            return None
        a, b = self._cd["words"][span[0]], self._cd["words"][span[1]]
        t0 = float(doc.segments[a[3]]["words"][a[4]]["start"])
        t1 = float(doc.segments[b[3]]["words"][b[4]]["end"])
        self._player_start(t0, t1 - t0 + 0.3)
        return t0

    def _cd_play_coding(self, kid: Optional[str]):
        r = next((r for r in self._cd["resolved"] if r["id"] == kid), None)
        if r is None:
            return None
        self._cd_select_coding(kid)
        self._player_start(max(0.0, r["start"] - 0.3), r["end"] - r["start"] + 0.6)
        return r["start"]

    def _cd_follow(self, pos: float):
        """While playing: the word being spoken, in the Coding tab too."""
        if self.__dict__.get("_cd") is None or self._cd.get("doc") is None or not self._cd_visible():
            return
        doc, words = self._cd["doc"], self._cd["words"]
        if not words:
            return
        starts = self._cd.get("starts")
        if starts is None or len(starts) != len(words):
            starts = self._cd["starts"] = [float(doc.segments[w[3]]["words"][w[4]]["start"]) for w in words]
        n = max(0, bisect.bisect_right(starts, pos) - 1)
        w = words[n]
        t = self.cd_text
        t.tag_remove("PLAYING", "1.0", tk.END)
        t.tag_add("PLAYING", f"{w[0]}.{w[1]}", f"{w[0]}.{w[2]}")
        if self.var_follow.get():
            t.see(f"{w[0]}.{w[1]}")
