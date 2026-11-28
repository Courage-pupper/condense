import os
import json, queue, shutil, struct, subprocess, sys, tempfile, threading
from pathlib import Path
os.environ["CONDENSE_PLATFORM"] = "windows"          # before Condense is imported
HOME = Path(os.environ["HOME"])
os.environ["APPDATA"] = str(HOME / "AppData" / "Roaming")
os.environ["LOCALAPPDATA"] = str(HOME / "AppData" / "Local")
sys.path.insert(0, os.environ["CONDENSE_TESTS"])
import stub_tk2
FAKES = Path(os.environ["CONDENSE_FAKES"])
os.environ["PYTHONPATH"] = str(FAKES / "torch_gpu")  # a PyTorch that reports one ROCm GPU (AMD on Windows)
from _compat import pipe
import condense.platform as plat
from condense.platform import windows as win
from condense import updates
ok = lambda m: print("  ✓", m)
TD = pipe.TranscriptDocument
media = Path(os.environ["CONDENSE_TEST_MEDIA"])

print("[1] Windows layer: paths, defaults, opener")
assert plat.PLATFORM == "windows" and plat.APP_SUPPORT_DIR == HOME / "AppData" / "Roaming" / "Condense"
assert plat.log_file() == HOME / "AppData" / "Local" / "Condense" / "Logs" / "condense.log"
assert plat.default_output_dir() == HOME / "Videos" / "Condense" and plat.open_command("C:/x") == ["explorer", "C:/x"]
assert plat.DEFAULT_ENCODER.startswith("auto_h265") and plat.DEFAULT_BACKEND.startswith("PyTorch GPU")
assert plat.SecretStore is plat.CredentialManagerSecret and plat.AppInstaller is plat.WindowsShortcutInstaller
assert win.gpu_vendor(["Intel(R) UHD Graphics 770", "NVIDIA GeForce RTX 4070"]) == "nvidia"
assert win.gpu_vendor(["AMD Radeon RX 7900 XTX"]) == "amd" and win.gpu_vendor(["Intel(R) Arc(TM) A770"]) == "intel"
kw = win.popen_defaults({"text": True})
assert kw["creationflags"] == win.CREATE_NO_WINDOW and kw["encoding"] == "utf-8" and kw["errors"] == "replace"
assert win.popen_defaults({"creationflags": 0})["creationflags"] == 0 and "encoding" not in win.popen_defaults({})
ok("%APPDATA%\\Condense, %LOCALAPPDATA% log, Explorer, auto GPU encoder + PyTorch GPU defaults, no console windows, UTF-8")

print("[2] Hugging Face token in the Credential Manager")
class FakeCreds(dict):
    def write(self, t, s): self[t] = s
    def read(self, t): return self.get(t)
    def delete(self, t): self.pop(t, None)
store = FakeCreds()
win.CredentialManagerSecret._backend = store
S = win.CredentialManagerSecret
assert S.available() and S.load() == "" and S.save("hf_WinToken7") and S.load() == "hf_WinToken7"
assert list(store) == ["Condense/huggingface-token"] and not S.save("bad token & del")
S.delete(); assert S.load() == ""
ok("save / load / reject odd characters / delete through the Credential Manager interface")

print("[3] icon and Start-menu shortcut")
ico = win.build_ico()
reserved, kind, count = struct.unpack("<HHH", ico[:6])
assert (reserved, kind, count) == (0, 1, 4)
sizes = [ico[6 + 16 * i] or 256 for i in range(count)]
assert sizes == [16, 32, 48, 256] and ico[struct.unpack("<I", ico[6 + 12:6 + 16])[0]:][:4] == b"\x89PNG"
ran = []
real_run = subprocess.run
def fake_run(cmd, *a, **k):
    if cmd and cmd[0] == "powershell":
        ran.append((cmd, k.get("env") or {}))
        return subprocess.CompletedProcess(cmd, 0, stdout=k["env"]["CONDENSE_LNK"] + "\r\nC:\\Users\\x\\Desktop\\Condense.lnk\r\n", stderr="")
    return real_run(cmd, *a, **k)
subprocess.run = fake_run
info = win.WindowsShortcutInstaller.install_shortcut()
subprocess.run = real_run
cmd, env = ran[0]
assert "CreateShortcut" in cmd[-1] and "'-m condense'" in cmd[-1] and env["CONDENSE_TARGET"] and env["CONDENSE_ICON"].endswith("condense.ico")
assert Path(info["app"]) == HOME / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Condense.lnk"
assert info["desktop_icon"].endswith("Desktop\\Condense.lnk") and (HOME / "AppData" / "Local" / "Condense" / "condense.ico").exists()
ok("4-size .ico; Start-menu + Desktop shortcuts via PowerShell, paths passed in environment variables")

print("[4] GPU video encoders")
cfg = {"hb_encoder": plat.DEFAULT_ENCODER, "hb_quality": 23, "hb_resolution": "1080p (FHD)", "hb_fps": "Same as source",
       "hb_audio_bitrate": "192", "hb_container": "MKV"}
w = pipe.PipelineWorker(cfg, queue.Queue(), threading.Event())
c, codec, q = w.build_hw_encode_command("nvenc", Path("in.mp4"), Path("o.mkv"), "nvenc_h265")
assert codec == "hevc_nvenc" and c[c.index("-cq") + 1] == "23" and "-b:v" in c and "hvc1" in c and c[c.index("-vf") + 1].startswith("scale=")
c, codec, _ = w.build_hw_encode_command("amf", Path("in.mp4"), Path("o.mkv"), "amf_h264")
assert codec == "h264_amf" and c[c.index("-qp_i") + 1] == "23" and "-qp_b" in c and c[c.index("-rc") + 1] == "cqp"
c, codec, _ = w.build_hw_encode_command("qsv", Path("in.mp4"), Path("o.mkv"), "qsv_h265")
assert codec == "hevc_qsv" and c[c.index("-global_quality") + 1] == "23"
c, codec, _ = w.build_hw_encode_command("cpu", Path("in.mp4"), Path("o.mkv"), "x265")
assert codec == "libx265" and c[c.index("-crf") + 1] == "23"
# this machine's ffmpeg *lists* hevc_nvenc but has no NVIDIA GPU: auto must test, not trust the list
listed = "hevc_nvenc" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
assert not plat.SystemProbe.ffmpeg_encoder_works("hevc_nvenc") and plat.SystemProbe.ffmpeg_encoder_works("libx265")
print(f"     hevc_nvenc listed by ffmpeg: {listed}, usable: False")
os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if not (Path(p) / "HandBrakeCLI").exists())
q1 = queue.Queue()
w = pipe.PipelineWorker(cfg, q1, threading.Event())
out = Path(tempfile.mkdtemp()) / "P02_c.mkv"
w.run_handbrake_encode_for_jobs(media / "P02.mp4", out, "Compress", 0.0, 50.0, [])
logs = [m.get("text", "") for m in list(q1.queue)]
assert out.exists() and out.stat().st_size > 1000 and w.last_encode["encoder"] == "libx265", w.last_encode
assert any("No GPU video encoder works" in t for t in logs) and any("HandBrakeCLI not found; encoding with ffmpeg's x265" in t for t in logs)
plat.SystemProbe._encoder_cache["hevc_amf"] = True     # pretend an AMD GPU encoder works
q2 = queue.Queue()
w = pipe.PipelineWorker(cfg, q2, threading.Event())
cmd2, codec2, _ = w.build_hw_encode_command("amf", Path("a"), Path("b"), "amf_h265")
picked = []
w.run_ffmpeg_hw_encode_for_jobs = lambda **k: picked.append((k["hw"], k["encoder_choice"])) or out
w.run_handbrake_encode_for_jobs(media / "P02.mp4", out, "Compress", 0.0, 50.0, [])
assert picked == [("amf", "amf_h265")], picked
ok("NVENC / AMF / Quick Sync / libx265 commands; auto tests each encoder, picks AMF, else CPU via ffmpeg without HandBrake")

print("[5] Whisper on the GPU, hardware probe")
shutil.rmtree(media / "P02", ignore_errors=True)
task = {"id": "w1", "name": "P02", "inputs": [str(media / "P02.mp4")], "steps": {"whisper": True}, "output_dir": str(media / "P02"),
        "settings": {"audio_format": "WAV (16kHz Mono - Optimal for Whisper)", "whisper_model": "small", "whisper_lang": "en (English)",
                     "diarize_mode": "Disabled (No Speaker Labels)", "whisper_backend": plat.DEFAULT_BACKEND, "fmt_srt": True}}
pipe.PipelineWorker({"tasks": [task]}, queue.Queue(), threading.Event()).run()
doc = TD.load(media / "P02" / "P02.transcript.json")
assert doc.data["provenance"]["whisper"]["backend_used"] == "openai-whisper on ROCm GPU (AMD Radeon RX 7900 XTX)"
win_gpu_names = win.gpu_names
pipe.SystemProbe  # noqa
import condense.platform.windows as wmod
wmod.gpu_names = lambda: ["AMD Radeon RX 7900 XTX"]
g = plat.SystemProbe.detect_gpu()
assert g["torch_gpu"] and g["gpu_api"] == "ROCm" and g["hw_encoders"] == ["hevc_amf"] and "AMF" in g["media_engine"], g
ok("PyTorch backend on the (ROCm) GPU; probe reports ROCm + AMF")

print("[6] updater and GUI on Windows")
upd = HOME / ".condense" / "install.ps1"
upd.parent.mkdir(parents=True, exist_ok=True); upd.write_text("# updater")
assert updates.update_command() == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(upd), "-Update"]
holder = {}
stub_tk2.tk.Tk.mainloop = lambda self: holder.setdefault("app", self)
pipe.launch_gui(); app = holder["app"]
assert app.var_hb_encoder.get() == plat.DEFAULT_ENCODER and app._install_label() == "Add Condense to the Start Menu"
assert app._quality_text(23) == "RF 23  ·  GPU qp 23"
app.gpu_info = g; app._update_header()
assert "ROCm ✓" in app.hw_details and "AMF ✓" in app.hw_details, app.hw_details
opened = []
real_popen = subprocess.Popen
pipe.subprocess.Popen = lambda cmd, *a, **k: opened.append(cmd) or real_popen([sys.executable, "-c", "pass"])
app._render_doc(doc); app._open_in_quicktime(); app._show_backups()
pipe.subprocess.Popen = real_popen
opened = [c for c in opened if c[0] in ("explorer", "xdg-open", "open", "osascript")]
assert [c[0] for c in opened] == ["explorer", "explorer"], opened
app._show_setup_modal()
ok("PowerShell updater command; Start-menu label, GPU qp label, ROCm + AMF in the header, Explorer for video and folders")
