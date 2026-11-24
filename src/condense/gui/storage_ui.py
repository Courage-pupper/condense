"""Project ▸ Storage…: how much space backups, leftovers and the audio cache take, and clearing them (see core/storage.py)."""
from condense.core import storage
from condense.gui._common import *  # noqa: F401,F403

KEEP_CHOICES = ["10", "5", "3", "1", "none"]


class StorageMixin:
    def _storage_folder(self) -> Optional[Path]:
        project = self._project_dir()
        if project is not None:
            return project
        if self.active_doc is not None:
            return self.active_doc.out_dir.parent
        d = self._ask_directory("Which folder? (a study folder, or one with transcripts)", self.settings.get("last_transcript_dir"))
        return Path(d) if d else None

    def _storage_dialog(self, folder: Optional[Path] = None):
        folder = folder or self._storage_folder()
        if folder is None:
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title(f"Storage — {Path(folder).name}")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, f"What Condense keeps in {folder} besides your recordings and transcripts. Only backups, leftovers "
                         "and the audio cache can be cleared here.", font="small", fg=T["fg_secondary"], bg=T["bg_panel"],
                    wraplength=560, justify=tk.LEFT).pack(anchor="w")
        body = tk.Frame(win, bg=T["bg_panel"])
        body.pack(fill=tk.X, pady=(10, 0))
        lbl_msg = self._label(win, "", font="small", fg=T["ok"], bg=T["bg_panel"])
        lbl_msg.pack(anchor="w", pady=(8, 0))
        v_keep = tk.StringVar(value=self.settings.get("storage_keep", "3"))
        st: Dict = {}

        def row(r, title, size, note, button=None):
            self._label(body, title, font="body_bold", bg=T["bg_panel"]).grid(row=r, column=0, sticky="w", pady=(6, 0))
            self._label(body, size, font="mono_small", fg=T["fg_primary"], bg=T["bg_panel"]).grid(row=r, column=1, sticky="e", padx=12, pady=(6, 0))
            if button is not None:
                button.grid(row=r, column=2, sticky="e", pady=(6, 0))
            self._label(body, note, font="small", fg=T["fg_dim"], bg=T["bg_panel"], wraplength=420, justify=tk.LEFT
                        ).grid(row=r + 1, column=0, columnspan=3, sticky="w")

        def fill():
            for w in body.winfo_children():
                w.destroy()
            rep = st["rep"] = storage.scan(Path(folder), APP_SUPPORT_DIR / "cache" / "audio")
            body.columnconfigure(0, weight=1)
            keep_box = tk.Frame(body, bg=T["bg_panel"])
            self._label(keep_box, "keep newest", font="small", fg=T["fg_secondary"], bg=T["bg_panel"]).pack(side=tk.LEFT)
            ttk.Combobox(keep_box, textvariable=v_keep, values=KEEP_CHOICES, state="readonly", width=5, font=self.f["small"]).pack(side=tk.LEFT, padx=4)
            self._tool(keep_box, "Thin out…", thin).pack(side=tk.LEFT)
            n_tr = len({(p.parent, storage.BACKUP_RE.match(p.name).group("base")) for p in rep["backups"]})
            row(0, "Backups", storage.human(rep["backups_bytes"]),
                f"{len(rep['backups'])} copies of {n_tr} transcript(s), made while editing (in each _backups folder). "
                "Restore one with Edit ▸ Restore Backup…", keep_box if rep["backups"] else None)
            row(2, "Leftovers", storage.human(rep["leftovers_bytes"]),
                f"{len(rep['leftovers'])} part file(s) of exports that were interrupted.",
                self._tool(body, "Delete…", lambda: clear("leftovers")) if rep["leftovers"] else None)
            row(4, "Audio cache", storage.human(rep["cache_bytes"]),
                "The sound of recordings, read once to find interview clips or link videos. Made again when needed.",
                self._tool(body, "Clear…", lambda: clear("cache")) if rep["cache"] else None)
            row(6, "Transcripts", storage.human(rep["transcript_bytes"]), f"{rep['transcripts']} transcript file(s). Not cleared here.")
            row(8, "Videos Condense made", storage.human(rep["made_bytes"]),
                f"{len(rep['made'])} compressed, trimmed or subtitled video(s) and playback audio. Results, not cleared here: "
                "delete those you no longer need in the folder.")
            if rep["truncated"]:
                lbl_msg.config(text="Very large folder: only part of it was looked at.", fg=T["warn"])

        def confirm(paths, what) -> bool:
            size = storage.human(sum(p.stat().st_size for p in paths if p.exists()))
            return messagebox.askyesno("Storage", f"Delete {len(paths)} {what} ({size})? This cannot be undone.", parent=win)

        def report(res, what):
            msg = f"Deleted {res['deleted']} {what}, freed {storage.human(res['bytes'])}."
            if res["failed"]:
                msg += f" {len(res['failed'])} could not be deleted (see Console)."
                for f in res["failed"]:
                    self._append_log(f"Storage: could not delete {f}", "WARN")
            self._append_log(f"Storage ({folder}): {msg}", "INFO")
            fill()
            lbl_msg.config(text=msg, fg=T["ok"])

        def thin():
            keep = 0 if v_keep.get() == "none" else int(v_keep.get())
            self.settings["storage_keep"] = v_keep.get()
            paths = storage.backups_to_remove(st["rep"]["backups"], keep)
            if not paths:
                lbl_msg.config(text=f"Nothing to delete: no transcript has more than {keep} backup(s).", fg=T["fg_secondary"])
                return None
            what = "backup(s)" + (f", keeping the newest {keep} of each transcript" if keep else " (all of them)")
            if confirm(paths, what):
                report(storage.delete(paths), "backup(s)")
            return None

        def clear(kind):
            paths = st["rep"][kind]
            if paths and confirm(paths, {"leftovers": "leftover file(s)", "cache": "cache file(s)"}[kind]):
                report(storage.delete(paths), {"leftovers": "leftover file(s)", "cache": "cache file(s)"}[kind])

        fill()
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(12, 0))
        self._tool(bar, "Close", win.destroy).pack(side=tk.RIGHT)
        self._tool(bar, "Show Folder", lambda: self._open_externally(folder)).pack(side=tk.RIGHT, padx=(0, 6))
        return win, {"keep": v_keep, "thin": thin, "clear": clear, "state": st, "refresh": fill}
