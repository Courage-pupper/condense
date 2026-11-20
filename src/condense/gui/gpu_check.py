"""Help ▸ Test GPU…: can Whisper use the GPU? Also warns before a queue run would transcribe on the CPU."""
from condense.gui._common import *  # noqa: F401,F403


class GpuCheckMixin:
    def _test_gpu(self):
        """Re-run the hardware probe (with a real calculation on the GPU) and show the result."""
        if self.__dict__.get("_gpu_test_running"):
            return
        self._gpu_test_running = True
        self.lbl_status.config(text="Testing the GPU… (the first GPU use can take up to a minute)")

        def work():
            try:
                gpu = SystemProbe.detect_gpu()
                info = SystemProbe.torch_info() if IS_MAC_PLATFORM else None  # Linux/Windows: already inside gpu
            except Exception as exc:
                gpu, info = {"gpu_name": "Unknown", "warning": f"The GPU test could not run: {exc}"}, None
            self.msg_queue.put({"type": "gpu_test", "gpu": gpu, "torch": info})

        threading.Thread(target=work, daemon=True).start()

    def _on_gpu_test(self, msg: Dict):
        self._gpu_test_running = False
        self.gpu_info = msg["gpu"]
        self._update_header()
        ok, text = self._gpu_report(msg["gpu"], msg.get("torch"))
        self.lbl_status.config(text="GPU ready for transcription." if ok else "Whisper cannot use the GPU — see the GPU test.")
        self._append_log(text.splitlines()[0], "SUCCESS" if ok else "WARN")
        self._show_text_window("GPU test", text)

    def _gpu_report(self, g: Dict, mac_torch: Optional[Dict] = None) -> Tuple[bool, str]:
        lines: List[str] = []
        if IS_MAC_PLATFORM:
            ok = bool(g.get("mlx_ok"))
            lines.append("✓ The GPU is ready: Whisper runs on it with MLX." if ok else
                         "✗ MLX Whisper is not available: transcription would run on the CPU (much slower).")
            lines.append("")
            lines.append(f"Chip:            {g.get('gpu_name')}")
            lines.append(f"MLX Whisper:     {'installed' if g.get('mlx_ok') else 'missing'}")
            t = mac_torch or {}
            if t.get("gpu"):
                metal = f"test passed in {t.get('test_ms')} ms" if t.get("works") else f"test failed: {t.get('gpu_error')}"
            else:
                metal = "not available"
            lines.append(f"Metal (speakers): {metal}")
        else:
            ok = bool(g.get("torch_gpu"))
            d = g.get("gpu_test") or {}
            lines.append("✓ The GPU is ready: Whisper and speaker detection will run on it." if ok else
                         "✗ Whisper cannot use the GPU: transcription would run on the CPU (much slower).")
            lines.append("")
            extra = ", ".join(x for x in (d.get("arch"), f"{d['vram_gb']} GB" if d.get("vram_gb") else "") if x)
            lines.append(f"GPU:             {g.get('gpu_name')}" + (f" ({extra})" if extra else ""))
            torch_line = d.get("torch") or "not installed"
            if d.get("api"):
                torch_line += f" · {d['api']} {d.get('api_version', '')}".rstrip()
            lines.append(f"PyTorch:         {torch_line}")
            if "works" in d and d.get("torch"):
                lines.append(f"Test calculation: {'passed in %s ms' % d.get('test_ms') if d.get('works') else 'failed — ' + str(d.get('gpu_error') or 'no GPU')}")
            lines.append(f"openai-whisper:  {'installed' if d.get('whisper') else 'missing'}")
            lines.append(f"Video encoding:  {g.get('media_engine', '—')}")
        lines.append(f"Whisper Python:  {MacAppInstaller.find_best_python()}")
        if g.get("warning"):
            lines += ["", g["warning"]]
        return ok, "\n".join(lines)

    def _show_text_window(self, title: str, text: str):
        T = THEME
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=T["bg_panel"])
        win.transient(self)
        body = tk.Text(win, width=88, height=min(18, text.count("\n") + 3), wrap=tk.WORD, bg=T["bg_input"], fg=T["fg_primary"],
                       font=self.f["mono_small"], relief=tk.FLAT, padx=12, pady=10, highlightthickness=0)
        body.insert("1.0", text)
        body.configure(state=tk.DISABLED)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(14, 8))
        bar = tk.Frame(win, bg=T["bg_panel"])
        bar.pack(fill=tk.X, padx=14, pady=(0, 14))
        self._tool(bar, "Close", win.destroy, primary=True).pack(side=tk.RIGHT)
        self._tool(bar, "Test again", lambda: (win.destroy(), self._test_gpu())).pack(side=tk.RIGHT, padx=(0, 6))
        self._tool(bar, "Copy", lambda: (self.clipboard_clear(), self.clipboard_append(text))).pack(side=tk.LEFT)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[-1])
        return win

    def _gpu_preflight(self, tasks: List[Dict]) -> bool:
        """Before a run: if a task would transcribe on the CPU although a GPU backend is chosen, ask first."""
        whisper = [t for t in tasks if (t.get("steps") or {}).get("whisper")]
        if not whisper:
            return True
        g = self.gpu_info
        backends = {str((t.get("settings") or {}).get("whisper_backend") or DEFAULT_BACKEND) for t in whisper}
        if IS_MAC_PLATFORM:
            cpu_bound = "mlx_ok" in g and not g["mlx_ok"] and any(b.startswith("MLX") for b in backends)
        else:
            cpu_bound = "torch_gpu" in g and not g["torch_gpu"] and any(b.startswith("PyTorch") for b in backends)
        if not cpu_bound:
            return True
        reason = g.get("warning") or "The GPU is not available."
        return bool(messagebox.askyesno(
            "Transcribe on the CPU?",
            f"{reason}\n\nTranscription would run on the CPU: it works, but takes much longer — often longer than the "
            f"recording itself.\n\nRun anyway? (Help ▸ Test GPU shows the details.)",
            icon="warning", parent=self))
