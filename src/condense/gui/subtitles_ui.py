"""Video with subtitles: the open transcript (Transcript ▾) or several from the Project tab (right-click), as a subtitle
track or burned into the picture. See media/subtitles.py."""
import threading

from condense.gui._common import *  # noqa: F401,F403
from condense.media import subtitles as sublib


class SubtitlesMixin:
    def _subtitle_jobs(self, paths: List[Path]) -> List[Dict]:
        jobs = []
        for p in paths:
            try:
                d = TranscriptDocument.load(Path(p))
            except Exception as exc:
                jobs.append({"path": Path(p), "name": Path(p).name, "video": None, "error": str(exc)})
                continue
            jobs.append({"path": d.path, "name": d.base_name, "video": d.video_on_timeline(), "out_dir": d.out_dir,
                         "checked": round(d.checked_fraction() * 100), "language": d.result.get("language")})
        return jobs

    def _subtitles_dialog(self, paths: Optional[List[Path]] = None):
        if paths is None:
            if self.active_doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return None
            paths = [self.active_doc.path]
        if self.active_doc is not None:
            self._autosave_edit(rerender=False)  # the subtitles show the text as it is now
        jobs = self._subtitle_jobs(paths)
        ready = [j for j in jobs if j.get("video")]
        if not ready:
            messagebox.showinfo("Video with subtitles", "No video on the transcript's timeline was found"
                                + ("" if len(jobs) == 1 else " for any of them") + ".\n\nIf the video was moved, use Playback ▾ ▸ "
                                "Link a video… first. Audio-only transcripts cannot get subtitles in a picture.", parent=self)
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title("Video with subtitles")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "A copy of the video with the transcript as subtitles, saved next to each transcript as "
                         "<name>_subtitled. Cut parts are left out of the subtitles; the video itself is not cut.",
                    font="small", fg=T["fg_secondary"], bg=T["bg_panel"], wraplength=540, justify=tk.LEFT).pack(anchor="w")
        lines = []
        for j in jobs:
            if not j.get("video"):
                lines.append(f"✕ {j['name']} — {j.get('error') or 'no video on its timeline (skipped)'}")
            elif j["checked"] < 100:
                lines.append(f"⚠ {j['name']} — {j['checked']} % checked")
            else:
                lines.append(f"✓ {j['name']} — checked")
        self._label(win, "\n".join(lines[:12]) + (f"\n… and {len(lines) - 12} more" if len(lines) > 12 else ""), font="small",
                    bg=T["bg_panel"], justify=tk.LEFT).pack(anchor="w", pady=(8, 4))
        if any(j.get("video") and j["checked"] < 100 for j in jobs):
            self._label(win, "Not every transcript is fully checked: the subtitles show the text as it is now.", font="small",
                        fg=T["warn"], bg=T["bg_panel"]).pack(anchor="w")
        v_mode = tk.StringVar(value=self.settings.get("subtitle_mode", "track"))
        burn_ok = sublib.can_burn()
        if not burn_ok:
            v_mode.set("track")
        for value, text in (("track", "Subtitle track — seconds, nothing re-encoded. Switch them on in the player "
                                      "(VLC, QuickTime, IINA, most video players)."),
                            ("burn", "Burned into the picture — always visible, in every player and wherever you share it. "
                                     "Re-encodes the video (a few minutes per hour).")):
            rb = tk.Radiobutton(win, text=text, variable=v_mode, value=value, bg=T["bg_panel"], fg=T["fg_primary"], selectcolor=T["bg_input"],
                                activebackground=T["bg_panel"], font=self.f["small"], anchor="w", justify=tk.LEFT, wraplength=520)
            rb.pack(anchor="w", fill=tk.X, pady=(6, 0))
            if value == "burn" and not burn_ok:
                rb.configure(state=tk.DISABLED)
        if not burn_ok:
            self._label(win, "This ffmpeg cannot burn subtitles in (built without libass).", font="small", fg=T["fg_dim"],
                        bg=T["bg_panel"]).pack(anchor="w")
        v_spk = tk.BooleanVar(value=bool(self.settings.get("subtitle_speakers", True)))
        self._check(win, "Show who speaks (\"Robin: …\" when the speaker changes)", v_spk, bg=T["bg_panel"], font="small").pack(anchor="w", pady=(8, 0))

        def go(wait: bool = False):
            mode, speakers = v_mode.get(), bool(v_spk.get())
            for j in ready:
                j["dst"] = sublib.output_path(j["video"], j["out_dir"], j["name"], mode)
            existing = [j["dst"].name for j in ready if j["dst"].exists()]
            if existing and not messagebox.askyesno("Video with subtitles", f"{len(existing)} file(s) exist already "
                                                    f"({', '.join(existing[:3])}{' …' if len(existing) > 3 else ''}). Replace them?", parent=win):
                return None
            self.settings["subtitle_mode"], self.settings["subtitle_speakers"] = mode, speakers
            win.destroy()
            return self._start_subtitles(ready, mode, speakers, wait=wait)

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(12, 0))
        self._tool(bar, "Create" if len(ready) == 1 else f"Create {len(ready)} videos", go, primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        return win, {"mode": v_mode, "speakers": v_spk, "go": go, "jobs": jobs}

    def _start_subtitles(self, jobs: List[Dict], mode: str, speakers: bool, wait: bool = False):
        q = self.msg_queue
        self._append_log(f"=== Video with subtitles ({'burned in' if mode == 'burn' else 'subtitle track'}): {len(jobs)} transcript(s) ===", "STAGE")

        def work():
            done, failed = [], []
            for k, j in enumerate(jobs):
                try:
                    segs = TranscriptDocument.load(j["path"]).kept_segments()
                    prog = lambda f, k=k, j=j: q.put({"type": "subtitles", "event": "progress", "name": j["name"], "k": k, "n": len(jobs), "f": f})
                    res = sublib.write(j["video"], segs, j["dst"], mode, speakers, j.get("language"), on_progress=prog)
                    done.append(str(res["dst"]))
                    q.put({"type": "subtitles", "event": "done", "dst": str(res["dst"]), "cues": res["cues"]})
                except Exception as exc:
                    failed.append(j["name"])
                    q.put({"type": "subtitles", "event": "failed", "name": j["name"], "error": str(exc)})
            q.put({"type": "subtitles", "event": "all_done", "done": done, "failed": failed})

        if wait:
            work()
            return None
        threading.Thread(target=work, daemon=True).start()
        return None

    def _on_subtitles_msg(self, msg: Dict):
        ev = msg["event"]
        if ev == "progress":
            pre = f"{msg['k'] + 1}/{msg['n']} " if msg["n"] > 1 else ""
            self.lbl_status.config(text=f"Subtitles: {pre}{msg['name']} {msg['f'] * 100:.0f} %")
        elif ev == "done":
            self._append_log(f"Video with subtitles: {msg['dst']} ({msg['cues']} subtitles)", "SUCCESS")
        elif ev == "failed":
            self._append_log(f"Video with subtitles failed for {msg['name']}: {msg['error']}", "ERROR")
        elif ev == "all_done":
            done, failed = msg["done"], msg["failed"]
            if done:
                self.lbl_status.config(text=f"Video with subtitles: {Path(done[0]).name}" + (f" and {len(done) - 1} more" if len(done) > 1 else "")
                                       + " — next to the transcript" + (f"; {len(failed)} failed (see Console)" if failed else "."))
            if failed:
                messagebox.showerror("Video with subtitles", f"Could not make: {', '.join(failed)}.\n\nThe Console tab has the details.", parent=self)
