"""Video preview synced to the player."""
from condense.gui._common import *  # noqa: F401,F403


class VideoMixin:
    # ----- video preview -----
    VIDEO_FPS = 10

    def _build_video_panel(self):
        T = THEME
        self.video_frame = tk.Frame(self.tx_paned, bg="#000000")
        self.video_label = tk.Label(self.video_frame, bg="#000000", fg=T["fg_dim"], font=self.f["small"], text="No video",
                                    justify=tk.CENTER, wraplength=260)
        self.video_label.pack(fill=tk.BOTH, expand=True)
        self.lbl_video_caption = self._label(self.video_frame, "", font="mono_small", fg=T["fg_dim"], bg="#000000")
        self.lbl_video_caption.pack(fill=tk.X)
        self._video_proc: Optional[subprocess.Popen] = None
        self._video_gen = 0
        self._video_q: "queue.Queue" = queue.Queue(maxsize=6)
        self._video_next = None
        self._still_q: "queue.Queue" = queue.Queue()
        self._still_req = 0
        self._video_img = None
        self._video_panel_shown = False
        self.after(40, self._video_pump)
        self.after_idle(self._toggle_video_panel)

    def _toggle_video_panel(self):
        want = bool(self.var_show_video.get())
        if want and not self._video_panel_shown:
            self.tx_paned.add(self.video_frame, minsize=200, width=420, stretch="never")
            self._video_panel_shown = True
            if not self.__dict__.get("playing", False):
                self._video_still(self.__dict__.get("play_pos", 0.0))
        elif not want and self._video_panel_shown:
            self._video_stop()
            self.tx_paned.forget(self.video_frame)
            self._video_panel_shown = False

    def _video_media(self) -> Optional[Path]:
        """A video on the transcript's timeline: the one recorded at transcription time, else (older transcripts)
        the compressed/merged output next to the transcript, else the original if no LosslessCut cuts were applied."""
        doc = self.active_doc
        if doc is None:
            return None
        p = doc.media_path("video")
        if p and p.exists():
            return p
        if "video" in (doc.data.get("media") or {}):
            return None  # recorded as "no video on this timeline"
        for ext in (".mkv", ".mp4", ".mov"):
            for name in (f"{doc.base_name}_compressed{ext}", f"{doc.base_name}{ext}"):
                cand = doc.out_dir / name
                if cand.exists():
                    return cand
        prov = doc.data.get("provenance") or {}
        proc = prov.get("processing") or {}
        inputs = prov.get("inputs") or []
        if len(inputs) == 1 and not proc.get("losslesscut") and not proc.get("merge"):
            cand = Path(inputs[0]["path"])
            if cand.exists():
                return cand
        return None

    def _video_width(self) -> int:
        w = self.video_label.winfo_width()
        w = w if w > 40 else 420
        return max(160, min(1280, w)) // 2 * 2

    def _video_ffmpeg(self, media: Path, pos: float, single: bool) -> List[str]:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if IS_MAC:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += ["-ss", f"{max(0.0, pos):.3f}", "-i", str(media), "-an", "-sn"]
        scale = f"scale='min({self._video_width()},iw)':-2"  # never upscale small videos
        vf = scale if single else f"fps={self.VIDEO_FPS},{scale}"
        cmd += ["-vf", vf] + (["-frames:v", "1"] if single else []) + ["-f", "image2pipe", "-vcodec", "ppm", "-"]
        return cmd

    def _video_start(self, pos: float):
        self._video_stop()
        media = self._video_media()
        if media is None or not self._video_panel_shown or not shutil.which("ffmpeg"):
            return
        self._video_gen += 1
        gen = self._video_gen
        self._video_q = queue.Queue(maxsize=6)
        q = self._video_q
        try:
            proc = subprocess.Popen(self._video_ffmpeg(media, pos, single=False), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception:
            return
        self._video_proc = proc

        def reader():
            i = 0
            while self._video_gen == gen:
                frame = read_ppm_frame(proc.stdout)
                if frame is None:
                    break
                item = (gen, pos + i / self.VIDEO_FPS, frame)
                i += 1
                while self._video_gen == gen:  # bounded queue = ffmpeg is paced by playback
                    try:
                        q.put(item, timeout=0.2)
                        break
                    except queue.Full:
                        continue

        threading.Thread(target=reader, daemon=True).start()

    def _video_stop(self):
        self._video_gen = self.__dict__.get("_video_gen", 0) + 1
        proc = self.__dict__.get("_video_proc")
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._video_proc = None
        self._video_next = None

    def _video_still(self, pos: float):
        if not self.__dict__.get("_video_panel_shown"):
            return
        media = self._video_media()
        if media is None:
            self._show_no_video()
            return
        self._still_req += 1
        req = self._still_req
        cmd = self._video_ffmpeg(media, pos, single=True)

        def grab():
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=20)
                import io
                frame = read_ppm_frame(io.BytesIO(res.stdout))
            except Exception:
                frame = None
            if frame is not None:
                self._still_q.put((req, pos, frame))

        threading.Thread(target=grab, daemon=True).start()

    def _show_no_video(self):
        self._video_img = None
        self.video_label.configure(image="", text="No video on this transcript's timeline.\n\nAudio-only, or LosslessCut-trimmed without the "
                                                   "Compress step (the trimmed file isn't kept).")
        self.lbl_video_caption.configure(text="")

    def _show_frame(self, pos: float, frame: Tuple[int, int, bytes]):
        w, h, data = frame
        try:
            img = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (w, h) + data, format="ppm")
        except Exception:
            return
        self._video_img = img  # keep a reference, or Tk drops the image
        self.video_label.configure(image=img, text="")
        self.lbl_video_caption.configure(text=f"{short_ts(pos)[:-2]}  ·  {self._video_media().name if self._video_media() else ''}")

    def _video_pump(self):
        try:
            if self._video_proc is not None and self.playing:
                now = self._current_pos()
                latest = None
                while True:
                    item = self._video_next
                    self._video_next = None
                    if item is None:
                        try:
                            item = self._video_q.get_nowait()
                        except queue.Empty:
                            break
                    if item[0] != self._video_gen:
                        continue
                    if item[1] <= now + 0.05:
                        latest = item
                    else:
                        self._video_next = item  # not due yet
                        break
                if latest is not None:
                    self._show_frame(latest[1], latest[2])
            while True:
                try:
                    req, pos, frame = self._still_q.get_nowait()
                except queue.Empty:
                    break
                if req == self._still_req and not (self.playing and self._video_proc is not None):
                    self._show_frame(pos, frame)
        finally:
            self.after(40, self._video_pump)

    def _open_externally(self, path) -> bool:
        """Open a file or folder with the desktop's default app (Finder / Files / the video player)."""
        try:
            subprocess.Popen(open_command(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as exc:
            self._append_log(f"Could not open {path}: {exc}", "ERROR")
            return False

    def _open_in_quicktime(self):
        """macOS: open the video in QuickTime at the current position. Linux: open it in the default video player."""
        media = self._video_media() or (self._media() if self.active_doc else None)
        if media is None:
            self.lbl_status.config(text="No video for this transcript.")
            return
        pos = self._current_pos()
        self._player_pause()
        if not IS_MAC_PLATFORM:
            if self._open_externally(media):
                self.lbl_status.config(text=f"Opened in your video player — jump to {short_ts(pos)[:-2]} there.")
            return
        path = str(media).replace("\\", "\\\\").replace('"', '\\"')
        script = (f'tell application "QuickTime Player"\nactivate\nset d to open (POSIX file "{path}")\n'
                  f"set current time of d to {pos:.2f}\nend tell")
        try:
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self._append_log(f"Could not open QuickTime: {exc}", "ERROR")

    def _apply_speaker_mapping(self, mapping: Dict[str, str]):
        doc = self.active_doc
        if doc is None:
            return
        count = doc.rename_speakers(mapping)
        if not count:
            return
        pairs = ", ".join(f"{o} → {n}" for o, n in mapping.items())
        if self._commit(f"Renamed {pairs}.") is not None:
            self._rerender()
            if self.edit_seg_idx is not None:
                self._load_segment_into_editor(self.edit_seg_idx)
            self._append_log(f"Renamed {pairs} ({count} segments); transcript and exports updated.", "SUCCESS")
