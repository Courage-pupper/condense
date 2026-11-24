"""Check for updates, show what is new, update with one click and restart."""
import webbrowser

from condense import __version__, updates
from condense.gui._common import *  # noqa: F401,F403


class UpdatesMixin:
    def _init_updates(self):
        self.var_update_auto = tk.BooleanVar(value=bool(self.settings.get("update_auto_check", True)))
        self._update_q: "queue.Queue" = queue.Queue()
        self._update_busy = False
        self._update_info: Optional[Dict] = None
        if updates.due(self.settings):
            self.after(4000, lambda: self._check_updates(manual=False))

    def _toggle_update_auto(self):
        self.settings["update_auto_check"] = bool(self.var_update_auto.get())
        SettingsStore.save(self.settings)

    # ----- checking -----
    def _check_updates(self, manual: bool = True):
        if self._update_busy:
            return
        self._update_busy = True
        if manual:
            self.lbl_status.config(text="Checking for updates…")

        def work():
            try:
                self._update_q.put(("checked", updates.latest_release(), manual))
            except Exception as exc:
                self._update_q.put(("check_failed", str(exc), manual))

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_updates)

    def _poll_updates(self):
        while True:
            try:
                msg = self._update_q.get_nowait()
            except queue.Empty:
                break
            self._handle_update_msg(msg)
        if self._update_busy:
            self.after(200, self._poll_updates)

    def _handle_update_msg(self, msg):
        kind = msg[0]
        if kind == "checked":
            _, info, manual = msg
            self._update_busy = False
            self.settings["update_last_check"] = time.time()
            SettingsStore.save(self.settings)
            if not updates.is_newer(info["version"]):
                if manual:
                    self.lbl_status.config(text=f"Condense {__version__} is the latest version.")
                    messagebox.showinfo("No update", f"You have the latest version of Condense ({__version__}).", parent=self)
                return
            self._update_info = info
            self._append_log(f"Condense {info['version']} is available (you have {__version__}) — Help ▸ Check for Updates.", "INFO")
            self.lbl_status.config(text=f"Update available: Condense {info['version']}.")
            if manual or self.settings.get("update_skip_version") != info["version"]:
                self._show_update_dialog(info)
        elif kind == "check_failed":
            _, error, manual = msg
            self._update_busy = False
            if manual:
                self.lbl_status.config(text="Could not check for updates.")
                messagebox.showwarning("Could not check for updates",
                                       f"GitHub could not be reached ({error}).\n\nAre you offline? You can also look at {updates.RELEASES_PAGE}",
                                       parent=self)
            else:
                self._append_log(f"Update check skipped: {error}", "INFO")
        elif kind == "line":
            self._append_log(f"  {msg[1]}", "CMD")
        elif kind == "updated":
            code = msg[1]
            self._update_busy = False
            if code == 0:
                self._append_log("Update installed.", "SUCCESS")
                self.lbl_status.config(text="Update installed — restart Condense to use it.")
                if messagebox.askyesno("Update installed", "The update is installed. Restart Condense now?", parent=self):
                    self._restart_app()
            else:
                self._append_log(f"The update failed (exit code {code}); details above. Condense was not changed or is still usable.", "ERROR")
                how = (f"powershell -ExecutionPolicy Bypass -File \"{updates.updater_path()}\" -Update" if PLATFORM == "windows"
                       else f"{updates.updater_path()} --update")
                messagebox.showerror("Update failed", "The update did not finish. The Console tab shows what happened.\n\n"
                                     f"You can also run it in a terminal:  {how}", parent=self)

    # ----- the dialog -----
    def _show_update_dialog(self, info: Dict):
        T = THEME
        win = tk.Toplevel(self)
        win.title("Update available")
        win.configure(bg=T["bg_panel"])
        win.transient(self)
        win.minsize(460, 300)
        self._label(win, f"Condense {info['version']} is available", font="title", bg=T["bg_panel"]).pack(anchor="w", padx=16, pady=(14, 2))
        self._label(win, f"You have {__version__}. Your settings, tasks and transcripts are kept.", font="small",
                    fg=T["fg_secondary"], bg=T["bg_panel"]).pack(anchor="w", padx=16)
        notes = tk.Text(win, height=14, width=70, wrap=tk.WORD, bg=T["bg_input"], fg=T["fg_primary"], font=self.f["small"],
                        relief=tk.FLAT, padx=10, pady=8, highlightthickness=0)
        notes.insert("1.0", info.get("notes") or "No release notes.")
        notes.configure(state=tk.DISABLED)
        notes.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, padx=16, pady=(0, 14))
        if updates.update_command() is not None:
            self._tool(bar, "Update now", lambda: (win.destroy(), self._start_update()), primary=True).pack(side=tk.RIGHT)
        else:
            self._label(bar, "This copy was not installed with the installer: update it with git pull and the install script.",
                        font="small", fg=T["warn"], bg=T["bg_panel"]).pack(side=tk.TOP, anchor="w", pady=(0, 6))
        self._tool(bar, "Later", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        self._tool(bar, "Skip this version", lambda: (self._skip_version(info["version"]), win.destroy())).pack(side=tk.RIGHT, padx=(0, 6))
        self._tool(bar, "Release page", lambda: webbrowser.open(info.get("url") or updates.RELEASES_PAGE)).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win

    def _skip_version(self, version: str):
        self.settings["update_skip_version"] = version
        SettingsStore.save(self.settings)

    # ----- updating -----
    def _start_update(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "The task queue is running. Update when it has finished.", parent=self)
            return
        if self._update_busy:
            return
        try:
            self._autosave_edit(rerender=False)  # nothing typed is lost
        except Exception:
            pass
        self._update_busy = True
        self.notebook.select(self.tab_console)
        self._append_log("=== Updating Condense (this can take a few minutes) ===", "STAGE")

        def work():
            try:
                code = updates.run_update(lambda line: self._update_q.put(("line", line)))
            except Exception as exc:
                self._update_q.put(("line", f"Error: {exc}"))
                code = 1
            self._update_q.put(("updated", code))

        threading.Thread(target=work, daemon=True).start()
        self.after(200, self._poll_updates)

    def _restart_app(self):
        """Close normally (saves everything), then start the freshly installed version in this process's place."""
        self._on_close()
        try:
            if self.winfo_exists():  # closing was cancelled (a task is running and you chose to keep it)
                return
        except tk.TclError:
            pass  # the window is gone: closed normally
        if os.name == "nt":  # exec does not replace a GUI process on Windows: start the new one, this one ends
            subprocess.Popen([sys.executable, "-m", "condense"], close_fds=True)
            return
        os.execv(sys.executable, [sys.executable, "-m", "condense"])

    def _open_url(self, url: str):
        webbrowser.open(url)
