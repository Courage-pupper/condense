import os
import io, json, queue, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
os.environ["CONDENSE_PLATFORM"] = "linux"  # before Condense is imported
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
HOME = Path(os.environ["HOME"])
FAKES = Path(os.environ["CONDENSE_FAKES"])
os.environ["PATH"] = str(FAKES / "linux_bin") + os.pathsep + os.environ["PATH"]
os.environ["PYTHONPATH"] = str(FAKES / "torch_gpu")  # the Whisper Python sees a ROCm PyTorch with one GPU
from _compat import pipe
import condense.platform as plat
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

print("[1] Linux layer: paths, defaults, opener")
assert plat.PLATFORM == "linux" and plat.APP_SUPPORT_DIR == HOME / ".config" / "condense"
assert plat.log_file() == HOME / ".local" / "state" / "condense" / "condense.log"
assert plat.default_output_dir() == HOME / "Videos" / "Condense"
assert plat.open_command("/x/y") == ["xdg-open", "/x/y"]
assert plat.DEFAULT_BACKEND.startswith("PyTorch GPU") and plat.DEFAULT_ENCODER.startswith("vaapi_h265")
assert plat.SecretStore is plat.SecretServiceSecret and plat.AppInstaller is plat.LinuxDesktopInstaller
assert pipe.SettingsStore.PATH == HOME / ".config" / "condense" / "settings.json"
ok("~/.config/condense, ~/.local/state log, ~/Videos/Condense, xdg-open, VA-API + PyTorch-GPU defaults")

print("[2] hardware probe through the Whisper Python")
info = plat.SystemProbe.torch_info()
assert info["gpu"] and info["api"] == "ROCm" and info["name"] == "AMD Radeon RX 7900 XTX" and info["whisper"], info
g = plat.SystemProbe.detect_gpu()
assert g["gpu_name"] == "AMD Radeon RX 7900 XTX" and g["torch_gpu"] and g["gpu_api"] == "ROCm" and not g["warning"], g
print("    ", g["gpu_name"], "|", g["accel_status"], "|", g["media_engine"])
ok("PyTorch ROCm build and GPU name detected")

print("[3] Whisper on the GPU via PyTorch; CPU backend stays on the CPU")
base = {"respect_llc": True, "audio_format": "WAV (16kHz Mono - Optimal for Whisper)", "whisper_model": "small",
        "whisper_lang": "en (English)", "diarize_mode": "Disabled (No Speaker Labels)", "fmt_srt": True}
logs = {}
for name, backend in (("P02", plat.DEFAULT_BACKEND), ("P03_48k", "openai-whisper (CPU)")):
    shutil.rmtree(media / name, ignore_errors=True)
    task = {"id": name, "name": name, "inputs": [str(media / f"{name}.mp4")], "steps": {"whisper": True}, "output_dir": str(media / name),
            "settings": dict(base, whisper_backend=backend)}
    q = queue.Queue()
    pipe.PipelineWorker({"tasks": [task]}, q, threading.Event()).run()
    logs[name] = "\n".join(m.get("text", "") for m in list(q.queue) if m.get("type") == "log")
gpu = TD.load(media / "P02" / "P02.transcript.json")
cpu = TD.load(media / "P03_48k" / "P03_48k.transcript.json")
assert "device=cuda fp16=True" in logs["P02"], logs["P02"][-2000:]
assert "amdgpu.ids" not in logs["P02"]  # harmless libdrm message kept out of the Console
assert gpu.data["provenance"]["whisper"]["backend_used"] == "openai-whisper on ROCm GPU (AMD Radeon RX 7900 XTX)", gpu.data["provenance"]["whisper"]
assert "device=cpu fp16=False" in logs["P03_48k"] and cpu.data["provenance"]["whisper"]["backend_used"] == "openai-whisper on CPU"
# provenance describes the real machine (this scenario may run on a Mac with CONDENSE_PLATFORM=linux)
assert gpu.data["provenance"]["system"]["os"] and (gpu.data["provenance"]["system"]["macos"] is None) == (sys.platform != "darwin")
assert [s["text"].strip() for s in gpu.segments] == ["Hello there.", "Pick a colour."]
ok("PyTorch backend → cuda device + fp16 (ROCm GPU named in provenance); CPU backend → cpu, fp32")

print("[4] video: VA-API command, CPU fallback through HandBrake without a render node")
cfg = {"hb_encoder": "vaapi_h265 (GPU via VA-API, no HandBrake)", "hb_quality": 22, "hb_resolution": "1080p (FHD)", "hb_fps": "Same as source",
       "hb_audio_bitrate": "192", "hb_container": "MKV"}
w = pipe.PipelineWorker(cfg, queue.Queue(), threading.Event())
cmd, codec, qv = w.build_hw_encode_command("vaapi", Path("in.mp4"), Path("out.mkv"), "vaapi_h265")
assert codec == "hevc_vaapi" and qv == "22" and cmd[cmd.index("-vaapi_device") + 1].startswith("/dev/dri/renderD")
assert "-qp" in cmd and "format=nv12,hwupload,scale_vaapi=" in cmd[cmd.index("-vf") + 1] and cmd.index("-vaapi_device") < cmd.index("-i")
_, codec, qv = w.build_hw_encode_command("vaapi", Path("in.mp4"), Path("out.mkv"), "vaapi_av1")
assert codec == "av1_vaapi" and qv == "88"
os.environ["CONDENSE_VAAPI_DEVICE"] = "/nonexistent/renderD999"  # behave like a machine without a VA-API GPU
q = queue.Queue()
w = pipe.PipelineWorker(cfg, q, threading.Event())
out = Path(tempfile.mkdtemp()) / "P02_compressed.mkv"
w.run_handbrake_encode_for_jobs(media / "P02.mp4", out, "Compress", 0.0, 50.0, [])
hb_args = (HOME / ".fake_handbrake_args").read_text().split("\n")
assert out.exists() and hb_args[hb_args.index("-e") + 1] == "x265" and hb_args[hb_args.index("-q") + 1] == "22"
assert any("No VA-API encoder available" in m.get("text", "") for m in list(q.queue)) and w.last_encode["encoder"] == "x265"
ok("hevc_vaapi CQP 22 (AV1 index 88) with hwupload/scale_vaapi; no render node → HandBrake x265 RF 22 with a warning")

print("[5] token in the keyring via secret-tool (stdin only)")
S = plat.SecretServiceSecret
assert S.available() and S.load() == ""
assert S.save("hf_LinuxToken42") and S.load() == "hf_LinuxToken42"
assert "hf_LinuxToken42" not in (HOME / ".fake_secret_tool_argv").read_text()
assert not S.save("bad token; rm -rf")
S.delete()
assert S.load() == ""
ok("saved, read back, never on a command line, rejected odd characters, deleted")

print("[6] app-menu entry")
info = plat.LinuxDesktopInstaller.install_shortcut()
desktop = Path(info["app"]).read_text()
launcher = HOME / ".local" / "share" / "condense" / "launcher.sh"
assert Path(info["app"]) == HOME / ".local" / "share" / "applications" / "condense.desktop"
assert f"Exec={launcher}" in desktop and "Icon=" in desktop and "Terminal=false" in desktop
assert os.access(launcher, os.X_OK) and sys.executable in launcher.read_text() and "-m condense" in launcher.read_text()
assert (HOME / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "condense.svg").exists()
ok(".desktop entry + icon + launcher that runs this Python")

print("[7] GUI on Linux")
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
assert app.var_hb_encoder.get() == plat.DEFAULT_ENCODER and app.var_whisper_backend.get() == plat.DEFAULT_BACKEND
assert app.var_diarize_mode.get().startswith("PyTorch GPU") and app.var_output_dir.get() == str(HOME / "Videos" / "Condense")
assert app._quality_text(22) == "RF 22  ·  VA-API qp 22"
app.gpu_info = g; app._update_header()
assert "ROCm ✓" in app.hw_details, app.hw_details
opened = []
real_popen = subprocess.Popen
pipe.subprocess.Popen = lambda cmd, *a, **k: opened.append(cmd) or real_popen(["true"])
app._render_doc(gpu)
app._open_in_quicktime()
app._show_backups()
pipe.subprocess.Popen = real_popen
opened = [c for c in opened if c[0] in ("xdg-open", "open", "osascript")]
assert [c[0] for c in opened] == ["xdg-open", "xdg-open"] and opened[0][1].endswith("P02.mp4"), opened
app._show_setup_modal()
ok("Linux defaults in Settings, VA-API quality label, ROCm in the header, xdg-open for video and folders")

print("[8] GPU test: a real calculation, the report, and a warning before running on the CPU")
d = g["gpu_test"]
assert d["works"] and d["arch"] == "gfx1100" and d["vram_gb"] == 24.0 and "test_ms" in d, d
assert "AMD Radeon RX 7900 XTX (24 GB)" in app.hw_details and "ROCm ✓" in app.hw_details
ok_, report = app._gpu_report(g)
assert ok_ and report.startswith("✓ The GPU is ready") and "gfx1100, 24.0 GB" in report and "Test calculation: passed in" in report
assert "PyTorch:         2.9.1+rocm6.4 · ROCm 6.4.43482" in report, report
# visible but unusable — what happens with a card the installed ROCm build does not support
os.environ["FAKE_TORCH_BROKEN"] = "1"
bad = plat.SystemProbe.detect_gpu()
os.environ.pop("FAKE_TORCH_BROKEN")
assert not bad["torch_gpu"] and "test calculation failed" in bad["warning"] and "HIP error: invalid device function" in bad["warning"]
assert "HSA_OVERRIDE_GFX_VERSION" in bad["warning"] and bad["gpu_name"] == "AMD Radeon RX 7900 XTX"
ok_, report = app._gpu_report(bad)
assert not ok_ and report.startswith("✗ Whisper cannot use the GPU") and "failed — HIP error: invalid device function" in report
windows_shown = []
app._show_text_window = lambda title, text: windows_shown.append((title, text))
app._on_gpu_test({"type": "gpu_test", "gpu": bad})
assert windows_shown and windows_shown[-1][0] == "GPU test" and "cannot use the GPU" in app.lbl_status.cget("text")
assert "ROCm ✗" in app.hw_details or "⚠" in app.hw_details
# before a run
asked = []
answer = {"v": False}
pipe.messagebox.askyesno = lambda title, text, **k: asked.append((title, text)) or answer["v"]
whisper_task = {"name": "P02", "inputs": ["x"], "steps": {"whisper": True}, "settings": {"whisper_backend": plat.DEFAULT_BACKEND}, "status": "Pending"}
cpu_task = dict(whisper_task, settings={"whisper_backend": "openai-whisper (CPU)"})
video_task = dict(whisper_task, steps={"compress": True})
assert not app._gpu_preflight([whisper_task]) and asked and "invalid device function" in asked[-1][1]
n = len(asked)
assert app._gpu_preflight([cpu_task]) and app._gpu_preflight([video_task]) and len(asked) == n  # no question when CPU is chosen / no Whisper
answer["v"] = True
assert app._gpu_preflight([whisper_task])
app.jobs = [dict(whisper_task, id="t1")]; answer["v"] = False; app.worker = None
app._start_pipeline()
assert app.worker is None and "Not started" in app.lbl_status.cget("text")
app.gpu_info = g
assert app._gpu_preflight([whisper_task]) and len(asked) == n + 2  # GPU fine again: no question
ok("GPU found + test calculation passed → ✓ with VRAM; visible but failing → ✗ with the error and a hint; "
   "Run asks first instead of silently using the CPU")

print("[9] the desktop's own file chooser instead of Tk's old one")
from condense.gui import file_dialogs as fd
argv_log = HOME / ".fake_dialog_argv"
last = lambda: json.loads(argv_log.read_text().splitlines()[-1])
os.environ.pop("XDG_CURRENT_DESKTOP", None)
assert fd.native_backend() == "zenity"
drive = Path(tempfile.mkdtemp(prefix="media_ExternalDrive_"))
os.environ["FAKE_DIALOG_OUT"] = f"{media / 'P02.mp4'}\n{media / 'P03_48k.mp4'}\n"
files = app._ask_open_files("Add videos (one task per video)", str(drive), pipe.MEDIA_FILETYPES)
assert files == (str(media / "P02.mp4"), str(media / "P03_48k.mp4")), files
a = last()
assert a[1] == "--file-selection" and "--multiple" in a and "--separator=\n" in a and f"--filename={drive}/" in a and "--modal" in a
vf = next(x for x in a if x.startswith("--file-filter=Video Files"))
assert "*.mp4" in vf and "*.MP4" in vf and "*.MTS" in vf and any(x == "--file-filter=All Files | *" for x in a), a
os.environ["FAKE_DIALOG_OUT"] = str(drive) + "\n"
assert app._ask_directory("Default output folder", "/media/unplugged-drive") == str(drive)
assert "--directory" in last() and f"--filename={HOME}/" in last()          # gone drive -> start at home
os.environ["FAKE_DIALOG_CODE"] = "1"
assert app._ask_open_files("x", None, pipe.MEDIA_FILETYPES) == ()           # cancelled
os.environ["FAKE_DIALOG_CODE"] = "5"                                         # broken -> Tk's dialog
pipe.filedialog.askopenfilename = lambda **k: "/from/tk.transcript.json"
assert app._ask_open_file("Open transcript", None, [("Condense transcript", "*.transcript.json")]) == "/from/tk.transcript.json"
assert "file dialog failed" in app.console_text.content
os.environ["FAKE_DIALOG_CODE"] = "0"
os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
assert fd.native_backend() == "kdialog"
os.environ["FAKE_DIALOG_OUT"] = f"{media / 'P02.mp4'}\n"
assert app._ask_open_files("Add", str(drive), pipe.MEDIA_FILETYPES) == (str(media / "P02.mp4"),)
k = last()
assert k[0].endswith("kdialog") and k[3:5] == ["--getopenfilename", str(drive)] and "--separate-output" in k and "|Video Files" in k[5]
os.environ.pop("XDG_CURRENT_DESKTOP")
os.environ["CONDENSE_FILE_DIALOG"] = "tk"
assert fd.native_backend() == "tk"
os.environ.pop("CONDENSE_FILE_DIALOG")
app.jobs = []  # drop the bare test task from [8]
before = len(app.jobs)
os.environ["FAKE_DIALOG_OUT"] = f"{media / 'P02.mp4'}\n"
app._add_videos()
assert len(app.jobs) == before + 1 and app.settings["last_add_dir"] == str(media)
ok("zenity (GNOME) or kdialog (KDE): multi-select, .MP4 matched too, starts in the last folder (or home if the drive is gone), "
   "cancel = nothing, failure → Tk's dialog; Add Videos uses it")


print("[10] Ctrl+Enter (save, check, next) works from the transcript too, but only on the Transcript tab")
from types import SimpleNamespace
app.jobs = []
app._render_doc(TD.load(media / "P02" / "P02.transcript.json"))
app._select_span(app.span_lookup[(0, 0)])
app.preview_text.focus_set()
ev = SimpleNamespace(widget=SimpleNamespace(winfo_toplevel=lambda: app))
app.notebook.select(app.tab_settings)
assert app._check_and_next_key(ev) is None and not TD.load(media / "P02" / "P02.transcript.json").segments[0].get("checked")
app.notebook.select(app.tab_preview)
assert app._check_and_next_key(ev) == "break"
d2 = TD.load(media / "P02" / "P02.transcript.json")
assert d2.segments[0].get("checked") and app.edit_seg_idx == 1
dialog = SimpleNamespace(widget=SimpleNamespace(winfo_toplevel=lambda: object()))
assert app._check_and_next_key(dialog) is None and app.edit_seg_idx == 1   # from a dialog: ignored
assert f"<{pipe.MOD}-KP_Enter>" in app.edit_text._binds  # MOD follows the real OS (Command on a Mac)
ok("Ctrl+Enter / keypad Enter from the transcript or the text box; ignored on other tabs and in dialogs")
