"""Trim before transcribing: mark parts of a video to leave out (setup, breaks …) on a filmstrip + waveform.

The cuts are saved as a LosslessCut project next to the video (<video>-proj.llc): Condense applies it before
transcribing (Settings: "Apply LosslessCut (.llc) trims"), and LosslessCut opens the same cuts.
"""
from condense.core import cuts as cutlib
from condense.gui._common import *  # noqa: F401,F403
from condense.media import trim as trimlib
from condense.media.losslesscut import LosslessCutHelper

STRIP_W, STRIP_H, WAVE_H = 900, 56, 64


def llc_cuts(video: Path, duration: float) -> List[Tuple[float, float]]:
    """Cuts described by an existing LosslessCut project (which lists the parts to keep)."""
    llc = LosslessCutHelper.find_llc_for_video(video)
    if not llc:
        return []
    keep = LosslessCutHelper.parse_llc_segments(llc)
    return cutlib.keep_intervals(keep, duration) if keep else []


def describe_trim(video: Path) -> str:
    llc = LosslessCutHelper.find_llc_for_video(video)
    if not llc:
        return "none"
    keep = LosslessCutHelper.parse_llc_segments(llc)
    if not keep:
        return f"{llc.name} (no segments)"
    kept = sum(b - a for a, b in keep)
    return f"{len(keep)} part(s) kept, {short_ts(kept)[:-2]} in total ({llc.name})"


class TrimDialogMixin:
    def _trim_task_video(self):
        task = self._editable_task()
        if task is None:
            return
        sel = self.lst_inputs.curselection()
        idx = sel[0] if sel else 0
        self._trim_dialog(Path(task["inputs"][idx]), on_saved=lambda: self._show_task_details(task))

    def _trim_dialog(self, video: Path, on_saved=None):
        T = THEME
        info = trimlib.probe(video)
        dur = info["duration"]
        if dur <= 0:
            messagebox.showerror("Trim", f"Could not read {video.name}.", parent=self)
            return None
        st = {"cuts": llc_cuts(video, dur), "pos": 0.0, "start": None, "imgs": {}, "proc": None}
        win = tk.Toplevel(self)
        win.title(f"Trim before transcribing — {video.name}")
        win.configure(bg=T["bg_panel"], padx=14, pady=12)
        win.transient(self)
        self._label(win, "Mark the parts to leave out (setup, breaks …). They are skipped before transcribing.", font="small",
                    fg=T["fg_secondary"], bg=T["bg_panel"]).pack(anchor="w")
        respect = self.__dict__.get("var_respect_llc")
        if respect is not None and not respect.get():
            self._label(win, "⚠ 'Apply LosslessCut (.llc) trims' is off in Settings — turn it on, or these cuts are ignored.",
                        font="small", fg=T["warn"], bg=T["bg_panel"]).pack(anchor="w")
        canvas = tk.Canvas(win, width=STRIP_W, height=STRIP_H + WAVE_H + 18, bg=T["bg_input"], highlightthickness=0, cursor="hand2")
        canvas.pack(pady=(8, 6))
        mid = tk.Frame(win, bg=T["bg_panel"])
        mid.pack(fill=tk.X)
        still = tk.Label(mid, bg="#000000", width=320, height=180)
        still.pack(side=tk.LEFT)
        right = tk.Frame(mid, bg=T["bg_panel"], padx=12)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lbl_pos = self._label(right, "", font="mono", bg=T["bg_panel"])
        lbl_pos.pack(anchor="w")
        lst = tk.Listbox(right, height=6, bg=T["bg_input"], fg=T["fg_primary"], font=self.f["mono_small"], relief=tk.FLAT,
                         highlightthickness=1, highlightbackground=T["border"], exportselection=False)
        lst.pack(fill=tk.BOTH, expand=True, pady=(6, 4))
        hint = self._label(right, "Click the strip · ←/→ 1 s (⇧ 10 s) · Space plays 5 s · I = cut starts · O = cut ends",
                           font="small", fg=T["fg_dim"], bg=T["bg_panel"])
        hint.pack(anchor="w")
        btns = tk.Frame(win, bg=T["bg_panel"])
        btns.pack(fill=tk.X, pady=(10, 0))
        st["win"], st["canvas"], st["still"], st["lst"], st["lbl_pos"] = win, canvas, still, lst, lbl_pos

        def x_of(t):
            return t / dur * STRIP_W

        def redraw():
            canvas.delete("all")
            if "strip" in st["imgs"]:
                canvas.create_image(0, 0, image=st["imgs"]["strip"], anchor="nw")
            if "wave" in st["imgs"]:
                canvas.create_image(0, STRIP_H + 2, image=st["imgs"]["wave"], anchor="nw")
            for a, b in st["cuts"]:
                canvas.create_rectangle(x_of(a), 0, max(x_of(a) + 2, x_of(b)), STRIP_H + WAVE_H + 2, fill="#000000",
                                        stipple="gray50", outline="#f59e0b")
            if st["start"] is not None:
                canvas.create_line(x_of(st["start"]), 0, x_of(st["start"]), STRIP_H + WAVE_H + 18, fill="#f59e0b", width=2, dash=(4, 2))
            x = x_of(st["pos"])
            canvas.create_line(x, 0, x, STRIP_H + WAVE_H + 18, fill="#ffffff", width=2)
            for i in range(0, 11):
                t = dur * i / 10
                canvas.create_text(min(STRIP_W - 24, max(20, x_of(t))), STRIP_H + WAVE_H + 10, text=short_ts(t)[:-2],
                                   fill=T["fg_dim"], font=self.f["small"])
            lst.delete(0, tk.END)
            for a, b in st["cuts"]:
                lst.insert(tk.END, f"✂ {short_ts(a)[:-2]} – {short_ts(b)[:-2]}  ({b - a:.0f} s)")
            kept = sum(b - a for a, b in cutlib.keep_intervals(st["cuts"], dur))
            lbl_pos.config(text=f"{short_ts(st['pos'])[:-2]}  /  {short_ts(dur)[:-2]}    kept: {short_ts(kept)[:-2]}"
                                + (f"    cut starts at {short_ts(st['start'])[:-2]} …" if st["start"] is not None else ""))

        def show_still():
            if not info["video"]:
                return
            pos = st["pos"]

            def work():
                try:
                    res = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{pos:.3f}", "-i", str(video),
                                          "-frames:v", "1", "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
                                          "-f", "image2pipe", "-vcodec", "ppm", "-"], capture_output=True, timeout=20)
                    if res.returncode == 0 and res.stdout:
                        self.msg_queue.put({"type": "trim_ui", "kind": "still", "data": res.stdout, "st": st})
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        def set_pos(t):
            st["pos"] = max(0.0, min(dur, t))
            redraw()
            show_still()

        def click(event):
            set_pos(event.x / STRIP_W * dur)

        def play5():
            stop_play()
            cmd = ["ffplay", "-hide_banner", "-loglevel", "error", "-autoexit", "-ss", f"{st['pos']:.2f}", "-t", "5"]
            cmd += (["-x", "480", "-y", "270", "-window_title", "Condense preview"] if info["video"] else ["-nodisp"])
            try:
                st["proc"] = subprocess.Popen(cmd + [str(video)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self.lbl_status.config(text="ffplay not found: install ffmpeg (with ffplay) to listen here.")

        def stop_play():
            if st["proc"] is not None and st["proc"].poll() is None:
                st["proc"].terminate()
            st["proc"] = None

        def mark_start():
            st["start"] = st["pos"]
            redraw()

        def mark_end():
            if st["start"] is None:
                self.lbl_status.config(text="Set where the cut starts first (I).")
                return
            a, b = sorted((st["start"], st["pos"]))
            if b - a >= 0.2:
                st["cuts"] = cutlib.normalize(st["cuts"] + [(a, b)])
            st["start"] = None
            redraw()

        def remove_selected():
            sel = lst.curselection()
            if sel:
                cuts = list(st["cuts"])
                del cuts[sel[0]]
                st["cuts"] = cuts
                redraw()

        def save():
            stop_play()
            target = video.parent / f"{video.stem}-proj.llc"
            existing = LosslessCutHelper.find_llc_for_video(video)
            ours = existing is not None and existing.read_text(encoding="utf-8", errors="ignore").startswith(cutlib.LLC_MARKER)
            if not st["cuts"]:
                if existing is not None and ours:
                    existing.unlink()
                    self._append_log(f"Removed the trim of {video.name}.", "INFO")
                elif existing is not None:
                    messagebox.showinfo("Trim", f"{existing.name} was made in LosslessCut; Condense leaves it as it is. "
                                                "Delete it yourself to transcribe the whole video.", parent=win)
                    return
                win.destroy()
                on_saved and on_saved()
                return
            if existing is not None and not ours and not messagebox.askyesno(
                    "Trim", f"{existing.name} (made in LosslessCut) will be replaced by these cuts. Continue?", parent=win):
                return
            if existing is not None and existing != target:
                existing.unlink()
            target.write_text(cutlib.llc_text(video.name, cutlib.keep_intervals(st["cuts"], dur)), encoding="utf-8")
            self._append_log(f"Trim saved for {video.name}: {cutlib.describe(st['cuts'])} left out before transcribing ({target.name}).", "INFO")
            win.destroy()
            on_saved and on_saved()

        def close():
            stop_play()
            win.destroy()

        for text, cmd in (("▶ Play 5 s", play5), ("Cut starts here (I)", mark_start), ("Cut ends here (O)", mark_end),
                          ("Remove selected cut", remove_selected)):
            self._tool(btns, text, cmd).pack(side=tk.LEFT, padx=(0, 6))
        self._tool(btns, "Save", save, primary=True).pack(side=tk.RIGHT)
        self._tool(btns, "Cancel", close).pack(side=tk.RIGHT, padx=(0, 6))
        canvas.bind("<Button-1>", click)
        canvas.bind("<B1-Motion>", click)
        win.bind("<Left>", lambda e: set_pos(st["pos"] - 1))
        win.bind("<Right>", lambda e: set_pos(st["pos"] + 1))
        win.bind("<Shift-Left>", lambda e: set_pos(st["pos"] - 10))
        win.bind("<Shift-Right>", lambda e: set_pos(st["pos"] + 10))
        win.bind("<space>", lambda e: (play5(), "break")[-1])
        for k in ("i", "I"):
            win.bind(f"<Key-{k}>", lambda e: mark_start())
        for k in ("o", "O"):
            win.bind(f"<Key-{k}>", lambda e: mark_end())
        win.bind("<Escape>", lambda e: (close(), "break")[-1])
        win.protocol("WM_DELETE_WINDOW", close)

        def make_images():
            tmp = Path(tempfile.mkdtemp(prefix="condense_trim_"))
            try:
                wave = tmp / "wave.png"
                if info["audio"]:
                    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-filter_complex",
                                    f"aformat=channel_layouts=mono,showwavespic=s={STRIP_W}x{WAVE_H}:colors=0x5b8def",
                                    "-frames:v", "1", str(wave)], timeout=300)
                strip = tmp / "strip.png"
                if info["video"]:
                    n = 12
                    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vf",
                                    f"fps={n}/{max(dur, 1):.3f},scale={STRIP_W // n}:{STRIP_H}:force_original_aspect_ratio=decrease,"
                                    f"pad={STRIP_W // n}:{STRIP_H}:(ow-iw)/2:(oh-ih)/2,tile={n}x1",
                                    "-frames:v", "1", str(strip)], timeout=300)
                self.msg_queue.put({"type": "trim_ui", "kind": "images", "wave": str(wave) if wave.exists() else None,
                                    "strip": str(strip) if strip.exists() else None, "st": st, "tmp": str(tmp)})
            except Exception:
                pass

        threading.Thread(target=make_images, daemon=True).start()
        st.update(redraw=redraw, set_pos=set_pos, mark_start=mark_start, mark_end=mark_end, remove=remove_selected, save=save,
                  close=close, play5=play5)
        set_pos(0.0)
        return win, st

    def _on_trim_ui_msg(self, msg: Dict):
        st = msg["st"]
        try:
            if not st["win"].winfo_exists():
                return
        except Exception:
            return
        if msg["kind"] == "still":
            data = msg["data"]
            try:
                st["imgs"]["still"] = tk.PhotoImage(data=data, format="ppm")
                st["still"].configure(image=st["imgs"]["still"])
            except Exception:
                pass
        elif msg["kind"] == "images":
            for key in ("strip", "wave"):
                if msg.get(key):
                    try:
                        st["imgs"][key] = tk.PhotoImage(file=msg[key])
                    except Exception:
                        pass
            shutil.rmtree(msg["tmp"], ignore_errors=True)
            st["redraw"]()
