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
assert "ROCm ✓" in app.sub_lbl.cget("text"), app.sub_lbl.cget("text")
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
