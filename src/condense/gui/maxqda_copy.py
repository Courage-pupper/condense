"""Copy for MAXQDA: an H.264 MP4 of the recording a transcript is timed to (at most 720p — MAXQDA's own advice — or
1080p), for MAXQDA and every player, when the working copy is AV1. Optional; it can always be made again."""
import re
import subprocess
import threading

from condense.gui._common import *  # noqa: F401,F403
from condense.media import compress as complib


def copy_path(out_dir: Path, name: str) -> Path:
    return Path(out_dir) / f"{name}_MAXQDA.mp4"


class MaxqdaCopyMixin:
    def _maxqda_copy_dialog(self, paths: Optional[List[Path]] = None):
        if paths is None:
            if self.active_doc is None:
                self.lbl_status.config(text="Open a transcript first.")
                return None
            paths = [self.active_doc.path]
        jobs = self._subtitle_jobs(paths)   # name, out_dir and the video on each transcript's timeline
        ready = [j for j in jobs if j.get("video")]
        if not ready:
            messagebox.showinfo("Copy for MAXQDA", "No video on the transcript's timeline was found. If it was moved, use "
                                "Playback ▾ ▸ Link a video… first.", parent=self)
            return None
        T = THEME
        win = tk.Toplevel(self)
        win.title("Copy for MAXQDA")
        win.configure(bg=T["bg_panel"], padx=16, pady=14)
        win.transient(self)
        self._label(win, "An H.264 MP4 of the recording, timed like the transcript, saved next to it as <name>_MAXQDA.mp4. "
                         "MAXQDA recommends H.264 and plays AV1 only on some computers; this copy opens everywhere. It can "
                         "be deleted after the analysis and made again at any time.", font="small", fg=T["fg_secondary"],
                    bg=T["bg_panel"], wraplength=540, justify=tk.LEFT).pack(anchor="w")
        lines = []
        for j in jobs:
            if not j.get("video"):
                lines.append(f"✕ {j['name']} — no video on its timeline (skipped)")
            else:
                info = complib.probe(j["video"])
                codec = (info.get("codec") or "?").upper()
                lines.append(f"• {j['name']} — {info['width']}×{info['height']} {codec}, {info['size'] / 1e9:.1f} GB")
        self._label(win, "\n".join(lines[:12]) + (f"\n… and {len(lines) - 12} more" if len(lines) > 12 else ""), font="small",
                    bg=T["bg_panel"], justify=tk.LEFT).pack(anchor="w", pady=(8, 4))
        v_h = tk.IntVar(value=int(self.settings.get("maxqda_copy_height", 720)))
        for value, text in ((720, "720p — what MAXQDA recommends: small, enough for a video window (about 1 GB per hour)"),
                            (1080, "1080p — sharper, about twice the size")):
            tk.Radiobutton(win, text=text, variable=v_h, value=value, bg=T["bg_panel"], fg=T["fg_primary"], selectcolor=T["bg_input"],
                           activebackground=T["bg_panel"], font=self.f["small"], anchor="w", justify=tk.LEFT,
                           wraplength=520).pack(anchor="w", fill=tk.X, pady=(6, 0))

        def go(wait: bool = False):
            height = int(v_h.get())
            for j in ready:
                j["dst"] = copy_path(j["out_dir"], j["name"])
            existing = [j["dst"].name for j in ready if j["dst"].exists()]
            if existing and not messagebox.askyesno("Copy for MAXQDA", f"{len(existing)} copy/copies exist already "
                                                    f"({', '.join(existing[:3])}{' …' if len(existing) > 3 else ''}). Replace them?", parent=win):
                return None
            self.settings["maxqda_copy_height"] = height
            win.destroy()
            return self._start_maxqda_copy(ready, height, wait=wait)

        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, pady=(12, 0))
        self._tool(bar, "Make copy" if len(ready) == 1 else f"Make {len(ready)} copies", go, primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Cancel", win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        return win, {"height": v_h, "go": go, "jobs": jobs}

    def _start_maxqda_copy(self, jobs: List[Dict], height: int, wait: bool = False):
        q = self.msg_queue
        self._append_log(f"=== Copy for MAXQDA (H.264, up to {height}p): {len(jobs)} recording(s) ===", "STAGE")

        def work():
            done, failed = [], []
            for k, j in enumerate(jobs):
                part = Path(str(j["dst"]) + ".part.mp4")
                try:
                    total = complib.probe(j["video"])["duration"] or 0.0
                    cmd = complib.maxqda_command(j["video"], part, max_h=height)
                    self._append_log("Executing: " + " ".join(cmd), "CMD")
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for line in proc.stdout:
                        m = re.match(r"out_time_us=(\d+)", line.strip())
                        if m and total:
                            q.put({"type": "maxqda_copy", "event": "progress", "name": j["name"], "k": k, "n": len(jobs),
                                   "f": min(1.0, int(m.group(1)) / 1e6 / total)})
                    err = proc.stderr.read() if proc.stderr else ""
                    if proc.wait() != 0:
                        raise RuntimeError(" ".join(err.strip().splitlines()[-2:]) or "ffmpeg failed")
                    part.replace(j["dst"])
                    done.append(str(j["dst"]))
                    q.put({"type": "maxqda_copy", "event": "done", "dst": str(j["dst"]), "bytes": j["dst"].stat().st_size})
                except Exception as exc:
                    part.unlink(missing_ok=True)
                    failed.append(j["name"])
                    q.put({"type": "maxqda_copy", "event": "failed", "name": j["name"], "error": str(exc)})
            q.put({"type": "maxqda_copy", "event": "all_done", "done": done, "failed": failed})

        if wait:
            work()
            return None
        threading.Thread(target=work, daemon=True).start()
        return None

    def _on_maxqda_copy_msg(self, msg: Dict):
        ev = msg["event"]
        if ev == "progress":
            pre = f"{msg['k'] + 1}/{msg['n']} " if msg["n"] > 1 else ""
            self.lbl_status.config(text=f"Copy for MAXQDA: {pre}{msg['name']} {msg['f'] * 100:.0f} %")
        elif ev == "done":
            self._append_log(f"Copy for MAXQDA: {msg['dst']} ({msg['bytes'] / 1e9:.2f} GB)", "SUCCESS")
        elif ev == "failed":
            self._append_log(f"Copy for MAXQDA failed for {msg['name']}: {msg['error']}", "ERROR")
        elif ev == "all_done":
            done, failed = msg["done"], msg["failed"]
            if done:
                self.lbl_status.config(text=f"Copy for MAXQDA: {Path(done[0]).name}" + (f" and {len(done) - 1} more" if len(done) > 1 else "")
                                       + " — next to the transcript" + (f"; {len(failed)} failed (see Console)" if failed else "."))
            if failed:
                messagebox.showerror("Copy for MAXQDA", f"Could not make: {', '.join(failed)}.\n\nThe Console tab has the details.", parent=self)
