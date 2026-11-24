"""Video preview synced to the player, and linking a video to a transcript afterwards (without re-encoding)."""
from condense.core import videolink
from condense.gui._common import *  # noqa: F401,F403
from condense.media import trim as trimlib


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
        self.btn_link_video = self._tool(self.video_frame, "Link a video…", self._link_video_dialog)
        self._link_btn_shown = False
        self._video_seg_end: Optional[float] = None  # transcript time where the linked video's current piece ends
        self._video_linking = False
        self._video_proc: Optional[subprocess.Popen] = None
        self._video_gen = 0
        self._video_q: "queue.Queue" = queue.Queue(maxsize=6)
        self._video_next = None
        self._still_q: "queue.Queue" = queue.Queue()
        self._still_req = 0
        self._still_busy = False      # one frame grab at a time …
        self._still_pending = None    # … and only the newest request waits (holding ↓ or scrolling fast)
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
        """A video for the open transcript (see TranscriptDocument.find_video)."""
        doc = self.active_doc
        return doc.find_video() if doc is not None else None

    def _video_pieces(self) -> Optional[List[List[float]]]:
        doc = self.active_doc
        return doc.video_pieces() if doc is not None and doc.media_path("video") else None

    def _video_on_timeline(self) -> Optional[Path]:
        """The video only if its time is the transcript's time (what a trimmed export may cut)."""
        media = self._video_media()
        return media if media is not None and videolink.is_same_timeline(self._video_pieces()) else None

    def _video_map(self, pos: float) -> Tuple[Optional[float], Optional[float]]:
        """(position in the video for transcript time pos or None, transcript time where that changes or None)."""
        return videolink.to_video_time(self._video_pieces(), pos)

    def _video_width(self) -> int:
        w = self.video_label.winfo_width()
        w = w if w > 40 else 420
        return max(160, min(1280, w)) // 2 * 2

    def _video_ffmpeg(self, media: Path, pos: float, single: bool, duration: Optional[float] = None) -> List[str]:
        """pos: position in the video file (already mapped from transcript time). duration: stop after that long."""
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if IS_MAC:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += ["-ss", f"{max(0.0, pos):.3f}", "-i", str(media), "-an", "-sn"]
        if duration is not None and not single:
            cmd += ["-t", f"{max(0.05, duration):.3f}"]
        scale = f"scale='min({self._video_width()},iw)':-2"  # never upscale small videos
        vf = scale if single else f"fps={self.VIDEO_FPS},{scale}"
        cmd += ["-vf", vf] + (["-frames:v", "1"] if single else []) + ["-f", "image2pipe", "-vcodec", "ppm", "-"]
        return cmd

    def _video_start(self, pos: float):
        self._video_stop()
        media = self._video_media()
        if media is None or not self._video_panel_shown or not shutil.which("ffmpeg"):
            return
        vpos, change_at = self._video_map(pos)
        self._video_seg_end = change_at  # the pump restarts the stream here (next piece of a linked video)
        if vpos is None:
            self._show_no_picture_here()
            return
        self._video_gen += 1
        gen = self._video_gen
        self._video_q = queue.Queue(maxsize=6)
        q = self._video_q
        duration = (change_at - pos) if change_at is not None else None
        try:
            proc = subprocess.Popen(self._video_ffmpeg(media, vpos, single=False, duration=duration),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        self._video_seg_end = None

    def _video_still(self, pos: float):
        if not self.__dict__.get("_video_panel_shown"):
            return
        media = self._video_media()
        if media is None:
            self._show_no_video()
            return
        vpos, _ = self._video_map(pos)
        self._still_req += 1
        req = self._still_req
        if vpos is None:
            self._show_no_picture_here()
            return
        cmd = self._video_ffmpeg(media, vpos, single=True)
        if self.__dict__.get("_still_busy"):
            self._still_pending = (req, pos, cmd)  # replaces any older waiting request
            return
        self._still_grab(req, pos, cmd)

    def _still_grab(self, req: int, pos: float, cmd: List[str]):
        self._still_busy = True

        def grab():
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=20)
                import io
                frame = read_ppm_frame(io.BytesIO(res.stdout))
            except Exception:
                frame = None
            self._still_q.put((req, pos, frame))  # also when it failed: the pump starts the next one

        threading.Thread(target=grab, daemon=True).start()

    def _set_link_button(self, shown: bool):
        if shown and not self._link_btn_shown:
            self.btn_link_video.pack(pady=(0, 10))
        elif not shown and self._link_btn_shown:
            self.btn_link_video.pack_forget()
        self._link_btn_shown = shown

    def _show_no_video(self):
        self._video_img = None
        doc = self.active_doc
        recorded = doc.media_path("video") if doc is not None else None
        if recorded is not None and not recorded.exists():
            why = f"The video {recorded.name} was moved or deleted."
        else:
            why = "No video on this transcript's timeline (audio only, or trimmed without the Compress step)."
        self.video_label.configure(image="", text=f"{why}\n\nLink the recording's video to watch it with the transcript — "
                                                   "nothing is re-encoded; Condense lines it up by its sound.")
        self.lbl_video_caption.configure(text="")
        self._set_link_button(doc is not None)

    def _show_no_picture_here(self):
        """A linked video that has no picture for this moment (cut out before transcribing, or outside the video)."""
        self._video_img = None
        self.video_label.configure(image="", text="The linked video has no picture for this moment.")
        self.lbl_video_caption.configure(text="")
        self._set_link_button(False)

    def _show_frame(self, pos: float, frame: Tuple[int, int, bytes]):
        w, h, data = frame
        try:
            img = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (w, h) + data, format="ppm")
        except Exception:
            return
        self._video_img = img  # keep a reference, or Tk drops the image
        self.video_label.configure(image=img, text="")
        self._set_link_button(False)
        self.lbl_video_caption.configure(text=f"{short_ts(pos)[:-2]}  ·  {self._video_media().name if self._video_media() else ''}")

    def _video_pump(self):
        try:
            if self.playing and self._video_seg_end is not None and self._current_pos() >= self._video_seg_end - 0.02:
                self._video_start(self._current_pos())  # next piece of a linked video (or a gap without picture)
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
                self._still_busy = False
                if frame is not None and req == self._still_req and not (self.playing and self._video_proc is not None):
                    self._show_frame(pos, frame)
            pending = self.__dict__.get("_still_pending")
            if pending is not None and not self._still_busy:
                self._still_pending = None
                if pending[0] == self._still_req:
                    self._still_grab(*pending)
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
        media = self._video_media()
        pos = self._current_pos()
        if media is not None:
            vpos, _ = self._video_map(pos)
            if vpos is None:  # no picture for this moment: open the transcript's own audio instead
                media = self._media()
            else:
                pos = vpos
        else:
            media = self._media() if self.active_doc else None
        if media is None:
            self.lbl_status.config(text="No video for this transcript.")
            return
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

    # ----- linking a video afterwards -----
    def _link_video_start_dir(self) -> str:
        doc = self.active_doc
        recorded = doc.media_path("video")
        if recorded is not None and recorded.parent.is_dir():
            return str(recorded.parent)
        for inp in (doc.data.get("provenance") or {}).get("inputs") or []:
            parent = Path(inp.get("path", "")).parent
            if parent.is_dir():
                return str(parent)
        return str(doc.out_dir)

    def _link_video_dialog(self, path: Optional[str] = None, wait: bool = False):
        """Pick a video and line it up with the transcript by its sound: the original recording of a LosslessCut-trimmed
        transcript, a moved or renamed video, or a separate camera's. Nothing is re-encoded."""
        doc = self.active_doc
        if doc is None:
            self.lbl_status.config(text="Open a transcript first.")
            return
        if self._video_linking:
            return
        if path is None:
            path = self._ask_open_file("Video for this transcript", self._link_video_start_dir(), MEDIA_FILETYPES)
            if not path:
                return
        video = Path(path)
        try:
            info = trimlib.probe(video)
        except Exception:
            info = {"video": False, "duration": 0.0}
        if not info["video"]:
            messagebox.showerror("Link a video", f"{video.name} has no picture, or could not be read.", parent=self)
            return
        audio = self._media()
        if audio is None:
            if messagebox.askyesno("Link a video", "The transcript's own audio was not found, so Condense cannot check by the "
                                   "sound where this video belongs.\n\nLink it anyway, starting together with the transcript?",
                                   parent=self):
                self._apply_video_link(video, None)
            return
        try:
            transcript_dur = trimlib.probe(audio)["duration"] or self._duration()
        except Exception:
            transcript_dur = self._duration()
        result: Dict = {}

        def work():
            try:
                result["link"] = videolink.find_link(audio, video, transcript_dur, info["duration"], doc.data.get("provenance"),
                                                     cache_dir=APP_SUPPORT_DIR / "cache" / "audio")
            except Exception as exc:
                result["error"] = str(exc)

        def finish():
            self._video_linking = False
            if self.active_doc is not doc:
                self.lbl_status.config(text="Linking stopped: another transcript was opened. Link the video again there.")
                return
            if "error" in result:
                messagebox.showerror("Link a video", f"Could not compare {video.name} with the transcript:\n\n{result['error']}", parent=self)
                self.lbl_status.config(text="")
                return
            link = result["link"]
            if link["confident"]:
                self._apply_video_link(video, link)
                return
            if messagebox.askyesno("Link a video", f"Condense could not find this transcript's sound in {video.name} "
                                   f"(best match {max(0.0, link['score']):.0%}).\n\nIs it the right recording? Link it anyway, "
                                   "starting together with the transcript?", parent=self):
                self._apply_video_link(video, None)
            else:
                self.lbl_status.config(text="No video linked.")

        self._video_linking = True
        self.lbl_status.config(text=f"Lining up {video.name} with the transcript by its sound (a few seconds per hour)…")
        if wait:
            work()
            finish()
            return

        def poll():
            if "link" in result or "error" in result:
                finish()
            else:
                self.after(200, poll)

        threading.Thread(target=work, daemon=True).start()
        self.after(200, poll)

    @staticmethod
    def _describe_video_link(link: Optional[Dict]) -> str:
        pieces = (link or {}).get("pieces")
        if videolink.is_same_timeline(pieces):
            return "on the transcript's timeline"
        if len(pieces) > 1:
            return f"in {len(pieces)} pieces (the parts kept in LosslessCut)"
        ts, vs, _ = pieces[0]
        shift = vs - ts
        return (f"the video starts {short_ts(abs(shift))[:-2]} {'before' if shift > 0 else 'after'} the transcript")

    def _apply_video_link(self, video: Path, link: Optional[Dict]):
        doc = self.active_doc
        doc.link_video(str(video), link)
        how = self._describe_video_link(link)
        if self._commit(f"Linked the video {video.name} ({how}).") is None:
            return
        self._append_log(f"Linked {video} to {doc.path.name}: {how}" + (f"; {link['method']}, match {link['score']:.0%}" if link else "")
                         + ". The video was not changed.", "SUCCESS")
        self._refresh_video()

    def _unlink_video(self):
        doc = self.active_doc
        if doc is None or doc.media_path("video") is None:
            self.lbl_status.config(text="No video is linked to this transcript.")
            return
        name = doc.media_path("video").name
        doc.link_video(None)
        if self._commit(f"Unlinked the video {name} (the file is not deleted).") is not None:
            self._refresh_video()

    def _refresh_video(self):
        self._video_stop()
        if self.__dict__.get("playing", False):
            self._video_start(self._current_pos())
        else:
            self._video_still(self.__dict__.get("play_pos", 0.0))

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
